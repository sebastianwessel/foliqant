"""Genuine authored research cases for native decision-data generation."""

from __future__ import annotations

from .decision_contracts import (
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

_SCENARIOS = {
    "mixed-sufficiency",
    "changed-deadline",
    "fund-ratio-answerable",
    "fund-ratio-missing",
    "legal-applicability-missing",
    "irrelevant-evidence",
}


def _citation(source_id: str, quote: str) -> Citation:
    return Citation(sourceId=source_id, quote=quote)


def _explanation(
    summary: str,
    *,
    evidence: list[Citation] | None = None,
    contrary: list[Citation] | None = None,
    missing: list[str] | None = None,
) -> Explanation:
    return Explanation(
        summary=summary,
        evidence=evidence or [],
        contraryEvidence=contrary or [],
        missingFacts=missing or [],
    )


def _answerability(status: str, *issues: str) -> Answerability:
    return Answerability.model_validate({"status": status, "issues": list(issues)}, strict=True)


def _mixed_sufficiency(index: int) -> tuple[DecisionInput, DecisionOutput]:
    cases = (
        (
            "Please refund the duplicate service fee. The transaction reference is absent, and "
            "the charged amount is unavailable.",
            "fee_refund",
            "Is the transaction reference present?",
            "Was the charged amount above EUR 50?",
            "the transaction reference",
            "The charged amount.",
        ),
        (
            "Please send the quarterly statement. No statement period was supplied, and the "
            "delivery channel is not stated.",
            "statement_request",
            "Is the statement period present?",
            "Was electronic delivery requested?",
            "the statement period",
            "The delivery channel.",
        ),
        (
            "Please update my postal address. The new address is missing, and identity-check "
            "results are unavailable.",
            "address_change",
            "Is the new postal address present?",
            "Did the identity check pass?",
            "the new postal address",
            "The identity-check result.",
        ),
        (
            "Please cancel the transfer. The transfer identifier is absent, and the settlement "
            "status is unavailable.",
            "transfer_cancel",
            "Is the transfer identifier present?",
            "Was the transfer already settled?",
            "the transfer identifier",
            "The settlement status.",
        ),
    )
    text, route_id, presence_prompt, unknown_prompt, absent_fact, missing_fact = cases[index]
    source = DecisionSource(id="request", kind="message", text=text)
    citation = _citation(source.id, text)
    options = [
        DecisionOption(id="fee_refund", description="Refund a fee"),
        DecisionOption(id="statement_request", description="Send a statement"),
        DecisionOption(id="address_change", description="Change a postal address"),
        DecisionOption(id="transfer_cancel", description="Cancel a transfer"),
    ]
    task = DecisionInput(
        state=DecisionState(sources=[source]),
        questions=[
            ChoiceQuestion(
                id="route",
                type="choice",
                prompt="Choose the one category matching the explicit requested action.",
                criteria=["Use the explicit requested action, independently of missing details."],
                allowedSourceIds=[source.id],
                options=options,
            ),
            PredicateQuestion(
                id="reference_present",
                type="predicate",
                prompt=presence_prompt,
                criteria=[
                    "True requires an explicit reference; false requires an explicit statement "
                    "that the reference is absent; otherwise answer unknown."
                ],
                allowedSourceIds=[source.id],
            ),
            PredicateQuestion(
                id="additional_fact_available",
                type="predicate",
                prompt=unknown_prompt,
                criteria=[
                    "True or false requires the fact itself; an unavailable fact is unknown."
                ],
                allowedSourceIds=[source.id],
            ),
        ],
    )
    output = DecisionOutput(
        results=[
            ChoiceResult(
                questionId="route",
                type="choice",
                answerability=_answerability("answerable"),
                answer=ChoiceAnswer(optionId=route_id),
                explanation=_explanation(
                    "The explicit requested action matches the selected category.",
                    evidence=[citation],
                ),
            ),
            PredicateResult(
                questionId="reference_present",
                type="predicate",
                answerability=_answerability("answerable"),
                answer=PredicateAnswer(value="false"),
                explanation=_explanation(
                    f"The source explicitly states that {absent_fact} is absent.",
                    evidence=[citation],
                ),
            ),
            PredicateResult(
                questionId="additional_fact_available",
                type="predicate",
                answerability=_answerability("not_answerable", "missing_information"),
                answer=PredicateAnswer(value="unknown"),
                explanation=_explanation(
                    "The allowed source says the required fact is unavailable.",
                    missing=[missing_fact],
                ),
            ),
        ]
    )
    return task, output


def _changed_deadline(index: int) -> tuple[DecisionInput, DecisionOutput]:
    cases = (
        ("filing", "June 30, 2026", "July 31, 2026"),
        ("appeal", "March 10, 2027", "April 5, 2027"),
        ("payment", "September 1, 2026", "September 15, 2026"),
        ("response", "November 20, 2026", "December 2, 2026"),
    )
    subject, old_date, new_date = cases[index]
    original = f"Policy version 1 sets the {subject} deadline to {old_date}."
    update = (
        f"Policy version 2, which supersedes version 1, sets the {subject} deadline to {new_date}."
    )
    sources = [
        DecisionSource(id="original-policy", kind="policy", text=original),
        DecisionSource(id="policy-update", kind="policy", text=update),
    ]
    question = PredicateQuestion(
        id="deadline_changed",
        type="predicate",
        prompt=f"Does the controlling policy change the {subject} deadline to {new_date}?",
        criteria=["The explicitly superseding policy version controls over the earlier version."],
        allowedSourceIds=[source.id for source in sources],
    )
    result = PredicateResult(
        questionId=question.id,
        type="predicate",
        answerability=_answerability("answerable"),
        answer=PredicateAnswer(value="true"),
        explanation=_explanation(
            "The later policy explicitly supersedes the earlier deadline.",
            evidence=[_citation("policy-update", update)],
            contrary=[_citation("original-policy", original)],
        ),
    )
    return DecisionInput(
        state=DecisionState(sources=sources), questions=[question]
    ), DecisionOutput(results=[result])


def _ratio_case(index: int, *, known: bool) -> tuple[DecisionInput, DecisionOutput]:
    cases = (
        (
            "expense_ratio",
            "For the 2025 report, is expense divided by revenue below 20 percent?",
            "Divide expense by revenue and compare the result with 20 percent.",
            "The 2025 report states expense 20 and revenue 120.",
            "The report states expense 20; revenue and the reporting period are absent.",
            "The expense-to-revenue ratio is 20/120, which is below 20 percent.",
            ["Revenue denominator.", "Reporting period."],
        ),
        (
            "debt_asset_ratio",
            "At year-end 2025, is debt divided by total assets below 50 percent?",
            "Divide debt by total assets and compare the result with 50 percent.",
            "At year-end 2025, debt is 45 and total assets are 100.",
            "Debt is 45; total assets and the measurement date are absent.",
            "The debt-to-total-assets ratio is 45/100, which is below 50 percent.",
            ["Total-assets denominator.", "Measurement date."],
        ),
        (
            "fee_aum_ratio",
            "For 2025, is the management fee divided by average AUM below 0.2 percent?",
            "Divide the management fee by average AUM and compare with 0.2 percent.",
            "For 2025, the management fee is 1.5 and average AUM is 1000.",
            "The management fee is 1.5; average AUM and the period are absent.",
            "The management-fee-to-average-AUM ratio is 1.5/1000, or 0.15 percent, "
            "which is below 0.2 percent.",
            ["Average-AUM denominator.", "Reporting period."],
        ),
        (
            "current_ratio",
            "At March 31, 2026, are current assets divided by current liabilities at least 1.25?",
            "Divide current assets by current liabilities and compare with 1.25.",
            "At March 31, 2026, current assets are 150 and current liabilities are 100.",
            "Current assets are 150; current liabilities and the measurement date are absent.",
            "The current ratio is 150/100, or 1.5, which is at least 1.25.",
            ["Current-liabilities denominator.", "Measurement date."],
        ),
    )
    question_id, prompt, criterion, complete, incomplete, summary, missing = cases[index]
    text = complete if known else incomplete
    source = DecisionSource(id="report", kind="document", text=text)
    question = PredicateQuestion(
        id=question_id,
        type="predicate",
        prompt=prompt,
        criteria=[criterion, "Both ratio inputs and the stated period are required."],
        allowedSourceIds=[source.id],
    )
    result = PredicateResult(
        questionId=question.id,
        type="predicate",
        answerability=_answerability(
            "answerable" if known else "not_answerable",
            *(() if known else ("missing_information",)),
        ),
        answer=PredicateAnswer(value="true" if known else "unknown"),
        explanation=_explanation(
            summary if known else "The ratio cannot be computed for the required period.",
            evidence=[_citation(source.id, text)] if known else [],
            missing=[] if known else missing,
        ),
    )
    return DecisionInput(
        state=DecisionState(sources=[source]), questions=[question]
    ), DecisionOutput(results=[result])


def _applicability_missing(index: int) -> tuple[DecisionInput, DecisionOutput]:
    cases = (
        (
            "Internal policy P applies from January 1, 2027 to firms in Country A.",
            "The case file does not identify whether the subject is a firm and omits its "
            "jurisdiction and event date.",
            ["Whether the subject is a firm.", "Case jurisdiction.", "Case event date."],
        ),
        (
            "Internal policy Q applies to fixed-rate products issued after July 1, 2026.",
            "The product was issued on August 4, 2026, but its rate type is not recorded.",
            ["Product rate type."],
        ),
        (
            "Internal policy R applies only to professional customers in Region B.",
            "The customer is in Region B, but the customer's classification is absent.",
            ["Customer classification."],
        ),
        (
            "Internal policy S applies to online transfers submitted from October 1, 2026.",
            "The transfer was submitted on October 3, 2026, but its channel is unavailable.",
            ["Transfer channel."],
        ),
    )
    policy, case, missing = cases[index]
    sources = [
        DecisionSource(id="internal-policy", kind="policy", text=policy),
        DecisionSource(id="case-file", kind="document", text=case),
    ]
    question = PredicateQuestion(
        id="rule_applies",
        type="predicate",
        prompt="Does the internal policy apply to this case?",
        criteria=["Every applicability condition in the policy must be resolved from the case."],
        allowedSourceIds=[source.id for source in sources],
    )
    result = PredicateResult(
        questionId=question.id,
        type="predicate",
        answerability=_answerability("not_answerable", "missing_information"),
        answer=PredicateAnswer(value="unknown"),
        explanation=_explanation(
            "At least one required applicability fact is absent from the case.", missing=missing
        ),
    )
    return DecisionInput(
        state=DecisionState(sources=sources), questions=[question]
    ), DecisionOutput(results=[result])


def _irrelevant_evidence(index: int) -> tuple[DecisionInput, DecisionOutput]:
    cases = (
        (
            "The report lists 18 branch offices.",
            "capital_requirement",
            "Was the required capital threshold satisfied?",
            "Required capital threshold and measured capital.",
        ),
        (
            "The company renewed its office lease for five years.",
            "liquidity_buffer",
            "Was the required liquidity buffer maintained?",
            "Required liquidity threshold and measured liquid assets.",
        ),
        (
            "The fund employs 42 people.",
            "fee_cap",
            "Was the management-fee cap respected?",
            "Applicable fee cap and charged management fee.",
        ),
        (
            "The payments team spent EUR 30,000 on marketing.",
            "settlement_timely",
            "Was the transfer settled within the required time?",
            "Required settlement deadline and actual settlement time.",
        ),
    )
    text, question_id, prompt, missing = cases[index]
    source = DecisionSource(id="unrelated-report", kind="document", text=text)
    question = PredicateQuestion(
        id=question_id,
        type="predicate",
        prompt=prompt,
        criteria=["Use only facts that measure both the stated requirement and the actual result."],
        allowedSourceIds=[source.id],
    )
    result = PredicateResult(
        questionId=question.id,
        type="predicate",
        answerability=_answerability("not_answerable", "missing_information"),
        answer=PredicateAnswer(value="unknown"),
        explanation=_explanation(
            "The supplied source does not address the required comparison.", missing=[missing]
        ),
    )
    return DecisionInput(
        state=DecisionState(sources=[source]), questions=[question]
    ), DecisionOutput(results=[result])


def build_research_case(
    scenario: str, index: int, *, language: str = "en"
) -> tuple[DecisionInput, DecisionOutput] | None:
    """Return one of four reviewed research cases for a supported scenario."""

    if scenario not in _SCENARIOS or index not in range(4):
        return None
    if scenario == "mixed-sufficiency":
        built = _mixed_sufficiency(index)
    elif scenario == "changed-deadline":
        built = _changed_deadline(index)
    elif scenario == "fund-ratio-answerable":
        built = _ratio_case(index, known=True)
    elif scenario == "fund-ratio-missing":
        built = _ratio_case(index, known=False)
    elif scenario == "legal-applicability-missing":
        built = _applicability_missing(index)
    else:
        built = _irrelevant_evidence(index)
    if language == "en":
        return built
    if language != "de":
        raise ValueError("research cases support only English and German")
    from .decision_german_research import RESEARCH_TRANSLATIONS
    from .decision_localization import localize_case

    return localize_case(*built, RESEARCH_TRANSLATIONS)
