from __future__ import annotations

import json
from typing import Any, cast

import pytest

import foliqant_model.curation.source_projections as projection_module
from foliqant.decisions import (
    ChoiceResult,
    MultiselectQuestion,
    MultiselectResult,
    OrdinalQuestion,
    OrdinalResult,
    PredicateResult,
    validate_decision_output,
)
from foliqant_model.contracts import ChatMessage, DataRecord
from foliqant_model.curation.contracts import ImportedRecord
from foliqant_model.curation.source_projections import (
    PROJECTION_VERSION,
    project_auxiliary,
    projection_priority,
)


def _source_row(
    source_id: str,
    user: object,
    assistant: object,
    *,
    record_id: str | None = None,
    tags: list[str] | None = None,
) -> ImportedRecord:
    identity = record_id or f"{source_id}.record.synthetic"
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id=identity,
            sourceId=source_id,
            language="en",
            groupKeys=[f"{source_id}:group:synthetic"],
            messages=[
                ChatMessage(role="system", content="Pinned source task."),
                ChatMessage(
                    role="user",
                    content=json.dumps(user, sort_keys=True, separators=(",", ":")),
                ),
                ChatMessage(
                    role="assistant",
                    content=json.dumps(assistant, sort_keys=True, separators=(",", ":")),
                ),
            ],
            tags=tags or [source_id],
            origin="teacher" if source_id == "typed-decisions" else "human",
            reviewed=False,
            familyId=f"{source_id}.family.synthetic",
        ),
        originalSplit="train",
        task=(
            "decision"
            if source_id == "typed-decisions"
            else "question-answering"
            if source_id == "tatqa"
            else "classification"
        ),
        originalId="synthetic",
    )


def _teacher_gold(
    kind: str,
    label: str,
    labels: list[str],
    *,
    highest: str | None = None,
) -> dict[str, object]:
    probabilities = {item: 0.1 for item in labels}
    probabilities[highest or label] = 0.7
    result: dict[str, object] = {
        "confidence": 0.6,
        "label": label,
        "probabilities": probabilities,
        "type": kind,
    }
    if kind == "noul":
        result["noul"] = 0.8
    elif kind == "score":
        result["score"] = 1.4
    return result


def _typed_case() -> tuple[dict[str, object], dict[str, object]]:
    questions: dict[str, object] = {
        "action": {
            "criteria": {
                "continue": "Let the agent proceed.",
                "stop": "Halt the agent now.",
            },
            "instructions": "What should happen next?",
            "type": "choice",
        },
        "needs_review": {
            "instructions": "This trace requires human review.",
            "type": "noul",
        },
        "outcome": {
            "criteria": {
                "failure": "The task failed.",
                "success": "The task completed.",
            },
            "instructions": "How did the run turn out?",
            "type": "choice",
        },
        "risk": {
            "criteria": ["Benign", "Routine", "Destructive"],
            "instructions": "How risky was the run?",
            "type": "score",
        },
        "urgency": {
            "criteria": ["Can wait", "Handle soon", "Handle now"],
            "instructions": "How urgent is the trace?",
            "type": "score",
        },
    }
    gold: dict[str, object] = {
        "action": _teacher_gold("choice", "continue", ["continue", "stop"], highest="stop"),
        "needs_review": _teacher_gold("noul", "true", ["false", "true"]),
        "outcome": _teacher_gold("choice", "success", ["failure", "success"]),
        "risk": _teacher_gold("score", "1", ["0", "1", "2"], highest="2"),
        "urgency": _teacher_gold("score", "2", ["0", "1", "2"]),
    }
    return {"questions": questions, "state": {"trace": "read only", "violations": 1}}, gold


