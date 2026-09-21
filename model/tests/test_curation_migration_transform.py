from __future__ import annotations

import json

from foliqant_decisions import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceResult,
    Citation,
    DecisionInput,
    DecisionOption,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    Explanation,
    PredicateAnswer,
    PredicateQuestion,
    PredicateResult,
)

from foliqant_model.contracts import ChatMessage, DataRecord, GenerationProvenance
from foliqant_model.curation.contracts import ImportedRecord
from foliqant_model.curation.decision_contracts import DecisionDataSettings
from foliqant_model.curation.decision_generation import canonical_decision_output
from foliqant_model.curation.decision_seeds import (
    DecisionSeed,
    build_authored_seeds,
    project_source,
)
from foliqant_model.curation.migration_transform import transform_record


def _source_row(source_id: str, user: object, assistant: object) -> ImportedRecord:
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id=f"{source_id}.record.original",
            sourceId=source_id,
            language="en",
            groupKeys=[f"{source_id}:group:original"],
            messages=[
                ChatMessage(role="system", content="Source task."),
                ChatMessage(role="user", content=json.dumps(user)),
                ChatMessage(role="assistant", content=json.dumps(assistant)),
            ],
            tags=[source_id],
            origin="human" if source_id == "banking77" else "synthetic",
            reviewed=False,
            familyId=f"{source_id}.family.original",
        ),
        originalSplit="train",
        task="classification" if source_id == "banking77" else "entailment",
        originalId="original",
    )


def _old_system_seed(current: DecisionSeed) -> DecisionSeed:
    parent = current.parent.model_copy(deep=True)
    parent.messages[0] = ChatMessage(role="system", content="Historical native instructions.")
    return current.model_copy(update={"parent": parent})


def _generated_derivative(seed: DecisionSeed) -> DataRecord:
    task = seed.input.model_copy(deep=True)
    for source in task.state.sources:
        if source.id in seed.rewriteSourceIds:
            source.text = "Case details: " + source.text
    output = canonical_decision_output(seed, task)
    return DataRecord(
        schemaVersion=1,
        id="generated-" + "a" * 64,
        sourceId="generated-" + seed.parent.sourceId,
        language=seed.parent.language,
        groupKeys=seed.parent.groupKeys,
        messages=[
            *seed.parent.messages[:-2],
            ChatMessage(role="user", content=task.model_dump_json()),
            ChatMessage(role="assistant", content=output.model_dump_json()),
        ],
        tags=seed.parent.tags,
        origin="teacher",
        reviewed=False,
        familyId=seed.parent.familyId,
        generation=GenerationProvenance(
            provider="openai-compatible",
            modelId="historical-model",
            modelIdentitySha256="a" * 64,
            promptSha256="b" * 64,
            parametersSha256="c" * 64,
            requestSha256="d" * 64,
            parentRecordIds=[seed.parent.id],
        ),
    )


