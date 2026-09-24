"""Offline deployment loading, compilation and one-time environment resolution."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from dotenv import dotenv_values
from pydantic import ValidationError

from foliqant.adapters.handlers import HandlerExecutor, HandlerRegistration
from foliqant.compiler import CompilationError, compile_workflow
from foliqant.compiler._loader import YamlLocator, load_yaml
from foliqant.compiler.models import ModelRegistry
from foliqant.compiler.schema_helpers import validate_confined_tool_schema
from foliqant.contracts.deployment import DeploymentConfig, HandlerDeclaration
from foliqant.contracts.models import ModelConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import Diagnostic, HandlerStepPlan, SourceLocation, WorkflowPlan

_MAX_CONFIG_BYTES = 1024 * 1024


def _read_settings_file(path: Path) -> bytes:
    """Read bounded regular data without blocking on special files or races."""
    selected = path.resolve(strict=True)
    if not selected.is_file():
        raise ValueError("configuration must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(selected, flags)
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_CONFIG_BYTES:
            raise ValueError("configuration must be a bounded regular file")
        raw = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ValueError("configuration size exceeds limit")
    return raw


@dataclass(frozen=True, slots=True)
class HandlerContract:
    """A declared handler contract with frozen, confined JSON Schemas."""

    input_schema: FrozenObject
    output_schema: FrozenObject
    effect: Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class PreparedApplication:
    """Immutable compiled startup state; callers receive a fresh settings copy.

    ``handlers`` holds the host registrations resolved against their declared
    ``handler_contracts``. ``diagnostics`` lists every non-fatal compiler finding.
    """

    source: Path
    plans: Mapping[str, WorkflowPlan] = field(repr=False)
    configuration_digest: str
    _configuration: FrozenObject = field(repr=False)
    handlers: Mapping[str, HandlerRegistration] = field(repr=False)
    _models: Mapping[str, ModelConfig] = field(repr=False)
    _model_admission_groups: Mapping[str, str] = field(repr=False)
    handler_contracts: Mapping[str, HandlerContract] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    diagnostics: tuple[Diagnostic, ...] = ()
    strict: bool = False
    """Prepared with ``strict=True``: :func:`open_application` refuses any warning."""
    _handler_locations: Mapping[str, SourceLocation] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )

    @property
    def config(self) -> DeploymentConfig:
        return DeploymentConfig.model_validate(thaw_json(self._configuration), strict=True)


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise ValueError("non-finite number")


def _contract_schema(
    value: str | dict[str, object], root: Path, location: SourceLocation, field_path: str
) -> FrozenObject:
    """Load one inline or confined local schema and check it is self-contained."""
    try:
        if isinstance(value, str):
            if "://" in value or Path(value).is_absolute():
                raise ValueError("schema path must be local")
            path = (root / value).resolve(strict=True)
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.suffix not in {".json", ".yaml", ".yml"}
            ):
                raise ValueError("schema path escapes the configuration")
            text = _read_settings_file(path).decode("utf-8")
            raw: object = (
                json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_reject_constant)
                if path.suffix == ".json"
                else load_yaml(text, relative_path=path.relative_to(root).as_posix())
            )
        else:
            raw = value
        if not isinstance(raw, dict):
            raise ValueError("schema must be an object")
        validate_confined_tool_schema(cast(dict[str, object], raw))
        frozen = freeze_json(raw)
        assert isinstance(frozen, Mapping)
        return frozen
    except Exception:
        # Schema errors, YAML errors and escaping paths share one safe reason.
        raise CompilationError("invalid_handler_schema", location, field=field_path) from None


def _handler_contracts(
    declarations: Mapping[str, HandlerDeclaration], root: Path, locator: YamlLocator
) -> dict[str, HandlerContract]:
    return {
        name: HandlerContract(
            _contract_schema(
                cast(str | dict[str, object], declaration.input_schema),
                root,
                locator.locate("handlers", name, "input_schema"),
                f"handlers.{name}.input_schema",
            ),
            _contract_schema(
                cast(str | dict[str, object], declaration.output_schema),
                root,
                locator.locate("handlers", name, "output_schema"),
                f"handlers.{name}.output_schema",
            ),
            declaration.effect,
        )
        for name, declaration in declarations.items()
    }


_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_$.-]{1,64}\Z")


def _first_difference(left: FrozenJson, right: FrozenJson, path: str = "") -> str | None:
    """Return the first differing JSON pointer (canonical key order), or None."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        for key in sorted(set(left) | set(right)):
            token = key if _SAFE_TOKEN.fullmatch(key) else "*"
            if key not in left or key not in right:
                return f"{path}/{token}"
            found = _first_difference(left[key], right[key], f"{path}/{token}")
            if found is not None:
                return found
        return None
    if isinstance(left, tuple) and isinstance(right, tuple):
        if len(left) != len(right):
            return path or "/"
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            found = _first_difference(a, b, f"{path}/{index}")
            if found is not None:
                return found
        return None
    numbers = all(
        isinstance(item, int | float) and not isinstance(item, bool) for item in (left, right)
    )
    # JSON numbers compare by value (`1` equals `1.0`); booleans never equal numbers.
    if (not numbers and type(left) is not type(right)) or left != right:
        return path or "/"
    return None


