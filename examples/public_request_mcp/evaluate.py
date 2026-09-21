"""Evaluate the real local MCP workflow and its isolated lookup step."""

from examples.common import command, evaluation_output
from examples.public_request_mcp.run import CONFIG_PATH, open_example
from foliqant import Envelope, ExecutionResult, prepare_application
from foliqant.core.json import JsonValue
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)


def suite() -> EvaluationSuite:
    return EvaluationSuite(
        name="public_request_lookup",
        revision="1",
        cases=tuple(
            EvaluationCase(
                f"request_{index}",
                Envelope(payload={"reference": reference}),
                (
                    Expectation("completed", "/execution/status", "completed"),
                    Expectation("reference", "/payload/reference", reference),
                    Expectation("status", "/payload/status", "in_review"),
                    Expectation("deadline", "/payload/due_date", "2026-10-05"),
                    Expectation("team", "/payload/assigned_team", "records_review"),
                    Expectation("one_tool", "/execution/usage/tool_calls", 1),
                    Expectation("no_model", "/execution/usage/model_requests", 0),
                ),
            )
            for index, reference in enumerate(("FOI-2026-0142", "FOI-2026-0310"))
        ),
    )


async def run_evaluations() -> dict[str, JsonValue]:
    prepared = prepare_application(CONFIG_PATH)
    async with open_example(prepared) as app:
        reports = []
        for isolated in (False, True):

            async def invoke(envelope: Envelope, step_only: bool = isolated) -> ExecutionResult:
                if step_only:
                    return await app.run_step("public_request_lookup", "lookup", envelope)
                return await app.run("public_request_lookup", envelope)

            reports.append(
                await evaluate(
                    suite(),
                    EvaluationVariant(
                        name="lookup_step" if isolated else "lookup_pipeline",
                        revision="1",
                        configuration_revision=prepared.configuration_digest,
                        run=invoke,
                    ),
                    timeout=10,
                )
            )
    return evaluation_output(reports, mode="local_stdio")


if __name__ == "__main__":
    raise SystemExit(command(run_evaluations))