def test_typed_projection_keeps_all_questions_and_explicit_labels_without_soft_targets() -> None:
    task, gold = _typed_case()
    row = _source_row(
        "typed-decisions",
        task,
        gold,
        tags=["typed-decisions", "workflow:agent_trace_observability"],
    )
    raw_before = row.model_dump_json()

    projected = project_auxiliary(row)

    assert projected.reason == "projected"
    assert projected.seed is not None
    seed = projected.seed
    assert row.model_dump_json() == raw_before
    assert len(seed.input.questions) == len(seed.oracle.results) == 5
    assert validate_decision_output(seed.input, seed.oracle) == []
    assert [question.type for question in seed.input.questions] == [
        "choice",
        "predicate",
        "choice",
        "ordinal",
        "ordinal",
    ]
    first_result = seed.oracle.results[0]
    risk_result = seed.oracle.results[3]
    assert isinstance(first_result, ChoiceResult) and first_result.answer is not None
    assert first_result.answer.optionId == "continue"
    assert isinstance(risk_result, OrdinalResult) and risk_result.answer is not None
    assert risk_result.answer.levelId == "1"
    assert "probabilities" not in seed.parent.messages[-2].content
    assert "confidence" not in seed.parent.messages[-2].content
    assert "probabilities" not in seed.parent.messages[-1].content
    assert "confidence" not in seed.parent.messages[-1].content
    assert [result.explanation.summary for result in seed.oracle.results] == [
        "The supplied state supports: Let the agent proceed.",
        "The supplied state supports the stated condition.",
        "The supplied state supports: The task completed.",
        "The supplied state meets: Routine",
        "The supplied state meets: Handle now",
    ]
    predicate = seed.input.questions[1]
    assert predicate.criteria == ["This trace requires human review."]
    ordinal = seed.input.questions[3]
    assert isinstance(ordinal, OrdinalQuestion)
    assert [level.description for level in ordinal.levels] == [
        "Benign",
        "Routine",
        "Destructive",
    ]


def test_typed_projection_retains_lineage_workflow_and_versioned_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, gold = _typed_case()
    row = _source_row(
        "typed-decisions",
        task,
        gold,
        tags=["typed-decisions", "workflow:invoice_processing"],
    )
    first = project_auxiliary(row).seed
    assert first is not None
    assert first.parent.familyId == row.record.familyId
    assert first.parent.groupKeys == row.record.groupKeys
    assert first.parent.language == row.record.language
    assert first.parent.origin == "teacher"
    assert first.parent.reviewed is False
    assert first.mode == "annotate" and first.rewriteSourceIds == []
    assert "workflow:invoice_processing" in first.parent.tags
    assert f"original-source-record:{row.record.id}" in first.parent.tags
    assert f"projection-version:{PROJECTION_VERSION}" in first.parent.tags
    assert "projection-task:typed-all-questions-v1" in first.parent.tags

    changed_task = json.loads(json.dumps(task))
    changed_task["state"]["violations"] = 2
    task_seed = project_auxiliary(
        _source_row(
            "typed-decisions",
            changed_task,
            gold,
            record_id=row.record.id,
            tags=["typed-decisions", "workflow:invoice_processing"],
        )
    ).seed
    changed_gold = json.loads(json.dumps(gold))
    changed_gold["action"]["label"] = "stop"
    oracle_seed = project_auxiliary(
        _source_row(
            "typed-decisions",
            task,
            changed_gold,
            record_id=row.record.id,
            tags=["typed-decisions", "workflow:invoice_processing"],
        )
    ).seed
    monkeypatch.setattr(projection_module, "PROJECTION_VERSION", "test-projection-v2")
    version_seed = project_auxiliary(row).seed
    assert task_seed is not None and oracle_seed is not None and version_seed is not None
    assert (
        len(
            {
                first.parent.id,
                task_seed.parent.id,
                oracle_seed.parent.id,
                version_seed.parent.id,
            }
        )
        == 4
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing-question", "missing-gold", "bad-rubric", "bad-workflow"],
)
def test_typed_projection_fails_the_full_case_when_any_part_is_malformed(mutation: str) -> None:
    task, gold = _typed_case()
    tags = ["typed-decisions", "workflow:customer_service"]
    questions = cast(dict[str, Any], task["questions"])
    if mutation == "missing-question":
        del questions["urgency"]
        del gold["urgency"]
    elif mutation == "missing-gold":
        del gold["urgency"]
    elif mutation == "bad-rubric":
        cast(dict[str, Any], questions["risk"])["criteria"] = ["only one"]
    else:
        tags = ["typed-decisions", "workflow:unknown"]
    projected = project_auxiliary(_source_row("typed-decisions", task, gold, tags=tags))
    assert projected.seed is None
    assert projected.reason == "malformed-typed-decisions"


