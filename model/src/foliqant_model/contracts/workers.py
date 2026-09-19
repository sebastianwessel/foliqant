"""Closed request/result contracts for isolated backend workers."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    Field,
    RootModel,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from .base import (
    ContractModel,
    Id,
    LocalPath,
    NonEmptyStr,
    NonNegativeFloat,
    NonNegativeInt,
    Omitted,
    SchemaVersion,
    StrictFalse,
    StrictOne,
    StrictTrue,
    StrictZero,
    UInt32,
    _reject_explicit_null,
)
from .details import GgufExportMetadata
from .inputs import ChatMessage


class MlxLoraParameters(ContractModel):
    rank: Annotated[StrictInt, Field(ge=1, le=256)]
    scale: Annotated[StrictFloat, Field(gt=0, le=256, allow_inf_nan=False)]
    dropout: Annotated[StrictFloat, Field(ge=0, lt=1, allow_inf_nan=False)]


class AdamwOptions(ContractModel):
    weight_decay: StrictZero


class MlxOptimizerConfig(ContractModel):
    adamw: AdamwOptions


class MlxTrainNamespace(ContractModel):
    model: LocalPath
    data: LocalPath
    adapter_path: LocalPath
    resume_adapter_file: LocalPath | None
    train: StrictTrue
    test: StrictFalse
    fine_tune_type: Literal["lora"]
    optimizer: Literal["adamw"]
    optimizer_config: MlxOptimizerConfig
    seed: UInt32
    num_layers: Annotated[StrictInt, Field(ge=-1, le=1024)]
    batch_size: Annotated[StrictInt, Field(ge=1, le=64)]
    iters: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    val_batches: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    learning_rate: Annotated[StrictFloat, Field(gt=0, le=1, allow_inf_nan=False)]
    steps_per_report: StrictOne
    steps_per_eval: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    save_every: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    test_batches: Literal[500]
    max_seq_length: Annotated[StrictInt, Field(ge=64, le=131_072)]
    config: None
    grad_checkpoint: StrictBool
    grad_accumulation_steps: Annotated[StrictInt, Field(ge=1, le=1024)]
    clear_cache_threshold: StrictZero
    lr_schedule: None
    report_to: None
    project_name: None
    lora_parameters: MlxLoraParameters
    mask_prompt: StrictTrue

    @field_validator("num_layers")
    @classmethod
    def valid_num_layers(cls, value: int) -> int:
        if value == 0:
            raise ValueError("num_layers is -1 or positive")
        return value


class _WorkerRequestCommon(ContractModel):
    schemaVersion: SchemaVersion
    requestId: Id


class DoctorWorkerRequest(_WorkerRequestCommon):
    operation: Literal["doctor"]


class TrainWorkerRequest(_WorkerRequestCommon):
    operation: Literal["train"]
    namespace: MlxTrainNamespace
    trainPath: LocalPath
    validationPath: LocalPath
    outputPath: LocalPath


class GenerateWorkerRequest(_WorkerRequestCommon):
    operation: Literal["generate"]
    modelPath: LocalPath
    adapterPath: LocalPath | None
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=255)]
    seed: UInt32
    maxTokens: Annotated[StrictInt, Field(ge=1, le=32_768)]
    temperature: StrictZero

    @model_validator(mode="after")
    def validate_prompt(self) -> GenerateWorkerRequest:
        roles = [message.role for message in self.messages]
        if roles[0] == "system":
            roles = roles[1:]
        if not roles or roles[-1] != "user":
            raise ValueError("generation prompt must end with a user message")
        expected = "user"
        for role in roles:
            if role != expected:
                raise ValueError("prompt messages must alternate user and assistant")
            expected = "assistant" if expected == "user" else "user"
        return self


class QuantizeWorkerRequest(_WorkerRequestCommon):
    operation: Literal["quantize"]
    modelPath: LocalPath
    outputPath: LocalPath
    bits: Literal[4, 8]
    groupSize: Literal[64]
    mode: Literal["affine"]


class MergeWorkerRequest(_WorkerRequestCommon):
    operation: Literal["merge"]
    modelPath: LocalPath
    adapterPath: LocalPath
    outputPath: LocalPath
    dequantize: StrictBool


class GgufWorkerRequest(_WorkerRequestCommon):
    operation: Literal["gguf"]
    modelPath: LocalPath
    outputPath: LocalPath
    outputPrecision: Literal["F16"]


type WorkerRequestValue = Annotated[
    DoctorWorkerRequest
    | TrainWorkerRequest
    | GenerateWorkerRequest
    | QuantizeWorkerRequest
    | MergeWorkerRequest
    | GgufWorkerRequest,
    Field(discriminator="operation"),
]


class WorkerRequest(RootModel[WorkerRequestValue]):
    pass


class ValidationObservation(ContractModel):
    iteration: NonNegativeInt
    loss: NonNegativeFloat
    elapsedSeconds: NonNegativeFloat


class DoctorWorkerResult(ContractModel):
    mlxImportAvailable: StrictBool
    metalAvailable: StrictBool
    mlxVersion: NonEmptyStr | None
    mlxLmVersion: NonEmptyStr | None
    unavailableReason: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_doctor(self) -> DoctorWorkerResult:
        if self.mlxImportAvailable:
            if (
                self.mlxVersion is None
                or self.mlxLmVersion is None
                or self.unavailableReason is not None
            ):
                raise ValueError("available MLX requires versions and null unavailableReason")
        elif (
            self.metalAvailable
            or self.mlxVersion is not None
            or self.mlxLmVersion is not None
            or self.unavailableReason is None
        ):
            raise ValueError("unavailable MLX requires null versions, false Metal, and a reason")
        return self


class TrainWorkerResult(ContractModel):
    finalLoss: NonNegativeFloat
    validation: list[ValidationObservation]
    peakMemoryBytes: NonNegativeInt
    elapsedSeconds: NonNegativeFloat
    adapterWeightsPath: LocalPath
    adapterConfigPath: LocalPath
    trainableTensorNames: Annotated[list[NonEmptyStr], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_result(self) -> TrainWorkerResult:
        iterations = [item.iteration for item in self.validation]
        if iterations != sorted(iterations) or len(iterations) != len(set(iterations)):
            raise ValueError("validation observations must be ordered by unique iteration")
        if self.trainableTensorNames != sorted(self.trainableTensorNames):
            raise ValueError("trainable tensor names must be sorted")
        if len(self.trainableTensorNames) != len(set(self.trainableTensorNames)):
            raise ValueError("trainable tensor names must be unique")
        return self


class GenerateWorkerResult(ContractModel):
    generated: str
    generatedTokens: NonNegativeInt
    meanTokenLogprob: Annotated[StrictFloat, Field(le=0, allow_inf_nan=False)] | None
    meanTokenLogprobUnavailableReason: Omitted[Literal["no-generated-tokens"]] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    finishReason: Literal["stop", "length"]
    elapsedSeconds: NonNegativeFloat

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "meanTokenLogprobUnavailableReason")

    @model_validator(mode="after")
    def validate_score(self) -> GenerateWorkerResult:
        missing_reason = self.meanTokenLogprobUnavailableReason is None
        if self.generatedTokens == 0:
            if self.meanTokenLogprob is not None or missing_reason:
                raise ValueError("empty generation requires null score and reason")
        elif self.meanTokenLogprob is None or not missing_reason:
            raise ValueError("nonempty generation requires score and omits reason")
        return self


class QuantizeWorkerResult(ContractModel):
    outputPath: LocalPath
    tensorCount: Annotated[StrictInt, Field(ge=1)]
    fromPrecision: NonEmptyStr
    toPrecision: NonEmptyStr


class MergeWorkerResult(ContractModel):
    outputPath: LocalPath
    tensorCount: Annotated[StrictInt, Field(ge=1)]
    dequantized: StrictBool


class GgufWorkerResult(ContractModel):
    outputPath: LocalPath
    metadata: GgufExportMetadata


class _WorkerSuccessCommon(ContractModel):
    schemaVersion: SchemaVersion
    requestId: Id
    ok: StrictTrue


class DoctorWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["doctor"]
    result: DoctorWorkerResult


class TrainWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["train"]
    result: TrainWorkerResult


class GenerateWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["generate"]
    result: GenerateWorkerResult


class QuantizeWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["quantize"]
    result: QuantizeWorkerResult


class MergeWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["merge"]
    result: MergeWorkerResult


class GgufWorkerSuccess(_WorkerSuccessCommon):
    operation: Literal["gguf"]
    result: GgufWorkerResult


type WorkerSuccessValue = Annotated[
    DoctorWorkerSuccess
    | TrainWorkerSuccess
    | GenerateWorkerSuccess
    | QuantizeWorkerSuccess
    | MergeWorkerSuccess
    | GgufWorkerSuccess,
    Field(discriminator="operation"),
]


class WorkerError(ContractModel):
    code: Literal[
        "INVALID_REQUEST",
        "SAFE_LOAD_FAILED",
        "UNSUPPORTED_ARCHITECTURE",
        "TOKENIZATION_FAILED",
        "SEQUENCE_TOO_LONG",
        "TRAINING_FAILED",
        "GENERATION_FAILED",
        "NONFINITE_METRIC",
        "OUTPUT_INVALID",
        "INTERRUPTED",
        "INTERNAL",
    ]
    message: NonEmptyStr


class WorkerFailure(ContractModel):
    schemaVersion: SchemaVersion
    requestId: Id
    operation: Literal["doctor", "train", "generate", "quantize", "merge", "gguf"]
    ok: StrictFalse
    error: WorkerError


type WorkerResultValue = WorkerSuccessValue | WorkerFailure


class WorkerResult(RootModel[WorkerResultValue]):
    pass
