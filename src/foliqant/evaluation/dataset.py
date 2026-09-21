"""Explicit JSON ground truth, loaded only when evaluation is requested."""

import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from foliqant.contracts.base import BoundaryModel, Version1
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.workflow import Id, NonBlank
from foliqant.core.json import JsonValue, freeze_json
from foliqant.settings import PreparedApplication

from .contracts import EvaluationCase, EvaluationSuite, Expectation
from .metrics import MetricSpec

MAX_DATASET_BYTES = 64 * 1024 * 1024


class GoldExpectation(BoundaryModel):
    """An explicit assertion over an ExecutionResult JSON pointer."""

    name: NonBlank
    path: str
    expected: JsonValue
    comparison: Literal["exact", "set"] = "exact"

    def expectation(self) -> Expectation:
        return Expectation(self.name, self.path, freeze_json(self.expected), self.comparison)

    @model_validator(mode="after")
    def validate_expectation(self) -> Self:
        self.expectation()
        return self


class GoldCase(BoundaryModel):
    """Caller-authored input and assertions; no response is inferred as gold."""

    id: NonBlank
    input: Envelope
    expectations: Annotated[list[GoldExpectation], Field(min_length=1, max_length=1024)]

    def case(self) -> EvaluationCase:
        return EvaluationCase(
            self.id, self.input, tuple(item.expectation() for item in self.expectations)
        )

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        self.case()
        return self


class MetricConfig(BoundaryModel):
    """Optional metric over a declared, ordered category catalog."""

    name: NonBlank
    path: str
    kind: Literal["classification", "multilabel"]
    labels: Annotated[list[NonBlank], Field(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def validate_metric(self) -> Self:
        MetricSpec(self.name, self.path, self.kind, tuple(self.labels))
        return self


class SuiteSpec(BoundaryModel):
    """A pipeline target, or one step with already-resolved step inputs."""

    name: NonBlank
    workflow: Id
    step: Id | None = None
    cases: Annotated[list[GoldCase], Field(min_length=1, max_length=100_000)] | NonBlank
    metrics: Annotated[list[MetricConfig], Field(max_length=64)] = Field(default_factory=list)

    @property
    def gold_cases(self) -> list[GoldCase]:
        """Return loaded cases; a reference requires explicit file loading first."""
        if isinstance(self.cases, str):
            raise ValueError("load referenced cases before evaluation")
        return self.cases

    @model_validator(mode="after")
    def validate_gold(self) -> Self:
        if isinstance(self.cases, str):
            return self
        EvaluationSuite(self.name, "validation", tuple(case.case() for case in self.cases))
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise ValueError("metric names must be unique")
        for metric in self.metrics:
            matched = 0
            for case in self.gold_cases:
                gold = [item for item in case.expectations if item.path == metric.path]
                if len(gold) > 1:
                    raise ValueError("metric path must have at most one gold expectation per case")
                if not gold:
                    continue
                matched += 1
                value = gold[0].expected
                if metric.kind == "classification":
                    if type(value) is not str or value not in metric.labels:
                        raise ValueError("classification gold must be a declared label")
                elif (
                    not isinstance(value, list)
                    or any(type(item) is not str or item not in metric.labels for item in value)
                    or len(set(value)) != len(value)
                ):
                    raise ValueError("multilabel gold must be a unique array of declared labels")
            if not matched:
                raise ValueError("metric requires matching gold")
        return self


class EvaluationDataset(BoundaryModel):
    """Versioned ground truth for configured pipeline and isolated-step evaluation.

    The JSON file is independent of deployment dependencies. Loading and target
    checks never resolve environment secrets, import user code or open SDKs.
    """

    version: Version1
    name: NonBlank
    revision: NonBlank
    suites: Annotated[list[SuiteSpec], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def unique_suites(self) -> Self:
        if len({suite.name for suite in self.suites}) != len(self.suites):
            raise ValueError("suite names must be unique")
        return self

    def to_suite(self, spec: SuiteSpec) -> EvaluationSuite:
        """Detach a file boundary into the shared immutable evaluator values."""
        return EvaluationSuite(
            spec.name, self.revision, tuple(case.case() for case in spec.gold_cases)
        )


def metric_specs(spec: SuiteSpec) -> tuple[MetricSpec, ...]:
    """Convert explicit metric declarations without guessing label semantics."""
    return tuple(
        MetricSpec(item.name, item.path, item.kind, tuple(item.labels)) for item in spec.metrics
    )


def read_json(path: Path, *, max_bytes: int = MAX_DATASET_BYTES) -> object:
    """Read one bounded regular JSON file; reject duplicate keys and nonfinite numbers."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        values: dict[str, object] = {}
        for key, value in items:
            if key in values:
                raise ValueError("duplicate JSON key")
            values[key] = value
        return values

    def constant(value: str) -> object:
        raise ValueError("nonfinite JSON number")

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
            raise ValueError("expected bounded regular JSON file")
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("JSON file exceeds size limit")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_targets(dataset: EvaluationDataset, prepared: PreparedApplication) -> None:
    """Check known targets and result roots, not unknowable dynamic output fields."""
    for suite in dataset.suites:
        plan = prepared.plans.get(suite.workflow)
        if plan is None:
            raise ValueError("unknown evaluation workflow")
        names = {step.name for step in plan.steps}
        if suite.step is not None and suite.step not in names:
            raise ValueError("unknown evaluation step")
        for case in suite.gold_cases:
            for expectation in case.expectations:
                parts = [
                    item.replace("~1", "/").replace("~0", "~")
                    for item in expectation.path.split("/")[1:]
                ]
                if not parts:
                    continue  # The empty pointer is the whole result.
                if parts[0] not in {"payload", "metadata", "decisions", "execution"}:
                    raise ValueError("unknown execution result root")
                if parts[0] == "decisions" and len(parts) >= 2:
                    if parts[1] not in names or (suite.step is not None and parts[1] != suite.step):
                        raise ValueError("expectation references an unavailable step")
                if (
                    parts[0] == "execution"
                    and len(parts) >= 2
                    and parts[1] not in {"id", "workflow", "revision", "status", "usage", "error"}
                ):
                    raise ValueError("unknown execution field")


def load_dataset(prepared: PreparedApplication) -> EvaluationDataset:
    """Load configured private gold on demand; application startup never calls this."""
    config = prepared.config.evaluation
    if config is None:
        raise ValueError("evaluation.dataset is required")
    path = Path(config.dataset)
    if not path.is_absolute():
        path = prepared.source.parent / path
    dataset = EvaluationDataset.model_validate(read_json(path), strict=True)
    # References are one-level case arrays, relative to the manifest, not cwd.
    # Materialize to the same boundary as inline gold before validating/scoring.
    document = dataset.model_dump(mode="json")
    for suite in document["suites"]:
        if isinstance(suite["cases"], str):
            case_path = Path(suite["cases"])
            if not case_path.is_absolute():
                case_path = path.parent / case_path
            suite["cases"] = read_json(case_path)
            if not isinstance(suite["cases"], list):
                raise ValueError("case file must contain a JSON array")
    dataset = EvaluationDataset.model_validate(document, strict=True)
    validate_targets(dataset, prepared)
    return dataset
