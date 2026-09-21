"""Accepted immutable data passed between the engine and its ports."""

from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ErrorCode, ServiceError
from .json import FrozenJson, FrozenObject, freeze_json


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
