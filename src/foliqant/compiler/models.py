"""Offline step-model selection, separated from public credential-free plans."""

import hashlib
from collections.abc import Mapping

from pydantic import ValidationError

from foliqant.contracts.models import ModelConfig, ModelProfileOverride, StepModel
from foliqant.core.plan import SourceLocation

from .errors import CompilationError


class ModelRegistry:
    """Collect effective profiles privately while plans retain only binding IDs."""

    def __init__(self, profiles: Mapping[str, ModelConfig] | None = None) -> None:
        self.profiles = dict(profiles or {})
        self.admission_groups: dict[str, str] = {}

    def select(
        self,
        selection: StepModel | None,
        *,
        default: str | None,
        aliases: Mapping[str, str],
        workflow: str,
        step: str,
        flow: str,
        location: SourceLocation,
    ) -> str:
        selected = selection if selection is not None else default
        if selected is None or isinstance(selected, str):
            if selected is None or selected not in aliases:
                raise CompilationError("unknown_model", location, field="model")
            return selected
        source: str | None = None
        if isinstance(selected, ModelProfileOverride):
            source = selected.profile
            base = self.profiles.get(source)
            if base is None or source not in aliases:
                raise CompilationError("unknown_model", location, field="model.profile")
            document = base.model_dump(mode="python")
            if selected.model is not None:
                document["model"] = selected.model
                # Profile prices describe the profile's own model.
                if selected.model != base.model:
                    document["pricing"] = None
            if "pricing" in selected.model_fields_set:
                document["pricing"] = (
                    selected.pricing.model_dump(mode="python")
                    if selected.pricing is not None
                    else None
                )
            document["options"] = {
                **base.options.model_dump(mode="python"),
                **selected.options.model_dump(mode="python", exclude_unset=True),
            }
            try:
                effective = type(base).model_validate(document, strict=True)
            except ValidationError:
                raise CompilationError(
                    "invalid_model_options", location, field="model.options"
                ) from None
        else:
            effective = selected
        # Identity is deliberately independent of credential values. Distinct
        # steps own distinct bindings; derived bindings share source admission.
        token = hashlib.sha256(f"{workflow}:{flow}:{step}".encode()).hexdigest()
        alias = f"step_model_{token}"
        while alias in aliases:
            alias = f"step_{alias}"
        self.profiles[alias] = effective
        if source is not None:
            self.admission_groups[alias] = source
        return alias
