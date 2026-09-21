"""Embedded construction cannot introduce mutable aliases into accepted input."""

from collections.abc import Mapping

import pytest

from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ServiceError
from foliqant.core.json import freeze_json, thaw_json


def test_direct_construction_defensively_copies_all_nested_data() -> None:
    payload = {"items": ["original"]}
    metadata = {"tenant_id": "first", "nested": {"trace": ["original"]}}
    accepted = AcceptedEnvelope(payload=payload, metadata=metadata)
    payload["items"].append("mutated")
    metadata["tenant_id"] = "second"
    metadata["nested"] = {"trace": ["changed"]}
    assert thaw_json(accepted.payload) == {"items": ["original"]}
    assert thaw_json(accepted.metadata) == {"tenant_id": "first", "nested": {"trace": ["original"]}}
    assert isinstance(accepted.payload, Mapping)
    with pytest.raises(TypeError):
        accepted.payload["bad"] = True  # type: ignore[index]


def test_already_frozen_data_is_accepted_without_thawing() -> None:
    payload = freeze_json({"items": [1, 2]})
    metadata = freeze_json({"principal_id": "person"})
    accepted = AcceptedEnvelope(payload=payload, metadata=metadata)
    assert thaw_json(accepted.payload) == {"items": [1, 2]}
    assert thaw_json(accepted.metadata) == {"principal_id": "person"}


@pytest.mark.parametrize("metadata", [None, [], "claim", 123, True])
def test_metadata_must_be_an_object(metadata: object) -> None:
    with pytest.raises(ServiceError):
        AcceptedEnvelope(payload=None, metadata=metadata)


def test_cycle_rejected_with_bounded_safe_error() -> None:
    metadata: dict[str, object] = {}
    metadata["cycle"] = metadata
    with pytest.raises(ServiceError):
        AcceptedEnvelope(payload=None, metadata=metadata)
