"""Strict boundary objects; internal execution uses standard-library values."""

from pydantic import BaseModel, ConfigDict


class BoundaryModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, allow_inf_nan=False
    )
