"""Pure row transforms for immutable native decision-data migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from foliqant.decisions import (
    ChoiceResult,
    DecisionInput,
    DecisionOutput,
    PredicateResult,
    semantic_signature,
    validate_decision_output,
)

from ..contracts.inputs import DataRecord
from ..errors import ModelError
from .decision_generation import canonical_decision_output, validate_decision_rewrite
from .decision_seeds import DecisionSeed
from .source_category_catalogs import banking77_catalog

MigrationOperation = Literal["retained", "reprojected"]


@dataclass(frozen=True, slots=True)
class MigrationTransform:
    """One accepted transform or a stable reason for refusing it."""

    record: DataRecord | None
    reason: str
    operation: MigrationOperation


def _failure(operation: MigrationOperation, reason: str) -> MigrationTransform:
    return MigrationTransform(record=None, reason=reason, operation=operation)


def _original_source_record(record: DataRecord) -> str | None:
    matches = [
        tag.removeprefix("original-source-record:")
        for tag in record.tags
        if tag.startswith("original-source-record:")
    ]
    if len(matches) != 1 or not matches[0]:
        return None
    return matches[0]


def _record_membership_problem(seed: DecisionSeed, record: DataRecord) -> str | None:
    parent = seed.parent
    if (
        record.language != parent.language
        or record.familyId != parent.familyId
        or record.groupKeys != parent.groupKeys
    ):
        return "record-lineage-mismatch"
    if record.generation is None:
        if record.sourceId != parent.sourceId or record != parent:
            return "non-generated-record-changed"
    else:
        if record.sourceId != "generated-" + parent.sourceId:
            return "generated-source-mismatch"
        if parent.id not in record.generation.parentRecordIds:
            return "generated-parent-missing"
    return None


def _record_contract_problem(
    seed: DecisionSeed, record: DataRecord, *, check_membership: bool = True
) -> str | None:
    if check_membership:
        membership = _record_membership_problem(seed, record)
        if membership is not None:
            return membership
    try:
        task = DecisionInput.model_validate_json(record.messages[-2].content, strict=True)
        output = DecisionOutput.model_validate_json(record.messages[-1].content, strict=True)
    except (ValidationError, ValueError, IndexError):
        return "record-native-json-invalid"
    if task.questions != seed.input.questions:
        return "record-question-contract-changed"
    if seed.mode == "annotate" and task != seed.input:
        return "annotated-task-changed"
    problems = validate_decision_output(task, output)
    if problems:
        return "record-output-invalid:" + problems[0]
    if semantic_signature(output) != semantic_signature(seed.oracle):
        return "record-reference-semantics-changed"
    if seed.mode == "rewrite" and record.generation is not None:
        rewrite_problem = validate_decision_rewrite(
            seed.input,
            task,
            rewrite_source_ids=seed.rewriteSourceIds,
        )
        if rewrite_problem is not None:
            return "record-" + rewrite_problem
    try:
        canonical = canonical_decision_output(seed, task)
    except ModelError:
        return "record-canonical-evidence-invalid"
    if output != canonical:
        return "record-canonical-output-changed"
    return None


def _seed_identity_problem(old_seed: DecisionSeed, current_seed: DecisionSeed) -> str | None:
    old_parent = old_seed.parent
    current_parent = current_seed.parent
    if (
        old_seed.scenario != current_seed.scenario
        or old_seed.mode != current_seed.mode
        or old_seed.rewriteSourceIds != current_seed.rewriteSourceIds
        or old_parent.sourceId != current_parent.sourceId
        or old_parent.language != current_parent.language
        or old_parent.familyId != current_parent.familyId
        or old_parent.groupKeys != current_parent.groupKeys
    ):
        return "seed-lineage-mismatch"
    return None


def _banking77_relation_problem(old_seed: DecisionSeed, current_seed: DecisionSeed) -> str | None:
    old_question = old_seed.input.questions[0]
    current_question = current_seed.input.questions[0]
    old_result = old_seed.oracle.results[0]
    current_result = current_seed.oracle.results[0]
    if old_question.type != "choice" or current_question.type != "choice":
        return "banking77-question-type-invalid"
    if not isinstance(old_result, ChoiceResult) or not isinstance(current_result, ChoiceResult):
        return "banking77-result-type-invalid"
    if old_result.answer is None or current_result.answer is None:
        return "banking77-source-label-missing"
    old_options = {option.id: option.description for option in old_question.options}
    source_label = old_options.get(old_result.answer.optionId)
    if source_label is None:
        return "banking77-source-label-missing"
    if f"source-label:{source_label}" not in current_seed.parent.tags:
        return "banking77-source-label-changed"
    expected_option = banking77_catalog(current_seed.parent.language).resolve_id(source_label)
    if current_result.answer.optionId != expected_option:
        return "banking77-source-label-changed"
    old_sources = {source.id: source.text for source in old_seed.input.state.sources}
    current_sources = {source.id: source.text for source in current_seed.input.state.sources}
    if old_sources.get("request") != current_sources.get("request"):
        return "banking77-request-changed"
    return None


def _wanli_relation_problem(old_seed: DecisionSeed, current_seed: DecisionSeed) -> str | None:
    old_question = old_seed.input.questions[0]
    current_question = current_seed.input.questions[0]
    old_result = old_seed.oracle.results[0]
    current_result = current_seed.oracle.results[0]
    if old_question.type != "predicate" or current_question.type != "choice":
        return "wanli-question-type-invalid"
    if not isinstance(old_result, PredicateResult) or not isinstance(current_result, ChoiceResult):
        return "wanli-result-type-invalid"
    if current_result.answer is None:
        return "wanli-source-label-missing"
    relation_by_value = {
        "true": ("supported", "entailment"),
        "false": ("contradicted", "contradiction"),
        "unknown": ("insufficient", "neutral"),
    }
    source_label, relation = relation_by_value[old_result.answer.value]
    if current_result.answer.optionId != relation:
        return "wanli-source-label-changed"
    required_tags = {f"source-label:{source_label}", f"source-nli-relation:{relation}"}
    if not required_tags <= set(current_seed.parent.tags):
        return "wanli-source-label-changed"
    old_sources = {source.id: source.text for source in old_seed.input.state.sources}
    current_sources = {source.id: source.text for source in current_seed.input.state.sources}
    if old_sources.get("evidence") != current_sources.get("premise"):
        return "wanli-premise-changed"
    prefixes = (
        "Does the supplied evidence establish this claim: ",
        "Belegen die bereitgestellten Nachweise diese Behauptung: ",
    )
    claim = next(
        (
            old_question.prompt[len(prefix) :]
            for prefix in prefixes
            if old_question.prompt.startswith(prefix)
        ),
        None,
    )
    if claim is None or claim != current_sources.get("hypothesis"):
        return "wanli-hypothesis-changed"
    return None


def _projection_relation_problem(old_seed: DecisionSeed, current_seed: DecisionSeed) -> str | None:
    if old_seed.scenario not in {"projection:banking77", "projection:wanli"}:
        return "projection-rule-unsupported"
    old_original = _original_source_record(old_seed.parent)
    current_original = _original_source_record(current_seed.parent)
    if old_original is None or old_original != current_original:
        return "projection-source-record-changed"
    if old_seed.mode != "annotate" or current_seed.mode != "annotate":
        return "projection-mode-invalid"
    if len(old_seed.input.questions) != 1 or len(current_seed.input.questions) != 1:
        return "projection-question-count-invalid"
    if old_seed.scenario == "projection:banking77":
        return _banking77_relation_problem(old_seed, current_seed)
    if old_seed.scenario == "projection:wanli":
        return _wanli_relation_problem(old_seed, current_seed)
    return "projection-rule-unsupported"


def transform_record(
    record: DataRecord,
    old_seed: DecisionSeed,
    current_seed: DecisionSeed,
) -> MigrationTransform:
    """Retain unchanged rows or rebuild supported projections without model inference.

    A retained result is the exact input object and keeps its historical generation
    provenance. A reprojected result is the current deterministic seed parent; callers
    must record source-annotation provenance and must not inherit historical blind
    verification from the replaced task.
    """

    identity_problem = _seed_identity_problem(old_seed, current_seed)
    if identity_problem is not None:
        return _failure("retained", identity_problem)
    old_problem = _record_contract_problem(old_seed, record)
    if old_problem is not None:
        return _failure("retained", old_problem)
    unchanged = old_seed.input == current_seed.input and semantic_signature(
        old_seed.oracle
    ) == semantic_signature(current_seed.oracle)
    if unchanged:
        current_problem = _record_contract_problem(current_seed, record, check_membership=False)
        if current_problem is not None:
            return _failure("retained", "current-" + current_problem)
        return MigrationTransform(
            record=record,
            reason="unchanged-task-and-reference-semantics",
            operation="retained",
        )
    projection_problem = _projection_relation_problem(old_seed, current_seed)
    if projection_problem is not None:
        return _failure("reprojected", projection_problem)
    current_problem = _record_contract_problem(current_seed, current_seed.parent)
    if current_problem is not None:
        return _failure("reprojected", "current-" + current_problem)
    return MigrationTransform(
        record=current_seed.parent,
        reason="deterministic-source-annotation-reprojection",
        operation="reprojected",
    )
