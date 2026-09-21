"""Evaluate support triage against in-code synthetic cases."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from typing import cast

from examples.local_qwen import local_qwen_profiles
from examples.support_triage.run import REPOSITORY_ROOT, compile_example, run_example
from foliqant.adapters.models import ModelExecutor
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue
from foliqant.core.runner import ExecutionLimits
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)


def suite() -> EvaluationSuite:
    """Construct a tiny golden suite in memory; no customer data is stored."""

    return EvaluationSuite(
        name="support_triage_smoke",
        revision="1",
        cases=(
            EvaluationCase(
                id="explicit_cancellation",
                envelope=Envelope(
                    payload={
                        "requestId": "eval-001",
                        "message": "Cancel renewal for account C-1049 by 30 September 2026.",
                    }
                ),
                expectations=(
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation(
                        "queue",
                        "/decisions/classify/result/answer/optionId",
                        "cancellation",
                    ),
                    Expectation("account", "/payload/account_reference", "C-1049"),
                ),
            ),
            EvaluationCase(
                id="explicit_billing_dispute",
                envelope=Envelope(
                    payload={
                        "requestId": "eval-002",
                        "message": "I dispute invoice INV-882. Please review the duplicate charge.",
                    }
                ),
                expectations=(
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation(
                        "queue",
                        "/decisions/classify/result/answer/optionId",
                        "billing_dispute",
                    ),
                    Expectation("account_absent", "/payload/account_reference", None),
                ),
            ),
        ),
    )


async def run_live() -> dict[str, JsonValue]:
    """Run sequentially against the exact local profile and return a safe report."""

    profiles = local_qwen_profiles(REPOSITORY_ROOT, os.environ)
    plan = compile_example({alias: profile.model for alias, profile in profiles.models.items()})
    schemas = WorkflowSchemas(plan)
    profile = profiles.models["local_qwen"]
    case_timeout = profile.request_timeout * 2 + 10
    configuration_revision = hashlib.sha256(
        json.dumps(
            {
                "workflow_revision": plan.revision,
                "profile": profile.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    logging_runtime = configure_logging()
    failed = False
    try:
        async with open_model_bindings(profiles, environment=os.environ) as bindings:
            executor = ModelExecutor(bindings, schemas)

            async def invoke(envelope: Envelope) -> ExecutionResult:
                payload = envelope.model_dump(mode="json")["payload"]
                return await run_example(
                    cast(dict[str, JsonValue], payload),
                    plan=plan,
                    executor=executor,
                    limits=ExecutionLimits(
                        run_timeout=case_timeout,
                        model_timeout=profile.request_timeout,
                    ),
                )

            report = await evaluate(
                suite(),
                EvaluationVariant(
                    name="local_qwen",
                    revision=profile.model,
                    configuration_revision=configuration_revision,
                    run=invoke,
                ),
                max_concurrency=1,
                timeout=case_timeout,
            )
    except BaseException:
        failed = True
        raise
    finally:
        clean = await asyncio.to_thread(logging_runtime.close, timeout=1.0)
        if not clean and not failed:
            raise RuntimeError("logging drain failed")
    return report.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run the configured local model")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.print_help()
        return 0
    try:
        report = asyncio.run(run_live())
    except KeyboardInterrupt:
        return 130
    except ServiceError as error:
        print(
            json.dumps({"error": {"code": error.code, "message": str(error)}}),
            file=sys.stderr,
        )
        return 1
    except Exception:
        fallback = ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        print(
            json.dumps({"error": {"code": fallback.code, "message": str(fallback)}}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
