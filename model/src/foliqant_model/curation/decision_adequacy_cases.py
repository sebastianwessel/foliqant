"""Finite task-grounded adequacy examples, without implicit execution requirements."""

from __future__ import annotations

from typing import Literal

from foliqant.decisions import (
    Answerability,
    Citation,
    DecisionInput,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    Explanation,
    PredicateAnswer,
    PredicateQuestion,
    PredicateResult,
)

# Each tuple defines a different task and four assessments: original state,
# proposed answer, and a reason checked against that task. These are synthetic
# exercises, not statements about actual customer obligations or regulations.
_CASES: tuple[tuple[str, dict[str, tuple[str, str, str]]], ...] = (
    (
        "Identify every requested action; do not perform the actions. For a fee-refund request "
        "also report its transaction reference, or unknown with the missing fact if no reference "
        "is supplied. Other execution details are not required. Do not add unrequested actions.",
        {
            "adequacy-complete": (
                "Please send my statement and update my postal address.",
                "Requested actions: send the statement; update the postal address.",
                "Both requested actions are listed. The task requires identification, not "
                "execution details, and the answer adds no other action.",
            ),
            "adequacy-omitted-request": (
                "Please send my statement and update my postal address.",
                "Requested actions: send the statement.",
                "The answer omits the explicitly requested postal-address update, so it fails "
                "the requirement to identify every requested action.",
            ),
            "adequacy-unsupported-evidence": (
                "Please send my statement.",
                "Requested actions: refund a fee. Evidence: Please send my statement.",
                "The statement request does not support a fee refund. The answer both adds an "
                "unrequested action and omits the actual request.",
            ),
            "adequacy-correct-unknown": (
                "Please refund the duplicate fee; the transaction reference is missing.",
                "Requested actions: refund the duplicate fee. Transaction reference: unknown; "
                "missing fact: the transaction reference.",
                "The refund action is preserved, and the absent transaction reference is "
                "reported as unknown with its missing fact, as the task requires.",
            ),
        },
    ),
    (
        "Extract revenue and expense from the supplied report in EUR. Return both named "
        "fields. Copy a stated value exactly; if a field is not supplied, return unknown "
        "for that field and name the missing fact. Do not substitute one field for another.",
        {
            "adequacy-complete": (
                "The report states revenue of EUR 120 and expense of EUR 20.",
                "Revenue: EUR 120. Expense: EUR 20.",
                "Both required report fields are present with their explicitly stated values "
                "and currency.",
            ),
            "adequacy-omitted-request": (
                "The report states revenue of EUR 120 and expense of EUR 20.",
                "Revenue: EUR 120.",
                "The answer omits expense even though the task requires both fields and the "
                "report supplies the expense value.",
            ),
            "adequacy-unsupported-evidence": (
                "The report states revenue of EUR 120 and expense of EUR 20.",
                "Revenue: EUR 120. Expense: EUR 120; evidence: revenue of EUR 120.",
                "Revenue is incorrectly used as the expense value. The report explicitly "
                "gives expense as EUR 20, so the proposed expense is unsupported.",
            ),
            "adequacy-correct-unknown": (
                "The report states revenue of EUR 120. It does not supply an expense value.",
                "Revenue: EUR 120. Expense: unknown; missing fact: the expense value.",
                "The answer preserves the known revenue and reports the absent expense as "
                "unknown with the required missing fact.",
            ),
        },
    ),
    (
        "Assess the supplied fictional rule using only the stated country and filing date. "
        "Return the rule identifier and whether it applies: true when all stated conditions "
        "hold, false when a condition is explicitly contradicted, otherwise unknown with "
        "the missing applicability fact. Do not infer an unstated country or date.",
        {
            "adequacy-complete": (
                "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. "
                "This report was filed in Country A on 2027-02-01.",
                "Rule identifier: R1. Applies: true.",
                "The answer names R1 and correctly finds both conditions satisfied: Country A "
                "and a filing date after the rule's start date.",
            ),
            "adequacy-omitted-request": (
                "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. "
                "This report was filed in Country A on 2027-02-01.",
                "Applies: true.",
                "The applicability result is supported, but the required rule identifier is "
                "missing, so the answer is incomplete.",
            ),
            "adequacy-unsupported-evidence": (
                "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. "
                "This report was filed in Country B on 2027-02-01.",
                "Rule identifier: R1. Applies: true; the report was filed in Country A.",
                "The answer invents Country A despite the explicit Country B filing. The "
                "country condition fails, so true is not supported.",
            ),
            "adequacy-correct-unknown": (
                "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. "
                "This report was filed on 2027-02-01; its filing country is not supplied.",
                "Rule identifier: R1. Applies: unknown; missing fact: the filing country.",
                "The answer names the rule and correctly leaves applicability unknown because "
                "the country condition cannot be checked from the supplied facts.",
            ),
        },
    ),
    (
        "Calculate each plan's annual total as twelve monthly charges plus its one-time setup "
        "charge. Return the annual total for both Plan A and Plan B in EUR, and identify the "
        "cheaper plan or a tie. Any total with a missing component is unknown; if a total is "
        "unknown, the comparison is also unknown. Name each missing component.",
        {
            "adequacy-complete": (
                "Plan A costs EUR 20 per month plus a EUR 10 setup charge. "
                "Plan B costs EUR 15 per month plus a EUR 5 setup charge.",
                "Plan A annual total: EUR 250. Plan B annual total: EUR 185. Cheaper: Plan B.",
                "The totals follow the supplied formula: 12 times 20 plus 10 is 250, and "
                "12 times 15 plus 5 is 185. Both totals and the cheaper plan are correct.",
            ),
            "adequacy-omitted-request": (
                "Plan A costs EUR 20 per month plus a EUR 10 setup charge. "
                "Plan B costs EUR 15 per month plus a EUR 5 setup charge.",
                "Plan A annual total: EUR 250. Cheaper: Plan B.",
                "The answer omits Plan B's required annual total, although its components "
                "are available. A correct comparison alone does not satisfy the full task.",
            ),
            "adequacy-unsupported-evidence": (
                "Plan A costs EUR 20 per month plus a EUR 10 setup charge. "
                "Plan B costs EUR 15 per month plus a EUR 5 setup charge.",
                "Plan A annual total: EUR 30. Plan B annual total: EUR 20. Cheaper: Plan B.",
                "The proposed totals use only one monthly charge plus setup. They do not "
                "follow the required twelve-month formula, despite the correct cheaper plan.",
            ),
            "adequacy-correct-unknown": (
                "Plan A costs EUR 20 per month plus a EUR 10 setup charge. "
                "Plan B has a EUR 5 setup charge; its monthly charge is not supplied.",
                "Plan A annual total: EUR 250. Plan B annual total: unknown; missing fact: "
                "Plan B's monthly charge. Cheaper: unknown because Plan B's total is unknown.",
                "The known Plan A total is calculated correctly. The answer preserves both "
                "required unknowns and names the missing Plan B monthly charge.",
            ),
        },
    ),
)


