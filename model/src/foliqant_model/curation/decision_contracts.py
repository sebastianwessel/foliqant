"""Generation-only settings kept outside the shared decision contracts."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictInt

from ..contracts.base import ContractModel


class DecisionDataSettings(ContractModel):
    """Bounded native decision-data generation settings."""

    examplesPerScenario: Annotated[StrictInt, Field(ge=4, le=10_000)] = 4
    sourceExamplesPerSource: Annotated[StrictInt, Field(ge=0, le=100_000)] = 100
    minimumAcceptedPerCell: Annotated[StrictInt, Field(ge=0, le=1_000)] = 1