def _handler_field(name: object) -> str:
    """Name a handler in a diagnostic field only when it is a valid identifier."""
    return (
        f"handlers.{name}" if isinstance(name, str) and _HANDLER_ID.fullmatch(name) else "handlers"
    )


_HANDLER_ID = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")


def _resolve_registrations(
    registered: Mapping[str, HandlerRegistration],
    contracts: Mapping[str, HandlerContract],
    locator: YamlLocator,
) -> dict[str, HandlerRegistration]:
    """Verify each registration against its declaration and fill omitted schemas."""
    effective: dict[str, HandlerRegistration] = {}
    for name, registration in registered.items():
        label = name if isinstance(name, str) and _HANDLER_ID.fullmatch(name) else "?"
        if not isinstance(registration, HandlerRegistration):
            raise CompilationError(
                "invalid_registry",
                locator.locate("handlers"),
                field=_handler_field(name),
                message=f"The registration for handler `{label}` is not a HandlerRegistration.",
            )
        contract = contracts.get(name)
        if contract is None:
            # Every registration needs a reviewed declaration under `handlers:`.
            raise CompilationError(
                "unknown_handler",
                locator.locate("handlers"),
                field=_handler_field(name),
                message=f"Handler `{label}` is registered by the host but not declared under "
                "`handlers` in settings.yaml.",
            )
        if registration.effect != contract.effect:
            raise CompilationError(
                "handler_contract_mismatch",
                locator.locate("handlers", name, "effect"),
                field=f"handlers.{name}.effect",
                message=f"Handler `{label}` is registered with effect `{registration.effect}` "
                f"but declared with `{contract.effect}`.",
            )
        for schema_field in ("input_schema", "output_schema"):
            given = getattr(registration, schema_field)
            if given is None:
                continue
            difference = _first_difference(given, getattr(contract, schema_field))
            if difference is not None:
                raise CompilationError(
                    "handler_contract_mismatch",
                    locator.locate("handlers", name, schema_field),
                    field=f"handlers.{name}.{schema_field}{difference}",
                    message=f"The registered {schema_field} of handler `{label}` differs from "
                    f"the declared one at `{difference}`.",
                )
        effective[name] = HandlerRegistration(
            registration.handler, contract.input_schema, contract.output_schema, contract.effect
        )
    return effective