def build_adequacy_case(
    scenario: str, index: int, *, language: str = "en"
) -> tuple[DecisionInput, DecisionOutput]:
    """Build one of four distinct task assessments for the named adequacy scenario."""

    if not 0 <= index < len(_CASES):
        raise ValueError("adequacy case index is outside the authored catalog")
    contract, cases = _CASES[index]
    if scenario not in cases:
        raise ValueError("unknown authored adequacy scenario")
    original, proposed, reason = cases[scenario]
    sources = [
        DecisionSource(id="task-contract", kind="policy", text=contract),
        DecisionSource(id="original-state", kind="message", text=original),
        DecisionSource(id="proposed-answer", kind="document", text=proposed),
    ]
    question = PredicateQuestion(
        id="whole_answer_adequate",
        type="predicate",
        prompt="Does the proposed answer satisfy every requirement of the supplied task contract?",
        criteria=[
            "Check the proposed answer's correctness, completeness, supporting evidence, "
            "and compliance with the supplied task's required outputs and unknown behavior.",
            "Return true only if all requirements hold, false if a supplied fact establishes "
            "a failure, and unknown only if a required assessment fact is absent.",
            "The proposed answer is the object being assessed, not independent evidence that "
            "its assertions are true. Do not invent an execution or legal-contract requirement.",
        ],
        allowedSourceIds=[source.id for source in sources],
    )
    answer: Literal["true", "false"] = (
        "true" if scenario in {"adequacy-complete", "adequacy-correct-unknown"} else "false"
    )
    result = PredicateResult(
        questionId=question.id,
        type="predicate",
        answerability=Answerability(status="answerable", issues=[]),
        answer=PredicateAnswer(value=answer),
        explanation=Explanation(
            summary=reason,
            evidence=[Citation(sourceId=source.id, quote=source.text) for source in sources],
            contraryEvidence=[],
            missingFacts=[],
        ),
    )
    built = (
        DecisionInput(state=DecisionState(sources=sources), questions=[question]),
        DecisionOutput(results=[result]),
    )
    if language == "en":
        return built
    if language != "de":
        raise ValueError("adequacy cases support only English and German")
    from .decision_german_research import ADEQUACY_TRANSLATIONS
    from .decision_localization import localize_case

    return localize_case(*built, ADEQUACY_TRANSLATIONS)
