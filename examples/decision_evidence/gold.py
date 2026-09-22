"""Small authored cases; ratings express this example's reviewed task interpretation.

Family IDs keep translations and invariance variants together. The limited cases
are judgments about suggested communicative purpose, not universal annotations
or phrase-to-strength rules. None of these records is a model prediction.
"""

from dataclasses import dataclass
from typing import Literal

from foliqant.core.json import JsonValue

QUESTION_IDS = ("triage", "labels", "dispute", "priority", "requests")
type Strength = Literal["limited", "strong"]
type Status = Literal["answerable", "partially_answerable", "not_answerable"]


@dataclass(frozen=True)
class Assessment:
    """Business gold excludes generated reason wording and request descriptions."""

    answer: JsonValue
    strength: Strength = "strong"
    status: Status = "answerable"
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    language: str
    message: str
    assessments: tuple[Assessment, ...]
    rationale: str


def choice(value: str, strength: Strength = "strong") -> Assessment:
    return Assessment({"optionId": value}, strength)


def labels(
    *values: str,
    strength: Strength = "strong",
    status: Status = "answerable",
    issues: tuple[str, ...] = (),
) -> Assessment:
    selected: list[JsonValue] = list(values)
    return Assessment({"optionIds": selected}, strength, status, issues)


def predicate(value: str) -> Assessment:
    return Assessment(
        {"value": value},
        status="not_answerable" if value == "unknown" else "answerable",
        issues=("no_supported_answer",) if value == "unknown" else (),
    )


def priority(value: str) -> Assessment:
    return Assessment({"levelId": value})


def abstain(issue: str = "no_supported_answer", strength: Strength = "strong") -> Assessment:
    return Assessment(None, strength, "not_answerable", (issue,))


def requests(
    *values: tuple[str, str | None],
    status: Status = "answerable",
    issues: tuple[str, ...] = (),
) -> Assessment:
    units: list[JsonValue] = [
        {"categoryId": category, "subject": subject, "status": "active"}
        for category, subject in values
    ]
    return Assessment({"units": units, "relations": []}, status=status, issues=issues)


_DIRECT = "Freeze my card. There is no urgency. I am not disputing any charge."
_DIRECT_GOLD = (
    choice("freeze_card"),
    labels("freeze_card"),
    predicate("false"),
    priority("routine"),
    requests(("freeze_card", None)),
)
_MULTI_GOLD = (
    abstain("multiple_valid_options"),
    labels("freeze_card", "send_statement"),
    predicate("false"),
    priority("time_sensitive"),
    requests(("freeze_card", None), ("send_statement", None)),
)

