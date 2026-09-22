"""Accepted immutable data passed between the engine and its ports."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from .errors import ErrorCode, ServiceError
from .json import MAX_EXECUTION_JSON_DEPTH, FrozenJson, FrozenObject, freeze_json


@dataclass(frozen=True, slots=True, init=False)
class AcceptedEnvelope:
    """Independent immutable data; ingress still owns authentication and claims.

    Direct embedded construction also copies nested values. Constructing this
    object alone does not authenticate tenant/principal metadata.
    """

    payload: FrozenJson
    metadata: FrozenObject

    def __init__(self, *, payload: object, metadata: object) -> None:
        frozen_metadata = freeze_json(metadata)
        if not isinstance(frozen_metadata, Mapping):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        object.__setattr__(self, "payload", freeze_json(payload))
        object.__setattr__(self, "metadata", frozen_metadata)

    @classmethod
    def _from_frozen(cls, *, payload: FrozenJson, metadata: FrozenObject) -> Self:
        """Transfer engine-owned immutable values across a compiled boundary.

        Only the runner may use this path, after binding validated frozen input,
        validated operation results, or immutable engine-generated ledger wrappers.
        Business depth was checked at the original input or operation boundary.
        Copy again under the bounded execution-depth ceiling, allowing generated
        wrappers without accepting mutable aliases or unbounded routed nesting.
        """
        envelope = object.__new__(cls)
        object.__setattr__(
            envelope, "payload", freeze_json(payload, max_depth=MAX_EXECUTION_JSON_DEPTH)
        )
        frozen_metadata = freeze_json(metadata)
        if not isinstance(frozen_metadata, Mapping):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        object.__setattr__(envelope, "metadata", frozen_metadata)
        return envelope
