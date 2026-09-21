"""Strict boundary objects; internal execution uses standard-library values."""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, PrivateAttr


class BoundaryModel(BaseModel):
    _environment_resolved: bool = PrivateAttr(default=False)

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, allow_inf_nan=False
    )


def _exact_integer_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("version must be the integer 1")
    return value


Version1 = Annotated[Literal[1], BeforeValidator(_exact_integer_version)]