DEVELOPMENT: tuple[Case, ...] = (
    Case(
        "direct",
        "direct_and_invariance",
        "en",
        _DIRECT,
        _DIRECT_GOLD,
        "An explicit action and explicit lack of urgency support different answers strongly.",
    ),
    Case(
        "direct_noise",
        "direct_and_invariance",
        "en",
        _DIRECT + " The unrelated brochure discusses urgent statement delivery as a topic.",
        _DIRECT_GOLD,
        "An unrelated topic does not add a communicative purpose or urgency.",
    ),
    Case(
        "direct_repeated",
        "direct_and_invariance",
        "en",
        _DIRECT + " " + _DIRECT,
        _DIRECT_GOLD,
        "Repeating the same instruction does not create another action or more support.",
    ),
    Case(
        "two_requests",
        "two_positive_purposes",
        "en",
        "Freeze my card and send my statement by 5 pm today. I am not disputing any charge.",
        _MULTI_GOLD,
        "Two positive purposes decisively exceed choice cardinality; abstention is strong.",
    ),
    Case(
        "two_requests_de",
        "two_positive_purposes",
        "de",
        "Sperren Sie meine Karte und senden Sie meinen Kontoauszug bis heute 17 Uhr. "
        "Ich beanstande keine Belastung.",
        _MULTI_GOLD,
        "German translation stays with its English family, including strong choice abstention.",
    ),
    Case(
        "no_action",
        "explicit_no_action",
        "en",
        "No card or statement action is wanted. This is a routine status note. "
        "I am not disputing any charge.",
        (abstain(), labels(), predicate("false"), priority("routine"), requests()),
        "Explicit no-action supports empty collections, false predicate, and choice abstention.",
    ),
    Case(
        "partial_conflict",
        "conflicted_card_subset",
        "en",
        "Send my statement. Freeze my card. Do not freeze my card. Neither card instruction "
        "withdraws or overrides the other. No work is urgent. I am not disputing any charge.",
        (
            abstain("conflicting_information"),
            labels(
                "send_statement", status="partially_answerable", issues=("conflicting_information",)
            ),
            predicate("false"),
            priority("routine"),
            requests(
                ("send_statement", None),
                status="partially_answerable",
                issues=("conflicting_information",),
            ),
        ),
        "The returned subset and the unresolved card conflict are both explicit: partial, strong.",
    ),
    Case(
        "explicit_correction",
        "correction_precedence",
        "en",
        "Earlier I requested a card freeze. Correction: withdraw that freeze request; "
        "send my statement instead, whenever convenient. I am not disputing any charge.",
        (
            choice("send_statement"),
            labels("send_statement"),
            predicate("false"),
            priority("routine"),
            requests(("send_statement", None)),
        ),
        "Explicit correction resolves precedence; sequence alone would not.",
    ),
    Case(
        "explicit_missing",
        "explicitly_absent_facts",
        "en",
        "Please handle my issue. The intended card or statement action has not been supplied. "
        "No timing information is available. Whether I dispute a charge has not been stated.",
        (abstain(), abstain(), predicate("unknown"), abstain(), abstain()),
        "The source explicitly establishes each essential information gap: strong abstentions.",
    ),
    Case(
        "suggested_purpose",
        "statement_access_interpretation",
        "en",
        "I am trying to review this month's transactions, but there is no statement in my inbox. "
        "I have not settled on an action to request. No timing or charge-dispute information "
        "is available.",
        (
            choice("send_statement"),
            labels("send_statement"),
            predicate("unknown"),
            abstain(),
            abstain(),
        ),
        "The stated transaction-review goal and explicitly missing statement decisively "
        "support the broad statement-obtaining purpose. An unsettled remedy does not weaken "
        "that purpose, but no actionable instruction is established.",
    ),
    Case(
        "mixed_purposes",
        "mixed_explicit_and_suggested",
        "en",
        "Freeze my card. Separately, I am trying to review this month's transactions, but "
        "there is no statement in my inbox. I have not settled on an action to request about "
        "the statement. There is no urgency. I am not disputing any charge.",
        (
            abstain("multiple_valid_options"),
            labels("freeze_card", "send_statement"),
            predicate("false"),
            priority("routine"),
            requests(("freeze_card", None)),
        ),
        "Both communication purposes are clear even though only the freeze is an established "
        "actionable instruction. The broad statement purpose does not require a chosen remedy; "
        "its coexistence with the freeze strongly supports single-choice abstention.",
    ),
    Case(
        "tentative_document",
        "uncertain_document_purpose",
        "en",
        "Some monthly paperwork from my bank is missing. I vaguely remember it listing "
        "transactions, but I am not sure; it may instead have been another kind of bank notice. "
        "I would like help with this difficulty, but I have not decided what action to request. "
        "No timing information is available. Whether I dispute a charge has not been stated.",
        (
            choice("send_statement", "limited"),
            labels("send_statement", strength="limited"),
            predicate("unknown"),
            abstain(),
            abstain(),
        ),
        "A positive but uncertain recollection suggests the statement-purpose category; "
        "the document could instead be a different kind of notice. The best-fit criteria "
        "permit this limited interpretation, not a claim that the document is a statement. "
        "The action and other missing facts are explicitly unresolved. This is an authored "
        "business judgment, not a universal label for the wording.",
    ),
    Case(
        "tentative_document_de",
        "uncertain_document_purpose",
        "de",
        "Mir fehlen monatliche Unterlagen meiner Bank. Ich erinnere mich vage daran, dass "
        "darin Transaktionen aufgelistet waren, bin mir aber nicht sicher; es könnte "
        "stattdessen eine andere Art von Bankmitteilung gewesen sein. Ich möchte Hilfe "
        "bei diesem Problem, habe aber noch nicht entschieden, welche Maßnahme ich "
        "anfordern möchte. Es liegen keine Angaben zum Zeitbedarf vor. Ob ich eine "
        "Belastung beanstande, wurde nicht angegeben.",
        (
            choice("send_statement", "limited"),
            labels("send_statement", strength="limited"),
            predicate("unknown"),
            abstain(),
            abstain(),
        ),
        "Same uncertain document-purpose judgment as the English case. Translation remains "
        "in the development family, with no invented statement fact or actionable instruction.",
    ),
    Case(
        "mixed_tentative_document",
        "uncertain_document_purpose",
        "en",
        "Freeze my card. Separately, some monthly paperwork from my bank is missing. I "
        "vaguely remember it listing transactions, but I am not sure; it may instead have "
        "been another kind of bank notice. I would like help with that difficulty, but I "
        "have not decided what action to request about the document. There is no urgency. "
        "I am not disputing any charge.",
        (
            abstain("multiple_valid_options", "limited"),
            labels("freeze_card", "send_statement", strength="limited"),
            predicate("false"),
            priority("routine"),
            requests(("freeze_card", None)),
        ),
        "The freeze purpose is explicit, while the document-purpose category depends on "
        "the same tentative interpretation. Both the collection and the two-purpose "
        "single-choice abstention therefore have limited overall support. Only the freeze "
        "is an established actionable instruction. This context variant stays in the same "
        "family, and the limited rating remains an authored business judgment.",
    ),
    Case(
        "policy_inference",
        "supplied_rule_inference",
        "en",
        "My card C-42 was stolen. The supplied response rule says every stolen card must be "
        "frozen. Please carry out that rule for C-42 now. I am not disputing any charge.",
        (
            choice("freeze_card"),
            labels("freeze_card"),
            predicate("false"),
            priority("time_sensitive"),
            requests(("freeze_card", "C-42")),
        ),
        "The supplied fact plus rule and instruction entail the action. "
        "Indirect support is strong.",
    ),
)

