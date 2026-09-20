"""Adequacy supervision must assess an explicit task, including required unknowns."""

from foliqant_model.curation.decision_adequacy_cases import build_adequacy_case
from foliqant_model.curation.decision_contracts import validate_decision_output


def test_all_adequacy_targets_have_explicit_tasks_and_grounded_assessment() -> None:
    for scenario, expected in (
        ("adequacy-complete", "true"),
        ("adequacy-omitted-request", "false"),
        ("adequacy-unsupported-evidence", "false"),
        ("adequacy-correct-unknown", "true"),
    ):
        inputs: set[str] = set()
        for variant in range(4):
            task, output = build_adequacy_case(scenario, variant)
            assert validate_decision_output(task, output) == []
            result = output.results[0]
            assert result.type == "predicate" and result.answer.value == expected
            assert result.answerability.status == "answerable"
            assert {source.id for source in task.state.sources} == {
                "task-contract",
                "original-state",
                "proposed-answer",
            }
            inputs.add(task.model_dump_json())
        assert len(inputs) == 4


def test_correct_unknown_preserves_the_known_request_instead_of_omitting_it() -> None:
    task, result = build_adequacy_case("adequacy-correct-unknown", 0)
    sources = {source.id: source.text for source in task.state.sources}
    assert "refund" in sources["proposed-answer"]
    assert "unknown" in sources["proposed-answer"]
    assert "transaction reference" in sources["proposed-answer"]
    assert "not perform" in sources["task-contract"]
    assert result.results[0].answer.value == "true"


def test_unknown_assessment_does_not_require_missing_execution_details() -> None:
    task, output = build_adequacy_case("adequacy-complete", 0)
    assert "actionable" not in task.state.sources[0].text
    assert output.results[0].answer.value == "true"
    assert output.results[0].explanation.missingFacts == []


def test_adequacy_variants_change_the_task_not_just_dates_or_formatting() -> None:
    tasks = [build_adequacy_case("adequacy-complete", i)[0] for i in range(4)]
    contracts = [task.state.sources[0].text for task in tasks]
    assert "requested action" in contracts[0]
    assert "revenue and expense" in contracts[1]
    assert "rule" in contracts[2]
    assert "annual total" in contracts[3]
