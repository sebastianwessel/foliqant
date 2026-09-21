"""Task-specific request schemas preserve the external V1 output contract."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator

import pytest
from jsonschema import Draft202012Validator, ValidationError

from foliqant.decisions import (
    DecisionInput,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    validate_decision_output,
)
from foliqant_model.contracts.inputs import ChatMessage
from foliqant_model.curation import decision_generation
from foliqant_model.curation.decision_generation import _decision_output_schema
from foliqant_model.curation.decision_seeds import _authored_case
from foliqant_model.curation.endpoint import LocalEndpointConfig, _generation_request_sha256

_SCENARIOS = {
    "choice": "choice-answerable",
    "multiselect": "multiselect",
    "predicate": "predicate-true",
    "ordinal": "ordinal-answerable",
    "request_units": "requests-different",
}
_TYPE_SUBSETS = [
    subset for count in range(1, 6) for subset in itertools.combinations(_SCENARIOS, count)
]


def _task(types: tuple[str, ...]) -> DecisionInput:
    return DecisionInput(
        state=DecisionState(sources=[DecisionSource(id="source", kind="message", text="Example.")]),
        questions=[
            _authored_case(_SCENARIOS[kind], 0, "en")[0]
            .questions[0]
            .model_copy(update={"allowedSourceIds": ["source"]})
            for kind in types
        ],
    )


def _references(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for nested in value.values():
            yield from _references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _references(nested)


@pytest.mark.parametrize("types", _TYPE_SUBSETS)
def test_every_type_subset_is_valid_closed_and_preserves_applicable_v1_outputs(
    types: tuple[str, ...],
) -> None:
    assert len(_TYPE_SUBSETS) == 31
    full = _decision_output_schema()
    schema = _decision_output_schema(_task(types))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    definitions = schema["$defs"]
    assert all(ref.removeprefix("#/$defs/") in definitions for ref in _references(schema))
    union = definitions["DecisionResult"]
    assert set(union["discriminator"]["mapping"]) == set(types)
    # The schema retains root order and the original order of every surviving definition.
    assert list(schema) == list(full)
    assert list(definitions) == [key for key in full["$defs"] if key in definitions]
    assert all(schema[key] == full[key] for key in schema if key != "$defs")
    for name, definition in definitions.items():
        if name != "DecisionResult":
            assert json.dumps(definition) == json.dumps(full["$defs"][name])
    assert union["oneOf"] == [
        branch
        for branch in full["$defs"]["DecisionResult"]["oneOf"]
        if branch["$ref"] in union["discriminator"]["mapping"].values()
    ]
    for kind, scenario in _SCENARIOS.items():
        task, oracle = _authored_case(scenario, 0, "en")
        output = oracle.model_dump(mode="json")
        assert validate_decision_output(task, oracle) == []
        DecisionOutput.model_validate(output, strict=True)
        if kind in types:
            validator.validate(output)
        else:
            with pytest.raises(ValidationError):
                validator.validate(output)
    # No call mutates a shared full schema or a subsequent request.
    assert json.dumps(_decision_output_schema()) == json.dumps(full)
    assert json.dumps(_decision_output_schema(_task(types))) == json.dumps(schema)


def test_all_types_preserve_exact_full_schema_declaration_bytes() -> None:
    assert json.dumps(_decision_output_schema(_task(tuple(_SCENARIOS)))) == json.dumps(
        _decision_output_schema()
    )


def test_specialization_does_not_narrow_ids_answers_or_answerability_values() -> None:
    full = _decision_output_schema()
    for types in _TYPE_SUBSETS:
        schema = _decision_output_schema(_task(types))
        for key, definition in schema["$defs"].items():
            if key != "DecisionResult":
                assert definition == full["$defs"][key]
    for scenario in ("predicate-true", "predicate-false", "predicate-unknown"):
        task, oracle = _authored_case(scenario, 0, "en")
        Draft202012Validator(_decision_output_schema(task)).validate(oracle.model_dump(mode="json"))
    task, oracle = _authored_case("choice-answerable", 0, "en")
    output = oracle.model_dump(mode="json")
    output["results"][0]["questionId"] = "another_question"
    output["results"][0]["answer"]["optionId"] = "another_option"
    Draft202012Validator(_decision_output_schema(task)).validate(output)
    # Existing semantic validation, not pruning, rejects mismatched question/option IDs.
    assert validate_decision_output(task, DecisionOutput.model_validate(output, strict=True))


def test_schema_pruning_versions_recipe_and_changes_exact_http_request_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = decision_generation.decision_generation_recipe_digest()
    monkeypatch.setattr(decision_generation, "_SOLVER_SCHEMA_PROJECTION_VERSION", "test-next")
    assert before != decision_generation.decision_generation_recipe_digest()
    task = _task(("choice", "predicate"))
    messages = [ChatMessage(role="user", content=task.model_dump_json())]
    kwargs = {"model_id": "offline-test", "messages": messages, "seed": 17}
    full = _generation_request_sha256(
        LocalEndpointConfig(), schema=_decision_output_schema(), **kwargs
    )
    pruned = _generation_request_sha256(
        LocalEndpointConfig(), schema=_decision_output_schema(task), **kwargs
    )
    assert full != pruned
    assert pruned == _generation_request_sha256(
        LocalEndpointConfig(), schema=_decision_output_schema(task), **kwargs
    )


def test_mixed_task_schema_removes_unneeded_collection_and_relation_definitions() -> None:
    full = _decision_output_schema()
    pruned = _decision_output_schema(_task(("choice", "predicate")))
    assert len(full["$defs"]) == 22
    assert len(pruned["$defs"]) == 10

    def encode(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    assert len(encode(pruned)) < 0.4 * len(encode(full))
