from __future__ import annotations

import json

import pytest

from foliqant.decisions import (
    DecisionInput,
    DecisionOutput,
    validate_decision_output,
)
from foliqant_model.contracts import ChatMessage, DataRecord
from foliqant_model.curation import source_category_catalogs
from foliqant_model.curation.contracts import ImportedRecord
from foliqant_model.curation.decision_seeds import decision_seed_recipe_digest, project_source
from foliqant_model.curation.source_category_catalogs import (
    BANKING77_CATALOG_VERSION,
    banking77_catalog,
    banking77_source_labels,
)

# The reviewed upstream taxonomy is a contract fixture, not downloaded records.
EXPECTED_LABELS = set(
    """
card_arrival card_linking exchange_rate card_payment_wrong_exchange_rate
extra_charge_on_statement pending_cash_withdrawal fiat_currency_support
card_delivery_estimate automatic_top_up card_not_working exchange_via_app
lost_or_stolen_card age_limit pin_blocked contactless_not_working
top_up_by_bank_transfer_charge pending_top_up cancel_transfer top_up_limits
wrong_amount_of_cash_received card_payment_fee_charged transfer_not_received_by_recipient
supported_cards_and_currencies getting_virtual_card card_acceptance top_up_reverted
balance_not_updated_after_cheque_or_cash_deposit card_payment_not_recognised
edit_personal_details why_verify_identity unable_to_verify_identity get_physical_card
visa_or_mastercard topping_up_by_card disposable_card_limits compromised_card atm_support
direct_debit_payment_not_recognised passcode_forgotten declined_cash_withdrawal
pending_card_payment lost_or_stolen_phone request_refund declined_transfer
Refund_not_showing_up declined_card_payment pending_transfer terminate_account card_swallowed
transaction_charged_twice verify_source_of_funds transfer_timing reverted_card_payment?
change_pin beneficiary_not_allowed transfer_fee_charged receiving_money failed_transfer
transfer_into_account verify_top_up getting_spare_card top_up_by_cash_or_cheque
order_physical_card virtual_card_not_working wrong_exchange_rate_for_cash_withdrawal
get_disposable_virtual_card top_up_failed balance_not_updated_after_bank_transfer
cash_withdrawal_not_recognised exchange_charge top_up_by_card_charge activate_my_card
cash_withdrawal_charge card_about_to_expire apple_pay_or_google_pay verify_my_identity
country_support
""".split()
)


def _row(label: str, language: str, labels: list[str]) -> ImportedRecord:
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id="banking77.synthetic.catalog-check",
            sourceId="banking77",
            language=language,
            familyId="banking77.synthetic.family",
            groupKeys=["banking77:synthetic:group"],
            messages=[
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "labels": labels,
                            "request": "Please explain the fee."
                            if language == "en"
                            else "Bitte erläutern Sie die Gebühr.",
                        }
                    ),
                ),
                ChatMessage(role="assistant", content=json.dumps({"intent": label})),
            ],
            tags=[],
            origin="human",
            reviewed=False,
        ),
        originalSplit="train",
        originalId="synthetic.catalog-check",
        task="classification",
    )


@pytest.mark.parametrize("language", ["en", "de"])
def test_complete_source_catalog_projects_every_annotation_without_invalid_references(
    language: str,
) -> None:
    labels = list(banking77_source_labels())
    assert set(labels) == EXPECTED_LABELS
    assert len(labels) == 77
    catalog = banking77_catalog(language)
    assert len(catalog.categories) == 77
    inputs = []
    for label in labels:
        row = _row(label, language, labels)
        before = row.model_dump(mode="json")
        seed = project_source(row)
        assert seed is not None
        question = seed.input.questions[0]
        assert question.type == "choice"
        assert question.options == catalog.decision_options()
        result = seed.oracle.results[0]
        assert result.type == "choice" and result.answer is not None
        assert result.answer.optionId == catalog.resolve_id(label)
        assert result.answerability.status == "answerable"
        assert validate_decision_output(seed.input, seed.oracle) == []
        assert row.model_dump(mode="json") == before
        assert f"source-label:{label}" in seed.parent.tags
        assert f"category-catalog:{BANKING77_CATALOG_VERSION}" in seed.parent.tags
        assert len(seed.parent.messages[-2].content) < 32_000
        inputs.append(seed.parent.messages[:-1])
    # Source reference labels never identify the expected option to the solver.
    assert all(item == inputs[0] for item in inputs)


def test_editorial_catalogs_explain_boundaries_and_do_not_invent_alias_distinctions() -> None:
    english = {item.id: item.description for item in banking77_catalog("en").categories}
    german = {item.id: item.description for item in banking77_catalog("de").categories}
    assert english.keys() == german.keys()
    assert all(english[key] != german[key] for key in english)
    assert all(len(description) >= 40 for description in [*english.values(), *german.values()])
    for first, second in (
        ("get_physical_card", "order_physical_card"),
        ("receiving_money", "transfer_into_account"),
    ):
        assert second in english[first] and first in english[second]
        assert "overlap" in english[first] and "overlap" in english[second]
        assert "exclusive boundary" in english[first]
    assert "duration" in english["transfer_not_received_by_recipient"]
    assert "not a separate payment fee" in english["card_payment_wrong_exchange_rate"]
    with pytest.raises(ValueError, match="unsupported"):
        banking77_catalog("fr")


def test_changed_category_definition_changes_recipe_and_projected_record_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels = ["card_arrival", "card_delivery_estimate"]
    row = _row("card_arrival", "en", labels)
    before_digest = decision_seed_recipe_digest()
    before_seed = project_source(row)
    assert before_seed is not None
    descriptions = source_category_catalogs._BANKING77_DEFINITIONS["card_arrival"]
    monkeypatch.setitem(
        source_category_catalogs._BANKING77_DEFINITIONS,
        "card_arrival",
        (
            descriptions[0] + " Changed editorial scope.",
            descriptions[1],
        ),
    )
    after_seed = project_source(row)
    assert after_seed is not None
    assert before_digest != decision_seed_recipe_digest()
    assert before_seed.parent.id != after_seed.parent.id
    assert before_seed.parent.familyId == after_seed.parent.familyId


@pytest.mark.parametrize("labels", [["card_arrival"], ["card_arrival", "card_arrival"]])
def test_invalid_source_label_cardinality_is_not_projected(labels: list[str]) -> None:
    assert project_source(_row("card_arrival", "en", labels)) is None


def test_historical_v1_predicate_projection_remains_valid() -> None:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 1,
            "state": {
                "sources": [
                    {"id": "evidence", "kind": "document", "text": "The transfer was submitted."}
                ]
            },
            "questions": [
                {
                    "id": "claim",
                    "type": "predicate",
                    "prompt": "Does the evidence establish that the transfer settled Tuesday?",
                    "criteria": ["Unknown means neither support nor contradiction."],
                    "allowedSourceIds": ["evidence"],
                }
            ],
        },
        strict=True,
    )
    oracle = DecisionOutput.model_validate(
        {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "claim",
                    "type": "predicate",
                    "answerability": {
                        "status": "not_answerable",
                        "issues": ["missing_information"],
                    },
                    "answer": {"value": "unknown"},
                    "explanation": {
                        "summary": "Settlement timing is not stated.",
                        "evidence": [],
                        "contraryEvidence": [],
                        "missingFacts": ["Settlement timing."],
                    },
                }
            ],
        },
        strict=True,
    )
    assert validate_decision_output(task, oracle) == []