def test_typed_projection_excludes_instead_of_truncating_long_canonical_summary() -> None:
    task, gold = _typed_case()
    questions = cast(dict[str, Any], task["questions"])
    cast(dict[str, Any], questions["action"])["criteria"]["continue"] = "x" * 400
    projected = project_auxiliary(
        _source_row(
            "typed-decisions",
            task,
            gold,
            tags=["typed-decisions", "workflow:customer_service"],
        )
    )
    assert projected.seed is None
    assert projected.reason == "malformed-typed-decisions"


def test_multidogo_projection_uses_fixed_catalog_and_preserves_every_intent() -> None:
    row = _source_row(
        "multidogo-finance",
        {"utterance": "send <redacted> and show balance"},
        {
            "intents": ["transfermoney", "checkbalance"],
            "slotLabels": ["O", "B-account", "O", "O", "O"],
        },
    )
    raw_before = row.model_dump_json()

    projected = project_auxiliary(row)

    assert projected.seed is not None
    seed = projected.seed
    question = seed.input.questions[0]
    result = seed.oracle.results[0]
    assert isinstance(question, MultiselectQuestion)
    assert isinstance(result, MultiselectResult)
    assert len(question.options) == question.maxSelections == 18
    assert question.minSelections == 1
    assert [option.id for option in question.options] == sorted(
        option.id for option in question.options
    )
    assert result.answer is not None
    assert result.answer.optionIds == ["transfermoney", "checkbalance"]
    assert seed.input.state.sources[0].text == "send <redacted> and show balance"
    assert "slotLabels" not in seed.parent.messages[-1].content
    assert row.model_dump_json() == raw_before
    assert validate_decision_output(seed.input, seed.oracle) == []
    assert projection_priority(row) == (0, row.record.id)


def test_projection_priority_is_multi_intent_first_then_record_id() -> None:
    single = _source_row(
        "multidogo-finance",
        {"utterance": "show balance"},
        {"intents": ["checkbalance"], "slotLabels": ["O", "O"]},
        record_id="multidogo-finance.record.a",
    )
    multi_b = _source_row(
        "multidogo-finance",
        {"utterance": "show balance and close account"},
        {
            "intents": ["checkbalance", "closeaccount"],
            "slotLabels": ["O", "O", "O", "O", "O"],
        },
        record_id="multidogo-finance.record.b",
    )
    multi_a = multi_b.model_copy(
        update={"record": multi_b.record.model_copy(update={"id": "multidogo-finance.record.0"})}
    )
    assert sorted([single, multi_b, multi_a], key=projection_priority) == [
        multi_a,
        multi_b,
        single,
    ]


@pytest.mark.parametrize(
    ("assistant", "reason"),
    [
        (
            {"intents": ["invented"], "slotLabels": ["O", "O"]},
            "malformed-multidogo-finance",
        ),
        (
            {"intents": ["checkbalance"], "slotLabels": ["O"]},
            "malformed-multidogo-finance",
        ),
    ],
)
def test_multidogo_projection_rejects_unknown_intents_and_bad_slot_alignment(
    assistant: object, reason: str
) -> None:
    result = project_auxiliary(
        _source_row("multidogo-finance", {"utterance": "show balance"}, assistant)
    )
    assert result.seed is None
    assert result.reason == reason


def _tatqa_row(table: list[list[str]], assistant: object | None = None) -> ImportedRecord:
    return _source_row(
        "tatqa",
        {
            "paragraphs": [{"text": "Irrelevant paragraph."}],
            "question": "An unrelated source question?",
            "table": table,
        },
        assistant if assistant is not None else {"answer": "ignore this gold"},
    )


