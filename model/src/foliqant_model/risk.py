"""Empirical threshold selection and independent fixed-policy risk auditing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from scipy.stats import beta  # type: ignore[import-untyped]

from .artifacts import (
    ArtifactTransaction,
    create_manifest,
    load_verified_artifact,
    parent_snapshot,
    producer_identity,
    require_disjoint_output,
)
from .contracts import (
    ArtifactManifest,
    AuditDetails,
    PolicyDetails,
    Prediction,
    RiskSampleSummary,
    VersionedComponent,
)
from .errors import ModelError
from .lineage import union_source_rights


@dataclass(frozen=True)
class ThresholdSelection:
    threshold: float | None
    counts: RiskSampleSummary


@dataclass(frozen=True)
class AuditStatistics:
    counts: RiskSampleSummary
    coverage: float
    upper_error_bound: float | None
    status: Literal["insufficient", "meets-bound", "fails-bound"]


def _validate_parameters(max_error: float, min_accepted: int) -> None:
    if isinstance(max_error, bool) or not math.isfinite(max_error) or not 0 < max_error < 1:
        raise ModelError(
            "ARGUMENT_INVALID", "Maximum error must be finite and strictly between zero and one"
        )
    if type(min_accepted) is not int or min_accepted < 1:
        raise ModelError("ARGUMENT_INVALID", "Minimum accepted count must be a positive integer")


def _eligible(representatives: Sequence[Prediction]) -> list[Prediction]:
    if not representatives:
        raise ModelError("RISK_REPRESENTATIVE_MISSING", "Evaluation has no representative rows")
    for row in representatives:
        if not row.representative:
            raise ModelError(
                "RISK_REPRESENTATIVE_MISSING", "Risk input contains a non-representative row"
            )
        if row.meanTokenLogprob is None or not math.isfinite(row.meanTokenLogprob):
            raise ModelError("RISK_SCORE_MISSING", "Every representative requires a finite score")
    return [row for row in representatives if row.applicableValid and row.finishReason == "stop"]


def _counts(
    representatives: Sequence[Prediction],
    eligible: Sequence[Prediction],
    accepted: Sequence[Prediction],
) -> RiskSampleSummary:
    return RiskSampleSummary(
        representativeCount=len(representatives),
        eligibleCount=len(eligible),
        acceptedCount=len(accepted),
        errors=sum(not row.exactCorrect for row in accepted),
    )


def select_threshold(
    representatives: Sequence[Prediction], *, max_error: float, min_accepted: int
) -> ThresholdSelection:
    """Maximize empirical acceptance; correctness labels never affect eligibility."""
    _validate_parameters(max_error, min_accepted)
    eligible = _eligible(representatives)
    # A descending single pass considers whole tied-score groups together.
    groups: dict[float, list[Prediction]] = {}
    for row in eligible:
        assert row.meanTokenLogprob is not None
        groups.setdefault(row.meanTokenLogprob, []).append(row)
    accepted_count, errors = 0, 0
    best = ThresholdSelection(None, _counts(representatives, eligible, []))
    for threshold in sorted(groups, reverse=True):
        accepted_count += len(groups[threshold])
        errors += sum(not row.exactCorrect for row in groups[threshold])
        counts = RiskSampleSummary(
            representativeCount=len(representatives),
            eligibleCount=len(eligible),
            acceptedCount=accepted_count,
            errors=errors,
        )
        if (
            counts.acceptedCount >= min_accepted
            and counts.errors / counts.acceptedCount <= max_error
        ):
            if counts.acceptedCount > best.counts.acceptedCount:
                best = ThresholdSelection(threshold, counts)
    return best


def audit_threshold(
    representatives: Sequence[Prediction],
    *,
    threshold: float | None,
    max_error: float,
    min_accepted: int,
) -> AuditStatistics:
    """Apply a fixed policy and compute its one-sided 95% exact binomial bound."""
    _validate_parameters(max_error, min_accepted)
    if threshold is not None and (not math.isfinite(threshold) or threshold > 0):
        raise ModelError("ARGUMENT_INVALID", "Threshold must be finite and at most zero")
    eligible = _eligible(representatives)
    accepted = (
        []
        if threshold is None
        else [
            row
            for row in eligible
            if row.meanTokenLogprob is not None and row.meanTokenLogprob >= threshold
        ]
    )
    counts = _counts(representatives, eligible, accepted)
    upper: float | None = None
    if counts.acceptedCount:
        upper = (
            1.0
            if counts.errors == counts.acceptedCount
            else float(beta.ppf(0.95, counts.errors + 1, counts.acceptedCount - counts.errors))
        )
        if not math.isfinite(upper) or not 0 <= upper <= 1:
            raise ModelError("OUTPUT_INVALID", "Exact binomial calculation failed")
    status: Literal["insufficient", "meets-bound", "fails-bound"] = "insufficient"
    if counts.acceptedCount >= min_accepted:
        assert upper is not None
        status = "meets-bound" if upper <= max_error else "fails-bound"
    return AuditStatistics(counts, counts.acceptedCount / counts.representativeCount, upper, status)


def _representatives(path: Path, manifest: ArtifactManifest) -> list[Prediction]:
    from .evaluation import read_verified_predictions

    return [row for row in read_verified_predictions(path, manifest) if row.representative]


def _publish(
    output: Path,
    kind: Literal["policy", "audit"],
    details: PolicyDetails | AuditDetails,
    parents: Sequence[ArtifactManifest],
) -> ArtifactManifest:
    command: Literal["calibrate", "audit"] = "calibrate" if kind == "policy" else "audit"
    scope = parents[0].root
    components = (
        [VersionedComponent(name="scipy", version=version("scipy"), role="library")]
        if command == "audit"
        else []
    )
    with ArtifactTransaction(
        output, command, parent_artifact_ids=tuple(p.root.artifactId for p in parents)
    ) as transaction:
        fields: dict[str, object] = {
            "schemaVersion": 1,
            "kind": kind,
            "name": f"{kind}-{transaction.run_id}",
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "stage": scope.stage,
            "parents": [parent_snapshot(p).model_dump(mode="json") for p in parents],
            "sourceRights": [
                right.model_dump(mode="json") for right in union_source_rights(parents)
            ],
            "producer": producer_identity(command, components).model_dump(mode="json"),
            "details": details.model_dump(mode="json"),
        }
        if scope.customer is not None:
            fields["customer"] = scope.customer
        manifest = create_manifest(transaction.staging_path, fields)
        transaction.publish(manifest)
        return manifest


def calibrate_policy(
    evaluation_path: Path, output: Path, *, max_error: float, min_accepted: int
) -> ArtifactManifest:
    """Select a policy using only a verified calibration-split evaluation."""
    _validate_parameters(max_error, min_accepted)
    evaluation = load_verified_artifact(evaluation_path)
    if evaluation.root.kind != "evaluation" or evaluation.root.details.split != "calibration":
        raise ModelError("ARGUMENT_INVALID", "Policy selection requires a calibration evaluation")
    require_disjoint_output(output, [evaluation_path])
    representatives = _representatives(evaluation_path, evaluation)
    selection = select_threshold(representatives, max_error=max_error, min_accepted=min_accepted)
    ev = evaluation.root.details
    values: dict[str, object] = {
        "evaluationArtifactId": evaluation.root.artifactId,
        "deploymentProfileId": ev.deploymentProfileId,
        "datasetArtifactId": ev.datasetArtifactId,
        "selectionUnit": "component-representative-v1",
        "representativeRecordIds": sorted(row.id for row in representatives),
        "representativeGroupIds": sorted(
            {group for row in representatives for group in row.groupIds}
        ),
        "threshold": selection.threshold,
        "maxError": max_error,
        "minAccepted": min_accepted,
        "selection": selection.counts.model_dump(mode="json"),
        "scoreMethod": "mean-generated-token-logprob-v1",
    }
    if selection.threshold is None:
        values["reason"] = (
            "No threshold satisfies the empirical error and minimum acceptance requirements"
        )
    return _publish(output, "policy", PolicyDetails.model_validate(values), [evaluation])


def audit_policy(evaluation_path: Path, policy_path: Path, output: Path) -> ArtifactManifest:
    """Audit a fixed policy on its matching, disjoint test evaluation."""
    evaluation, policy = (
        load_verified_artifact(evaluation_path),
        load_verified_artifact(policy_path),
    )
    require_disjoint_output(output, [evaluation_path, policy_path])
    if evaluation.root.kind != "evaluation" or evaluation.root.details.split != "test":
        raise ModelError("ARGUMENT_INVALID", "Audit requires a test evaluation")
    if policy.root.kind != "policy":
        raise ModelError("ARGUMENT_INVALID", "Audit requires a selected policy")
    ev, pol = evaluation.root.details, policy.root.details
    if ev.deploymentProfileId != pol.deploymentProfileId:
        raise ModelError("PROFILE_MISMATCH", "Test and calibration deployment profiles differ")
    if ev.datasetArtifactId != pol.datasetArtifactId:
        raise ModelError("LINEAGE_MISMATCH", "Test and calibration datasets differ")
    calibration = policy.root.parents[0]
    if calibration.kind != "evaluation":
        raise ModelError("LINEAGE_MISMATCH", "Policy has no calibration evaluation")
    if set(ev.selectedRecordIds) & set(calibration.details.selectedRecordIds) or set(
        ev.selectedGroupIds
    ) & set(calibration.details.selectedGroupIds):
        raise ModelError("SAMPLE_OVERLAP", "Calibration and test samples overlap")
    representatives = _representatives(evaluation_path, evaluation)
    result = audit_threshold(
        representatives,
        threshold=pol.threshold,
        max_error=pol.maxError,
        min_accepted=pol.minAccepted,
    )
    details = AuditDetails(
        evaluationArtifactId=evaluation.root.artifactId,
        policyArtifactId=policy.root.artifactId,
        deploymentProfileId=ev.deploymentProfileId,
        datasetArtifactId=ev.datasetArtifactId,
        selectionUnit="component-representative-v1",
        representativeRecordIds=sorted(row.id for row in representatives),
        representativeGroupIds=sorted({group for row in representatives for group in row.groupIds}),
        counts=result.counts,
        total=ev.aggregate.total,
        coverage=result.coverage,
        upperErrorBound=result.upper_error_bound,
        status=result.status,
        diagnostic=ev.diagnostic,
        assumptions=sorted(
            [
                "One predetermined representative per declared independent component",
                "Policy fixed before examining test outcomes",
                "Undeclared relatedness and distribution shifts are not covered",
                "Diagnostic datasets do not establish production readiness",
            ]
        ),
    )
    return _publish(output, "audit", details, [evaluation, policy])
