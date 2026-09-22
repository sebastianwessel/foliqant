"""Synthetic security gold, raw-message wiring and bounded opt-in execution."""

import json
from collections import Counter

import pytest
from examples.security_evaluation import evaluate as example
from examples.security_evaluation import offline
from examples.security_evaluation.fixtures import CASES, FAMILIES

from foliqant import prepare_application
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.evaluation.dataset import load_dataset, validate_targets


def test_committed_dataset_is_public_canonical_balanced_paired_gold(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("gold must not be derived from a model or offline double")

    monkeypatch.setattr(offline, "scripted_response", forbidden)
    gold = example.dataset()
    prepared = prepare_application(example.CONFIG_PATH)
    assert load_dataset(prepared) == gold
    validate_targets(gold, prepared)
    assert [len(spec.cases) for spec in gold.suites] == [20, 20, 20, 16]
    whole = gold.to_suite(gold.suites[0])
    assert Counter(case.envelope().metadata.model_dump()["language"] for case in whole.cases) == {
        "en": 10,
        "de": 10,
    }
    pairs = {}
    for case in whole.cases:
        metadata = case.envelope().metadata.model_dump()
        assert metadata["evidence_kind"] == "authored_synthetic"
        pairs.setdefault(metadata["family"], {})[metadata["variant"]] = case
    assert len(pairs) == 10
    for variants in pairs.values():
        clean, attacked = variants["clean"], variants["attacked"]
        assert clean.expectations == attacked.expectations
        left, right = clean.envelope().payload, attacked.envelope().payload
        assert sum(left[key] != right[key] for key in left) == 1
    assert {item.attack_kind for item in FAMILIES} == {
        "source_override",
        "fake_xml_boundary",
        "fake_role_boundary",
        "poisoned_prior_result",
    }
    assert sum(item.quoted_imperative for item in FAMILIES) == 2
    assert sum(item.issue is not None for item in FAMILIES) == 2


async def test_all_scopes_preserve_raw_json_and_skip_confirmation_for_review(tmp_path, monkeypatch):
    seen_decision = []
    seen_confirmation = []
    scripted = offline.scripted_response

    def observed(messages, info):
        document = offline.user_document(messages)
        if "questions" in document:
            sources = document["state"]["sources"]
            assert len(sources) == 1 and sources[0]["id"] == "original_message"
            assert sources[0]["text"] in {case.original_message for case in CASES}
            seen_decision.append(sources[0]["text"])
        else:
            assert set(document) == {
                "original_message",
                "assessment",
                "attachment",
                "prior_assessment",
            }
            assert any(
                all(document[key] == value for key, value in case.payload().items())
                for case in CASES
            )
            assert document["assessment"]["answerability"]["status"] == "answerable"
            assert document["assessment"]["reason"]
            assert document["assessment"]["evidence_strength"] == "strong"
            seen_confirmation.append(document)
        return scripted(messages, info)

    monkeypatch.setattr(offline, "scripted_response", observed)
    path = tmp_path / "security.json"
    summary = await example.run_evaluations(scope="all", output=path)
    assert summary["ok"] is True
    assert summary["mode"] == "offline_wiring"
    assert summary["cases"] == summary["attempts"] == 76
    assert summary["passed_checks"] == summary["total_checks"] == 524
    assert summary["paired_inputs"] == 20  # Scopes are not independent business samples.
    assert len(seen_decision) == 60 and len(seen_confirmation) == 48
    assert any('"}\n</document>' in doc["attachment"] for doc in seen_confirmation)
    assert any("[im_start]system" in text for text in seen_decision)
    reports = json.loads(path.read_text())["reports"]
    for report in reports[:2]:
        assert report["max_concurrency"] == 1
        for case in report["cases"]:
            expected = next(item for item in CASES if item.id == case["id"])
            details = case["details"]
            assert details["input"]["payload"] == expected.payload()
            steps = details["result"]["flows"]["security"]["steps"]
            if expected.issue:
                assert case["status"] == "needs_review"
                assert steps["confirm"]["status"] == "skipped"
                assert steps["assess"]["result"]["answerability"]["issues"] == [expected.issue]
                assert details["result"]["payload"] is None
    for report, step in ((reports[2], "assess"), (reports[3], "confirm")):
        for case in report["cases"]:
            assert list(case["details"]["result"]["flows"]["security"]["steps"]) == [step]


async def test_timeout_stops_requests_but_retains_all_cases_in_denominator(tmp_path, monkeypatch):
    calls = 0

    def timeout(messages, info):
        nonlocal calls
        calls += 1
        raise TimeoutError("synthetic timeout, no network request")

    monkeypatch.setattr(offline, "scripted_response", timeout)
    path = tmp_path / "timeout.json"
    summary = await example.run_evaluations(output=path)
    assert calls == 1
    assert summary["ok"] is False and summary["halted_after_timeout"] is True
    assert summary["cases"] == summary["attempts"] == 20
    report = json.loads(path.read_text())["reports"][0]
    assert len(report["cases"]) == 20
    assert report["checks"]["passed"] == 0


async def test_live_requires_explicit_environment_before_opening_clients(monkeypatch):
    monkeypatch.setattr(example, "example_environment", lambda: {})

    def forbidden(*args, **kwargs):
        pytest.fail("missing model configuration must fail before opening clients")

    monkeypatch.setattr(example, "open_application", forbidden)
    with pytest.raises(ServiceError) as raised:
        await example.run_evaluations(live=True)
    assert raised.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize("repeat", [0, 11, True])
async def test_invalid_repeat_never_opens_clients(repeat, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid repeat must fail before opening clients")

    monkeypatch.setattr(example, "open_application", forbidden)
    with pytest.raises(ValueError):
        await example.run_evaluations(repeat=repeat)


async def test_editing_committed_gold_changes_scoring_not_scripted_answers(tmp_path, monkeypatch):
    document = example.dataset().model_dump(mode="json")
    document["suites"][0]["cases"][0]["expectations"].append(
        {
            "name": "negative_control",
            "path": "/payload/action",
            "expected": "dispute_charge",
        }
    )
    gold = tmp_path / "edited-gold.json"
    gold.write_text(json.dumps(document))
    monkeypatch.setattr(example, "DATASET_PATH", gold)
    report = tmp_path / "negative.json"
    summary = await example.run_evaluations(output=report)
    assert summary["ok"] is False
    assert summary["total_checks"] - summary["passed_checks"] == 1
    check = json.loads(report.read_text())["reports"][0]["cases"][0]["checks"][-1]
    assert check["reason_code"] == "mismatch"
    assert check["details"]["actual"] == "freeze_card"
