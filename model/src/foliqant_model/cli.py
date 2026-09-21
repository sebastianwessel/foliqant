"""Command-line entry point for the implemented local lifecycle operations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Never, cast

from .contracts import ArtifactManifest, CliSuccess
from .contracts.base import Command
from .contracts.cli import CreatedArtifactResult, PrepareResult, SplitRecordCounts, VerifyResult
from .errors import ModelError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # Argparse messages can echo unrecognized values; keep the JSON boundary redacted.
        raise ModelError("ARGUMENT_INVALID", "Invalid arguments; use --help for supported options")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="foliqant-model", description="Build and customize models locally.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local backend capabilities")
    schema = commands.add_parser("schema", help="Export or check public JSON schemas")
    schema_mode = schema.add_mutually_exclusive_group(required=True)
    schema_mode.add_argument("--output", type=Path)
    schema_mode.add_argument("--check", type=Path)
    setup = commands.add_parser("setup", help="Download and prepare the small local exercise")
    setup.add_argument("--workspace", type=Path)
    setup.add_argument("--offline", action="store_true")
    setup.add_argument("--timeout-seconds", type=int, default=3600)
    fetch = commands.add_parser("fetch", help="Download a pinned upstream model")
    fetch.add_argument("--repo", required=True)
    fetch.add_argument("--revision", required=True)
    fetch.add_argument("--license", dest="license_ref", required=True)
    fetch.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Validate and split local training data")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    projections = commands.add_parser(
        "prepare-source-projections", help="Prepare scoped source decisions without model calls"
    )
    projections.add_argument("--from-run", type=Path, required=True)
    projections.add_argument("--output", type=Path)
    projections.add_argument("--pilot", action="store_true", help="Prepare 32 new training tasks")
    migrate = commands.add_parser(
        "migrate-decisions", help="Migrate a completed native run without model calls"
    )
    migrate.add_argument("--from-run", type=Path, required=True)
    migrate.add_argument("--output", type=Path)
    rerun = commands.add_parser(
        "rerun-migrated-decisions", help="Run selected pending tasks from a frozen migration"
    )
    rerun.add_argument("--from-migration", type=Path, required=True)
    rerun.add_argument("--output", type=Path)
    rerun.add_argument("--limit", type=int)
    rerun.add_argument("--job-id", dest="job_ids", action="append", metavar="RECORD_ID")
    rerun.add_argument("--progress", choices=("auto", "always", "never"), default="auto")
    curate = commands.add_parser("curate", help="Prepare or resume automated dataset curation")
    curate.add_argument("--config", type=Path, required=True)
    curate.add_argument("--workspace", type=Path)
    curate.add_argument("--prepare-only", action="store_true")
    curate.add_argument("--offline", action="store_true")
    curate.add_argument("--progress", choices=("auto", "always", "never"), default="auto")
    curate.add_argument(
        "--repair-from",
        type=Path,
        help="Create or resume an immutable child run that retries only quarantined jobs",
    )
    curate.add_argument(
        "--continue-from",
        type=Path,
        help="Keep completed native outcomes and generate only unfinished jobs in a child run",
    )
    curate.add_argument("--extend-projections-from", type=Path)
    curate.add_argument("--projection-plan", type=Path)
    for name in ("train", "customize"):
        operation = commands.add_parser(name, help="Train a shared or customer adapter")
        operation.add_argument("--config", type=Path, required=True)
        operation.add_argument("--model", type=Path, required=True)
        operation.add_argument("--dataset", type=Path, required=True)
        operation.add_argument("--output", type=Path, required=True)
        operation.add_argument("--warm-start", type=Path)
        if name == "customize":
            operation.add_argument("--customer", required=True)
    evaluate = commands.add_parser("evaluate", help="Generate and score held-out responses")
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--model", type=Path, required=True)
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--adapter", type=Path)
    evaluate.add_argument("--split", choices=("validation", "calibration", "test"), required=True)
    calibrate = commands.add_parser("calibrate", help="Select an empirical acceptance threshold")
    calibrate.add_argument("--evaluation", type=Path, required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--max-error", type=float, required=True)
    calibrate.add_argument("--min-accepted", type=int, required=True)
    audit = commands.add_parser("audit", help="Audit a fixed policy on a separate test split")
    audit.add_argument("--evaluation", type=Path, required=True)
    audit.add_argument("--policy", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    for name in ("quantize", "merge", "export"):
        operation = commands.add_parser(name, help="Convert or package verified model weights")
        operation.add_argument("--model", type=Path, required=True)
        operation.add_argument("--output", type=Path, required=True)
        operation.add_argument("--timeout-seconds", type=int, default=3600)
        if name == "quantize":
            operation.add_argument("--bits", type=int, choices=(4, 8), required=True)
            operation.add_argument("--group-size", type=int, choices=(64,), default=64)
        elif name == "merge":
            operation.add_argument("--adapter", type=Path, required=True)
        else:
            operation.add_argument("--format", choices=("checkpoint", "gguf"), required=True)
    verify = commands.add_parser("verify", help="Verify a completed artifact and recorded ancestry")
    verify.add_argument("path", type=Path)
    return parser


def _created(manifest: ArtifactManifest, output: Path) -> CreatedArtifactResult:
    return CreatedArtifactResult(
        outputPath=str(output.absolute()),
        artifactId=manifest.root.artifactId,
        kind=manifest.root.kind,
        stage=manifest.root.stage,
        customer=manifest.root.customer,
    )


def main(argv: list[str] | None = None) -> int:
    """Run one command with exactly one success object or a redacted failure envelope."""
    command: Command | None = None
    try:
        args = _parser().parse_args(argv)
        command = cast(Command, args.command)
        if command == "doctor":
            from .doctor import diagnose

            payload = diagnose(
                [
                    "doctor",
                    "schema",
                    "setup",
                    "fetch",
                    "prepare",
                    "prepare-source-projections",
                    "migrate-decisions",
                    "rerun-migrated-decisions",
                    "curate",
                    "train",
                    "customize",
                    "evaluate",
                    "calibrate",
                    "audit",
                    "verify",
                    "quantize",
                    "merge",
                    "export",
                ]
            ).model_dump(mode="json")
        elif command == "schema":
            from .schemas import export_schemas

            payload = export_schemas(
                args.check if args.check is not None else args.output,
                check=args.check is not None,
            ).model_dump(mode="json")
        elif command == "setup":
            from .setup import run_setup

            result = run_setup(
                args.workspace, offline=args.offline, timeout_seconds=args.timeout_seconds
            )
            payload = result.model_dump(mode="json")
        elif command == "fetch":
            from .contracts.cli import FetchResult
            from .fetch import fetch_model

            manifest = fetch_model(
                repo=args.repo,
                revision=args.revision,
                license_ref=args.license_ref,
                output=args.output,
            )
            payload = FetchResult(
                command="fetch",
                artifact=CreatedArtifactResult(
                    outputPath=str(args.output.absolute()),
                    artifactId=manifest.root.artifactId,
                    kind="checkpoint",
                    stage="upstream",
                    customer=None,
                ),
                fileCount=len(manifest.root.files),
            ).model_dump(mode="json")
        elif command == "prepare":
            from .data import prepare_dataset

            manifest = prepare_dataset(args.config, args.output)
            if manifest.root.kind != "dataset":
                raise ModelError("INTERNAL_ERROR", "Preparation returned an unexpected artifact")
            details = manifest.root.details
            payload = PrepareResult(
                command="prepare",
                artifact=CreatedArtifactResult(
                    outputPath=str(args.output.absolute()),
                    artifactId=manifest.root.artifactId,
                    kind="dataset",
                    stage="none",
                    customer=None,
                ),
                datasetContentId=details.datasetContentId,
                partitionRecords=SplitRecordCounts(
                    train=details.partitions.train.records,
                    validation=details.partitions.validation.records,
                    calibration=details.partitions.calibration.records,
                    test=details.partitions.test.records,
                ),
                diagnostic=details.diagnostic,
            ).model_dump(mode="json")
        elif command == "prepare-source-projections":
            from .curation.projection_preparation import prepare_source_projections

            payload = prepare_source_projections(
                args.from_run, args.output, pilot=args.pilot
            ).model_dump(mode="json")
        elif command == "migrate-decisions":
            from .contracts.cli import MigrationResult
            from .curation.migration import migrate_decisions

            migrated = migrate_decisions(args.from_run, args.output)
            payload = MigrationResult.model_validate(
                {
                    "command": command,
                    **{
                        field: migrated[field]
                        for field in (
                            "migrationPath",
                            "datasetPath",
                            "artifactId",
                            "reportPath",
                            "records",
                            "pendingTasks",
                            "reviewItems",
                        )
                    },
                },
                strict=True,
            ).model_dump(mode="json")
        elif command == "rerun-migrated-decisions":
            from .contracts.cli import MigrationResult
            from .curation.migration_rerun import rerun_migration
            from .curation.runtime import CurationControl

            rerun_control = CurationControl(progress=args.progress, handle_signals=True)
            rerun_result = rerun_migration(
                args.from_migration,
                args.output,
                limit=args.limit,
                job_ids=args.job_ids,
                control=rerun_control,
            )
            payload = MigrationResult.model_validate(
                {
                    "command": command,
                    **{
                        field: rerun_result[field]
                        for field in (
                            "migrationPath",
                            "datasetPath",
                            "artifactId",
                            "reportPath",
                            "records",
                            "pendingTasks",
                            "reviewItems",
                        )
                    },
                },
                strict=True,
            ).model_dump(mode="json")
        elif command == "curate":
            from .curation.runner import run_curation
            from .curation.runtime import CurationControl

            curation_control = CurationControl(progress=args.progress, handle_signals=True)
            curate_result = run_curation(
                args.config,
                args.workspace,
                prepare_only=args.prepare_only,
                offline=args.offline,
                repair_from=args.repair_from,
                continue_from=args.continue_from,
                extend_projections_from=args.extend_projections_from,
                projection_plan=args.projection_plan,
                control=curation_control,
            )
            payload = curate_result.model_dump(mode="json")
        elif command in {"train", "customize"}:
            from .contracts.cli import CustomizeResult, TrainResult
            from .training import train_model

            manifest = train_model(
                args.config,
                args.model,
                args.dataset,
                args.output,
                customer=args.customer if command == "customize" else None,
                warm_start=args.warm_start,
            )
            if manifest.root.kind != "adapter":
                raise ModelError("INTERNAL_ERROR", "Training returned an unexpected artifact")
            result_values = {
                "command": command,
                "artifact": _created(manifest, args.output),
                "finalLoss": manifest.root.details.finalLoss,
                "elapsedSeconds": manifest.root.details.elapsedSeconds,
            }
            result_class = TrainResult if command == "train" else CustomizeResult
            payload = result_class.model_validate(result_values).model_dump(mode="json")
        elif command == "evaluate":
            from .contracts.cli import EvaluateResult
            from .evaluation import evaluate_model

            manifest = evaluate_model(
                args.config,
                args.model,
                args.dataset,
                args.output,
                split=args.split,
                adapter_path=args.adapter,
            )
            if manifest.root.kind != "evaluation":
                raise ModelError("INTERNAL_ERROR", "Evaluation returned an unexpected artifact")
            evaluation_details = manifest.root.details
            payload = EvaluateResult(
                command="evaluate",
                artifact=_created(manifest, args.output),
                split=evaluation_details.split,
                exampleCount=evaluation_details.aggregate.total,
                representativeCount=len(evaluation_details.representatives),
                exact=evaluation_details.aggregate.exact,
                predictionsPath=str(args.output.absolute() / evaluation_details.predictions.path),
            ).model_dump(mode="json")
        elif command == "calibrate":
            from .contracts.cli import CalibrateResult
            from .risk import calibrate_policy

            manifest = calibrate_policy(
                args.evaluation,
                args.output,
                max_error=args.max_error,
                min_accepted=args.min_accepted,
            )
            if manifest.root.kind != "policy":
                raise ModelError("INTERNAL_ERROR", "Calibration returned an unexpected artifact")
            policy_details = manifest.root.details
            payload = CalibrateResult(
                command="calibrate",
                artifact=_created(manifest, args.output),
                threshold=policy_details.threshold,
                selection=policy_details.selection,
                abstainAll=policy_details.threshold is None,
            ).model_dump(mode="json")
        elif command == "audit":
            from .contracts.cli import AuditResult
            from .risk import audit_policy

            manifest = audit_policy(args.evaluation, args.policy, args.output)
            if manifest.root.kind != "audit":
                raise ModelError("INTERNAL_ERROR", "Audit returned an unexpected artifact")
            audit_details = manifest.root.details
            payload = AuditResult(
                command="audit",
                artifact=_created(manifest, args.output),
                status=audit_details.status,
                counts=audit_details.counts,
                coverage=audit_details.coverage,
                upperErrorBound=audit_details.upperErrorBound,
            ).model_dump(mode="json")
        elif command == "quantize":
            from .contracts.cli import QuantizeResult
            from .transformations import quantize_model

            manifest = quantize_model(
                args.model,
                args.output,
                bits=args.bits,
                group_size=args.group_size,
                timeout_seconds=args.timeout_seconds,
            )
            payload = QuantizeResult(
                command="quantize",
                artifact=_created(manifest, args.output),
                bits=args.bits,
                groupSize=args.group_size,
            ).model_dump(mode="json")
        elif command == "merge":
            from .contracts.cli import MergeResult
            from .transformations import merge_model

            manifest = merge_model(
                args.model, args.adapter, args.output, timeout_seconds=args.timeout_seconds
            )
            if manifest.root.kind != "merged":
                raise ModelError("INTERNAL_ERROR", "Merge returned an unexpected artifact")
            payload = MergeResult(
                command="merge",
                artifact=_created(manifest, args.output),
                modelArtifactId=manifest.root.details.modelParentArtifactId,
                adapterArtifactId=manifest.root.details.adapterArtifactId,
            ).model_dump(mode="json")
        elif command == "export":
            from .contracts.cli import ExportResult
            from .transformations import export_model

            manifest = export_model(
                args.model, args.output, format=args.format, timeout_seconds=args.timeout_seconds
            )
            payload = ExportResult(
                command="export",
                artifact=_created(manifest, args.output),
                format=args.format,
                compatibilityStatus="unverified",
            ).model_dump(mode="json")
        elif command == "verify":
            from .artifacts import load_verified_artifact

            manifest = load_verified_artifact(args.path)

            from .lineage import collect_ancestor_nodes

            payload = VerifyResult(
                command="verify",
                path=str(args.path.absolute()),
                artifactId=manifest.root.artifactId,
                kind=manifest.root.kind,
                stage=manifest.root.stage,
                customer=manifest.root.customer,
                fileCount=len(manifest.root.files),
                lineageNodeCount=len(collect_ancestor_nodes(manifest)),
                valid=True,
            ).model_dump(mode="json")
        else:
            raise ModelError("ARGUMENT_INVALID", "Unsupported command")
        envelope = CliSuccess.model_validate(
            {
                "schemaVersion": 1,
                "ok": True,
                "command": command,
                "result": payload,
            }
        )
        print(envelope.model_dump_json())
        return 0
    except ModelError as error:
        failure = error
    except KeyboardInterrupt:
        failure = ModelError("INTERRUPTED", "Operation interrupted")
    except OSError:
        failure = ModelError("IO_FAILED", "Cannot access the requested local files")
    except Exception:
        failure = ModelError("INTERNAL_ERROR", "Unexpected operation failure")
    print(failure.as_failure(command).model_dump_json(), file=sys.stderr)
    return failure.exit_code