def prepare_application(
    config_path: Path,
    *,
    handlers: Mapping[str, HandlerRegistration] | None = None,
    strict: bool = False,
) -> PreparedApplication:
    """Compile local settings/workflows without resolving secrets or constructing SDKs.

    Handlers are declared in ``settings.yaml``; ``handlers`` supplies the trusted
    callables. Declared handlers without a registration compile (so offline
    commands work) and fail at :func:`open_application`. With ``strict`` any
    warning diagnostic raises :class:`CompilationError`.
    """
    locator: YamlLocator | None = None
    try:
        source = config_path.resolve(strict=True)
        raw = _read_settings_file(source)
        text = raw.decode("utf-8")
        data = load_yaml(text, relative_path=source.name)
        locator = YamlLocator(text, relative_path=source.name)
        settings_locator = locator
        freeze_json(data)
        root = source.parent
        if isinstance(data, dict) and "workflows" not in data:
            discovered = {
                path.parent.name: path.parent.relative_to(root).as_posix()
                for path in sorted(root.glob("*/workflow.yaml"))
                if not path.parent.name.startswith(".")
            }
            data["workflows"] = discovered
        config = DeploymentConfig.model_validate(data, strict=True)
        if config.workflows is None:
            raise ValueError("workflows must be configured or discoverable")
        contracts = _handler_contracts(config.handlers, root, settings_locator)
        registered = _resolve_registrations(dict(handlers or {}), contracts, settings_locator)
        plans: dict[str, WorkflowPlan] = {}
        models = ModelRegistry(config.models)
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
                handler_names=set(contracts),
                model_profiles=config.models,
                handler_schemas=contracts,
                _model_registry=models,
                configuration_root=source.parent,
                max_steps=config.execution.max_steps,
            )
            if any(
                isinstance(step, HandlerStepPlan) and contracts[step.handler].effect != "read"
                for flow in plan.flows
                for step in flow.steps
            ):
                raise ValueError("write handlers are unsupported by the read-only pipeline")
            if plan.name != name:
                raise ValueError("workflow name differs from deployment key")
            plans[name] = plan
        document = data
        # Secret literals are redacted; authored references retain their names.
        # Evaluation-only references neither affect execution nor require gold
        # to be deployed. Changing the reference must not revise runtime plans.
        digest_document = config.model_dump(
            mode="json", exclude_none=True, exclude={"evaluation", "handlers"}
        )
        digest_input = {
            "settings": digest_document,
            "workflows": {name: plan.revision for name, plan in plans.items()},
            "handlers": {
                name: {
                    "input_schema": thaw_json(item.input_schema),
                    "output_schema": thaw_json(item.output_schema),
                    "effect": item.effect,
                }
                for name, item in sorted(contracts.items())
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
        diagnostics = tuple(item for plan in plans.values() for item in plan.diagnostics)
        prepared = PreparedApplication(
            source,
            MappingProxyType(plans),
            digest,
            cast(FrozenObject, freeze_json(document)),
            MappingProxyType(registered),
            MappingProxyType(models.profiles),
            MappingProxyType(models.admission_groups),
            MappingProxyType(contracts),
            diagnostics,
            strict,
            MappingProxyType(
                {name: settings_locator.locate("handlers", name) for name in sorted(contracts)}
            ),
        )
    except CompilationError:
        raise
    except ValidationError as error:
        raise _deployment_error(error, locator, config_path) from None
    except OSError:
        raise CompilationError(
            "invalid_deployment",
            SourceLocation(config_path.name, 1, 1),
            message="The settings file or a workflow directory it names cannot be read.",
        ) from None
    except (RuntimeError, UnicodeError, ValueError, ServiceError):
        raise CompilationError(
            "invalid_deployment", SourceLocation(config_path.name, 1, 1)
        ) from None
    if strict:
        require_no_warnings(prepared)
    return prepared


def _deployment_error(
    error: ValidationError, locator: YamlLocator | None, config_path: Path
) -> CompilationError:
    """Locate the first settings validation problem without echoing values."""
    if locator is None:
        return CompilationError("invalid_deployment", SourceLocation(config_path.name, 1, 1))
    issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
    kept, location, final = locator.follow(tuple(issue["loc"]), frozenset())
    parts = [
        str(part) if isinstance(part, int) or _HANDLER_ID.fullmatch(part) else "*"
        for part in (*kept, *((final,) if isinstance(final, str) else ()))
    ]
    reason = "unknown_field" if issue["type"] == "extra_forbidden" else "invalid_deployment"
    return CompilationError(reason, location, field=".".join(parts) or None)


def require_no_warnings(prepared: PreparedApplication) -> None:
    """Fail with every warning of an application prepared with ``strict=True``."""
    if not prepared.strict:
        return
    warnings = tuple(item for item in prepared.diagnostics if item.level == "warning")
    if warnings:
        raise CompilationError.from_problems(warnings, prepared.diagnostics)


def require_handler_registrations(prepared: PreparedApplication) -> None:
    """Fail before activation when a declared handler has no host registration."""
    missing = sorted(set(prepared.handler_contracts) - set(prepared.handlers))
    if missing:
        raise CompilationError.from_problems(
            CompilationError(
                "missing_handler_registration",
                prepared._handler_locations.get(name, SourceLocation(prepared.source.name, 1, 1)),
                field=_handler_field(name),
                message=f"Handler `{name}` is declared in settings.yaml but the host registered "
                "no callable for it; pass it to prepare_application(handlers=...).",
            ).problems[0]
            for name in missing
        )


def load_environment(config_path: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """Read configuration-local .env once; explicit process values always win."""
    values: dict[str, str] = {}
    try:
        path = config_path.resolve().parent / ".env"
        if path.exists() or path.is_symlink():
            raw = _read_settings_file(path)
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
    except (OSError, RuntimeError, UnicodeError, ValueError):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
