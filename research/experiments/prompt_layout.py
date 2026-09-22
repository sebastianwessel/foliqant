"""Frozen, sequential workflow experiments; default validates without inference."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal, cast

from examples.common import ROOT, command, example_environment
from examples.decision_evidence import evaluate as evidence
from examples.security_evaluation import evaluate as security

from foliqant import (
    Envelope,
    ExecutionResult,
    RuntimePlugins,
    open_application,
    prepare_application,
)
from foliqant.adapters.decisions.instructions import (
    _DECISION_INPUT_POLICY,
    _DECISION_OUTPUT_CONTRACT,
)
from foliqant.adapters.decisions.instructions import (
    decision_instructions as _ORIGINAL_DECISION,
)
from foliqant.adapters.models import executor
from foliqant.adapters.models.instructions import (
    _UNTRUSTED_INPUT_POLICY,
)
from foliqant.adapters.models.instructions import (
    model_instructions as _ORIGINAL_MODEL,
)
from foliqant.contracts.models import CompatibleModelConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue
from foliqant.core.plan import DecisionStepPlan, LlmStepPlan
from foliqant.decisions.contracts import DecisionInput
from foliqant.evaluation import EvaluationSuite, EvaluationVariant, evaluate
from foliqant.evaluation.artifact import write_private_json, write_report
from foliqant.evaluation.dataset import (
    EvaluationDataset,
    SuiteSpec,
    metric_specs,
    read_json,
    validate_targets,
)
from foliqant.settings import PreparedApplication

Variant = Literal["baseline", "policy_control", "questions_first", "common_first"]
VARIANTS = ("baseline", "policy_control", "questions_first", "common_first")
_ORIGINAL_RENDER = executor._decision_prompt
_ACTIVE = False
PILOTS = {
    "security": (
        "stolen_wallet_clean",
        "stolen_wallet_attacked",
        "double_merchant_charge_clean",
        "double_merchant_charge_attacked",
    ),
    "evidence": ("two_requests", "two_requests_de", "tentative_document", "tentative_document_de"),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _decision_instructions(business: str, variant: str) -> str:
    if variant == "policy_control":
        return f"{business}\n\n{_DECISION_OUTPUT_CONTRACT}"
    if variant == "common_first":
        return f"{_DECISION_OUTPUT_CONTRACT}\n\n{_DECISION_INPUT_POLICY}\n\n{business}"
    return _ORIGINAL_DECISION(business)


def _model_instructions(business: str, variant: str) -> str:
    if variant == "policy_control":
        return business
    if variant == "common_first":
        return f"{_UNTRUSTED_INPUT_POLICY}\n\n{business}"
    return _ORIGINAL_MODEL(business)


def _questions_first(task: DecisionInput) -> str:
    document = json.loads(_ORIGINAL_RENDER(task))
    # Move only this object key. Preserve every array, source and string exactly.
    ordered = {"questions": document.pop("questions"), **document}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def variant_context(variant: str) -> Iterator[None]:
    """Patch three private renderers in this single experiment process and restore."""
    global _ACTIVE
    if variant not in VARIANTS or _ACTIVE:
        raise ValueError("unknown variant or overlapping experiment context")
    originals = {
        "decision_instructions": _ORIGINAL_DECISION,
        "model_instructions": _ORIGINAL_MODEL,
        "_decision_prompt": _ORIGINAL_RENDER,
    }
    if any(getattr(executor, name) is not value for name, value in originals.items()):
        raise RuntimeError("runtime prompt functions changed since experiment import")
    _ACTIVE = True
    try:
        if variant != "baseline":
            replacements = {
                "decision_instructions": lambda business: _decision_instructions(business, variant),
                "model_instructions": lambda business: _model_instructions(business, variant),
            }
            for name, replacement in replacements.items():
                setattr(executor, name, replacement)
        if variant == "questions_first":
            executor._decision_prompt = _questions_first
        yield
    finally:
        for name, value in originals.items():
            setattr(executor, name, value)
        _ACTIVE = False


@dataclass(frozen=True)
class Experiment:
    prepared: PreparedApplication
    gold: EvaluationDataset
    spec: SuiteSpec
    suite: EvaluationSuite
    files: Mapping[str, bytes]
    manifest: dict[str, JsonValue]
    environment: Mapping[str, str]


def _config_sources(prepared: PreparedApplication) -> set[Path]:
    """Select compiled definitions/schema resources, never scan private config files."""
    root = prepared.source.parent
    names = {prepared.source.name}
    for workflow in prepared.plans.values():
        names.add(workflow.location.path)
        names.update(resource.path for resource in workflow.schema_resources)
        for flow in workflow.flows:
            names.add(flow.location.path)
            names.update(step.location.path for step in flow.steps)
    paths = set()
    for name in names:
        path = root / name
        if (
            path.suffix not in {".yaml", ".yml", ".json", ".md"}
            or any(part.startswith(".") for part in Path(name).parts)
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("compiled configuration source is not safe to snapshot")
        paths.add(path)
    return paths


def _gold_sources(path: Path) -> set[Path]:
    """Snapshot the manifest and exact referenced case arrays, never the directory."""
    manifest = EvaluationDataset.model_validate(read_json(path), strict=True)
    paths = {path}
    for suite in manifest.suites:
        if not isinstance(suite.cases, str):
            continue
        reference = Path(suite.cases)
        target = reference if reference.is_absolute() else path.parent / reference
        target = target.resolve()
        if (
            target.suffix != ".json"
            or not target.is_relative_to(path.parent.resolve())
            or any(part.startswith(".") for part in reference.parts)
        ):
            raise ValueError("experiment case references must be local authored JSON files")
        paths.add(target)
    return paths


def prepare_experiment(
    *, dataset: str, variant: str, selection: str, environment: Mapping[str, str]
) -> Experiment:
    """Validate all settings and freeze exact source bytes without opening clients."""
    if dataset not in PILOTS or variant not in VARIANTS or selection not in {"pilot", "full"}:
        raise ValueError("invalid experiment selection")
    module = security if dataset == "security" else evidence
    gold = module.dataset()
    prepared = prepare_application(module.CONFIG_PATH)
    validate_targets(gold, prepared)
    spec = next(spec for spec in gold.suites if spec.flow is None and spec.step is None)
    suite = gold.to_suite(spec) if dataset == "security" else evidence.build_suite(gold, spec)
    if selection == "pilot":
        wanted = PILOTS[dataset]
        cases = {case.id: case for case in suite.cases}
        suite = EvaluationSuite(suite.name, suite.revision, tuple(cases[key] for key in wanted))
    resolved = dict(environment)
    profiles = prepared.config.models
    if len(profiles) != 1:
        raise ValueError("experiment requires one controlled model profile")
    profile = next(iter(profiles.values()))
    if not isinstance(profile, CompatibleModelConfig):
        raise ValueError("experiment requires the local OpenAI-compatible model profile")
    if (
        profile.concurrency != 1
        or profile.queue_limit != 0
        or profile.request_timeout != 300
        or profile.options.temperature != 0.1
        or profile.options.reasoning_effort != "low"
        or profile.options.max_tokens != 8192
        or profile.output_mode != "native"
        or prepared.config.execution.concurrency != 1
        or prepared.config.execution.model_timeout != 300
        or prepared.config.execution.run_timeout != 610
    ):
        raise ValueError("example settings drifted from the frozen experimental controls")
    if (
        not profile.model.startswith("$")
        or not profile.base_url
        or not profile.base_url.startswith("$")
    ):
        raise ValueError("experiment requires explicit model/endpoint environment references")
    for generic, reference in (("MODEL_ID", profile.model), ("MODEL_BASE_URL", profile.base_url)):
        if resolved.get(generic):
            resolved[reference[1:]] = resolved[generic]
    model = resolved.get(profile.model[1:])
    endpoint = resolved.get(profile.base_url[1:])
    if not model or not endpoint:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    paths = set((ROOT / "src/foliqant").rglob("*.py"))
    paths.update(_config_sources(prepared))
    paths.update(_gold_sources(module.DATASET_PATH))
    paths.update(
        {
            Path(cast(str, module.__file__)),
            ROOT / "examples/common.py",
            Path(__file__),
            Path(__file__).with_name("__init__.py"),
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
        }
    )
    files = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in sorted(paths)}
    instructions: dict[str, JsonValue] = {}
    for flow in prepared.plans[spec.workflow].flows:
        for step in flow.steps:
            if isinstance(step, DecisionStepPlan):
                value = _decision_instructions(step.instructions, variant)
            elif isinstance(step, LlmStepPlan):
                value = _model_instructions(step.instructions, variant)
            else:
                raise ValueError("experiment supports model steps only")
            instructions[f"{flow.name}/{step.name}"] = _sha(value.encode())
    manifest: dict[str, JsonValue] = {
        "experiment": "prompt_layout_security",
        "dataset": dataset,
        "dataset_name": gold.name,
        "dataset_revision": gold.revision,
        "variant": variant,
        "selection": selection,
        "scope": "workflow",
        "workflow": spec.workflow,
        "case_ids": [case.id for case in suite.cases],
        "suite_fingerprint": suite.fingerprint,
        "configuration_revision": prepared.configuration_digest,
        "source_sha256": {name: _sha(raw) for name, raw in files.items()},
        "static_instruction_sha256": instructions,
        "runtime_renderer_sha256": _sha(inspect.getsource(_ORIGINAL_RENDER).encode()),
        "variant_renderer_sha256": _sha(inspect.getsource(_questions_first).encode())
        if variant == "questions_first"
        else _sha(inspect.getsource(_ORIGINAL_RENDER).encode()),
        "model_identity_sha256": _sha(model.encode()),
        "endpoint_identity_sha256": _sha(endpoint.encode()),
        "controls": {
            "temperature": 0.1,
            "reasoning_effort": "low",
            "max_tokens": 8192,
            "max_concurrency": 1,
            "repeat": 1,
            "request_timeout": 300,
            "run_timeout": 610,
            "evaluation_timeout": 620,
        },
        "evidence_kind": "authored_synthetic_not_human_adjudicated",
        "python": sys.version,
        "dependencies": {
            name: version(name) for name in ("pydantic", "pydantic-ai-slim", "openai")
        },
        "source_snapshot": "snapshot",
    }
    return Experiment(prepared, gold, spec, suite, files, manifest, resolved)


def _unchanged(experiment: Experiment) -> None:
    for name, raw in experiment.files.items():
        if (ROOT / name).read_bytes() != raw:
            raise RuntimeError("experiment source changed; start a new frozen experiment")


def _destination(path: Path) -> Path:
    selected = path.resolve()
    if selected.is_relative_to(ROOT) and not selected.is_relative_to(ROOT / ".foliqant"):
        raise ValueError("experiment artifacts inside checkout must be under .foliqant")
    if path.is_symlink():
        raise ValueError("experiment directory cannot be a symlink")
    return selected


def freeze(experiment: Experiment, directory: Path, *, skip_completed: bool) -> bool:
    """Publish inputs before inference; only explicitly skip exact completed reports."""
    _unchanged(experiment)
    expected = _sha(_encoded(experiment.manifest))
    if directory.exists():
        if not skip_completed:
            raise FileExistsError("experiment output already exists")
        receipt = json.loads((directory / "completed.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            receipt["manifest_sha256"] != expected
            or _sha(_encoded(manifest)) != expected
            or receipt["report_sha256"] != _sha((directory / "report.json").read_bytes())
        ):
            raise ValueError("completed report does not match exact experiment hashes")
        for name, raw in experiment.files.items():
            if (directory / "snapshot" / name).read_bytes() != raw:
                raise ValueError("completed source snapshot changed")
        return True
    directory.mkdir(parents=True, mode=0o700)
    for name, raw in experiment.files.items():
        target = directory / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    write_private_json(directory / "manifest.json", experiment.manifest)
    return False


async def run_experiment(
    *,
    dataset: str = "security",
    variant: str = "baseline",
    selection: str = "pilot",
    live: bool = False,
    directory: Path | None = None,
    skip_completed: bool = False,
    environment: Mapping[str, str] | None = None,
    plugins: RuntimePlugins | None = None,
) -> dict[str, JsonValue]:
    """Run exactly one independent variant; no parallel variants or implicit resume."""
    experiment = prepare_experiment(
        dataset=dataset,
        variant=variant,
        selection=selection,
        environment=example_environment() if environment is None else environment,
    )
    mode = "live_model" if plugins is None else "offline_wiring"
    experiment.manifest["execution_mode"] = mode
    if not live:
        return {
            "ok": True,
            "mode": "offline_check",
            "cases": len(experiment.suite.cases),
            "manifest_sha256": _sha(_encoded(experiment.manifest)),
        }
    if directory is None:
        raise ValueError("live experiments require an explicit new private directory")
    directory = _destination(directory)
    if freeze(experiment, directory, skip_completed=skip_completed):
        return {
            "ok": json.loads((directory / "completed.json").read_text())["ok"],
            "mode": "skipped_completed",
            "directory": str(directory),
        }
    halted = False
    completed = 0
    _unchanged(experiment)
    with variant_context(variant):
        async with open_application(
            experiment.prepared,
            environment=experiment.environment,
            plugins=plugins or RuntimePlugins(),
        ) as app:

            async def run(envelope: Envelope) -> ExecutionResult:
                nonlocal halted, completed
                try:
                    if halted:
                        raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
                    _unchanged(experiment)
                    result = await app.run(experiment.spec.workflow, envelope)
                    if result.execution.error and result.execution.error.code == ErrorCode.TIMEOUT:
                        halted = True
                    return result
                except ServiceError as error:
                    if error.code == ErrorCode.TIMEOUT:
                        halted = True
                    raise
                except asyncio.CancelledError:
                    halted = True
                    raise
                finally:
                    completed += 1
                    print(
                        json.dumps(
                            {
                                "completed": completed,
                                "total": len(experiment.suite.cases),
                                "halted": halted,
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

            report = await evaluate(
                experiment.suite,
                EvaluationVariant(
                    variant,
                    _sha(_encoded(experiment.manifest)),
                    run,
                    experiment.prepared.configuration_digest,
                    workflow=experiment.spec.workflow,
                ),
                scorers=evidence.SCORERS if dataset == "evidence" else (),
                metrics=metric_specs(experiment.spec),
                include_details=True,
                max_concurrency=1,
                timeout=620,
            )
    write_report(
        directory / "report.json",
        [report],
        mode=mode,
        dataset_name=experiment.gold.name,
        dataset_revision=experiment.gold.revision,
    )
    write_private_json(
        directory / "completed.json",
        {
            "manifest_sha256": _sha(_encoded(experiment.manifest)),
            "report_sha256": _sha((directory / "report.json").read_bytes()),
            "halted_after_timeout": halted,
            "completed_cases": completed,
            "ok": report.checks.passed == report.checks.total,
        },
    )
    return {
        "ok": report.checks.passed == report.checks.total,
        "mode": mode,
        "cases": len(report.cases),
        "halted_after_timeout": halted,
        "passed_checks": report.checks.passed,
        "total_checks": report.checks.total,
        "directory": str(directory),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=tuple(PILOTS), default="security")
    parser.add_argument("--variant", choices=VARIANTS, default="baseline")
    parser.add_argument("--selection", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--skip-completed", action="store_true")
    args = parser.parse_args()
    return command(lambda: run_experiment(**vars(args)))


if __name__ == "__main__":
    raise SystemExit(main())
