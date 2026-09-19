"""Recursive artifact manifests and lineage invariants."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, model_validator

from .base import (
    ContractModel,
    Digest,
    FileEntry,
    Id,
    InventoryFileRef,
    Omitted,
    ProducerIdentity,
    SchemaVersion,
    SourceRight,
    Stage,
    Timestamp,
    _reject_explicit_null,
    canonical_digest,
)
from .details import (
    AdapterDetails,
    AuditDetails,
    CheckpointDetails,
    DatasetDetails,
    EvaluationDetails,
    ExportDetails,
    MergedDetails,
    PolicyDetails,
    QuantizedDetails,
)


class _ParentCommon(ContractModel):
    artifactId: Digest
    name: Id
    createdAt: Timestamp
    stage: Stage
    customer: Omitted[Id] = Field(default=None, exclude_if=lambda value: value is None)
    files: list[FileEntry]
    parents: list[ParentRef]
    producer: ProducerIdentity
    sourceRights: list[SourceRight]

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "customer")

    @model_validator(mode="after")
    def validate_common(self) -> _ParentCommon:
        customer_missing = self.customer is None
        if (self.stage == "customer") == customer_missing:
            raise ValueError("customer is required exactly for customer stage")
        paths = [entry.path for entry in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("files must be sorted and unique by path")
        right_ids = [right.sourceId for right in self.sourceRights]
        if right_ids != sorted(right_ids) or len(right_ids) != len(set(right_ids)):
            raise ValueError("sourceRights must be sorted and unique by sourceId")
        return self


class DatasetParent(_ParentCommon):
    kind: Literal["dataset"]
    details: DatasetDetails

    @model_validator(mode="after")
    def validate_kind(self) -> DatasetParent:
        if self.stage != "none" or self.customer is not None or self.parents:
            raise ValueError("dataset artifacts have none stage, no customer, and no parents")
        _validate_producer(self, "prepare")
        _validate_source_rights(self)
        content_payload = {
            "normalizationVersion": self.details.normalizationVersion,
            "splitSettings": self.details.splitSettings.model_dump(mode="json"),
            "sourceRights": [right.model_dump(mode="json") for right in self.sourceRights],
            "assignments": {
                record_id: assignment.model_dump(mode="json")
                for record_id, assignment in self.details.assignments.items()
            },
            "chatFileDigests": {
                split: getattr(self.details.chatFiles, split).sha256
                for split in ("train", "validation", "calibration", "test")
            },
            "recordFileDigests": {
                split: getattr(self.details.recordFiles, split).sha256
                for split in ("train", "validation", "calibration", "test")
            },
        }
        if self.details.datasetConfig.frozenFamilies is not None:
            content_payload["frozenFamilies"] = {
                key: value.model_dump(mode="json")
                for key, value in self.details.datasetConfig.frozenFamilies.items()
            }
        if self.details.datasetContentId != canonical_digest(content_payload):
            raise ValueError("datasetContentId does not match canonical dataset content")
        _validate_snapshot_id(self)
        return self


class CheckpointParent(_ParentCommon):
    kind: Literal["checkpoint"]
    details: CheckpointDetails

    @model_validator(mode="after")
    def validate_kind(self) -> CheckpointParent:
        if self.stage != "upstream" or self.customer is not None or self.parents:
            raise ValueError("checkpoints have upstream stage, no customer, and no parents")
        _validate_producer(self, "fetch")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class QuantizedParent(_ParentCommon):
    kind: Literal["quantized"]
    details: QuantizedDetails

    @model_validator(mode="after")
    def validate_kind(self) -> QuantizedParent:
        if len(self.parents) != 1 or self.parents[0].kind not in {"checkpoint", "merged"}:
            raise ValueError("quantized artifacts require one checkpoint or merged parent")
        parent = self.parents[0]
        if self.details.parentArtifactId != parent.artifactId:
            raise ValueError("quantized parent identity mismatch")
        if self.details.exposureLeakageIndex.file.path != "leakage.jsonl":
            raise ValueError("quantized exposure path must be leakage.jsonl")
        _require_same_scope(self, parent)
        _validate_producer(self, "quantize")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class AdapterParent(_ParentCommon):
    kind: Literal["adapter"]
    details: AdapterDetails

    @model_validator(mode="after")
    def validate_kind(self) -> AdapterParent:
        if self.stage not in {"shared", "customer"} or len(self.parents) not in {2, 3}:
            raise ValueError("adapter stage or parent count is invalid")
        model_parent, dataset_parent = self.parents[:2]
        if model_parent.kind not in {"checkpoint", "quantized", "merged"}:
            raise ValueError("adapter model parent kind is invalid")
        if dataset_parent.kind != "dataset":
            raise ValueError("adapter second parent must be a dataset")
        if self.details.modelArtifactId != model_parent.artifactId:
            raise ValueError("adapter model identity mismatch")
        if self.details.datasetArtifactId != dataset_parent.artifactId:
            raise ValueError("adapter dataset identity mismatch")
        warm_present = self.details.warmStartArtifactId is not None
        if warm_present != (len(self.parents) == 3):
            raise ValueError("warm-start identity and parent must appear together")
        if self.stage == "shared":
            _validate_shared_training_parent(model_parent)
            if _has_customer_ancestry(model_parent):
                raise ValueError("shared adapter cannot have customer ancestry")
            expected_command = "train"
        else:
            _validate_customer_training_parent(model_parent)
            expected_command = "customize"
        expected_method = "qlora" if model_parent.kind == "quantized" else "lora"
        if self.details.backendConfig.method != expected_method:
            raise ValueError("training method does not match model parent kind")
        if isinstance(dataset_parent, DatasetParent):
            if (
                self.details.backendConfig.trainFileSha256
                != dataset_parent.details.chatFiles.train.sha256
                or self.details.backendConfig.validationFileSha256
                != dataset_parent.details.chatFiles.validation.sha256
            ):
                raise ValueError("backend dataset file identities do not match dataset parent")
        if len(self.parents) == 3:
            warm = self.parents[2]
            if warm.kind != "adapter" or warm.artifactId != self.details.warmStartArtifactId:
                raise ValueError("warm-start parent is invalid")
            _require_same_scope(self, warm)
            if warm.details.modelArtifactId != model_parent.artifactId:
                raise ValueError("warm-start model parent mismatch")
        _validate_producer(self, expected_command)
        loss_components = [
            item
            for item in self.producer.components
            if item.name == "foliqant-completion-loss"
            and item.version == "1"
            and item.role == "library"
        ]
        if len(loss_components) != 1:
            raise ValueError("adapter producer must identify completion loss version 1")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class MergedParent(_ParentCommon):
    kind: Literal["merged"]
    details: MergedDetails

    @model_validator(mode="after")
    def validate_kind(self) -> MergedParent:
        if len(self.parents) != 2 or self.parents[1].kind != "adapter":
            raise ValueError("merged artifacts require model then adapter parents")
        model_parent, adapter_parent = self.parents
        assert isinstance(adapter_parent, AdapterParent)
        if model_parent.kind not in {"checkpoint", "quantized", "merged"}:
            raise ValueError("merged model parent kind is invalid")
        if self.details.modelParentArtifactId != model_parent.artifactId:
            raise ValueError("merged model parent identity mismatch")
        if self.details.adapterArtifactId != adapter_parent.artifactId:
            raise ValueError("merged adapter identity mismatch")
        if adapter_parent.details.modelArtifactId != model_parent.artifactId:
            raise ValueError("adapter does not belong to merged model parent")
        if model_parent.kind == "quantized" and not any(
            change.operation == "dequantize-for-fusion" and change.lossy
            for change in self.details.precisionHistory
        ):
            raise ValueError("quantized merge must record lossy dequantization")
        _require_same_scope(self, adapter_parent)
        _validate_producer(self, "merge")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class ExportParent(_ParentCommon):
    kind: Literal["export"]
    details: ExportDetails

    @model_validator(mode="after")
    def validate_kind(self) -> ExportParent:
        if len(self.parents) != 1 or self.parents[0].kind != "merged":
            raise ValueError("export requires one merged parent")
        parent = self.parents[0]
        if self.details.mergedArtifactId != parent.artifactId:
            raise ValueError("export parent identity mismatch")
        _require_same_scope(self, parent)
        _validate_producer(self, "export")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class EvaluationParent(_ParentCommon):
    kind: Literal["evaluation"]
    details: EvaluationDetails

    @model_validator(mode="after")
    def validate_kind(self) -> EvaluationParent:
        if len(self.parents) not in {2, 3}:
            raise ValueError("evaluation requires model, optional adapter, and dataset")
        model_parent = self.parents[0]
        adapter_parent = self.parents[1] if len(self.parents) == 3 else None
        dataset_parent = self.parents[-1]
        if model_parent.kind not in {"checkpoint", "quantized", "merged", "export"}:
            raise ValueError("evaluation model parent kind is invalid")
        if dataset_parent.kind != "dataset":
            raise ValueError("evaluation final parent must be a dataset")
        assert isinstance(dataset_parent, DatasetParent)
        if self.details.modelArtifactId != model_parent.artifactId:
            raise ValueError("evaluation model identity mismatch")
        if self.details.datasetArtifactId != dataset_parent.artifactId:
            raise ValueError("evaluation dataset identity mismatch")
        if adapter_parent is None:
            if self.details.adapterArtifactId is not None:
                raise ValueError("adapter identity requires an adapter parent")
            _require_same_scope(self, model_parent)
        else:
            if adapter_parent.kind != "adapter":
                raise ValueError("evaluation second parent must be an adapter")
            if self.details.adapterArtifactId != adapter_parent.artifactId:
                raise ValueError("evaluation adapter identity mismatch")
            if adapter_parent.details.modelArtifactId != model_parent.artifactId:
                raise ValueError("evaluation adapter/model lineage mismatch")
            _require_same_scope(self, adapter_parent)
        model_identity = model_parent.details.model
        profile = self.details.deploymentProfile
        if (
            profile.configSha256 != model_identity.configSha256
            or profile.tokenizerSha256 != model_identity.tokenizerSha256
            or profile.chatTemplateSha256 != model_identity.chatTemplateSha256
        ):
            raise ValueError("deployment profile does not match model identity")
        selected_assignments = {
            record_id: dataset_parent.details.assignments.get(record_id)
            for record_id in self.details.selectedRecordIds
        }
        if any(
            assignment is None or assignment.split != self.details.split
            for assignment in selected_assignments.values()
        ):
            raise ValueError("selected records must belong to the evaluation split")
        selected_groups = sorted(
            {
                group_id
                for assignment in selected_assignments.values()
                if assignment is not None
                for group_id in assignment.groupIds
            }
        )
        if selected_groups != self.details.selectedGroupIds:
            raise ValueError("selectedGroupIds do not match selected records")
        split_assignments = {
            record_id: assignment
            for record_id, assignment in dataset_parent.details.assignments.items()
            if assignment.split == self.details.split
        }
        component_minima: dict[str, str] = {}
        for record_id, assignment in split_assignments.items():
            current = component_minima.get(assignment.componentId)
            if current is None or record_id < current:
                component_minima[assignment.componentId] = record_id
        selected_components = {
            assignment.componentId
            for assignment in selected_assignments.values()
            if assignment is not None
        }
        expected_representatives = [
            {
                "componentId": component_id,
                "recordId": component_minima[component_id],
                "groupIds": split_assignments[component_minima[component_id]].groupIds,
            }
            for component_id in sorted(selected_components)
        ]
        actual_representatives = [
            item.model_dump(mode="python") for item in self.details.representatives
        ]
        if actual_representatives != expected_representatives:
            raise ValueError("component representatives do not match split minima")
        if self.details.diagnostic != dataset_parent.details.diagnostic:
            raise ValueError("evaluation diagnostic flag must match dataset")
        _validate_producer(self, "evaluate")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class PolicyParent(_ParentCommon):
    kind: Literal["policy"]
    details: PolicyDetails

    @model_validator(mode="after")
    def validate_kind(self) -> PolicyParent:
        if len(self.parents) != 1 or self.parents[0].kind != "evaluation":
            raise ValueError("policy requires one evaluation parent")
        evaluation = self.parents[0]
        if evaluation.details.split != "calibration":
            raise ValueError("policy evaluation must use calibration split")
        if self.details.evaluationArtifactId != evaluation.artifactId:
            raise ValueError("policy evaluation identity mismatch")
        if self.details.datasetArtifactId != evaluation.details.datasetArtifactId:
            raise ValueError("policy dataset identity mismatch")
        if self.details.deploymentProfileId != evaluation.details.deploymentProfileId:
            raise ValueError("policy profile identity mismatch")
        expected_ids = sorted(item.recordId for item in evaluation.details.representatives)
        expected_groups = sorted(
            {group_id for item in evaluation.details.representatives for group_id in item.groupIds}
        )
        if self.details.representativeRecordIds != expected_ids:
            raise ValueError("policy representative IDs do not match evaluation")
        if self.details.representativeGroupIds != expected_groups:
            raise ValueError("policy representative groups do not match evaluation")
        _require_same_scope(self, evaluation)
        _validate_producer(self, "calibrate")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


class AuditParent(_ParentCommon):
    kind: Literal["audit"]
    details: AuditDetails

    @model_validator(mode="after")
    def validate_kind(self) -> AuditParent:
        if len(self.parents) != 2:
            raise ValueError("audit requires test evaluation then policy")
        evaluation, policy = self.parents
        if evaluation.kind != "evaluation" or policy.kind != "policy":
            raise ValueError("audit parents must be evaluation then policy")
        if evaluation.details.split != "test":
            raise ValueError("audit evaluation must use test split")
        if self.details.evaluationArtifactId != evaluation.artifactId:
            raise ValueError("audit evaluation identity mismatch")
        if self.details.policyArtifactId != policy.artifactId:
            raise ValueError("audit policy identity mismatch")
        if self.details.datasetArtifactId != evaluation.details.datasetArtifactId:
            raise ValueError("audit dataset identity mismatch")
        if self.details.deploymentProfileId != evaluation.details.deploymentProfileId:
            raise ValueError("audit profile identity mismatch")
        if policy.details.datasetArtifactId != self.details.datasetArtifactId:
            raise ValueError("audit policy dataset mismatch")
        if policy.details.deploymentProfileId != self.details.deploymentProfileId:
            raise ValueError("audit policy profile mismatch")
        expected_ids = sorted(item.recordId for item in evaluation.details.representatives)
        expected_groups = sorted(
            {group_id for item in evaluation.details.representatives for group_id in item.groupIds}
        )
        if self.details.representativeRecordIds != expected_ids:
            raise ValueError("audit representative IDs do not match evaluation")
        if self.details.representativeGroupIds != expected_groups:
            raise ValueError("audit representative groups do not match evaluation")
        if self.details.diagnostic != evaluation.details.diagnostic:
            raise ValueError("audit diagnostic flag must match evaluation")
        if set(policy.details.representativeRecordIds) & set(self.details.representativeRecordIds):
            raise ValueError("calibration and test representative IDs must be disjoint")
        if set(policy.details.representativeGroupIds) & set(self.details.representativeGroupIds):
            raise ValueError("calibration and test groups must be disjoint")
        _require_same_scope(self, evaluation)
        _validate_producer(self, "audit")
        _validate_source_rights(self)
        _validate_snapshot_id(self)
        return self


type ParentRef = Annotated[
    DatasetParent
    | CheckpointParent
    | QuantizedParent
    | AdapterParent
    | MergedParent
    | ExportParent
    | EvaluationParent
    | PolicyParent
    | AuditParent,
    Field(discriminator="kind"),
]


class DatasetArtifact(DatasetParent):
    schemaVersion: SchemaVersion


class CheckpointArtifact(CheckpointParent):
    schemaVersion: SchemaVersion


class QuantizedArtifact(QuantizedParent):
    schemaVersion: SchemaVersion


class AdapterArtifact(AdapterParent):
    schemaVersion: SchemaVersion


class MergedArtifact(MergedParent):
    schemaVersion: SchemaVersion


class ExportArtifact(ExportParent):
    schemaVersion: SchemaVersion


class EvaluationArtifact(EvaluationParent):
    schemaVersion: SchemaVersion


class PolicyArtifact(PolicyParent):
    schemaVersion: SchemaVersion


class AuditArtifact(AuditParent):
    schemaVersion: SchemaVersion


type ArtifactManifestValue = Annotated[
    DatasetArtifact
    | CheckpointArtifact
    | QuantizedArtifact
    | AdapterArtifact
    | MergedArtifact
    | ExportArtifact
    | EvaluationArtifact
    | PolicyArtifact
    | AuditArtifact,
    Field(discriminator="kind"),
]


class ArtifactManifest(RootModel[ArtifactManifestValue]):
    """Closed artifact kind/details union with bounded recursive lineage."""

    @model_validator(mode="after")
    def validate_manifest(self) -> ArtifactManifest:
        _validate_artifact_id(self.root)
        _validate_node_inventory(self.root)
        seen_path: set[str] = set()
        count = 0

        def visit(node: ParentRef, depth: int) -> None:
            nonlocal count
            if depth > 32:
                raise ValueError("lineage exceeds 32 parent edges")
            count += 1
            if count > 256:
                raise ValueError("lineage exceeds 256 parent occurrences")
            if node.artifactId in seen_path:
                raise ValueError("lineage cycle detected")
            _validate_node_inventory(node)
            seen_path.add(node.artifactId)
            for parent in node.parents:
                visit(parent, depth + 1)
            seen_path.remove(node.artifactId)

        for parent in self.root.parents:
            visit(parent, 1)
        return self


def _validate_producer(node: _ParentCommon, command: str) -> None:
    if node.producer.command != command:
        raise ValueError(f"{node.kind} artifacts must be produced by {command}")  # type: ignore[attr-defined]


def _scope(node: _ParentCommon) -> tuple[Stage, object]:
    return node.stage, node.customer


def _require_same_scope(node: _ParentCommon, parent: _ParentCommon) -> None:
    if _scope(node) != _scope(parent):
        raise ValueError("stage/customer scope must match parent")


def _has_customer_ancestry(node: _ParentCommon) -> bool:
    return node.stage == "customer" or any(
        _has_customer_ancestry(parent) for parent in node.parents
    )


def _validate_shared_training_parent(parent: _ParentCommon) -> None:
    if parent.kind == "checkpoint":  # type: ignore[attr-defined]
        return
    if parent.kind == "quantized":  # type: ignore[attr-defined]
        if parent.stage == "upstream":
            return
        if parent.stage == "shared" and parent.parents[0].kind == "merged":
            return
    if parent.kind == "merged" and parent.stage == "shared":  # type: ignore[attr-defined]
        return
    raise ValueError("shared adapter model parent is invalid")


def _validate_customer_training_parent(parent: _ParentCommon) -> None:
    if parent.kind == "merged" and parent.stage == "shared":  # type: ignore[attr-defined]
        return
    if (
        parent.kind == "quantized"  # type: ignore[attr-defined]
        and parent.stage == "shared"
        and parent.parents[0].kind == "merged"
        and parent.parents[0].stage == "shared"
    ):
        return
    raise ValueError("customer adapter requires merged/shared or its quantized child")


def _source_right_payload(right: SourceRight) -> dict[str, object]:
    return right.model_dump(mode="json")


def _inventory_file_refs(value: object) -> list[InventoryFileRef]:
    if isinstance(value, InventoryFileRef):
        return [value]
    if isinstance(value, ContractModel):
        refs: list[InventoryFileRef] = []
        for field_name in type(value).model_fields:
            refs.extend(_inventory_file_refs(getattr(value, field_name)))
        return refs
    if isinstance(value, list):
        return [ref for item in value for ref in _inventory_file_refs(item)]
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _inventory_file_refs(item)]
    return []


def _validate_node_inventory(node: ArtifactManifestValue | ParentRef) -> None:
    inventory = {entry.path: entry.sha256 for entry in node.files}
    for reference in _inventory_file_refs(node.details):
        if inventory.get(reference.path) != reference.sha256:
            raise ValueError("inventory file reference is absent or has a different digest")


def _validate_source_rights(node: _ParentCommon) -> None:
    expected: dict[str, SourceRight] = {}
    if node.kind == "dataset":  # type: ignore[attr-defined]
        for source in node.details.datasetConfig.sources:  # type: ignore[attr-defined]
            payload = source.model_dump(mode="python")
            payload["sourceId"] = payload.pop("id")
            expected[source.id] = SourceRight.model_validate(payload)
    else:
        for parent in node.parents:
            for right in parent.sourceRights:
                existing = expected.get(right.sourceId)
                if existing is not None and _source_right_payload(
                    existing
                ) != _source_right_payload(right):
                    raise ValueError("conflicting source rights for the same source ID")
                expected[right.sourceId] = right
    expected_list = [_source_right_payload(expected[key]) for key in sorted(expected)]
    actual_list = [_source_right_payload(right) for right in node.sourceRights]
    if actual_list != expected_list:
        raise ValueError("sourceRights must equal the transitive canonical union")


def _snapshot_identity_payload(node: _ParentCommon) -> dict[str, object]:
    payload = node.model_dump(mode="json", exclude={"artifactId"})
    payload["schemaVersion"] = 1
    return payload


def _validate_snapshot_id(node: _ParentCommon) -> None:
    if canonical_digest(_snapshot_identity_payload(node)) != node.artifactId:
        raise ValueError("parent snapshot artifactId does not match canonical contents")


def _validate_artifact_id(node: _ParentCommon) -> None:
    payload = node.model_dump(mode="json", exclude={"artifactId"})
    if canonical_digest(payload) != node.artifactId:
        raise ValueError("artifactId does not match canonical manifest contents")


for _model in (
    DatasetParent,
    CheckpointParent,
    QuantizedParent,
    AdapterParent,
    MergedParent,
    ExportParent,
    EvaluationParent,
    PolicyParent,
    AuditParent,
):
    _model.model_rebuild(_types_namespace={"ParentRef": ParentRef})
