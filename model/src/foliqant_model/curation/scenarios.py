"""Deterministic code-authored regression scenarios for local curation."""

from __future__ import annotations

import json

from ..contracts.base import canonical_digest
from ..contracts.inputs import ChatMessage, DataRecord
from .contracts import CurationConfig, ImportedRecord, Scenario

_SOURCE_ID = "foliqant-scenarios"
_SCENARIO_PROMPT_VERSION = "authored-scenarios-v2"
_SYSTEM = (
    "Analyze only the authored facts in the user message. Apply this complete rule catalog: "
    "a withdrawn request maps to decision no-action and reason request-withdrawn; multiple "
    "intents map to decision manual-review and reason multiple-intents; missing evidence maps "
    "to decision request-evidence and reason missing-evidence; conflicting information maps to "
    "decision manual-review and reason conflicting-information; and a changed deadline maps to "
    "decision use-latest-deadline and reason changed-deadline. Return the requested JSON object, "
    "quote only evidence present in the facts, and do not invent missing evidence."
)


def _answer(decision: str, reason: str, evidence: str) -> str:
    return json.dumps(
        {"decision": decision, "evidence": [evidence], "reason": reason},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def scenario_recipe_digest() -> str:
    """Identify the authored scenario instructions and deterministic template version."""

    return canonical_digest(
        {
            "promptVersion": _SCENARIO_PROMPT_VERSION,
            "system": _SYSTEM,
            "taskShape": "decision-reason-evidence-with-exact-quotes",
        }
    )


def _scenario(scenario: Scenario, index: int, language: str) -> tuple[str, str]:
    amount = 1200 + index * 17
    first_day = index % 20 + 1
    later_day = first_day + 7
    first_date = f"2027-10-{first_day:02d}"
    later_date = f"2027-11-{later_day:02d}"
    reference = f"CASE-{index:04d}"
    if language == "de":
        task = (
            "Aufgabe: Entscheide anhand der Fakten. Gib ausschließlich JSON mit den Feldern "
            "decision, reason und evidence zurück; evidence enthält wörtliche Belege."
        )
        if scenario == "withdrawn-request":
            evidence = f"Bitte ziehen Sie den Auftrag {reference} vollständig zurück."
            facts = (
                f"Fakt 1: Am {first_date} wurde eine Zahlung über EUR {amount} beauftragt.\n"
                f'Fakt 2: Spätere Nachricht: "{evidence}"'
            )
            expected = _answer("no-action", "request-withdrawn", evidence)
        elif scenario == "multiple-intents":
            evidence = f"Zahlen Sie EUR {amount} und ändern Sie zugleich die Postanschrift."
            facts = f'Nachricht zu {reference}: "{evidence}" Frist: {later_date}.'
            expected = _answer("manual-review", "multiple-intents", evidence)
        elif scenario == "missing-evidence":
            evidence = f"Für die Erstattung von EUR {amount} liegt kein Beleg vor."
            facts = f'Vorgang {reference}: "{evidence}" Angefragtes Datum: {first_date}.'
            expected = _answer("request-evidence", "missing-evidence", evidence)
        elif scenario == "conflicting-information":
            evidence = f"Für {reference} werden gleichzeitig {first_date} und {later_date} genannt."
            facts = f'Konflikt: "{evidence}" Betrag: EUR {amount}.'
            expected = _answer("manual-review", "conflicting-information", evidence)
        else:
            evidence = f"Die neue Frist für {reference} ist {later_date}."
            facts = (
                f'Frühere Frist: {first_date}. Spätere Nachricht: "{evidence}" '
                f"Betrag: EUR {amount}."
            )
            expected = _answer("use-latest-deadline", "changed-deadline", evidence)
    else:
        task = (
            "Task: decide from the facts. Return only JSON with fields decision, reason, and "
            "evidence; evidence must contain exact supporting quotes."
        )
        if scenario == "withdrawn-request":
            evidence = f"Please withdraw request {reference} in full."
            facts = (
                f"Fact 1: A payment of EUR {amount} was requested on {first_date}.\n"
                f'Fact 2: Later message: "{evidence}"'
            )
            expected = _answer("no-action", "request-withdrawn", evidence)
        elif scenario == "multiple-intents":
            evidence = f"Pay EUR {amount} and also change the postal address."
            facts = f'Message for {reference}: "{evidence}" Deadline: {later_date}.'
            expected = _answer("manual-review", "multiple-intents", evidence)
        elif scenario == "missing-evidence":
            evidence = f"No receipt is available for the EUR {amount} refund."
            facts = f'Case {reference}: "{evidence}" Requested date: {first_date}.'
            expected = _answer("request-evidence", "missing-evidence", evidence)
        elif scenario == "conflicting-information":
            evidence = f"The case {reference} states both {first_date} and {later_date}."
            facts = f'Conflict: "{evidence}" Amount: EUR {amount}.'
            expected = _answer("manual-review", "conflicting-information", evidence)
        else:
            evidence = f"The new deadline for {reference} is {later_date}."
            facts = (
                f'Earlier deadline: {first_date}. Later message: "{evidence}" Amount: EUR {amount}.'
            )
            expected = _answer("use-latest-deadline", "changed-deadline", evidence)
    user = f"Authored facts:\n{facts}\n{task}"
    return user, expected


def scenario_records(config: CurationConfig) -> list[ImportedRecord]:
    """Create deterministic synthetic regression rows before any model generation."""

    records: list[ImportedRecord] = []
    for index in range(config.generation.scenarioFamilies):
        scenario = config.generation.scenarios[index % len(config.generation.scenarios)]
        language = config.generation.languages[index % len(config.generation.languages)]
        identity = canonical_digest({"index": index, "seed": config.seed})
        family_id = f"scenario-family-{identity[:48]}"
        record_id = f"scenario-{identity}"
        user, expected = _scenario(scenario, index, language)
        record = DataRecord(
            schemaVersion=1,
            id=record_id,
            sourceId=_SOURCE_ID,
            language=language,
            groupKeys=[family_id],
            messages=[
                ChatMessage(role="system", content=_SYSTEM),
                ChatMessage(role="user", content=user),
                ChatMessage(role="assistant", content=expected),
            ],
            tags=sorted(["authored-scenario", f"scenario:{scenario}", "synthetic-regression"]),
            origin="synthetic",
            reviewed=False,
            familyId=family_id,
        )
        records.append(
            ImportedRecord(
                record=record,
                originalSplit="unspecified",
                task="decision",
                originalId=f"scenario-{index:06d}",
            )
        )
    return records
