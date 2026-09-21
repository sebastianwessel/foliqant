"""Expose only a host-authorized tool session to a PydanticAI conversation."""

from typing import Any, cast

from pydantic_ai import Tool
from pydantic_ai.settings import ModelSettings

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import ToolPolicyPlan
from foliqant.ports.tools import ToolSession


class ModelTools:
    """Per-invocation tool state, never shared on a configured model binding."""

    def __init__(self, session: ToolSession, policy: ToolPolicyPlan) -> None:
        self.session = session
        self.policy = policy
        self.tools = [self._tool(name) for name in session.names]

    def _tool(self, name: str) -> Tool[None]:
        async def call(**arguments: Any) -> object:
            result = await self.session.call(name, cast(FrozenObject, freeze_json(arguments)))
            return thaw_json(result)

        schema = self.session.input_schema(name)
        description = schema.get("description")
        return Tool.from_schema(
            call,
            name=name,
            description=description
            if isinstance(description, str)
            else f"Call approved tool {name}.",
            json_schema=schema,
            # This prevents an emitted batch from starting unbounded MCP work.
            sequential=True,
        )

    def settings(self, original: ModelSettings | None) -> ModelSettings:
        """Require a successful function call once, then permit the final answer."""
        result = dict(original or {})
        if (
            self.policy.choice_mode == "named"
            and self.policy.choice_name not in self.session.successful
        ):
            if self.policy.choice_name is None:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            result["tool_choice"] = [self.policy.choice_name]
        elif self.policy.choice_mode == "required" and not self.session.successful:
            result["tool_choice"] = "required"
        else:
            result["tool_choice"] = "auto"
        return cast(ModelSettings, result)

    def validate_completion(self) -> None:
        if self.policy.choice_mode == "required" and not self.session.successful:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        if (
            self.policy.choice_mode == "named"
            and self.policy.choice_name not in self.session.successful
        ):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