def test_tatqa_uses_only_table_and_compares_displayed_signed_values() -> None:
    table = [
        ["", "2024", "2023", "Change"],
        ["", "$ in millions", "$ in millions", "%"],
        ["Net income", "(1,200)", "(900)", "33%"],
    ]
    first_row = _tatqa_row(table, {"answer": 300, "derivation": "gold says otherwise"})
    other_gold = _tatqa_row(table, {"malformed": [float("nan")]})

    first = project_auxiliary(first_row)
    second = project_auxiliary(other_gold)

    assert first.seed is not None and second.seed is not None
    assert first.seed.parent.id == second.seed.parent.id
    assert first.seed.parent.messages[-2:] == second.seed.parent.messages[-2:]
    assert first.seed.input.state.sources[0].text == json.dumps(
        table, sort_keys=True, separators=(",", ":")
    )
    result = first.seed.oracle.results[0]
    assert isinstance(result, PredicateResult)
    assert result.answer.value == "false"
    assert result.answerability.status == "answerable"
    assert result.answerability.issues == []
    assert [citation.quote for citation in result.explanation.evidence] == [
        json.dumps(table[0], separators=(",", ":")),
        json.dumps(table[2], separators=(",", ":")),
    ]
    assert validate_decision_output(first.seed.input, first.seed.oracle) == []


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("$ 1,200.50", "$ (900.25)", "true"),
        ("12.5%", "-2%", "true"),
        ("+7", "7", "false"),
        ("(1)", "(2)", "true"),
    ],
)
def test_tatqa_strict_decimal_exact_cases(first: str, second: str, expected: str) -> None:
    projected = project_auxiliary(_tatqa_row([["", "2024", "2023"], ["Value", first, second]]))
    assert projected.seed is not None
    result = projected.seed.oracle.results[0]
    assert isinstance(result, PredicateResult)
    assert result.answer.value == expected


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("1,200(a)", "900"),
        ("NaN", "900"),
        ("1e3", "900"),
        ("$1,000", "10%"),
        ("$10%", "$9%"),
        ("-(1,000)", "900"),
        ("(-1,000)", "900"),
        ("1,00", "90"),
    ],
)
def test_tatqa_rejects_unsafe_or_malformed_displayed_numbers(first: str, second: str) -> None:
    projected = project_auxiliary(_tatqa_row([["", "2024", "2023"], ["Value", first, second]]))
    assert projected.seed is None
    assert projected.reason == "no-eligible-tatqa-comparison"


def test_tatqa_selects_first_eligible_adjacent_period_pair_and_row() -> None:
    table = [
        ["", "2024", "2023", "2022"],
        ["Mixed", "$1", "2%", "3%"],
        ["Eligible later", "10", "9", "8"],
    ]
    projected = project_auxiliary(_tatqa_row(table))
    assert projected.seed is not None
    question = projected.seed.input.questions[0]
    assert "Eligible later" in question.prompt
    assert '"2024"' in question.prompt and '"2023"' in question.prompt


def test_tatqa_accepts_nonconsecutive_years_in_adjacent_columns() -> None:
    projected = project_auxiliary(_tatqa_row([["", "2021", "2018"], ["Revenue", "2", "1"]]))
    assert projected.seed is not None
    result = projected.seed.oracle.results[0]
    assert isinstance(result, PredicateResult)
    assert result.answer.value == "true"


def test_tatqa_requires_year_only_header_cells() -> None:
    projected = project_auxiliary(
        _tatqa_row([["", "dollars in 2021", "2018"], ["Revenue", "2", "1"]])
    )
    assert projected.seed is None
    assert projected.reason == "no-eligible-tatqa-comparison"


def test_tatqa_skips_repeated_unrelated_labels_and_selects_unique_good_row() -> None:
    projected = project_auxiliary(
        _tatqa_row(
            [
                ["", "2024", "2023"],
                ["Repeated", "9", "8"],
                ["Repeated", "7", "6"],
                ["Unique", "2", "1"],
            ]
        )
    )
    assert projected.seed is not None
    assert "Unique" in projected.seed.input.questions[0].prompt


@pytest.mark.parametrize(
    "table",
    [
        [["", "2024", "2023"], ["A", "1"]],
        [["", "2024", "2024"], ["A", "1", "2"]],
    ],
)
def test_tatqa_rejects_malformed_tables_and_duplicate_periods(
    table: list[list[str]],
) -> None:
    projected = project_auxiliary(_tatqa_row(table))
    assert projected.seed is None
    assert projected.reason == "malformed-tatqa"


def test_tatqa_rejects_oversized_exact_citation_rows() -> None:
    projected = project_auxiliary(_tatqa_row([["", "2024", "2023"], ["x" * 4097, "2", "1"]]))
    assert projected.seed is None
    assert projected.reason == "malformed-tatqa"


def test_unsupported_source_has_stable_exclusion_reason() -> None:
    projected = project_auxiliary(_source_row("banking77", {"request": "x"}, {"intent": "x"}))
    assert projected.seed is None
    assert projected.reason == "unsupported-source"
