"""Strict authoring contract for caller-defined classification categories."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator, Field, StringConstraints, field_validator

from ..contracts.base import ContractModel
from .decision_contracts import DecisionOption

_CATEGORY_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def normalize_category_key(value: object) -> str:
    """Return one canonical ASCII snake-case category key.

    ASCII letters are lowercased, runs of spaces or punctuation become one
    underscore, and outer separators are removed. Non-ASCII and control
    characters are rejected so normalization never drops meaningful letters.
    """

    if not isinstance(value, str):
        raise ValueError("category IDs must be strings")
    if len(value) > 128:
        raise ValueError("category IDs must not exceed 128 characters")
    if not value or any(not (character == " " or "!" <= character <= "~") for character in value):
        raise ValueError("category IDs must contain only printable ASCII characters")
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not _CATEGORY_KEY.fullmatch(normalized):
        raise ValueError(
            "category IDs must normalize to lowercase snake_case starting with a letter"
        )
    if len(normalized) > 128:
        raise ValueError("normalized category IDs must not exceed 128 characters")
    return normalized


CategoryKey = Annotated[
    str,
    BeforeValidator(normalize_category_key),
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]
CategoryDescription = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, pattern=r"\S"),
]


class CategoryDefinition(ContractModel):
    """One stable machine key with caller-authored category semantics."""

    id: CategoryKey = Field(
        pattern=r"^[\x20-\x7e]+$",
        description=(
            "Printable ASCII authoring key. The Python CategoryCatalog parser serializes its "
            "deterministic lowercase snake_case normalization."
        ),
    )
    description: CategoryDescription


class CategoryCatalog(ContractModel):
    """A bounded classification catalog prepared before model inference.

    Category keys are normalized deterministically and descriptions are
    preserved exactly. Unsupported external taxonomies require an explicit
    mapping before they enter this contract.
    """

    categories: Annotated[list[CategoryDefinition], Field(min_length=1, max_length=1024)]

    @field_validator("categories")
    @classmethod
    def unique_category_keys(cls, value: list[CategoryDefinition]) -> list[CategoryDefinition]:
        if len({category.id for category in value}) != len(value):
            raise ValueError("category IDs must be unique")
        return value

    def decision_options(self) -> list[DecisionOption]:
        """Convert the authored catalog into the existing decision option contract."""

        return [
            DecisionOption(id=category.id, description=category.description)
            for category in self.categories
        ]

    def resolve_id(self, raw: str) -> str:
        """Normalize a supplied key and require exact catalog membership."""

        category_id = normalize_category_key(raw)
        if category_id not in {category.id for category in self.categories}:
            raise ValueError(f"unknown category ID: {category_id}")
        return category_id
