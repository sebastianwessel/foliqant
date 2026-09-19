"""Offline regression tests for the preregistered task-prefix experiment."""

from __future__ import annotations

import importlib.util
import itertools
import json
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from foliqant_model.contracts.inputs import ChatMessage, DataRecord


def _load_script() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/evaluate_task_prefixes.py"
    spec = importlib.util.spec_from_file_location("foliqant_prefix_experiment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPERIMENT = _load_script()


def _user(source: str, marker: str) -> str:
    if source == "banking77":
        value: object = {
            "labels": ["cash_withdrawal", "card_arrival"],
            "request": f"Banking request {marker}",
        }
    elif source == "wanli":
        value = {
            "claim": f"Claim {marker}",
            "evidence": f"Evidence {marker}",
            "options": [
                {"id": "supported", "description": "Evidence establishes the claim"},
                {"id": "contradicted", "description": "Evidence establishes the opposite"},
            ],
        }
    else:
        value = {
            "paragraphs": [{"order": 1, "text": f"Paragraph {marker}"}],
            "question": f"Question {marker}?",
            "table": [["Item", marker]],
        }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _answer(source: str, variant: int = 0) -> str:
    if source == "banking77":
        value: object = {"intent": ("cash_withdrawal", "card_arrival")[variant % 2]}
    elif source == "wanli":
        value = {"label": ("supported", "contradicted")[variant % 2]}
    else:
        value = {
            "answer": ["EUR +1,200."],
            "answerFrom": "table-text",
            "answerType": "span",
            "derivation": "",
            "scale": "million",
        }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(
    source: str,
    family: str,
    record_id: str,
    *,
    answer_variant: int = 0,
    history: list[ChatMessage] | None = None,
) -> DataRecord:
    return DataRecord(
        schemaVersion=1,
        id=record_id,
        sourceId=source,
        language="en",
        groupKeys=[family],
        familyId=family,
        messages=[
            ChatMessage(role="system", content=f"Original system bytes for {source}."),
            *(history or []),
            ChatMessage(role="user", content=_user(source, record_id)),
            ChatMessage(role="assistant", content=_answer(source, answer_variant)),
        ],
        tags=[source],
        origin="human",
        reviewed=False,
    )


def test_requests_do_not_depend_on_the_reference_answer() -> None:
    first = _record("banking77", "family-a", "banking-a", answer_variant=0)
    changed_gold = _record("banking77", "family-a", "banking-a", answer_variant=1)

    for arm in EXPERIMENT.ARMS:
        assert EXPERIMENT.messages_for(first, arm) == EXPERIMENT.messages_for(changed_gold, arm)
    assert EXPERIMENT.schema_for(first) == EXPERIMENT.schema_for(changed_gold)
    assert first.messages[-1] != changed_gold.messages[-1]


def test_wrappers_preserve_original_messages_and_only_wrap_the_final_user() -> None:
    history = [
        ChatMessage(role="user", content="Earlier user bytes."),
        ChatMessage(role="assistant", content="Earlier assistant bytes."),
    ]
    record = _record("wanli", "family-a", "wanli-a", history=history)
    prompt = record.messages[:-1]

    assert EXPERIMENT.messages_for(record, "baseline") == prompt
    for arm in EXPERIMENT.ARMS[1:]:
        wrapped = EXPERIMENT.messages_for(record, arm)
        assert wrapped[:-1] == prompt[:-1]
        assert wrapped[-1].role == "user"
        assert wrapped[-1].content.endswith(prompt[-1].content)
        assert wrapped[-1].content == EXPERIMENT.TEMPLATES[arm].format(
            task=EXPERIMENT.TASKS[record.sourceId][0],
            directive=EXPERIMENT.TASKS[record.sourceId][1],
            input=prompt[-1].content,
        )


def _write_candidates(path: Path, *, reverse_answers: bool) -> None:
    rows = []
    for source_index, source in enumerate(EXPERIMENT.SOURCES):
        for family_index in range(9):
            family = f"{source}-family-{family_index:02}"
            # Two rows in one family prove representative choice is by ID, not label.
            ids = (
                [f"{source}-{family_index:02}-a", f"{source}-{family_index:02}-z"]
                if family_index == 0
                else [f"{source}-{family_index:02}-a"]
            )
            for row_index, record_id in enumerate(ids):
                variant = (source_index + family_index + row_index) % 2
                if reverse_answers:
                    variant = 1 - variant
                rows.append(_record(source, family, record_id, answer_variant=variant))
    path.write_text(
        "".join(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_validation_family_selection_is_label_independent(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jsonl"
    changed_gold_path = tmp_path / "changed-gold.jsonl"
    _write_candidates(first_path, reverse_answers=False)
    _write_candidates(changed_gold_path, reverse_answers=True)

    first = EXPERIMENT.select_records(first_path)
    changed_gold = EXPERIMENT.select_records(changed_gold_path)

    assert [row.id for row in first] == [row.id for row in changed_gold]
    assert [row.familyId for row in first] == [row.familyId for row in changed_gold]
    assert len(first) == 24
    assert Counter(row.sourceId for row in first) == Counter(
        {source: 8 for source in EXPERIMENT.SOURCES}
    )
    assert len({row.familyId for row in first}) == 24
    assert all(not row.id.endswith("-z") for row in first)


def test_schedule_uses_every_arm_order_exactly_four_times() -> None:
    records = [
        _record(source, f"{source}-family-{index}", f"{source}-{index}")
        for index in range(8)
        for source in EXPERIMENT.SOURCES
    ]
    sequence = EXPERIMENT.schedule(records)
    blocks = [sequence[index : index + 3] for index in range(0, len(sequence), 3)]
    orders = Counter(tuple(arm for _record_value, arm in block) for block in blocks)

    assert len(sequence) == 72
    assert all(len({record.id for record, _arm in block}) == 1 for block in blocks)
    assert orders == Counter({order: 4 for order in itertools.permutations(EXPERIMENT.ARMS)})


def test_paired_stats_returns_registered_probability_for_six_unopposed_wins() -> None:
    result = EXPERIMENT.paired_stats(6, 0)

    assert result["oneSidedP"] == pytest.approx(0.015625)
    assert result["wins"] == 6
    assert result["losses"] == 0
    assert result["ties"] == 18


def _tatqa_record() -> DataRecord:
    return _record("tatqa", "tatqa-family", "tatqa-record")


def test_tatqa_task_answer_normalizes_only_whitespace_and_one_terminal_period() -> None:
    record = _tatqa_record()
    output = json.loads(record.messages[-1].content)
    output["answer"] = ["  EUR   +1,200  "]

    scores = EXPERIMENT.score(record, output)
    assert scores == {"valid": True, "exact": False, "taskAnswer": True, "annotations": True}


@pytest.mark.parametrize(
    "answer",
    ["EUR +1,200.", ["USD +1,200."], ["EUR -1,200."], ["EUR +1200."]],
)
def test_tatqa_task_answer_preserves_list_shape_units_signs_and_commas(answer: object) -> None:
    record = _tatqa_record()
    output = json.loads(record.messages[-1].content)
    output["answer"] = answer

    assert EXPERIMENT.score(record, output)["taskAnswer"] is False


def test_tatqa_task_answer_preserves_scale() -> None:
    record = _tatqa_record()
    output = json.loads(record.messages[-1].content)
    output["scale"] = "percent"

    assert EXPERIMENT.score(record, output)["taskAnswer"] is False


def _outcomes(
    arm_scores: dict[str, dict[str, dict[str, bool]]],
) -> list[dict[str, Any]]:
    outcomes = []
    for source in EXPERIMENT.SOURCES:
        for index in range(8):
            record_id = f"{source}-{index}"
            for arm in EXPERIMENT.ARMS:
                scores = arm_scores.get(arm, {}).get(
                    record_id,
                    {"valid": True, "exact": False, "taskAnswer": False, "annotations": False},
                )
                outcomes.append(
                    {
                        "recordId": record_id,
                        "source": source,
                        "arm": arm,
                        "scores": scores,
                        "wallSeconds": 1.0,
                        "inputCharacters": 100,
                    }
                )
    return outcomes


def _scores(*, valid: bool, exact: bool) -> dict[str, bool]:
    return {
        "valid": valid,
        "exact": exact,
        "taskAnswer": exact,
        "annotations": exact,
    }


def test_summary_does_not_nominate_an_arm_with_a_per_source_regression() -> None:
    baseline = {"banking77-0": _scores(valid=True, exact=True)}
    treatment = {
        **{f"wanli-{index}": _scores(valid=True, exact=True) for index in range(8)},
        "banking77-0": _scores(valid=True, exact=False),
    }
    result = EXPERIMENT.summarize(
        _outcomes(
            {
                "baseline": baseline,
                "task-key-user-v1": treatment,
            }
        )
    )

    comparison = result["comparisons"]["task-key-user-v1"]
    assert comparison["exact"]["oneSidedP"] <= 0.025
    assert comparison["nominateForConfirmation"] is False
    assert result["decision"] == "retain-baseline-no-demonstrated-benefit"


def test_summary_does_not_nominate_an_arm_with_lower_validity() -> None:
    wins = {f"wanli-{index}": _scores(valid=True, exact=True) for index in range(6)}
    baseline = {"banking77-0": _scores(valid=True, exact=False)}
    treatment = {**wins, "banking77-0": _scores(valid=False, exact=False)}
    result = EXPERIMENT.summarize(
        _outcomes(
            {
                "baseline": baseline,
                "task-key-user-v1": treatment,
            }
        )
    )

    comparison = result["comparisons"]["task-key-user-v1"]
    assert comparison["exact"]["oneSidedP"] == pytest.approx(0.015625)
    assert comparison["nominateForConfirmation"] is False
    assert result["decision"] == "retain-baseline-no-demonstrated-benefit"


def test_summary_does_not_trade_a_new_source_invalidity_for_another_source() -> None:
    baseline = {"wanli-7": _scores(valid=False, exact=False)}
    treatment = {
        **{f"wanli-{index}": _scores(valid=True, exact=True) for index in range(6)},
        "wanli-7": _scores(valid=True, exact=False),
        "banking77-0": _scores(valid=False, exact=False),
    }
    result = EXPERIMENT.summarize(
        _outcomes(
            {
                "baseline": baseline,
                "task-key-user-v1": treatment,
            }
        )
    )

    comparison = result["comparisons"]["task-key-user-v1"]
    assert comparison["exact"]["oneSidedP"] == pytest.approx(0.015625)
    assert result["arms"]["task-key-user-v1"]["valid"] == result["arms"]["baseline"]["valid"]
    assert (
        result["arms"]["task-key-user-v1"]["perSource"]["banking77"]["valid"]
        < (result["arms"]["baseline"]["perSource"]["banking77"]["valid"])
    )
    assert comparison["nominateForConfirmation"] is False
    assert result["decision"] == "retain-baseline-no-demonstrated-benefit"
