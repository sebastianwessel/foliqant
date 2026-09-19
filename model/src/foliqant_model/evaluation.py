"""Verified model evaluation orchestration and prediction artifact loading."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import stat
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import ValidationError

from .artifacts import (
    ArtifactTransaction,
    create_manifest,
    load_verified_artifact,
    parent_snapshot,
    producer_identity,
    require_disjoint_output,
    write_private_bytes,
)
from .configuration import load_config, parse_json
from .contracts import (
    ArtifactManifest,
    ComponentRepresentative,
    ConfigIdentity,
    DataRecord,
    DatasetDetails,
    DeploymentProfile,
    EvaluationAggregate,
    EvaluationConfig,
    GenerateWorkerRequest,
    GenerateWorkerResult,
    InventoryFileRef,
    Prediction,
    VersionedComponent,
    WorkerFailure,
    WorkerRequest,
    canonical_digest,
)
from .errors import ModelError
from .execution import run_worker
from .lineage import leakage_reference, read_leakage_index, union_source_rights
from .scoring import (
    ReferenceScore,
    aggregate_predictions,
    json_pointer_value,
    parse_strict_json,
    prepare_reference,
    score_generated,
    structural_equal,
    validate_output_schema,
)

_MAX_LOCAL_FILE_BYTES = 16 * 1024 * 1024


class _SchemaValidator(Protocol):
    def is_valid(self, instance: object) -> bool: ...


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _read_regular(path: Path, *, label: str, max_bytes: int) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ModelError("UNSAFE_ARTIFACT_PATH", f"{label} must be a regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ModelError("INTEGRITY_FAILED", f"{label} changed while opening")
        data = os.read(descriptor, max_bytes + 1)
        if len(data) > max_bytes:
            raise ModelError("CONFIG_TOO_LARGE", f"{label} exceeds the size limit")
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise ModelError("INTEGRITY_FAILED", f"{label} changed while reading")
        return data
    except ModelError:
        raise
    except FileNotFoundError as error:
        raise ModelError("INPUT_NOT_FOUND", f"{label} does not exist") from error
    except OSError as error:
        raise ModelError("IO_FAILED", f"{label} could not be read") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_schema(config_path: Path, configured: str | None) -> tuple[bytes | None, object | None]:
    if configured is None:
        return None, None
    path = Path(configured)
    if not path.is_absolute():
        path = config_path.parent / path
    data = _read_regular(path, label="output schema", max_bytes=_MAX_LOCAL_FILE_BYTES)
    try:
        value = parse_json(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ModelError) as error:
        raise ModelError("CONFIG_INVALID", "Output schema must be strict UTF-8 JSON") from error
    validate_output_schema(value)
    return data, value


def _read_split_records(
    dataset_path: Path, details: DatasetDetails, split: Literal["validation", "calibration", "test"]
) -> list[DataRecord]:
    reference = getattr(details.recordFiles, split)
    data = _read_regular(
        dataset_path / reference.path,
        label="dataset split",
        max_bytes=max(reference.recordCount * 16_777_216, 1),
    )
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ModelError("INTEGRITY_FAILED", "Dataset split digest changed")
    lines = data.splitlines(keepends=True)
    if any(not line.endswith(b"\n") for line in lines):
        raise ModelError("INTEGRITY_FAILED", "Dataset rows must end with LF")
    records: list[DataRecord] = []
    for line in lines:
        try:
            value = parse_json(line[:-1].decode("utf-8", errors="strict"))
            record = DataRecord.model_validate(value, strict=True)
        except (UnicodeError, ModelError, ValidationError) as error:
            raise ModelError("INTEGRITY_FAILED", "Dataset row is invalid") from error
        assignment = details.assignments.get(record.id)
        if assignment is None or assignment.split != split:
            raise ModelError("INTEGRITY_FAILED", "Dataset row assignment is inconsistent")
        records.append(record)
    if len(records) != reference.recordCount:
        raise ModelError("INTEGRITY_FAILED", "Dataset split count changed")
    identifiers = [record.id for record in records]
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        raise ModelError("INTEGRITY_FAILED", "Dataset split rows must be sorted and unique")
    return records


def _validate_model_and_adapter(model: ArtifactManifest, adapter: ArtifactManifest | None) -> None:
    if model.root.kind not in {"checkpoint", "quantized", "merged", "export"}:
        raise ModelError("LINEAGE_MISMATCH", "Evaluation model artifact kind is invalid")
    if adapter is None:
        return
    if adapter.root.kind != "adapter":
        raise ModelError("LINEAGE_MISMATCH", "Evaluation adapter artifact kind is invalid")
    if adapter.root.details.modelArtifactId != model.root.artifactId:
        raise ModelError("LINEAGE_MISMATCH", "Adapter does not belong to the evaluation model")


def _reject_selected_leakage(
    selected_ids: list[str],
    dataset_path: Path,
    dataset: ArtifactManifest,
    model_path: Path,
    model: ArtifactManifest,
    adapter_path: Path | None,
    adapter: ArtifactManifest | None,
) -> None:
    if dataset.root.kind != "dataset":
        raise ModelError("LINEAGE_MISMATCH", "Evaluation dataset artifact kind is invalid")
    dataset_entries = read_leakage_index(
        dataset_path / dataset.root.details.leakageIndex.file.path,
        dataset.root.details.leakageIndex.file,
    )
    by_id = {entry.recordId: entry for entry in dataset_entries}
    selected = [by_id[record_id] for record_id in selected_ids]
    exposed = []
    for directory, manifest in ((model_path, model), (adapter_path, adapter)):
        if directory is None or manifest is None:
            continue
        reference = leakage_reference(manifest.root)
        if reference is not None:
            exposed.extend(read_leakage_index(directory / reference.path, reference))
    for candidate in selected:
        candidate_groups = set(candidate.groupKeyHashes)
        if any(
            candidate.recordId == item.recordId
            or not candidate_groups.isdisjoint(item.groupKeyHashes)
            or candidate.conversationHash == item.conversationHash
            or candidate.promptHash == item.promptHash
            for item in exposed
        ):
            raise ModelError("LEAKAGE_DETECTED", "Evaluation data overlaps training ancestry")


def _runtime_components() -> list[VersionedComponent]:
    try:
        versions = {
            "mlx": importlib.metadata.version("mlx"),
            "mlx-lm": importlib.metadata.version("mlx-lm"),
            "jsonschema": importlib.metadata.version("jsonschema"),
        }
    except importlib.metadata.PackageNotFoundError as error:
        raise ModelError(
            "DEPENDENCY_MISSING", "Pinned evaluation dependencies are unavailable"
        ) from error
    return sorted(
        [
            VersionedComponent(name="mlx-lm", version=versions["mlx-lm"], role="inference-runtime"),
            VersionedComponent(name="jsonschema", version=versions["jsonschema"], role="library"),
            VersionedComponent(name="mlx", version=versions["mlx"], role="library"),
        ],
        key=lambda item: (item.role, item.name),
    )


def _worker_failure(result: WorkerFailure) -> ModelError:
    error = result.error
    if error.code == "INTERRUPTED":
        return ModelError("INTERRUPTED", "Generation worker was interrupted")
    if error.code == "UNSUPPORTED_ARCHITECTURE":
        return ModelError("ARCHITECTURE_UNSUPPORTED", "Model architecture is unsupported")
    if error.code in {"INVALID_REQUEST", "TOKENIZATION_FAILED", "SEQUENCE_TOO_LONG"}:
        return ModelError("CONFIG_INVALID", "Generation input is incompatible with the model")
    if error.code in {"NONFINITE_METRIC", "OUTPUT_INVALID"}:
        return ModelError("OUTPUT_INVALID", "Generation worker produced invalid output")
    return ModelError("BACKEND_FAILED", "Generation worker failed")


def _same_manifest(left: ArtifactManifest, right: ArtifactManifest) -> bool:
    return left.root.model_dump(mode="json") == right.root.model_dump(mode="json")


def _file_ref(
    path: str, data: bytes, count: int, format_: Literal["json", "jsonl"]
) -> InventoryFileRef:
    return InventoryFileRef(
        path=path, sha256=hashlib.sha256(data).hexdigest(), recordCount=count, format=format_
    )


def _slice_aggregates(
    predictions: list[Prediction],
    field_pointers: list[str],
    attribute: Literal["language", "sourceId"],
) -> dict[str, EvaluationAggregate]:
    groups: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        groups[getattr(prediction, attribute)].append(prediction)
    return {key: aggregate_predictions(groups[key], field_pointers) for key in sorted(groups)}


def evaluate_model(
    config_path: Path,
    model_path: Path,
    dataset_path: Path,
    output: Path,
    *,
    split: Literal["validation", "calibration", "test"],
    adapter_path: Path | None = None,
) -> ArtifactManifest:
    """Run real deterministic generation and publish a verified evaluation artifact."""

    config = load_config(config_path, EvaluationConfig)
    field_pointers = sorted(config.fieldPointers)
    schema_bytes, schema_value = _load_schema(config_path, config.outputSchema)
    schema = None if schema_value is None else validate_output_schema(schema_value)
    model = load_verified_artifact(model_path)
    dataset = load_verified_artifact(dataset_path)
    adapter = None if adapter_path is None else load_verified_artifact(adapter_path)
    _validate_model_and_adapter(model, adapter)
    if dataset.root.kind != "dataset":
        raise ModelError("LINEAGE_MISMATCH", "Evaluation dataset artifact kind is invalid")
    input_paths = [model_path, dataset_path]
    if adapter_path is not None:
        input_paths.append(adapter_path)
    require_disjoint_output(output, input_paths)
    records = _read_split_records(dataset_path, dataset.root.details, split)
    limit = len(records) if config.maxExamples is None else min(config.maxExamples, len(records))
    selected_records = records[:limit]
    selected_ids = [record.id for record in selected_records]
    if split in {"calibration", "test"}:
        _reject_selected_leakage(
            selected_ids,
            dataset_path,
            dataset,
            model_path,
            model,
            adapter_path,
            adapter,
        )

    references: dict[str, ReferenceScore] = {}
    for record in selected_records:
        references[record.id] = prepare_reference(
            record.messages[-1].content,
            record.messages[:-1],
            schema=schema,
            evidence_pointer=config.evidencePointer,
            field_pointers=field_pointers,
        )

    assignments = dataset.root.details.assignments
    split_minima: dict[str, str] = {}
    for record_id, assignment in assignments.items():
        if assignment.split != split:
            continue
        current = split_minima.get(assignment.componentId)
        if current is None or record_id < current:
            split_minima[assignment.componentId] = record_id
    selected_components = {assignments[record_id].componentId for record_id in selected_ids}
    representatives = [
        ComponentRepresentative(
            componentId=component_id,
            recordId=split_minima[component_id],
            groupIds=assignments[split_minima[component_id]].groupIds,
        )
        for component_id in sorted(selected_components)
    ]
    representative_ids = {item.recordId for item in representatives}

    normalized_config = config.model_dump(mode="json")
    normalized_config["fieldPointers"] = field_pointers
    config_digest = canonical_digest(normalized_config)
    components = _runtime_components()
    if model.root.kind == "checkpoint":
        model_identity = model.root.details.model
    elif model.root.kind == "quantized":
        model_identity = model.root.details.model
    elif model.root.kind == "merged":
        model_identity = model.root.details.model
    elif model.root.kind == "export":
        model_identity = model.root.details.model
    else:  # guarded by _validate_model_and_adapter
        raise ModelError("LINEAGE_MISMATCH", "Evaluation model artifact kind is invalid")
    schema_digest = None if schema_bytes is None else hashlib.sha256(schema_bytes).hexdigest()
    profile = DeploymentProfile.model_validate(
        {
            "modelArtifactId": model.root.artifactId,
            "adapterArtifactId": None if adapter is None else adapter.root.artifactId,
            "configSha256": model_identity.configSha256,
            "tokenizerSha256": model_identity.tokenizerSha256,
            "chatTemplateSha256": model_identity.chatTemplateSha256,
            "generation": {"temperature": 0, "seed": config.seed, "maxTokens": config.maxTokens},
            "validators": {
                "outputSchemaSha256": schema_digest,
                "evidencePointer": config.evidencePointer,
                "fieldPointers": field_pointers,
                "jsonRequired": bool(
                    schema is not None or config.evidencePointer is not None or field_pointers
                ),
            },
            "scoring": {
                "jsonParserVersion": "rfc8259-v1",
                "exactVersion": "canonical-json-or-stripped-text-v1",
                "evidenceVersion": "exact-input-substring-v1",
                "fieldVersion": "json-pointer-equality-v1",
            },
            "pythonVersion": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine() or "unknown",
            "runtime": [item.model_dump(mode="json") for item in components],
        },
        strict=True,
    )
    parent_ids = [model.root.artifactId]
    if adapter is not None:
        parent_ids.append(adapter.root.artifactId)
    parent_ids.append(dataset.root.artifactId)
    with ArtifactTransaction(
        output,
        "evaluate",
        configuration=ConfigIdentity(kind="evaluation", sha256=config_digest),
        parent_artifact_ids=parent_ids,
        dataset_artifact_id=dataset.root.artifactId,
    ) as transaction:
        predictions: list[Prediction] = []
        generation_deadline = time.monotonic() + config.timeoutSeconds
        for index, record in enumerate(selected_records):
            remaining = generation_deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError("TIMEOUT", "Evaluation generation exceeded its deadline")
            workspace = transaction.workspace_path / f"example-{index:08d}"
            workspace.mkdir(mode=0o700)
            prompt = record.messages[:-1]
            request = WorkerRequest(
                root=GenerateWorkerRequest(
                    schemaVersion=1,
                    requestId=f"{transaction.run_id}-{index}",
                    operation="generate",
                    modelPath=str(model_path.absolute()),
                    adapterPath=None if adapter_path is None else str(adapter_path.absolute()),
                    messages=prompt,
                    seed=config.seed,
                    maxTokens=config.maxTokens,
                    temperature=0,
                )
            )
            worker = run_worker(
                request,
                workspace=workspace,
                timeout_seconds=max(1, math.ceil(remaining)),
            )
            if not worker.root.ok:
                raise _worker_failure(worker.root)
            result = cast(GenerateWorkerResult, worker.root.result)
            scored = score_generated(
                result.generated,
                record.messages[-1].content,
                references[record.id],
                prompt,
                schema=schema,
                evidence_pointer=config.evidencePointer,
                field_pointers=field_pointers,
            )
            assignment = assignments[record.id]
            payload: dict[str, object] = {
                "id": record.id,
                "sourceId": record.sourceId,
                "language": record.language,
                "tags": sorted(record.tags),
                "componentId": assignment.componentId,
                "groupIds": assignment.groupIds,
                "representative": record.id in representative_ids,
                "expected": record.messages[-1].content,
                "generated": result.generated,
                "expectedJson": references[record.id].parsed.value,
                "generatedJson": scored.parsed.value,
                "elapsedSeconds": result.elapsedSeconds,
                "generatedTokens": result.generatedTokens,
                "meanTokenLogprob": result.meanTokenLogprob,
                "jsonValid": scored.parsed.valid,
                "finishReason": result.finishReason,
                "schemaValid": scored.schema_valid,
                "evidenceValid": scored.evidence_valid,
                "fieldPresent": dict(scored.field_present),
                "fieldValid": dict(scored.field_valid),
                "applicableValid": scored.applicable_valid,
                "exactCorrect": scored.exact_correct,
            }
            if result.meanTokenLogprobUnavailableReason is not None:
                payload["meanTokenLogprobUnavailableReason"] = (
                    result.meanTokenLogprobUnavailableReason
                )
            predictions.append(Prediction.model_validate(payload, strict=True))

        if not _same_manifest(model, load_verified_artifact(model_path)) or not _same_manifest(
            dataset, load_verified_artifact(dataset_path)
        ):
            raise ModelError("INTEGRITY_FAILED", "Evaluation input artifact changed")
        if (
            adapter is not None
            and adapter_path is not None
            and not _same_manifest(adapter, load_verified_artifact(adapter_path))
        ):
            raise ModelError("INTEGRITY_FAILED", "Evaluation adapter artifact changed")

        prediction_bytes = b"".join(
            _canonical_bytes(item.model_dump(mode="json")) + b"\n" for item in predictions
        )
        write_private_bytes(transaction.staging_path / "predictions.jsonl", prediction_bytes)
        prediction_ref = _file_ref("predictions.jsonl", prediction_bytes, len(predictions), "jsonl")
        schema_ref = None
        if schema_bytes is not None:
            write_private_bytes(transaction.staging_path / "output-schema.json", schema_bytes)
            schema_ref = _file_ref("output-schema.json", schema_bytes, 1, "json")
        effective = adapter.root if adapter is not None else model.root
        parents = [parent_snapshot(model)]
        manifests = [model]
        if adapter is not None:
            parents.append(parent_snapshot(adapter))
            manifests.append(adapter)
        parents.append(parent_snapshot(dataset))
        manifests.append(dataset)
        aggregate = aggregate_predictions(predictions, field_pointers)
        details: dict[str, object] = {
            "modelArtifactId": model.root.artifactId,
            "adapterArtifactId": None if adapter is None else adapter.root.artifactId,
            "datasetArtifactId": dataset.root.artifactId,
            "split": split,
            "evaluationConfigSha256": config_digest,
            "deploymentProfile": profile.model_dump(mode="json"),
            "deploymentProfileId": canonical_digest(profile.model_dump(mode="json")),
            "selectedRecordIds": selected_ids,
            "selectedGroupIds": sorted(
                {group for record_id in selected_ids for group in assignments[record_id].groupIds}
            ),
            "representatives": [item.model_dump(mode="json") for item in representatives],
            "selectionUnit": "component-representative-v1",
            "predictions": prediction_ref.model_dump(mode="json"),
            "outputSchemaFile": None if schema_ref is None else schema_ref.model_dump(mode="json"),
            "aggregate": aggregate.model_dump(mode="json"),
            "languages": {
                key: value.model_dump(mode="json")
                for key, value in _slice_aggregates(predictions, field_pointers, "language").items()
            },
            "sources": {
                key: value.model_dump(mode="json")
                for key, value in _slice_aggregates(predictions, field_pointers, "sourceId").items()
            },
            "diagnostic": dataset.root.details.diagnostic,
        }
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "evaluation",
                "name": f"evaluation-{model.root.artifactId[:12]}",
                "createdAt": _timestamp(),
                "stage": effective.stage,
                **({} if effective.customer is None else {"customer": effective.customer}),
                "parents": [item.model_dump(mode="json") for item in parents],
                "producer": producer_identity("evaluate", components).model_dump(mode="json"),
                "sourceRights": [
                    item.model_dump(mode="json") for item in union_source_rights(manifests)
                ],
                "details": details,
            },
        )
        transaction.publish(manifest)
        return manifest


def read_verified_predictions(
    evaluation_path: Path, manifest: ArtifactManifest
) -> list[Prediction]:
    """Read predictions and reconcile their durable metadata and aggregates."""

    if manifest.root.kind != "evaluation":
        raise ModelError("LINEAGE_MISMATCH", "Prediction artifact must be an evaluation")
    details = manifest.root.details
    schema = _read_copied_schema(evaluation_path, details.outputSchemaFile)
    reference = details.predictions
    data = _read_regular(
        evaluation_path / reference.path,
        label="predictions",
        max_bytes=max(reference.recordCount * 16_777_216, 1),
    )
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ModelError("INTEGRITY_FAILED", "Prediction digest does not match manifest")
    lines = data.splitlines(keepends=True)
    if any(not line.endswith(b"\n") for line in lines):
        raise ModelError("INTEGRITY_FAILED", "Prediction rows must end with LF")
    predictions: list[Prediction] = []
    for line in lines:
        raw = line[:-1]
        try:
            value = parse_json(raw.decode("utf-8", errors="strict"))
            prediction = Prediction.model_validate(value, strict=True)
        except (UnicodeError, ModelError, ValidationError) as error:
            raise ModelError("INTEGRITY_FAILED", "Prediction row is invalid") from error
        if raw != _canonical_bytes(prediction.model_dump(mode="json")):
            raise ModelError("INTEGRITY_FAILED", "Prediction row is not canonical JSON")
        predictions.append(prediction)
    if len(predictions) != reference.recordCount:
        raise ModelError("INTEGRITY_FAILED", "Prediction count does not match manifest")
    if [item.id for item in predictions] != details.selectedRecordIds:
        raise ModelError("INTEGRITY_FAILED", "Prediction IDs do not match evaluation selection")

    dataset = manifest.root.parents[-1]
    if dataset.kind != "dataset":
        raise ModelError("LINEAGE_MISMATCH", "Evaluation dataset snapshot is invalid")
    representative_ids = {item.recordId for item in details.representatives}
    validators = details.deploymentProfile.validators
    for prediction in predictions:
        assignment = dataset.details.assignments.get(prediction.id)
        if assignment is None or (
            prediction.componentId != assignment.componentId
            or prediction.groupIds != assignment.groupIds
            or prediction.representative != (prediction.id in representative_ids)
        ):
            raise ModelError("INTEGRITY_FAILED", "Prediction assignment metadata is inconsistent")
        if list(prediction.fieldPresent) != validators.fieldPointers:
            raise ModelError("INTEGRITY_FAILED", "Prediction field validators are inconsistent")
        if (prediction.schemaValid is not None) != (validators.outputSchemaSha256 is not None) or (
            prediction.evidenceValid is not None
        ) != (validators.evidencePointer is not None):
            raise ModelError("INTEGRITY_FAILED", "Prediction validator flags are inconsistent")
        _verify_stored_score(prediction, validators.jsonRequired, schema)

    field_pointers = validators.fieldPointers
    if aggregate_predictions(predictions, field_pointers) != details.aggregate:
        raise ModelError("INTEGRITY_FAILED", "Prediction aggregate does not match evaluation")
    if _slice_aggregates(predictions, field_pointers, "language") != details.languages:
        raise ModelError("INTEGRITY_FAILED", "Prediction language slices do not match evaluation")
    if _slice_aggregates(predictions, field_pointers, "sourceId") != details.sources:
        raise ModelError("INTEGRITY_FAILED", "Prediction source slices do not match evaluation")
    return predictions


def _read_copied_schema(
    evaluation_path: Path, reference: InventoryFileRef | None
) -> _SchemaValidator | None:
    if reference is None:
        return None
    data = _read_regular(
        evaluation_path / reference.path,
        label="evaluation output schema",
        max_bytes=_MAX_LOCAL_FILE_BYTES,
    )
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ModelError("INTEGRITY_FAILED", "Evaluation schema digest does not match manifest")
    try:
        value = parse_json(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ModelError) as error:
        raise ModelError("INTEGRITY_FAILED", "Evaluation schema is invalid") from error
    return cast(_SchemaValidator, validate_output_schema(value))


def _verify_stored_score(
    prediction: Prediction, json_required: bool, schema: _SchemaValidator | None
) -> None:
    expected = parse_strict_json(prediction.expected)
    generated = parse_strict_json(prediction.generated)
    generated_value_matches = structural_equal(generated.value, prediction.generatedJson)
    if generated.valid != prediction.jsonValid or not generated_value_matches:
        raise ModelError("INTEGRITY_FAILED", "Stored prediction JSON parse is inconsistent")
    expected_value_matches = structural_equal(expected.value, prediction.expectedJson)
    if not expected_value_matches or (json_required and not expected.valid):
        raise ModelError("INTEGRITY_FAILED", "Stored reference JSON parse is inconsistent")
    if schema is not None:
        observed_schema = generated.valid and schema.is_valid(generated.value)
        if prediction.schemaValid != observed_schema:
            raise ModelError("INTEGRITY_FAILED", "Stored schema score is inconsistent")
    for pointer, present in prediction.fieldPresent.items():
        observed_present, generated_value = (
            json_pointer_value(generated.value, pointer) if generated.valid else (False, None)
        )
        expected_present, expected_value = (
            json_pointer_value(expected.value, pointer) if expected.valid else (False, None)
        )
        if present != observed_present:
            raise ModelError("INTEGRITY_FAILED", "Stored field presence is inconsistent")
        observed_valid = (
            present and expected_present and structural_equal(generated_value, expected_value)
        )
        if prediction.fieldValid[pointer] != observed_valid:
            raise ModelError("INTEGRITY_FAILED", "Stored field score is inconsistent")
    applicable = (
        (generated.valid or not json_required)
        and prediction.schemaValid is not False
        and prediction.evidenceValid is not False
        and all(prediction.fieldPresent.values())
    )
    if applicable != prediction.applicableValid:
        raise ModelError("INTEGRITY_FAILED", "Stored applicability is inconsistent")
    if generated.valid and expected.valid:
        base_equal = structural_equal(generated.value, expected.value)
    elif not generated.valid and not expected.valid:
        base_equal = prediction.generated.strip() == prediction.expected.strip()
    else:
        base_equal = False
    if prediction.exactCorrect != (base_equal and applicable):
        raise ModelError("INTEGRITY_FAILED", "Stored exact score is inconsistent")
