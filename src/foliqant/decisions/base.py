"""Canonical scalar definitions shared by Foliqant decision contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints


class ContractModel(BaseModel):
    """Base for every closed strict contract object."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        populate_by_name=False,
        serialize_by_alias=True,
    )


def _non_empty(value: str) -> str:
    if not value:
        raise ValueError("must be nonempty")
    return value


def _strict_one(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("must be the integer 1")
    return value


def _strict_two(value: object) -> object:
    if type(value) is not int or value != 2:
        raise ValueError("must be the integer 2")
    return value


NonEmptyStr = Annotated[
    str, StringConstraints(strict=True, min_length=1), AfterValidator(_non_empty)
]
Id = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        min_length=1,
        max_length=128,
    ),
]
SchemaVersion = Annotated[Literal[1], BeforeValidator(_strict_one)]

DecisionSchemaVersion = Annotated[Literal[2], BeforeValidator(_strict_two)]
"""Wire version for native decision inputs and outputs only."""
