"""Strict boundary objects; internal execution uses standard-library values."""

from pydantic import BaseModel, ConfigDict, PrivateAttr


class BoundaryModel(BaseModel):
    _environment_resolved: bool = PrivateAttr(default=False)

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, allow_inf_nan=False
    )
