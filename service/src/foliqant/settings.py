"""Offline deployment loading, compilation and one-time environment resolution."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import cast

from dotenv import dotenv_values
from pydantic import ValidationError

from foliqant.adapters.handlers import HandlerExecutor, HandlerRegistration
from foliqant.compiler import CompilationError, compile_workflow
from foliqant.compiler._loader import load_yaml
from foliqant.contracts.deployment import DeploymentConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import HandlerStepPlan, SourceLocation, WorkflowPlan

_MAX_CONFIG_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedApplication:
    """Immutable compiled startup state; callers receive a fresh settings copy."""

    source: Path
    plans: Mapping[str, WorkflowPlan] = field(repr=False)
    configuration_digest: str
    _configuration: FrozenObject = field(repr=False)
    handlers: Mapping[str, HandlerRegistration] = field(repr=False)

    @property
    def config(self) -> DeploymentConfig:
        return DeploymentConfig.model_validate(thaw_json(self._configuration), strict=True)


def prepare_application(
    config_path: Path, *, handlers: Mapping[str, HandlerRegistration] | None = None
) -> PreparedApplication:
    """Compile local settings/workflows without resolving secrets or constructing SDKs."""
    try:
        source = config_path.resolve(strict=True)
        with source.open("rb") as stream:
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("configuration size exceeds limit")
        data = load_yaml(raw.decode("utf-8"), relative_path="foliqant.yaml")
        freeze_json(data)
        config = DeploymentConfig.model_validate(data, strict=True)
        root = source.parent
        plans: dict[str, WorkflowPlan] = {}
        registered = dict(handlers or {})
        HandlerExecutor(registered)  # Validate declared schemas offline before activation.
        for name, relative in config.workflows.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("invalid workflow directory")
            bundle = (root / path).resolve(strict=True)
            if not bundle.is_relative_to(root) or not bundle.is_dir():
                raise ValueError("workflow directory escapes deployment")
            plan = compile_workflow(
                bundle,
                model_aliases={alias: profile.model for alias, profile in config.models.items()},
                tool_catalogs={alias: profile.catalog for alias, profile in config.mcp.items()},
                handler_names=set(registered),
            )
            if any(
                isinstance(step, HandlerStepPlan) and registered[step.handler].effect != "read"
                for step in plan.steps
            ):
                raise ValueError("write handlers are unsupported by the read-only pipeline")
            if plan.name != name:
                raise ValueError("workflow name differs from deployment key")
            plans[name] = plan
        document = config.model_dump(mode="json", exclude_none=True)
        # Environment overlay values can be credentials; only their names identify
        # deployment structure. Prefer credential hooks for secret-bearing inputs.
        digest_document = config.model_dump(mode="json", exclude_none=True)
        for profile in digest_document["mcp"].values():
            if profile["transport"]["type"] == "stdio":
                profile["transport"]["env"] = sorted(profile["transport"]["env"])
        digest_input = {
            "settings": digest_document,
            "workflows": {name: plan.revision for name, plan in plans.items()},
            "handlers": {
                name: {
                    "input_schema": thaw_json(item.input_schema),
                    "output_schema": thaw_json(item.output_schema),
                    "effect": item.effect,
                }
                for name, item in registered.items()
            },
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_input, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        plans = {
            name: replace(
                plan, revision=hashlib.sha256(f"{digest}:{plan.revision}".encode()).hexdigest()
            )
            for name, plan in plans.items()
        }
        return PreparedApplication(
            source,
            MappingProxyType(plans),
            digest,
            cast(FrozenObject, freeze_json(document)),
            MappingProxyType(registered),
        )
    except CompilationError:
        raise
    except (OSError, UnicodeError, ValueError, ValidationError, ServiceError):
        raise CompilationError(
            "invalid_deployment", SourceLocation("foliqant.yaml", 1, 1)
        ) from None


def load_environment(config_path: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """Read configuration-local .env once; explicit process values always win."""
    values: dict[str, str] = {}
    path = config_path.resolve().parent / ".env"
    try:
        if path.exists():
            with path.open("rb") as stream:
                raw = stream.read(_MAX_CONFIG_BYTES + 1)
            if len(raw) > _MAX_CONFIG_BYTES:
                raise ValueError("environment file exceeds limit")
            values.update(
                {
                    key: value
                    for key, value in dotenv_values(
                        stream=StringIO(raw.decode("utf-8")), interpolate=False
                    ).items()
                    if value is not None
                }
            )
        values.update(environment)
        return values
    except (OSError, UnicodeError, ValueError):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
