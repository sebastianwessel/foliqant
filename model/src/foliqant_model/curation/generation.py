"""Bounded local candidate generation with independent automatic checking."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from ..contracts.base import canonical_digest
from ..contracts.inputs import ChatMessage, DataRecord, GenerationProvenance
from ..errors import ModelError
from ..scoring import parse_strict_json, structural_equal
from .contracts import (
    CandidateCheck,
    CandidateJob,
    CandidateOutcome,
    CandidateText,
    CurationConfig,
)
from .endpoint import (
    EndpointModelIdentity,
    GenerationResponse,
    _generation_request_sha256,
    generate_json,
)
from .storage import ensure_directory, load_object, store_object
from .task_input import (
    assemble_input,
    candidate_structure_problem,
    editable_input,
    task_input_recipe,
    task_name,
)

_GENERATOR_PROMPT_VERSION = "candidate-scoped-input-v4"
_CHECKER_PROMPT_VERSION = "candidate-independent-check-v6"
_TRANSFORMATIONS = {
    "irrelevant-context": (
        "Rephrase the text and optionally add a short greeting or courtesy phrase. "
        "Do not invent unrelated events, people, amounts, dates or instructions."
    ),
    "default": "Rephrase the text naturally while preserving its exact task and meaning.",
}
_CHECKER_TASK = (
    "Independently solve candidateInput using instructions and the ordered prior messages in "
    "context. Put its exact complete final serialization in the outer answer string. When the "
    "task requests a JSON object, answer must contain that whole object with every requested "
    "field and the types specified by answerFormat. Judge support from the supplied facts and "
    "instructions, and leave issues empty unless the task itself is malformed."
)
_ANSWER_FORMATS = {
    "foliqant-scenarios": (
        "JSON object with decision: string, reason: string, evidence: array of exact quote "
        "strings. Always use an array for evidence, even for one quote."
    ),
    "banking77": "JSON object with intent: one string from the supplied labels.",
    "wanli": "JSON object with label: one id string from the supplied options.",
    "tatqa": (
        "JSON object with answer, answerFrom, answerType, derivation, scale. "
        "For span or multi-span extraction, answer is an array of exact source strings, "
        "even for one span. For arithmetic, answer is a JSON number. For count, answer is "
        "a string containing the count. answerFrom is table, text or table-text. answerType "
        "is span, multi-span, arithmetic or count. derivation is an arithmetic expression "
        "when calculation is required, otherwise an empty string. scale is an empty string, "
        "thousand, million, billion or percent, as stated in the source. Copy source spans "
        "exactly, including units and punctuation; do not paraphrase extracted answers."
    ),
}
_GENERATOR_SYSTEM = (
    "You edit one text field for a training example. Rewrite only textToRewrite according to "
    "the supplied transformation and language. The task field describes the eventual task; "
    "do not perform or answer that task. Preserve meaning, uncertainty, negation, chronology, "
    "every date, number, identifier, currency, unit, and every quoted span exactly. Preserve "
    "quoted spans even when translating surrounding wording. Do not add or remove facts. "
    "Do not include system instructions, role labels, a message array, task metadata, a rule "
    'catalog, an answer, or a copy of this request envelope. Return only {"input":"rewritten '
    'text"}; input is the text itself. Example: textToRewrite="Can I update my address?" '
    'may become {"input":"How do I change my address?"}.'
)
_CHECKER_SYSTEM = (
    "Solve the supplied candidate task independently using its instructions, the ordered prior "
    "messages in context, and candidateInput as the final user message. Respect answerFormat. "
    "The outer response field answer is a string containing the complete final "
    "answer required by candidateInput. Preserve the requested answer serialization exactly: if "
    "the task requests JSON, put the entire serialized JSON object in answer, including every "
    "required field and value. Never replace it with prose, a summary, or only one field such as "
    "a decision or reason. For an evidence or quote field, copy the complete exact text inside the "
    "relevant quotation marks in candidateInput, without the quote characters, a prefix, a "
    "paraphrase, or surrounding narration. Set supported=true when the answer you selected is "
    "grounded in the candidate facts and instructions. An abstention, contradiction finding, "
    "evidence request, or manual-review decision can be the fully supported intended answer. Put "
    "entries in issues only when the candidate task itself is malformed or cannot be answered as "
    "instructed; intentional conflicts, missing evidence, multiple intents, and other difficult "
    "facts are not issues when the task defines how to handle them. You are not given a reference "
    "answer or confidence label. Return only the requested outer structured object."
)
_GENERATOR_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"input": {"type": "string", "minLength": 1}},
    "required": ["input"],
    "additionalProperties": False,
}
_CHECKER_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "answer": {"type": "string", "minLength": 1},
        "supported": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["answer", "supported", "issues"],
    "additionalProperties": False,
}
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?![A-Za-z0-9])")


def generation_recipe_digest() -> str:
    """Identify every code-authored generation, checking, and scenario prompt recipe."""

    from .scenarios import scenario_recipe_digest

    return canonical_digest(
        {
            "checker": {
                "promptVersion": _CHECKER_PROMPT_VERSION,
                "schema": _CHECKER_SCHEMA,
                "system": _CHECKER_SYSTEM,
                "task": _CHECKER_TASK,
                "answerFormats": _ANSWER_FORMATS,
            },
            "generator": {
                "promptVersion": _GENERATOR_PROMPT_VERSION,
                "schema": _GENERATOR_SCHEMA,
                "system": _GENERATOR_SYSTEM,
                "transformations": _TRANSFORMATIONS,
            },
            "taskInput": task_input_recipe(),
            "scenarios": scenario_recipe_digest(),
        }
    )


_OUTPUT_FAILURE_SUFFIXES = {
    "Local endpoint returned a different model identity": "model-changed",
    "Local endpoint completion envelope is invalid": "envelope-invalid",
    "Local endpoint truncated the generated output": "output-truncated",
    "Local endpoint did not complete structured output": "output-incomplete",
    "Local endpoint completion message is invalid": "message-invalid",
    "Local endpoint refused structured generation": "refused",
    "Local endpoint returned unsupported tool calls": "tool-calls",
    "Local endpoint returned no structured output": "no-structured-output",
    "Local endpoint response is not strict JSON": "response-not-json",
    "Generated structured output is not strict JSON": "output-not-json",
    "Generated structured output must be an object": "output-not-object",
    "Generated output does not match its schema": "output-schema-invalid",
    "Local endpoint response exceeds the size limit": "response-too-large",
}


def _canonical_text(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _same_observed_model(expected: EndpointModelIdentity, observed: EndpointModelIdentity) -> bool:
    return (
        expected.modelId == observed.modelId and expected.metadataSha256 == observed.metadataSha256
    )


def _call_identity(
    config: CurationConfig,
    identity: EndpointModelIdentity,
    *,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
    prompt_version: str,
) -> dict[str, object]:
    return {
        "endpoint": config.endpoint.model_dump(mode="json"),
        "messages": [message.model_dump(mode="json") for message in messages],
        "model": identity.model_dump(mode="json"),
        "promptVersion": prompt_version,
        "schema": schema,
        "seed": seed,
    }


def _endpoint_request_sha256(
    config: CurationConfig,
    identity: EndpointModelIdentity,
    *,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
) -> str:
    return _generation_request_sha256(
        config.endpoint,
        model_id=identity.modelId,
        messages=messages,
        schema=schema,
        seed=seed,
    )


def _cached_generation(
    config: CurationConfig,
    identity: EndpointModelIdentity,
    *,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
    prompt_version: str,
    cache_dir: Path,
) -> tuple[str, GenerationResponse]:
    call_identity = _call_identity(
        config,
        identity,
        messages=messages,
        schema=schema,
        seed=seed,
        prompt_version=prompt_version,
    )
    call_id = canonical_digest(call_identity)
    calls_directory = cache_dir / "calls"
    ensure_directory(calls_directory)
    path = calls_directory / f"{call_id}.json"
    if path.exists() or path.is_symlink():
        payload = load_object(path)
        if not isinstance(payload, dict) or set(payload) != {"callIdentity", "response"}:
            raise ModelError("INTEGRITY_FAILED", "Cached generation record is invalid")
        if payload["callIdentity"] != call_identity:
            raise ModelError("INTEGRITY_FAILED", "Cached generation identity changed")
        try:
            response = GenerationResponse.model_validate(payload["response"], strict=True)
        except ValidationError as error:
            raise ModelError("INTEGRITY_FAILED", "Cached generation response is invalid") from error
    else:
        response = generate_json(
            config.endpoint,
            model_id=identity.modelId,
            messages=messages,
            schema=schema,
            seed=seed,
        )
        store_object(
            path,
            {
                "callIdentity": call_identity,
                "response": response.model_dump(mode="json"),
            },
        )
    if not _same_observed_model(identity, response.model):
        raise ModelError("INTEGRITY_FAILED", "Generation model identity changed")
    if response.schemaSha256 != canonical_digest(schema):
        raise ModelError("INTEGRITY_FAILED", "Generation schema identity changed")
    if response.requestSha256 != _endpoint_request_sha256(
        config,
        identity,
        messages=messages,
        schema=schema,
        seed=seed,
    ):
        raise ModelError("INTEGRITY_FAILED", "Generation request identity changed")
    return call_id, response


def _candidate_messages(parent: DataRecord, job: CandidateJob) -> list[ChatMessage]:
    transformation = _TRANSFORMATIONS.get(job.operation, _TRANSFORMATIONS["default"])
    payload = {
        "task": task_name(parent),
        "language": job.language,
        "transformation": transformation,
        "textToRewrite": editable_input(parent),
    }
    return [
        ChatMessage(role="system", content=_GENERATOR_SYSTEM),
        ChatMessage(role="user", content=_canonical_text(payload)),
    ]


def _checker_messages(candidate: str, parent: DataRecord, job: CandidateJob) -> list[ChatMessage]:
    payload = {
        "taskType": task_name(parent),
        "candidateInput": assemble_input(parent, candidate),
        "context": [
            message.model_dump(mode="json")
            for message in parent.messages[:-2]
            if message.role != "system"
        ],
        "instructions": [
            message.content for message in parent.messages[:-1] if message.role == "system"
        ],
        "language": job.language,
        "task": _CHECKER_TASK,
        "answerFormat": _ANSWER_FORMATS.get(
            parent.sourceId, "Follow the supplied task instructions."
        ),
    }
    return [
        ChatMessage(role="system", content=_CHECKER_SYSTEM),
        ChatMessage(role="user", content=_canonical_text(payload)),
    ]


def _expected_evidence(expected: str) -> list[str]:
    parsed = parse_strict_json(expected)
    if not parsed.valid or not isinstance(parsed.value, dict):
        return []
    evidence = parsed.value.get("evidence")
    if isinstance(evidence, str) and evidence:
        return [evidence]
    if isinstance(evidence, list) and all(isinstance(item, str) and item for item in evidence):
        return [item for item in evidence if isinstance(item, str)]
    return []


def _candidate_problem(parent: DataRecord, candidate: str, *, max_characters: int) -> str | None:
    if not candidate.strip():
        return "candidate-empty"
    if len(candidate) > max_characters:
        return "candidate-too-long"
    structure_problem = candidate_structure_problem(parent, candidate)
    if structure_problem is not None:
        return structure_problem
    expected = parent.messages[-1].content
    parsed_expected = parse_strict_json(expected)
    if (
        parsed_expected.valid
        and isinstance(parsed_expected.value, (dict, list))
        and _canonical_text(parsed_expected.value) in candidate
    ) or any(
        marker + expected.casefold() in candidate.casefold()
        for marker in ("reference answer: ", "expected answer: ")
    ):
        return "candidate-reference-leak"
    original = editable_input(parent)
    if Counter(_DATE.findall(original)) != Counter(_DATE.findall(candidate)):
        return "candidate-dates-changed"
    if Counter(_NUMBER.findall(original)) != Counter(_NUMBER.findall(candidate)):
        return "candidate-numbers-changed"
    original_quotes = re.findall(r'"([^"\n]+)"', original)
    if any(quote not in candidate for quote in original_quotes):
        return "candidate-quotes-changed"
    complete_input = assemble_input(parent, candidate)
    if len(complete_input) > max_characters:
        return "candidate-too-long"
    if any(evidence not in complete_input for evidence in _expected_evidence(expected)):
        return "candidate-evidence-missing"
    return None


def _answers_match(actual: str, expected: str) -> bool:
    actual_json = parse_strict_json(actual)
    expected_json = parse_strict_json(expected)
    if actual_json.valid and expected_json.valid:
        return structural_equal(actual_json.value, expected_json.value)
    return actual.strip() == expected.strip()


def _validate_job(parent: DataRecord, job: CandidateJob) -> None:
    if parent.id != job.parentRecordId or parent.familyId != job.familyId:
        raise ModelError("ARGUMENT_INVALID", "Candidate job does not match its parent")
    if job.purpose == "training-augmentation" and job.split != "train":
        raise ModelError("ARGUMENT_INVALID", "Training augmentation requires the train split")
    if job.purpose == "synthetic-regression" and parent.sourceId != "foliqant-scenarios":
        raise ModelError(
            "ARGUMENT_INVALID", "Synthetic regression requires an authored scenario parent"
        )
    if len("\n".join(message.content for message in parent.messages[:-1])) == 0:
        raise ModelError("ARGUMENT_INVALID", "Candidate parent has no input text")


def _outcome_digest(values: list[str], *, empty_reason: str) -> str:
    return canonical_digest(values if values else {"reason": empty_reason})


def _output_failure_reason(error: ModelError, *, phase: str) -> str:
    """Map only adapter-owned redacted messages to stable persisted reason codes."""

    suffix = _OUTPUT_FAILURE_SUFFIXES.get(error.message, "output-invalid")
    return f"{phase}-{suffix}"


def generate_candidate(
    config: CurationConfig,
    *,
    identity: EndpointModelIdentity,
    parent: DataRecord,
    job: CandidateJob,
    cache_dir: Path,
) -> CandidateOutcome:
    """Generate and independently check one candidate with immutable resumable calls."""

    _validate_job(parent, job)
    parent_characters = len("\n".join(message.content for message in parent.messages[:-1]))
    if parent_characters > config.generation.maxInputCharacters:
        reason = "parent-input-too-long"
        return CandidateOutcome(
            jobId=job.jobId,
            status="quarantined",
            reason=reason,
            # This is one completed validation attempt and made no endpoint request.
            attempts=1,
            requestSha256=_outcome_digest([], empty_reason="no-completed-request"),
            responseSha256=_outcome_digest([], empty_reason=reason),
            record=None,
        )
    expected = parent.messages[-1].content
    request_digests: list[str] = []
    response_digests: list[str] = []
    last_reason = "model-output-invalid"
    accepted: tuple[str, GenerationResponse, GenerationResponse, int] | None = None
    completed_attempts = 0
    seed_base = (config.seed + int(job.jobId[:8], 16)) % 4_294_967_296
    for attempt in range(1, config.generation.maxAttempts + 1):
        completed_attempts = attempt
        seed = (seed_base + attempt - 1) % 4_294_967_296
        generator_messages = _candidate_messages(parent, job)
        try:
            _call_id, generated = _cached_generation(
                config,
                identity,
                messages=generator_messages,
                schema=_GENERATOR_SCHEMA,
                seed=seed,
                prompt_version=_GENERATOR_PROMPT_VERSION,
                cache_dir=cache_dir,
            )
        except ModelError as error:
            if error.code != "OUTPUT_INVALID":
                raise
            request_digests.append(
                canonical_digest(
                    _call_identity(
                        config,
                        identity,
                        messages=generator_messages,
                        schema=_GENERATOR_SCHEMA,
                        seed=seed,
                        prompt_version=_GENERATOR_PROMPT_VERSION,
                    )
                )
            )
            last_reason = _output_failure_reason(error, phase="model")
            continue
        request_digests.append(_call_id)
        response_digests.append(generated.rawResponseSha256)
        try:
            candidate = CandidateText.model_validate(generated.output, strict=True).input
        except ValidationError:
            last_reason = "candidate-output-invalid"
            continue
        problem = _candidate_problem(
            parent,
            candidate,
            max_characters=config.generation.maxInputCharacters,
        )
        if problem is not None:
            last_reason = problem
            continue
        checker_messages = _checker_messages(candidate, parent, job)
        try:
            _check_call_id, checked = _cached_generation(
                config,
                identity,
                messages=checker_messages,
                schema=_CHECKER_SCHEMA,
                seed=(seed + 2_147_483_647) % 4_294_967_296,
                prompt_version=_CHECKER_PROMPT_VERSION,
                cache_dir=cache_dir,
            )
        except ModelError as error:
            if error.code != "OUTPUT_INVALID":
                raise
            request_digests.append(
                canonical_digest(
                    _call_identity(
                        config,
                        identity,
                        messages=checker_messages,
                        schema=_CHECKER_SCHEMA,
                        seed=(seed + 2_147_483_647) % 4_294_967_296,
                        prompt_version=_CHECKER_PROMPT_VERSION,
                    )
                )
            )
            last_reason = _output_failure_reason(error, phase="checker")
            continue
        request_digests.append(_check_call_id)
        response_digests.append(checked.rawResponseSha256)
        try:
            check = CandidateCheck.model_validate(checked.output, strict=True)
        except ValidationError:
            last_reason = "checker-output-invalid"
            continue
        if not check.supported:
            last_reason = "checker-unsupported"
            continue
        if check.issues:
            last_reason = "checker-reported-issues"
            continue
        if not _answers_match(check.answer, expected):
            last_reason = "checker-answer-mismatch"
            continue
        accepted = (candidate, generated, checked, seed)
        break

    outcome_request = _outcome_digest(request_digests, empty_reason="no-completed-request")
    outcome_response = _outcome_digest(response_digests, empty_reason=last_reason)
    if accepted is None:
        return CandidateOutcome(
            jobId=job.jobId,
            status="quarantined",
            reason=last_reason,
            attempts=completed_attempts,
            requestSha256=outcome_request,
            responseSha256=outcome_response,
            record=None,
        )

    candidate, generated, checked, accepted_seed = accepted
    prompt_digest = generation_recipe_digest()
    parameters_digest = canonical_digest(
        {
            "acceptedSeed": accepted_seed,
            "checkerSchema": _CHECKER_SCHEMA,
            "endpoint": config.endpoint.model_dump(mode="json"),
            "generatorSchema": _GENERATOR_SCHEMA,
            "job": job.model_dump(mode="json"),
        }
    )
    generation = GenerationProvenance(
        provider="openai-compatible",
        modelId=identity.modelId,
        modelIdentitySha256=identity.metadataSha256,
        promptSha256=prompt_digest,
        parametersSha256=parameters_digest,
        requestSha256=canonical_digest([generated.requestSha256, checked.requestSha256]),
        parentRecordIds=[parent.id],
    )
    prefix = parent.messages[:-2]
    try:
        record = DataRecord(
            schemaVersion=1,
            id=f"generated-{job.jobId}",
            sourceId=f"generated-{parent.sourceId}",
            language=job.language,
            groupKeys=list(parent.groupKeys),
            messages=[
                *prefix,
                ChatMessage(role="user", content=assemble_input(parent, candidate)),
                ChatMessage(role="assistant", content=expected),
            ],
            tags=list(parent.tags),
            origin="teacher",
            reviewed=False,
            familyId=job.familyId,
            generation=generation,
        )
    except ValidationError as error:
        raise ModelError(
            "OUTPUT_INVALID", "Generated record does not satisfy its contract"
        ) from error
    return CandidateOutcome(
        jobId=job.jobId,
        status="accepted",
        reason="automated-checks-passed",
        attempts=completed_attempts,
        requestSha256=outcome_request,
        responseSha256=outcome_response,
        record=record,
    )
