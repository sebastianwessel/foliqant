"""Shared published contracts must remain visible to downstream type checkers."""

from importlib.resources import files


def test_shared_contract_distribution_is_typed() -> None:
    assert files("foliqant_decisions").joinpath("py.typed").is_file()


def test_service_distribution_is_typed() -> None:
    assert files("foliqant").joinpath("py.typed").is_file()
