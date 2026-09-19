#!/usr/bin/env python3
"""Preregister and run a bounded validation-only prefix ablation; never trains."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.configuration import load_config
from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord
from foliqant_model.curation.contracts import CurationConfig
from foliqant_model.curation.endpoint import _select_model, discover_models, generate_json
from foliqant_model.curation.environment import apply_curation_environment
from foliqant_model.curation.storage import load_object, run_lock, store_object
from foliqant_model.errors import ModelError
from foliqant_model.scoring import structural_equal

ROOT = Path(__file__).resolve().parents[1]
SEED = 20260919
SOURCES = ("banking77", "wanli", "tatqa")
ARMS = ("baseline", "task-key-user-v1", "instruct-query-user-v1")
PER_SOURCE = 8
TASKS = {
    "banking77": ("intent-classification", "Classify the online-banking request."),
    "wanli": ("evidence-assessment", "Assess the claim against the supplied evidence."),
    "tatqa": (
        "financial-question-answering",
        "Answer the financial-report question from the supplied source material.",
    ),
}
TEMPLATES = {
    "baseline": "{input}",
    "task-key-user-v1": "Task: {task}\nInput: {input}",
    "instruct-query-user-v1": "Instruct: {directive}\nQuery: {input}",
}


def messages_for(record: DataRecord, arm: str) -> list[ChatMessage]:
    """Apply an answer-neutral wrapper to only the final user message."""
    messages = list(record.messages[:-1])
    task, directive = TASKS[record.sourceId]
    content = TEMPLATES[arm].format(task=task, directive=directive, input=messages[-1].content)
    messages[-1] = ChatMessage(role="user", content=content)
    return messages


def schema_for(record: DataRecord) -> dict[str, Any]:
    """Derive output constraints from the public task contract, never its answer."""
    user = json.loads(record.messages[-2].content)
    if record.sourceId == "banking77":
        properties = {"intent": {"type": "string", "enum": user["labels"]}}
    elif record.sourceId == "wanli":
        properties = {
            "label": {"type": "string", "enum": [option["id"] for option in user["options"]]}
        }
    else:
        properties = {
            "answer": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "answerFrom": {"type": "string", "enum": ["table", "text", "table-text"]},
            "answerType": {"type": "string", "enum": ["span", "multi-span", "arithmetic", "count"]},
            "derivation": {"type": "string"},
            "scale": {"type": "string", "enum": ["", "thousand", "million", "billion", "percent"]},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def normalized_answer(value: Any) -> Any:
    """Secondary diagnostic only: normalize whitespace and one terminal period."""
    if isinstance(value, str):
        return " ".join(value.split()).removesuffix(".")
    if isinstance(value, list):
        return [normalized_answer(item) for item in value]
    return value


def score(record: DataRecord, output: dict[str, Any] | None) -> dict[str, bool]:
    """Keep full exact matching primary, with separate TAT-QA answer diagnostics."""
    expected = json.loads(record.messages[-1].content)
    exact = output is not None and structural_equal(output, expected)
    task_answer = exact
    annotations = exact
    if record.sourceId == "tatqa" and output is not None:
        task_answer = (
            structural_equal(
                normalized_answer(output.get("answer")), normalized_answer(expected["answer"])
            )
            and output.get("answerType") == expected["answerType"]
            and output.get("scale") == expected["scale"]
        )
        annotations = all(output.get(key) == expected[key] for key in ("answerFrom", "derivation"))
    return {
        "valid": output is not None,
        "exact": exact,
        "taskAnswer": task_answer,
        "annotations": annotations,
    }


def select_records(path: Path) -> list[DataRecord]:
    """Choose eight original English validation families per task before inference."""
    rows = [DataRecord.model_validate_json(line) for line in path.read_text().splitlines()]
    buckets: dict[str, list[DataRecord]] = {}
    seen: set[str] = set()
    for source in SOURCES:
        representatives: dict[str, DataRecord] = {}
        for row in sorted(rows, key=lambda item: item.id):
            if row.sourceId == source and row.language == "en":
                if row.generation is not None or row.familyId is None:
                    raise ValueError("Only original records with frozen families are eligible")
                representatives.setdefault(row.familyId, row)
        ranked = sorted(representatives, key=lambda family: canonical_digest([SEED, family]))
        chosen = [representatives[family] for family in ranked if family not in seen][:PER_SOURCE]
        if len(chosen) != PER_SOURCE:
            raise ValueError("Insufficient distinct validation families")
        seen.update(str(row.familyId) for row in chosen)
        buckets[source] = chosen
    return [buckets[source][index] for index in range(PER_SOURCE) for source in SOURCES]


def schedule(records: list[DataRecord]) -> list[tuple[DataRecord, str]]:
    """Balance all six arm orders globally, interleaving source tasks."""
    orders = list(itertools.permutations(ARMS))
    return [
        (record, arm)
        for index, record in enumerate(records)
        for arm in orders[(index // len(SOURCES) + 2 * (index % len(SOURCES))) % 6]
    ]


def paired_stats(wins: int, losses: int) -> dict[str, Any]:
    """One-sided exact sign test, with a 97.5% lower bound on discordant win chance."""
    n = wins + losses

    def tail(p: float) -> float:
        return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(wins, n + 1))

    probability = tail(0.5) if n else 1.0
    low, high = 0.0, 1.0
    if wins:
        for _ in range(80):
            middle = (low + high) / 2
            if tail(middle) < 0.025:
                low = middle
            else:
                high = middle
    return {
        "wins": wins,
        "losses": losses,
        "ties": 24 - n,
        "oneSidedP": probability,
        "discordantWinChanceLower97_5": low if n else None,
        "riskDifference": (wins - losses) / 24,
    }


def summarize(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute preregistered paired comparisons without selecting new examples."""
    result: dict[str, Any] = {"arms": {}, "comparisons": {}}
    lookup = {(row["recordId"], row["arm"]): row for row in outcomes}
    for arm in ARMS:
        rows = [row for row in outcomes if row["arm"] == arm]
        result["arms"][arm] = {
            **{
                metric: sum(row["scores"][metric] for row in rows)
                for metric in ("valid", "exact", "taskAnswer", "annotations")
            },
            "count": len(rows),
            "meanSeconds": statistics.mean(row["wallSeconds"] for row in rows),
            "medianSeconds": statistics.median(row["wallSeconds"] for row in rows),
            "meanInputCharacters": statistics.mean(row["inputCharacters"] for row in rows),
            "failures": dict(Counter(row["error"]["message"] for row in rows if "error" in row)),
            "perSource": {
                source: {
                    metric: sum(row["scores"][metric] for row in rows if row["source"] == source)
                    for metric in ("valid", "exact", "taskAnswer", "annotations")
                }
                for source in SOURCES
            },
        }
    baseline = [row for row in outcomes if row["arm"] == "baseline"]
    for arm in ARMS[1:]:
        metrics = {}
        for metric in ("exact", "taskAnswer"):
            pairs = [
                (base["scores"][metric], lookup[(base["recordId"], arm)]["scores"][metric])
                for base in baseline
            ]
            metrics[metric] = paired_stats(
                sum(not a and b for a, b in pairs), sum(a and not b for a, b in pairs)
            )
            metrics[metric]["bothCorrect"] = sum(a and b for a, b in pairs)
            metrics[metric]["bothIncorrect"] = sum(not a and not b for a, b in pairs)
        current = result["arms"][arm]
        base = result["arms"]["baseline"]
        qualified = (
            current["exact"] - base["exact"] >= 2
            and metrics["exact"]["oneSidedP"] <= 0.025
            and (metrics["exact"]["discordantWinChanceLower97_5"] or 0) > 0.5
            and current["valid"] >= base["valid"]
            and all(
                current["perSource"][s][metric] >= base["perSource"][s][metric]
                for s in SOURCES
                for metric in ("exact", "valid")
            )
            and current["perSource"]["tatqa"]["taskAnswer"]
            >= base["perSource"]["tatqa"]["taskAnswer"]
            and current["medianSeconds"] <= 1.2 * base["medianSeconds"]
        )
        result["comparisons"][arm] = {**metrics, "nominateForConfirmation": qualified}
    result["decision"] = (
        "confirm-on-disjoint-validation"
        if any(entry["nominateForConfirmation"] for entry in result["comparisons"].values())
        else "retain-baseline-no-demonstrated-benefit"
    )
    nominees = [arm for arm in ARMS[1:] if result["comparisons"][arm]["nominateForConfirmation"]]
    result["selectedForConfirmation"] = (
        min(
            nominees,
            key=lambda arm: (-result["arms"][arm]["exact"], -result["arms"][arm]["valid"], arm),
        )
        if nominees
        else None
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Run the frozen 72-call experiment")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Experiment data must stay outside the checkout")
    environment = dict(os.environ)
    for line in ROOT.joinpath(".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.startswith("FOLIQANT_CURATION_"):
                environment[key] = value
    config = apply_curation_environment(
        load_config(ROOT / "model/examples/curation.yaml", CurationConfig), environment
    )
    endpoint = config.endpoint.model_copy(update={"temperature": 0.0})
    dataset = args.dataset.expanduser().resolve()
    manifest = load_verified_artifact(dataset)
    if manifest.root.kind != "dataset":
        raise ValueError("Expected a verified dataset")
    validation = dataset / manifest.root.details.recordFiles.validation.path
    records = select_records(validation)
    identity = _select_model(endpoint, discover_models(endpoint))
    sequence = schedule(records)
    plan: dict[str, Any] = {
        "experiment": "task-prefix-ablation-v1",
        "seed": SEED,
        "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "datasetId": manifest.root.artifactId,
        "split": "validation",
        "selection": "smallest record ID per family, then SHA256([seed,family]); eight per source",
        "endpoint": endpoint.model_dump(mode="json"),
        "model": identity.model_dump(mode="json"),
        "taskMappings": TASKS,
        "templates": TEMPLATES,
        "maxCalls": 72,
        "adoption": "No default change: nominate only for disjoint validation confirmation if "
        "exact gain >=2/24, one-sided paired p<=.025, no source exact regression, no increase in "
        "invalid outputs globally or per source, no TAT-QA task-answer regression, and median "
        "wall latency <=120% of baseline.",
        "exclusions": [
            "test",
            "calibration",
            "train",
            "generated records",
            "authored scenarios",
            "typed-decisions",
            "multidogo-finance",
        ],
        "calls": [
            {
                "recordId": row.id,
                "recordSha256": canonical_digest(row.model_dump(mode="json")),
                "family": row.familyId,
                "source": row.sourceId,
                "arm": arm,
                "seed": int(canonical_digest([SEED, row.id])[:8], 16),
                "messages": [message.model_dump(mode="json") for message in messages_for(row, arm)],
                "schema": schema_for(row),
            }
            for row, arm in sequence
        ],
    }
    with run_lock(output):
        store_object(output / "plan.json", plan)
        if not args.execute:
            print(json.dumps({"status": "preregistered", "calls": 72, "output": str(output)}))
            return 0
        outcomes: list[dict[str, Any]] = []
        for index, ((record, arm), call) in enumerate(zip(sequence, plan["calls"], strict=True)):
            path = output / "calls" / f"{index:03}.json"
            if path.exists():
                stored = load_object(path)
                if not isinstance(stored, dict):
                    raise ValueError("Stored call must be an object")
                outcome = stored
                if outcome["callSha256"] != canonical_digest(call):
                    raise ValueError("Stored call identity mismatch")
                if outcome.get("fatal"):
                    raise ValueError(
                        "Prior uncertain transport failure; inspect server before a new run"
                    )
            else:
                started = time.monotonic()
                outcome = {
                    "recordId": record.id,
                    "source": record.sourceId,
                    "arm": arm,
                    "callSha256": canonical_digest(call),
                    "inputCharacters": sum(len(m.content) for m in messages_for(record, arm)),
                }
                response = None
                fatal = False
                try:
                    response = generate_json(
                        endpoint,
                        model_id=identity.modelId,
                        messages=messages_for(record, arm),
                        schema=schema_for(record),
                        seed=call["seed"],
                    )
                    if response.model.metadataSha256 != identity.metadataSha256:
                        raise ModelError(
                            "INTEGRITY_FAILED", "Endpoint model changed during experiment"
                        )
                    outcome["response"] = response.model_dump(mode="json")
                except ModelError as error:
                    outcome["error"] = {"code": error.code, "message": error.message}
                    fatal = error.code != "OUTPUT_INVALID"
                    outcome["fatal"] = fatal
                outcome["wallSeconds"] = time.monotonic() - started
                outcome["scores"] = score(record, None if response is None else response.output)
                store_object(path, outcome)
                if fatal:
                    raise ValueError(
                        "Stopped after endpoint failure; no retry or following request"
                    )
            outcomes.append(outcome)
            print(json.dumps({"completed": index + 1, "total": 72}), flush=True)
        report = {"planSha256": canonical_digest(plan), **summarize(outcomes)}
        store_object(output / "report.json", report)
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
