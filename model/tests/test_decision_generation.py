"""Focused tests for native typed-decision generation boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from foliqant_decisions import DecisionInput, DecisionOutput, DecisionSource

from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord, GenerationProvenance
from foliqant_model.curation import decision_generation, generation
from foliqant_model.curation.contracts import (
    CandidateJob,
    CurationConfig,
    GenerationSettings,
)
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
        assert kwargs["schema"] == decision_generation._decision_output_schema(seed.input)
        assert kwargs["schema"] != decision_generation._decision_output_schema()
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
    from foliqant_decisions import (
        Citation,
        RequestUnitsResult,
        validate_decision_output,
    )

    from foliqant_model.curation.decision_contracts import DecisionDataSettings
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


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Payment is due at year-end 2025.", "Payment falls due at the end of 2025."),
        ("By the end of the year 2030, pay EUR 40.", "Pay EUR 40 by year-end 2030."),
        ("Zahlung zum Jahresende 2025.", "Die Zahlung erfolgt Ende 2025."),
        ("Zahlung Ende des Jahres 2030.", "Die Zahlung erfolgt zum Jahresende 2030."),
        ("At year-end 2025, the term is 5 years.", "At the end of 2025, the term is 5 years."),
        ("Zum Jahresende 2025 läuft die Frist 5 Jahre.", "Ende 2025 läuft die Frist 5 Jahre."),
    ],
)
def test_calendar_year_end_paraphrases_preserve_units(original: str, rewritten: str) -> None:
    assert decision_generation._source_text_problem(original, rewritten) is None
    assert decision_generation._source_text_problem(rewritten, original) is None


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Payment at year-end 2025.", "Payment in 2025."),
        ("Zahlung zum Jahresende 2025.", "Zahlung im Jahr 2025."),
        ("The term is 5 years.", "The term is 5."),
        ("Die Frist beträgt 5 Jahre.", "Die Frist beträgt 5."),
        ("At year-end 2025, the term is 5 years.", "At the end of 2025, the term is 5 months."),
        ("Ende 2025 läuft die Frist 5 Jahre.", "Zum Jahresende 2025 läuft die Frist 5 Monate."),
        ("At year-end 2025, pay EUR 40 per year.", "At the end of 2025, pay EUR 40 per month."),
        ("Zum Jahresende 2025 sind 5 Prozent fällig.", "Ende 2025 sind 5 Basispunkte fällig."),
        ("At year-end 2025, pay 5 percent.", "At year-end 2026, pay 5 percent."),
    ],
)
def test_calendar_alias_does_not_weaken_boundaries_or_units(original: str, rewritten: str) -> None:
    assert decision_generation._source_text_problem(original, rewritten) is not None


def _rewritten_payload() -> dict[str, object]:
    return {
        "sources": [
            {
                "id": "message-1",
                "kind": "message",
                "text": 'EUR 40 is the transfer amount. "Please send a receipt."',
            }
        ]
    }


def _invalid_solver_payload() -> dict[str, object]:
    payload = _output().model_dump(mode="json")
    payload["results"][0]["answer"]["value"] = "unknown"
    payload["results"][0]["explanation"]["evidence"][0]["quote"] = "invented quote"
    return payload


def _cached_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    outputs: list[dict[str, object] | ModelError],
) -> list[dict[str, object]]:
    """Exercise the real immutable cache with deterministic offline endpoint responses."""
    requests: list[dict[str, object]] = []

    def generate(endpoint, **kwargs):  # type: ignore[no-untyped-def]
        requests.append(kwargs)
        output = outputs.pop(0)
        if isinstance(output, ModelError):
            raise output
        request_digest = generation._generation_request_sha256(
            endpoint,
            model_id=kwargs["model_id"],
            messages=kwargs["messages"],
            schema=kwargs["schema"],
            seed=kwargs["seed"],
        )
        return GenerationResponse(
            model=kwargs["observed_identity"],
            output=output,
            finishReason="stop",
            requestSha256=request_digest,
            schemaSha256=canonical_digest(kwargs["schema"]),
            rawResponseSha256=canonical_digest(output),
            finalAssistantResponse=_json(output),
            elapsedSeconds=0.1,
        )

    monkeypatch.setattr(generation, "generate_json", generate)
    return requests


def test_solver_retry_keeps_rewrite_and_repairs_actual_solver_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(
        monkeypatch,
        [_rewritten_payload(), _invalid_solver_payload(), _output().model_dump(mode="json")],
    )
    outcome = generate_decision_candidate(
        _config(attempts=2), identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path
    )
    assert outcome.status == "accepted"
    assert [[call.phase for call in trace.calls] for trace in outcome.attemptTrace] == [
        ["rewrite", "solver"],
        ["solver"],
    ]
    assert len(requests) == 3
    messages = requests[-1]["messages"]
    assert isinstance(messages, list)
    assert json.loads(messages[-2].content) == _invalid_solver_payload()
    feedback = json.loads(messages[-1].content)
    assert feedback["phase"] == "solver"
    assert {item["code"] for item in feedback["validationProblems"]} == {
        "question:receipt-requested:unknown-on-answerable",
        "question:receipt-requested:explanation:citation:0:quote-not-found",
    }
    assert "not_answerable" in _json(feedback)
    assert all("PRIVATE ORACLE" not in message.content for message in messages)
    assert json.loads(messages[-3].content)["state"] == _rewritten_payload()
    assert outcome.record is not None
    assert json.loads(outcome.record.messages[-2].content)["state"] == _rewritten_payload()


def test_rewrite_defect_repairs_rewrite_before_any_solver_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    original = _task().state.model_dump(mode="json")
    requests = _cached_endpoint(
        monkeypatch, [original, _rewritten_payload(), _output().model_dump(mode="json")]
    )
    outcome = generate_decision_candidate(
        _config(attempts=2), identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path
    )
    assert outcome.status == "accepted"
    assert [[call.phase for call in trace.calls] for trace in outcome.attemptTrace] == [
        ["rewrite"],
        ["rewrite", "solver"],
    ]
    messages = requests[1]["messages"]
    assert isinstance(messages, list)
    assert json.loads(messages[-2].content) == original
    assert json.loads(messages[-1].content)["phase"] == "rewrite"
    solver_messages = requests[2]["messages"]
    assert isinstance(solver_messages, list)
    assert all("rejectionReason" not in message.content for message in solver_messages)


def test_solver_interruption_resumes_cached_rewrite_and_rejection_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(
        monkeypatch,
        [
            _rewritten_payload(),
            _invalid_solver_payload(),
            ModelError("TIMEOUT", "interrupted"),
            _output().model_dump(mode="json"),
        ],
    )
    kwargs = dict(identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path)
    with pytest.raises(ModelError, match="interrupted"):
        generate_decision_candidate(_config(attempts=2), **kwargs)
    outcome = generate_decision_candidate(_config(attempts=2), **kwargs)
    assert outcome.status == "accepted"
    assert len(requests) == 4
    assert requests[-2] == requests[-1]
    assert generate_decision_candidate(_config(attempts=2), **kwargs) == outcome
    assert len(requests) == 4
    assert len(list((tmp_path / "calls").glob("*.json"))) == 3


def test_external_solver_repair_recovers_last_valid_state_and_retains_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(
        monkeypatch,
        [
            _rewritten_payload(),
            _invalid_solver_payload(),
            _invalid_solver_payload(),
            _output().model_dump(mode="json"),
        ],
    )
    config = _config(attempts=2)
    parent = tmp_path / "parent"
    prior = generate_decision_candidate(
        config, identity=_identity(), seed=seed, job=_job(seed), cache_dir=parent
    )
    assert prior.status == "quarantined" and prior.attempts == 2
    assert [call.phase for call in prior.attemptTrace[-1].calls] == ["solver"]
    before = {path.name: path.read_bytes() for path in (parent / "calls").glob("*.json")}
    child = tmp_path / "child"
    job = _job(seed).model_copy(update={"jobId": "e" * 64})
    kwargs = dict(
        identity=_identity(),
        seed=seed,
        job=job,
        cache_dir=child,
        prior_rejection=prior,
        prior_cache_dir=parent,
        prior_response=_json(_invalid_solver_payload()),
        request_namespace="repair-test",
    )
    repaired = generate_decision_candidate(config, **kwargs)
    assert repaired.status == "accepted" and repaired.attempts == 1
    assert len(requests) == 4
    assert repaired.attemptTrace[0].calls[0] == prior.attemptTrace[0].calls[0]
    assert repaired.record is not None
    assert json.loads(repaired.record.messages[-2].content)["state"] == _rewritten_payload()
    assert generate_decision_candidate(config, **kwargs) == repaired
    assert len(requests) == 4
    assert before == {path.name: path.read_bytes() for path in (parent / "calls").glob("*.json")}
    assert (child / "calls" / (prior.attemptTrace[0].calls[0].callId + ".json")).exists()


def test_semantic_mismatch_never_requests_another_label_inline_or_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(
        monkeypatch, [_rewritten_payload(), _output(value="false").model_dump(mode="json")]
    )
    parent = tmp_path / "parent"
    prior = generate_decision_candidate(
        _config(attempts=3), identity=_identity(), seed=seed, job=_job(seed), cache_dir=parent
    )
    assert prior.reason == "solver-semantic-mismatch" and prior.attempts == 1
    assert len(requests) == 2
    job = _job(seed).model_copy(update={"jobId": "e" * 64})
    repaired = generate_decision_candidate(
        _config(attempts=3),
        identity=_identity(),
        seed=seed,
        job=job,
        cache_dir=tmp_path / "child",
        prior_rejection=prior,
        prior_cache_dir=parent,
        request_namespace="repair-test",
    )
    assert repaired == prior.model_copy(update={"jobId": job.jobId})
    assert len(requests) == 2


def test_external_canonical_support_failure_repairs_rewrite_with_rewrite_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_decisions import Citation

    seed = _seed(mode="rewrite")
    oracle = seed.oracle.model_copy(deep=True)
    oracle.results[0].explanation.evidence = [
        Citation(sourceId="message-1", quote=_task().state.sources[0].text)
    ]
    seed = seed.model_copy(
        update={
            "parent": seed.parent.model_copy(
                update={
                    "messages": [
                        *seed.parent.messages[:-1],
                        ChatMessage(
                            role="assistant", content=_json(oracle.model_dump(mode="json"))
                        ),
                    ]
                }
            )
        }
    )
    long_rewrite = _rewritten_payload()
    long_rewrite["sources"][0]["text"] += " " + "word " * 1000
    requests = _cached_endpoint(
        monkeypatch,
        [
            long_rewrite,
            _output().model_dump(mode="json"),
            _rewritten_payload(),
            _output().model_dump(mode="json"),
        ],
    )
    parent = tmp_path / "parent"
    prior = generate_decision_candidate(
        _config(), identity=_identity(), seed=seed, job=_job(seed), cache_dir=parent
    )
    assert prior.reason == "rewrite-canonical-support-unmappable"
    repaired = generate_decision_candidate(
        _config(),
        identity=_identity(),
        seed=seed,
        job=_job(seed).model_copy(update={"jobId": "e" * 64}),
        cache_dir=tmp_path / "child",
        prior_rejection=prior,
        prior_cache_dir=parent,
        request_namespace="repair-test",
    )
    assert repaired.status == "accepted"
    assert len(requests) == 4
    messages = requests[2]["messages"]
    assert isinstance(messages, list)
    assert json.loads(messages[-2].content) == long_rewrite
    assert json.loads(messages[-1].content)["phase"] == "rewrite"


def test_external_solver_repair_refuses_corrupted_retained_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(monkeypatch, [_rewritten_payload(), _invalid_solver_payload()])
    parent = tmp_path / "parent"
    prior = generate_decision_candidate(
        _config(), identity=_identity(), seed=seed, job=_job(seed), cache_dir=parent
    )
    call = prior.attemptTrace[0].calls[0]
    cache = parent / "calls" / (call.callId + ".json")
    payload = json.loads(cache.read_text())
    payload["payload"]["response"]["requestSha256"] = "f" * 64
    payload["sha256"] = canonical_digest(payload["payload"])
    cache.write_text(_json(payload))
    with pytest.raises(ModelError, match="response identity changed"):
        generate_decision_candidate(
            _config(),
            identity=_identity(),
            seed=seed,
            job=_job(seed).model_copy(update={"jobId": "e" * 64}),
            cache_dir=tmp_path / "child",
            prior_rejection=prior,
            prior_cache_dir=parent,
            request_namespace="repair-test",
        )
    assert len(requests) == 2


def test_structured_repair_feedback_is_bounded_and_contains_no_validation_input() -> None:
    invalid = _invalid_solver_payload()
    # Untrusted invalid question IDs may be arbitrary; do not reproduce unlimited
    # locations, raw exception context, expected targets, or arbitrary input values.
    invalid["results"] = [
        {**invalid["results"][0], "questionId": f"{index}-" + "x" * 500} for index in range(40)
    ]
    messages = decision_generation._decision_repair_messages(
        [],
        task=_task(),
        phase="solver",
        previous_response=_json(invalid),
        reason="solver-output-invalid-" + "x" * 1000,
    )
    feedback = json.loads(messages[-1].content)
    assert len(feedback["rejectionReason"]) == 256
    assert len(feedback["validationProblems"]) == 16
    assert feedback["problemsTruncated"] is True
    assert all(len(problem["code"]) <= 256 for problem in feedback["validationProblems"])
    assert "PRIVATE ORACLE" not in messages[-1].content
    assert "input" not in feedback and "ctx" not in feedback
    assert len(messages[-2].content) <= 32_768


@pytest.mark.parametrize("field", ["requestSha256", "schemaSha256", "modelId", "metadataSha256"])
def test_external_recovery_binds_response_to_original_call_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    seed = _seed(mode="rewrite")
    requests = _cached_endpoint(monkeypatch, [_rewritten_payload(), _invalid_solver_payload()])
    parent = tmp_path / "parent"
    prior = generate_decision_candidate(
        _config(), identity=_identity(), seed=seed, job=_job(seed), cache_dir=parent
    )
    call = prior.attemptTrace[0].calls[0]
    cache = parent / "calls" / (call.callId + ".json")
    envelope = json.loads(cache.read_text())
    response = envelope["payload"]["response"]
    if field in {"modelId", "metadataSha256"}:
        response["model"][field] = "f" * 64
    else:
        response[field] = "f" * 64
    if field == "requestSha256":
        # Even a coherently altered trace must remain bound to the request ID
        # inside the original immutable call identity, not merely to itself.
        prior = prior.model_copy(deep=True)
        prior.attemptTrace[0].calls[0] = call.model_copy(update={"requestSha256": "f" * 64})
    envelope["sha256"] = canonical_digest(envelope["payload"])
    cache.write_text(_json(envelope))
    with pytest.raises(ModelError, match="Retained decision response identity changed"):
        generate_decision_candidate(
            _config(),
            identity=_identity(),
            seed=seed,
            job=_job(seed).model_copy(update={"jobId": "e" * 64}),
            cache_dir=tmp_path / "child",
            prior_rejection=prior,
            prior_cache_dir=parent,
            request_namespace="repair-test",
        )
    assert len(requests) == 2


def _solver_sse(content: str, *, model: str | None = None) -> bytes:
    chunks = []
    for delta, finish in [
        ({"role": "assistant"}, None),
        ({"reasoning_content": "PRIVATE REASONING NEVER PERSIST"}, None),
        ({"content": content}, None),
        ({}, "stop"),
    ]:
        chunks.append(
            b"data: "
            + _json(
                {
                    "id": "test-completion",
                    "object": "chat.completion.chunk",
                    "model": model or _identity().modelId,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
            ).encode()
            + b"\n\n"
        )
    return b"".join(chunks) + b"data: [DONE]\n\n"


def _in_memory_sse_endpoint(
    monkeypatch: pytest.MonkeyPatch, bodies: list[bytes]
) -> list[dict[str, object]]:
    """Exercise actual SSE validation/cache logic with no worker or network sockets."""
    import io

    from foliqant_model.curation import endpoint

    requests: list[dict[str, object]] = []

    class Response(io.BytesIO):
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self, body: bytes, url: str):
            super().__init__(body)
            self.url = url

        def geturl(self) -> str:
            return self.url

    class Opener:
        def open(self, request, **kwargs):  # type: ignore[no-untyped-def]
            requests.append(json.loads(request.data))
            assert bodies, "Unexpected automatic endpoint retry"
            return Response(bodies.pop(0), request.full_url)

    def direct(config, **kwargs):  # type: ignore[no-untyped-def]
        request = endpoint._GenerationRequest(
            config=config,
            modelId=kwargs["model_id"],
            expectedModel=kwargs["observed_identity"],
            messages=kwargs["messages"],
            schema=kwargs["schema"],
            seed=kwargs["seed"],
        )
        return endpoint._generate_json_direct(request)

    monkeypatch.setattr(endpoint, "_opener", Opener)
    monkeypatch.setattr(generation, "generate_json", direct)
    return requests


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"data: {malformed\n\n", "BACKEND_FAILED"),
        (
            _solver_sse(_json(_output().model_dump(mode="json")), model="changed-model"),
            "INTEGRITY_FAILED",
        ),
    ],
)
def test_fatal_sse_failure_stops_generation_without_retry_or_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: bytes, code: str
) -> None:
    seed = _seed()
    requests = _in_memory_sse_endpoint(monkeypatch, [body])
    with pytest.raises(ModelError) as raised:
        generate_decision_candidate(
            _config(attempts=3), identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path
        )
    assert raised.value.code == code
    assert len(requests) == 1
    assert not list((tmp_path / "calls").glob("*.json"))


def test_sse_whitespace_rejection_is_cached_and_repairs_only_solver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_model.curation.storage import load_object

    seed = _seed(mode="rewrite")
    partial = '{"schemaVersion":1,"results":' + " " * 1024
    requests = _in_memory_sse_endpoint(
        monkeypatch,
        [
            _solver_sse(_json(_rewritten_payload())),
            _solver_sse(partial),
            _solver_sse(_json(_output().model_dump(mode="json"))),
        ],
    )
    kwargs = dict(identity=_identity(), seed=seed, job=_job(seed), cache_dir=tmp_path)
    outcome = generate_decision_candidate(_config(attempts=3), **kwargs)
    assert outcome.status == "accepted" and outcome.attempts == 2
    assert [[call.phase for call in trace.calls] for trace in outcome.attemptTrace] == [
        ["rewrite", "solver"],
        ["solver"],
    ]
    assert len(requests) == 3
    messages = requests[-1]["messages"]
    assert isinstance(messages, list)
    assert messages[-2]["content"] == partial
    assert json.loads(messages[-3]["content"])["state"] == _rewritten_payload()
    rejected = outcome.attemptTrace[0].calls[-1]
    payload = load_object(tmp_path / "calls" / (rejected.callId + ".json"))
    assert isinstance(payload, dict)
    retention = payload["rejection"]
    assert retention["finalAssistantResponse"] == partial
    assert retention["contentDiagnostic"] == "long-json-whitespace-run"
    assert "finishReason" not in retention and "tokenUsage" not in retention
    assert "PRIVATE REASONING" not in _json(payload)
    assert generate_decision_candidate(_config(attempts=3), **kwargs) == outcome
    assert len(requests) == 3