def _old_banking77_seed(row: ImportedRecord, current: DecisionSeed) -> DecisionSeed:
    source = json.loads(row.record.messages[-2].content)
    annotation = json.loads(row.record.messages[-1].content)
    labels = source["labels"]
    selected = labels.index(annotation["intent"])
    task = DecisionInput(
        state=DecisionState(
            sources=[DecisionSource(id="request", kind="message", text=source["request"])]
        ),
        questions=[
            ChoiceQuestion(
                id="intent",
                type="choice",
                prompt="Choose the single best matching banking request category.",
                criteria=["Use the meaning of the complete request."],
                allowedSourceIds=["request"],
                options=[
                    DecisionOption(id=f"label-{index:03d}", description=label)
                    for index, label in enumerate(labels)
                ],
            )
        ],
    )
    oracle = DecisionOutput(
        results=[
            ChoiceResult(
                questionId="intent",
                type="choice",
                answerability=Answerability(status="answerable", issues=[]),
                answer=ChoiceAnswer(optionId=f"label-{selected:03d}"),
                explanation=Explanation(
                    summary=f"The complete request matches {annotation['intent']}.",
                    evidence=[Citation(sourceId="request", quote=source["request"])],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    parent = current.parent.model_copy(deep=True)
    parent.id = "historical-banking77"
    parent.tags = [
        tag for tag in parent.tags if not tag.startswith(("source-label:", "category-catalog:"))
    ]
    parent.messages = [
        ChatMessage(role="system", content="Historical native instructions."),
        ChatMessage(role="user", content=task.model_dump_json()),
        ChatMessage(role="assistant", content=oracle.model_dump_json()),
    ]
    return DecisionSeed(
        parent=parent,
        scenario="projection:banking77",
        mode="annotate",
        rewriteSourceIds=[],
    )


def _old_wanli_seed(row: ImportedRecord, current: DecisionSeed) -> DecisionSeed:
    source = json.loads(row.record.messages[-2].content)
    annotation = json.loads(row.record.messages[-1].content)
    values = {"supported": "true", "contradicted": "false", "insufficient": "unknown"}
    value = values[annotation["label"]]
    task = DecisionInput(
        state=DecisionState(
            sources=[DecisionSource(id="evidence", kind="document", text=source["evidence"])]
        ),
        questions=[
            PredicateQuestion(
                id="claim",
                type="predicate",
                prompt="Does the supplied evidence establish this claim: " + source["claim"],
                criteria=[
                    "True requires support, false requires contradiction, and unknown means "
                    "neither is established."
                ],
                allowedSourceIds=["evidence"],
            )
        ],
    )
    oracle = DecisionOutput(
        results=[
            PredicateResult(
                questionId="claim",
                type="predicate",
                answerability=Answerability(
                    status="not_answerable" if value == "unknown" else "answerable",
                    issues=["missing_information"] if value == "unknown" else [],
                ),
                answer=PredicateAnswer(value=value),
                explanation=Explanation(
                    summary="Historical source relation.",
                    evidence=(
                        []
                        if value == "unknown"
                        else [Citation(sourceId="evidence", quote=source["evidence"])]
                    ),
                    contraryEvidence=[],
                    missingFacts=(
                        ["Evidence that establishes or contradicts the claim."]
                        if value == "unknown"
                        else []
                    ),
                ),
            )
        ]
    )
    parent = current.parent.model_copy(deep=True)
    parent.id = "historical-wanli"
    parent.tags = [
        tag for tag in parent.tags if not tag.startswith(("source-label:", "source-nli-relation:"))
    ]
    parent.tags = [
        "question-type:predicate" if tag == "question-type:choice" else tag for tag in parent.tags
    ]
    if value == "unknown":
        parent.tags = [
            "answerability:not_answerable" if tag == "answerability:answerable" else tag
            for tag in parent.tags
        ]
    parent.messages = [
        ChatMessage(role="system", content="Historical native instructions."),
        ChatMessage(role="user", content=task.model_dump_json()),
        ChatMessage(role="assistant", content=oracle.model_dump_json()),
    ]
    return DecisionSeed(
        parent=parent,
        scenario="projection:wanli",
        mode="annotate",
        rewriteSourceIds=[],
    )


def test_unchanged_authored_parent_is_retained_as_the_same_object() -> None:
    current = build_authored_seeds(
        DecisionDataSettings(examplesPerScenario=4), seed=42, languages=["en"]
    )[0]
    old = _old_system_seed(current)

    transformed = transform_record(old.parent, old, current)

    assert transformed.operation == "retained"
    assert transformed.reason == "unchanged-task-and-reference-semantics"
    assert transformed.record is old.parent
    assert transformed.record.messages[0].content == "Historical native instructions."


def test_unchanged_accepted_derivative_keeps_generation_bytes() -> None:
    current = next(
        seed
        for seed in build_authored_seeds(
            DecisionDataSettings(examplesPerScenario=4), seed=42, languages=["en"]
        )
        if seed.scenario == "choice-answerable"
    )
    old = _old_system_seed(current)
    derivative = _generated_derivative(old)

    transformed = transform_record(derivative, old, current)

    assert transformed.record is derivative
    assert transformed.operation == "retained"
    assert transformed.record.generation == derivative.generation


def test_banking77_is_reprojected_from_exact_source_label_with_catalog_ids() -> None:
    row = _source_row(
        "banking77",
        {
            "labels": ["Refund_not_showing_up", "reverted_card_payment?"],
            "request": "My card payment was reversed.",
        },
        {"intent": "reverted_card_payment?"},
    )
    current = project_source(row)
    assert current is not None
    old = _old_banking77_seed(row, current)

    transformed = transform_record(old.parent, old, current)

    assert transformed.operation == "reprojected"
    assert transformed.reason == "deterministic-source-annotation-reprojection"
    assert transformed.record is current.parent
    result = current.oracle.results[0]
    assert isinstance(result, ChoiceResult)
    assert result.answer is not None
    assert result.answer.optionId == "reverted_card_payment"


def test_wanli_unknown_becomes_source_labeled_neutral_without_model_output() -> None:
    row = _source_row(
        "wanli",
        {"claim": "The transfer settled Tuesday.", "evidence": "The transfer was submitted."},
        {"label": "insufficient"},
    )
    current = project_source(row)
    assert current is not None
    old = _old_wanli_seed(row, current)

    transformed = transform_record(old.parent, old, current)

    assert transformed.operation == "reprojected"
    assert transformed.record is current.parent
    result = current.oracle.results[0]
    assert isinstance(result, ChoiceResult)
    assert result.answer is not None
    assert result.answer.optionId == "neutral"
    assert result.answerability.status == "answerable"
    assert "source-label:insufficient" in transformed.record.tags


def test_changed_non_projection_task_fails_closed() -> None:
    old = build_authored_seeds(
        DecisionDataSettings(examplesPerScenario=4), seed=42, languages=["en"]
    )[0]
    current = old.model_copy(deep=True)
    task = current.input.model_copy(deep=True)
    task.questions[0].prompt += " Updated."
    current.parent.messages[-2] = ChatMessage(role="user", content=task.model_dump_json())

    transformed = transform_record(old.parent, old, current)

    assert transformed.record is None
    assert transformed.operation == "reprojected"
    assert transformed.reason == "projection-rule-unsupported"


def test_projection_refuses_a_changed_historical_parent() -> None:
    row = _source_row(
        "banking77",
        {
            "labels": ["cash_withdrawal_charge", "top_up_by_cash_or_cheque"],
            "request": "Why was a fee charged for withdrawing cash?",
        },
        {"intent": "cash_withdrawal_charge"},
    )
    current = project_source(row)
    assert current is not None
    old = _old_banking77_seed(row, current)
    changed = old.parent.model_copy(deep=True)
    changed.tags.append("untracked-change")

    transformed = transform_record(changed, old, current)

    assert transformed.record is None
    assert transformed.operation == "retained"
    assert transformed.reason == "non-generated-record-changed"
