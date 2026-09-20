"""Focused tests for native typed-decision generation boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord, GenerationProvenance
from foliqant_model.curation import decision_generation, generation
from foliqant_model.curation.contracts import (
    CandidateJob,
    CurationConfig,
    GenerationSettings,
)
from foliqant_model.curation.decision_contracts import DecisionInput, DecisionOutput, DecisionSource
from foliqant_model.curation.decision_generation import (
    canonical_decision_output,
    generate_decision_candidate,
    validate_decision_rewrite,
)
from foliqant_model.curation.decision_seeds import DecisionSeed
from foliqant_model.curation.endpoint import EndpointModelIdentity, GenerationResponse
from foliqant_model.errors import ModelError


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _task() -> DecisionInput:
    return DecisionInput.model_validate(
        {
            "schemaVersion": 1,
            "state": {
                "sources": [
                    {
                        "id": "message-1",
                        "kind": "message",
                        "text": 'The transfer is EUR 40. "Please send a receipt."',
                    }
                ]
            },
            "questions": [
                {
                    "id": "receipt-requested",
                    "type": "predicate",
                    "prompt": "Is a receipt requested?",
                    "criteria": ["Use an explicit request."],
                    "allowedSourceIds": ["message-1"],
                }
            ],
        },
        strict=True,
    )


def _output(*, value: str = "true", summary: str = "The request is explicit.") -> DecisionOutput:
    return DecisionOutput.model_validate(
        {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "receipt-requested",
                    "type": "predicate",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {"value": value},
                    "explanation": {
                        "summary": summary,
                        "evidence": [{"sourceId": "message-1", "quote": "Please send a receipt."}],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ],
        },
        strict=True,
    )


def _seed(*, mode: str = "annotate") -> DecisionSeed:
    task = _task()
    oracle = _output(summary="PRIVATE ORACLE EXPLANATION")
    family = "decision-family-one"
    parent = DataRecord(
        schemaVersion=1,
        id="decision-seed-one",
        sourceId="foliqant-decision-seeds",
        language="en",
        groupKeys=[family],
        messages=[
            ChatMessage(role="system", content="Answer every typed question as JSON."),
            ChatMessage(role="user", content=_json(task.model_dump(mode="json"))),
            ChatMessage(role="assistant", content=_json(oracle.model_dump(mode="json"))),
        ],
        tags=["native-decision"],
        origin="synthetic",
        reviewed=False,
        familyId=family,
    )
    return DecisionSeed(
        parent=parent,
        scenario="clear-request",
        mode=mode,  # type: ignore[arg-type]
        rewriteSourceIds=["message-1"] if mode == "rewrite" else [],
    )


def _config(*, attempts: int = 1) -> CurationConfig:
    return CurationConfig(
        generation=GenerationSettings(
            maxCandidates=4,
            maxAttempts=attempts,
            languages=["en"],
            scenarioFamilies=4,
            maxInputCharacters=16_000,
        )
    )


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="local-test-model",
        modelType="llm",
        publisher="local",
        architecture="test",
        format="gguf",
        quantization="q4",
        sizeBytes=1024,
        maxContextLength=8192,
        metadataSha256="a" * 64,
        immutableRevision=None,
    )


def _job(seed: DecisionSeed, *, split: str = "train") -> CandidateJob:
    assert seed.parent.familyId is not None
    return CandidateJob(
        jobId="b" * 64,
        parentRecordId=seed.parent.id,
        familyId=seed.parent.familyId,
        split=split,  # type: ignore[arg-type]
        language="en",
        purpose="decision-training",
        operation="native-decision",
    )


def _response(output: dict[str, object], *, marker: str) -> GenerationResponse:
    return GenerationResponse(
        model=_identity(),
        output=output,
        finishReason="stop",
        requestSha256=canonical_digest({"request": marker}),
        schemaSha256="c" * 64,
        rawResponseSha256=canonical_digest({"response": marker}),
        elapsedSeconds=0.1,
    )


def test_annotate_blind_solves_without_exposing_oracle_and_keeps_exact_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    observed: list[list[ChatMessage]] = []
    generated = _output(summary="The source directly states the request.")

    def fake_cached(*_args: object, **kwargs: object) -> tuple[str, GenerationResponse]:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        observed.append(messages)
        return "d" * 64, _response(generated.model_dump(mode="json"), marker="solve")

    monkeypatch.setattr(decision_generation, "_cached_generation", fake_cached)
    outcome = generate_decision_candidate(
        _config(),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "accepted"
    assert outcome.record is not None
    assert len(observed) == 1
    assert observed[0][0] == seed.parent.messages[0]
    request_text = "\n".join(message.content for message in observed[0])
    assert "PRIVATE ORACLE EXPLANATION" not in request_text
    assert seed.scenario not in request_text
    assert outcome.record == seed.parent
    assert "The source directly states the request." not in outcome.record.messages[-1].content
    assert outcome.record.origin == "synthetic" and not outcome.record.reviewed
    assert outcome.record.sourceId == seed.parent.sourceId
    assert outcome.record.familyId == seed.parent.familyId
    assert outcome.record.messages[-2].content == seed.parent.messages[-2].content
    assert outcome.record.generation is None


def test_rewrite_preserves_questions_and_uses_rewritten_state_for_blind_solve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    rewritten = 'EUR 40 is the transfer amount. "Please send a receipt."'
    calls = 0

    def fake_cached(*_args: object, **kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        if calls == 1:
            assert "PRIVATE ORACLE EXPLANATION" not in messages[-1].content
            assert seed.scenario not in messages[-1].content
            schema = kwargs["schema"]
            assert isinstance(schema, dict)
            assert "prefixItems" not in _json(schema)
            payload = {"sources": [{"id": "message-1", "kind": "message", "text": rewritten}]}
            return "1" * 64, _response(payload, marker="rewrite")
        assert "PRIVATE ORACLE EXPLANATION" not in messages[-1].content
        assert seed.scenario not in messages[-1].content
        assert json.loads(messages[-1].content)["state"]["sources"][0]["text"] == rewritten
        return "2" * 64, _response(
            _output(summary="The rewritten source contains the request.").model_dump(mode="json"),
            marker="solve",
        )

    monkeypatch.setattr(decision_generation, "_cached_generation", fake_cached)
    outcome = generate_decision_candidate(
        _config(),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "accepted" and outcome.record is not None
    published = json.loads(outcome.record.messages[-2].content)
    assert published["state"]["sources"][0]["text"] == rewritten
    assert published["questions"] == _task().model_dump(mode="json")["questions"]
    target = DecisionOutput.model_validate_json(outcome.record.messages[-1].content)
    assert target.results[0].explanation.summary == "PRIVATE ORACLE EXPLANATION"
    assert "The rewritten source contains the request." not in outcome.record.messages[-1].content
    assert calls == 2


def test_semantic_mismatch_is_quarantined_even_when_output_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    wrong = _output(value="false")
    monkeypatch.setattr(
        decision_generation,
        "_cached_generation",
        lambda *_args, **_kwargs: (
            "d" * 64,
            _response(wrong.model_dump(mode="json"), marker="wrong"),
        ),
    )

    outcome = generate_decision_candidate(
        _config(),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "quarantined"
    assert outcome.reason == "solver-semantic-mismatch"
    assert outcome.record is None


def test_rewrite_currency_drift_is_rejected_before_solver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    calls = 0

    def fake_cached(*_args: object, **_kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        payload = {
            "sources": [
                {
                    "id": "message-1",
                    "kind": "message",
                    "text": 'The transfer is USD 40. "Please send a receipt."',
                }
            ]
        }
        return "d" * 64, _response(payload, marker="rewrite")

    monkeypatch.setattr(decision_generation, "_cached_generation", fake_cached)
    outcome = generate_decision_candidate(
        _config(),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "quarantined"
    assert outcome.reason == "rewrite-currencies-changed"
    assert calls == 1


def test_unchanged_rewrite_is_quarantined_without_spending_a_solver_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    calls = 0

    def unchanged(*_args: object, **_kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        return "d" * 64, _response(seed.input.state.model_dump(mode="json"), marker="unchanged")

    monkeypatch.setattr(decision_generation, "_cached_generation", unchanged)
    outcome = generate_decision_candidate(
        _config(attempts=2), identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path
    )
    assert calls == 2  # Only the two bounded rewrite attempts; no solver calls.
    assert outcome.status == "quarantined"
    assert outcome.reason == "rewrite-no-wording-change"
    assert outcome.record is None


def test_replay_keeps_original_seeds_but_rejects_unchanged_generated_derivatives() -> None:
    from foliqant_model.curation.decision_runner import _validate_record

    seed = _seed(mode="rewrite")
    _validate_record(seed, seed.parent)
    derivative = seed.parent.model_copy(deep=True)
    derivative.generation = GenerationProvenance(
        provider="openai-compatible",
        modelId="test",
        modelIdentitySha256="a" * 64,
        promptSha256="b" * 64,
        parametersSha256="c" * 64,
        requestSha256="d" * 64,
        parentRecordIds=[seed.parent.id],
    )
    with pytest.raises(ModelError, match="rewrite validation"):
        _validate_record(seed, derivative)


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Received on 2026-04-28.", "Received on April 28, 2026."),
        ("Eingegangen am 2026-04-28.", "Eingegangen am 28. April 2026."),
        ("The amount is EUR 40.", "The amount is €40."),
    ],
)
def test_typed_fact_guard_accepts_equivalent_date_and_money_formatting(
    original: str, rewritten: str
) -> None:
    assert decision_generation._source_text_problem(original, rewritten) is None


@pytest.mark.parametrize(
    ("rewritten", "reason"),
    [
        ("Received on April 29, 2026 for account ending 4000.", "rewrite-dates-changed"),
        ("Received on April 28, 2026 for account ending 4001.", "rewrite-numbers-changed"),
        (
            "Received on April 28, 2026 for account ending 4000; amount USD 40.",
            "rewrite-currencies-changed",
        ),
    ],
)
def test_typed_fact_guard_rejects_changed_values(rewritten: str, reason: str) -> None:
    original = "Received on 2026-04-28 for account ending 4000; amount EUR 40."
    assert decision_generation._source_text_problem(original, rewritten) == reason


@pytest.mark.parametrize(
    ("original", "rewritten", "reason"),
    [
        (
            "The threshold is at least 5 percent.",
            "The threshold is less than 5 percent.",
            "rewrite-comparisons-changed",
        ),
        ("The deadline is 5 days.", "The deadline is 5 months.", "rewrite-units-changed"),
        (
            "Account AB-12 is unavailable.",
            "Account AC-12 is available.",
            "rewrite-identifiers-changed",
        ),
        ("The ledger is unavailable.", "The ledger is available.", "rewrite-negations-changed"),
    ],
)
def test_guard_rejects_clear_relation_unit_identifier_and_negation_drift(
    original: str, rewritten: str, reason: str
) -> None:
    assert decision_generation._source_text_problem(original, rewritten) == reason


@pytest.mark.parametrize(
    ("original", "rewritten", "reason"),
    [
        (
            "EUR 10 and USD 20",
            "EUR 20 and USD 10",
            "rewrite-money-associations-changed",
        ),
        ("EUR 1,000", "EUR 1.000", "rewrite-numbers-changed"),
        ("USD 10", "$10", "rewrite-currencies-changed"),
        ("EUR -10", "EUR 10", "rewrite-numbers-changed"),
    ],
)
def test_money_guard_preserves_pairs_ambiguous_formatting_and_signs(
    original: str, rewritten: str, reason: str
) -> None:
    assert decision_generation._source_text_problem(original, rewritten) == reason


def test_guard_accepts_supported_unit_and_negation_paraphrases() -> None:
    assert (
        decision_generation._source_text_problem(
            "The delay is not more than 5 days.", "The delay is not more than 5 Tage."
        )
        is None
    )


def test_month_unit_guard_accepts_monthly_but_rejects_other_cadences() -> None:
    for rate in ("per month", "per  month", "per\nmonth"):
        assert (
            decision_generation._source_text_problem(
                f"The charge is EUR 20 {rate}.", "The monthly charge is EUR 20."
            )
            is None
        )
    for changed in ("The daily charge is EUR 20.", "The annual charge is EUR 20."):
        assert (
            decision_generation._source_text_problem("The monthly charge is EUR 20.", changed)
            == "rewrite-units-changed"
        )
    assert (
        decision_generation._source_text_problem(
            "Review the case in one month.", "Perform a monthly review of the case."
        )
        == "rewrite-units-changed"
    )
    assert (
        decision_generation._source_text_problem(
            "The pauper month example is fictional.", "The monthly example is fictional."
        )
        == "rewrite-units-changed"
    )


def test_rewrite_prompt_requires_a_real_change_only_in_selected_sources() -> None:
    messages = decision_generation._rewrite_messages(_task(), "en", ["message-1"])
    assert "genuinely different wording" in messages[0].content
    assert "do not add filler" in messages[0].content
    assert "Copy every other source text byte-for-byte" in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["rewriteSourceIds"] == ["message-1"]


def test_rewrite_metadata_must_be_copied_exactly() -> None:
    task = _task().model_copy(deep=True)
    task.state.sources.append(
        DecisionSource(id="metadata", kind="metadata", text="Case 2026-04-28")
    )
    payload = {
        "sources": [source.model_dump(mode="json") for source in task.state.sources[:-1]]
        + [{"id": "metadata", "kind": "metadata", "text": "Case April 28, 2026"}]
    }
    rewritten, reason = decision_generation._apply_rewrite(
        task,
        payload,
        rewrite_source_ids=["message-1"],
        max_characters=16_000,
    )
    assert rewritten is None
    assert reason == "rewrite-frozen-source-changed"


def test_canonical_output_replaces_solver_prose_and_remaps_rewritten_citations() -> None:
    seed = _seed(mode="rewrite")
    candidate_task = _task().model_copy(deep=True)
    candidate_task.state.sources[0].text = 'EUR 40 is the transfer amount. "Please send a receipt."'
    canonical = canonical_decision_output(seed, candidate_task)
    assert canonical.results[0].explanation.summary == "PRIVATE ORACLE EXPLANATION"
    assert canonical.results[0].explanation.evidence[0].quote == "Please send a receipt."


def test_canonical_output_fails_closed_when_support_cannot_be_remapped() -> None:
    seed = _seed(mode="rewrite")
    candidate_task = _task().model_copy(deep=True)
    candidate_task.state.sources[0].text = "x" * 4097
    with pytest.raises(ModelError, match="canonical support"):
        canonical_decision_output(seed, candidate_task)


def test_persisted_rewrite_revalidation_rejects_changed_uncited_fact() -> None:
    candidate_task = _task().model_copy(deep=True)
    candidate_task.state.sources[0].text = 'The transfer is EUR 41. "Please send a receipt."'
    assert (
        validate_decision_rewrite(_task(), candidate_task, rewrite_source_ids=["message-1"])
        == "rewrite-numbers-changed"
    )


def test_rewrite_source_selection_freezes_unselected_contract_sources() -> None:
    original = _task().model_copy(deep=True)
    original.state.sources.append(
        DecisionSource(id="task-contract", kind="policy", text="Return both required fields.")
    )
    candidate = original.model_copy(deep=True)
    candidate.state.sources[0].text = 'EUR 40 is the transfer amount. "Please send a receipt."'
    assert validate_decision_rewrite(original, candidate, rewrite_source_ids=["message-1"]) is None
    candidate.state.sources[1].text = "Return all required fields."
    assert (
        validate_decision_rewrite(original, candidate, rewrite_source_ids=["message-1"])
        == "rewrite-frozen-source-changed"
    )


def test_canonical_replay_rejects_a_changed_frozen_source() -> None:
    base = _seed(mode="rewrite")
    original = base.input.model_copy(deep=True)
    original.state.sources.append(
        DecisionSource(id="task-contract", kind="policy", text="Return both required fields.")
    )
    parent = base.parent.model_copy(deep=True)
    parent.messages[-2].content = _json(original.model_dump(mode="json"))
    seed = DecisionSeed(
        parent=parent,
        scenario=base.scenario,
        mode="rewrite",
        rewriteSourceIds=["message-1"],
    )
    candidate = original.model_copy(deep=True)
    candidate.state.sources[1].text = "Return all required fields."
    with pytest.raises(ModelError, match="canonical support"):
        canonical_decision_output(seed, candidate)


def test_decision_seed_rejects_invalid_rewrite_source_controls() -> None:
    rewrite_seed = _seed(mode="rewrite")
    with pytest.raises(ValueError, match="unique"):
        DecisionSeed(
            parent=rewrite_seed.parent,
            scenario=rewrite_seed.scenario,
            mode="rewrite",
            rewriteSourceIds=["message-1", "message-1"],
        )
    with pytest.raises(ValueError, match="unknown"):
        DecisionSeed(
            parent=rewrite_seed.parent,
            scenario=rewrite_seed.scenario,
            mode="rewrite",
            rewriteSourceIds=["unknown"],
        )
    with pytest.raises(ValueError, match="cannot rewrite"):
        DecisionSeed(
            parent=rewrite_seed.parent,
            scenario=rewrite_seed.scenario,
            mode="annotate",
            rewriteSourceIds=["message-1"],
        )
    metadata_task = rewrite_seed.input.model_copy(deep=True)
    metadata_task.state.sources.append(
        DecisionSource(id="case-id", kind="metadata", text="case-17")
    )
    metadata_parent = rewrite_seed.parent.model_copy(deep=True)
    metadata_parent.messages[-2].content = _json(metadata_task.model_dump(mode="json"))
    with pytest.raises(ValueError, match="metadata"):
        DecisionSeed(
            parent=metadata_parent,
            scenario=rewrite_seed.scenario,
            mode="rewrite",
            rewriteSourceIds=["case-id"],
        )


def test_annotate_acceptance_returns_exact_parent_without_generation_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="annotate")
    generated = _output(summary="Unchecked generated rationale.")
    monkeypatch.setattr(
        decision_generation,
        "_cached_generation",
        lambda *_args, **_kwargs: (
            "d" * 64,
            _response(generated.model_dump(mode="json"), marker="solve"),
        ),
    )
    outcome = generate_decision_candidate(
        _config(), identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path
    )
    assert outcome.status == "accepted"
    assert outcome.record == seed.parent
    assert outcome.record is not None and outcome.record.generation is None
    assert "Unchecked generated rationale" not in outcome.record.messages[-1].content


def test_german_rewrite_publishes_german_reference_prose_with_english_enums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_model.curation.decision_contracts import (
        Citation,
        DecisionDataSettings,
        RequestUnitsResult,
        validate_decision_output,
    )
    from foliqant_model.curation.decision_seeds import build_authored_seeds

    seed = next(
        item
        for item in build_authored_seeds(DecisionDataSettings(), seed=42, languages=["de"])
        if item.scenario == "requests-same" and "TX-101" in item.input.state.sources[0].text
    )
    rewritten = (
        'Ich bitte um Erstattung der Gebühren mit den Kennzeichnungen "TX-101" und "TX-202".'
    )
    solver = seed.oracle.model_copy(deep=True)
    result = solver.results[0]
    assert isinstance(result, RequestUnitsResult) and result.answer is not None
    result.explanation.summary = "Both fees are explicitly requested."
    result.explanation.evidence = [Citation(sourceId="message-1", quote=rewritten)]
    for unit in result.answer.units:
        unit.description = "Refund the named fee"
        unit.evidence = [Citation(sourceId="message-1", quote=rewritten)]
    replies = iter(
        [
            {"sources": [{"id": "message-1", "kind": "message", "text": rewritten}]},
            solver.model_dump(mode="json"),
        ]
    )

    def fake_cached(*_args: object, **kwargs: object) -> tuple[str, GenerationResponse]:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        assert seed.oracle.results[0].explanation.summary not in messages[-1].content
        return "d" * 64, _response(next(replies), marker="german")

    monkeypatch.setattr(decision_generation, "_cached_generation", fake_cached)
    config = _config()
    config.generation.languages = ["de"]
    job = _job(seed).model_copy(update={"language": "de"})
    outcome = generate_decision_candidate(
        config, identity=_identity(), seed=seed, job=job, cache_dir=tmp_path
    )
    assert outcome.status == "accepted" and outcome.record is not None
    assert outcome.record.language == "de"
    task = DecisionInput.model_validate_json(outcome.record.messages[-2].content)
    output = DecisionOutput.model_validate_json(outcome.record.messages[-1].content)
    published = output.results[0]
    assert isinstance(published, RequestUnitsResult) and published.answer is not None
    original = seed.oracle.results[0]
    assert isinstance(original, RequestUnitsResult) and original.answer is not None
    assert published.explanation.summary == original.explanation.summary
    assert [unit.description for unit in published.answer.units] == [
        unit.description for unit in original.answer.units
    ]
    assert published.answerability.status == "answerable"
    assert all(unit.status == "active" for unit in published.answer.units)
    assert all(unit.categoryId == "fee_refund" for unit in published.answer.units)
    assert task.state.sources[0].text == rewritten
    assert validate_decision_output(task, output) == []


def test_transport_failure_propagates_and_heldout_job_never_calls_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        raise ModelError("NETWORK_FAILED", "test interruption")

    monkeypatch.setattr(decision_generation, "_cached_generation", fail)
    with pytest.raises(ModelError, match="test interruption"):
        generate_decision_candidate(
            _config(),
            identity=_identity(),
            seed=seed,
            job=_job(seed),
            cache_dir=tmp_path,
        )
    assert calls == 1

    with pytest.raises(ModelError, match="train split"):
        generate_decision_candidate(
            _config(),
            identity=_identity(),
            seed=seed,
            job=_job(seed, split="validation"),
            cache_dir=tmp_path,
        )
    assert calls == 1


def test_invalid_structured_output_retries_within_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    calls = 0
    message_sets: list[list[ChatMessage]] = []

    def retry(*_args: object, **_kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        messages = _kwargs.get("messages")
        assert isinstance(messages, list)
        message_sets.append(messages)
        if calls == 1:
            raise ModelError("OUTPUT_INVALID", "Generated output does not match its schema")
        return "d" * 64, _response(
            _output(summary="The second bounded attempt succeeded.").model_dump(mode="json"),
            marker="second",
        )

    monkeypatch.setattr(decision_generation, "_cached_generation", retry)
    outcome = generate_decision_candidate(
        _config(attempts=2),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "accepted"
    assert outcome.attempts == 2
    assert calls == 2
    assert [trace.status for trace in outcome.attemptTrace] == ["quarantined", "accepted"]
    assert outcome.attemptTrace[0].calls[0].responseSource == "absent"
    correction = json.loads(message_sets[1][-1].content)
    assert correction["rejectionReason"] == "solver-output-schema-invalid"
    assert correction["previousResponseTruncated"] is False


def test_oversized_summary_is_rejected_and_retried_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    oversized_summary = "s" * 401
    oversized = _output().model_dump(mode="json")
    oversized["results"][0]["explanation"]["summary"] = oversized_summary
    calls = 0
    message_sets: list[list[ChatMessage]] = []

    def retry(*_args: object, **kwargs: object) -> tuple[str, GenerationResponse]:
        nonlocal calls
        calls += 1
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        message_sets.append(messages)
        output = oversized if calls == 1 else _output().model_dump(mode="json")
        return str(calls) * 64, _response(output, marker=f"summary-{calls}")

    monkeypatch.setattr(decision_generation, "_cached_generation", retry)
    outcome = generate_decision_candidate(
        _config(attempts=2),
        identity=_identity(),
        seed=seed,
        job=_job(seed),
        cache_dir=tmp_path,
    )

    assert outcome.status == "accepted"
    assert [trace.status for trace in outcome.attemptTrace] == ["quarantined", "accepted"]
    first_call = outcome.attemptTrace[0].calls[0]
    assert first_call.reason == "solver-output-invalid"
    assert first_call.responseSource == "canonical-output"
    assert oversized_summary in (first_call.finalAssistantResponsePreview or "")
    assert oversized_summary in message_sets[1][-2].content


def test_decision_recipe_identity_binds_shared_retry_feedback_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = decision_generation.decision_generation_recipe_digest()
    monkeypatch.setattr(generation, "_RETRY_FEEDBACK_VERSION", "changed-for-test")
    assert decision_generation.decision_generation_recipe_digest() != before


def test_solver_prompt_requests_a_grounded_concise_summary_without_truncation() -> None:
    prompt = decision_generation._SOLVER_GROUNDING_SYSTEM

    assert "one grounded, concise reason" in prompt
    assert "aiming for 160 characters or fewer" in prompt
    assert "second sentence only for a decisive limitation" in prompt
    assert "400-character schema limit" in prompt
    assert "truncate mid-thought" in prompt