# These families are reserved for evaluation after prompt selection. Do not use
# their messages or failed responses as prompt examples and still call them held out.
VALIDATION: tuple[Case, ...] = (
    Case(
        "separate_accounts",
        "distinct_same_category_subjects",
        "en",
        "Send the statement for account S-71 and the statement for account S-72 whenever "
        "convenient. I dispute the duplicate fee.",
        (
            choice("send_statement"),
            labels("send_statement"),
            predicate("true"),
            priority("routine"),
            requests(("send_statement", "S-71"), ("send_statement", "S-72")),
        ),
        "Two distinct subjects require two units but only one category; "
        "charge dispute is separate.",
    ),
    Case(
        "separate_accounts_de",
        "distinct_same_category_subjects",
        "de",
        "Senden Sie die Kontoauszüge für Konto S-71 und Konto S-72, wenn es Ihnen passt. "
        "Ich beanstande die doppelte Gebühr.",
        (
            choice("send_statement"),
            labels("send_statement"),
            predicate("true"),
            priority("routine"),
            requests(("send_statement", "S-71"), ("send_statement", "S-72")),
        ),
        "Same held-out family and expectations as the English distinct-subject case.",
    ),
    Case(
        "only_conflict",
        "complete_unresolved_conflict",
        "en",
        "Freeze my card. Do not freeze my card. Both instructions remain in force; neither "
        "takes precedence. No statement is wanted. There is no urgency. "
        "I am not disputing any charge.",
        (
            abstain("conflicting_information"),
            abstain("conflicting_information"),
            predicate("false"),
            priority("routine"),
            abstain("conflicting_information"),
        ),
        "No unconflicted purpose/action survives; all three abstentions have decisive support.",
    ),
    Case(
        "only_conflict_de",
        "complete_unresolved_conflict",
        "de",
        "Sperren Sie meine Karte. Sperren Sie meine Karte nicht. Beide Anweisungen gelten "
        "weiter; keine hat Vorrang. Ein Kontoauszug ist nicht gewünscht. Es besteht keine "
        "Eile. Ich beanstande keine Belastung.",
        (
            abstain("conflicting_information"),
            abstain("conflicting_information"),
            predicate("false"),
            priority("routine"),
            abstain("conflicting_information"),
        ),
        "Same held-out conflict family; translation is not an independent validation family.",
    ),
)

# Same source and same predicate gold in a one-question request exposes task mixing.
ISOLATED_PREDICATE_CASES = ("two_requests_de", "explicit_missing")
