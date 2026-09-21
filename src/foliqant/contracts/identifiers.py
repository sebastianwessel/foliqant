"""Stable identifiers shared by workflow and deployment contracts."""

from typing import Annotated

from pydantic import Field

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")]
