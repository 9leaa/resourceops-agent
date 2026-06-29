from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas import PlannedToolCall, ResourceType, ToolCatalog, ToolPermissionLevel, ToolPlan
from tools.registry import ToolRegistry


@dataclass(frozen=True)
class PlanValidationResult:
    """ToolPlan 校验结果。"""

    valid: bool
    errors: list[str] = field(default_factory=list)
    normalized_plan: ToolPlan | None = None


class PlanValidator:
    """校验 LLM 或 deterministic planner 生成的 ToolPlan。

    P9 的安全边界在这里：LLM 只能提出计划，不能直接执行计划。
    只有通过校验的计划才会交给 ResourceAgent 执行。
    """

    def __init__(
        self,
        *,
        tool_catalog: ToolCatalog,
        registry: ToolRegistry,
        max_steps: int = 8,
        allow_write_tools: bool = False,
        allow_dangerous_tools: bool = False,
    ) -> None:
        self.tool_catalog = tool_catalog
        self.registry = registry
        self.max_steps = max_steps
        self.allow_write_tools = allow_write_tools
        self.allow_dangerous_tools = allow_dangerous_tools

    def validate(self, plan: ToolPlan, expected_resource_type: ResourceType | None = None) -> PlanValidationResult:
        errors: list[str] = []
        normalized_steps: list[PlannedToolCall] = []
        catalog_by_name = {tool.name: tool for tool in self.tool_catalog.tools}
        seen_tools: set[str] = set()

        if expected_resource_type is not None and plan.resource_type != expected_resource_type:
            errors.append(
                f"resource_type mismatch: expected={expected_resource_type.value}, got={plan.resource_type}"
            )

        if not plan.steps:
            errors.append("plan has no steps")

        if len(plan.steps) > self.max_steps:
            errors.append(f"too many planned steps: {len(plan.steps)} > {self.max_steps}")

        for index, step in enumerate(plan.steps):
            if step.step_index != index:
                errors.append(f"step_index must be sequential: index={index}, got={step.step_index}")

            catalog_item = catalog_by_name.get(step.tool_name)
            if catalog_item is None:
                errors.append(f"unknown tool: {step.tool_name}")
                continue

            if step.tool_name in seen_tools:
                errors.append(f"duplicate tool in plan: {step.tool_name}")
            seen_tools.add(step.tool_name)

            if catalog_item.permission_level == ToolPermissionLevel.WRITE and not self.allow_write_tools:
                errors.append(f"write tool is not allowed in planner mode: {step.tool_name}")

            if catalog_item.permission_level == ToolPermissionLevel.DANGEROUS and not self.allow_dangerous_tools:
                errors.append(f"dangerous tool is not allowed in planner mode: {step.tool_name}")

            if catalog_item.requires_approval:
                errors.append(f"approval-required tool is not allowed in planner mode: {step.tool_name}")

            validated_args = self._validate_tool_args(step.tool_name, step.args, errors)
            normalized_steps.append(
                PlannedToolCall(
                    planned_call_id=step.planned_call_id,
                    step_index=index,
                    tool_name=step.tool_name,
                    args=validated_args,
                    reason=step.reason.strip() or "planner selected this tool",
                    expected_result=step.expected_result,
                    permission_level=catalog_item.permission_level,
                    requires_approval=catalog_item.requires_approval,
                    required=step.required,
                    tags=list(catalog_item.tags),
                )
            )

        if errors:
            return PlanValidationResult(valid=False, errors=errors)

        normalized_plan = ToolPlan(
            plan_id=plan.plan_id,
            planner_mode=plan.planner_mode,
            resource_type=plan.resource_type,
            user_question=plan.user_question,
            steps=normalized_steps,
            max_steps=len(normalized_steps),
            budget=normalize_budget(plan.budget, len(normalized_steps)),
            fallback_plan=plan.fallback_plan,
            tool_catalog_version=plan.tool_catalog_version or self.tool_catalog.catalog_version,
            created_at=plan.created_at,
        )
        return PlanValidationResult(valid=True, normalized_plan=normalized_plan)

    def _validate_tool_args(self, tool_name: str, args: dict[str, Any], errors: list[str]) -> dict[str, Any]:
        try:
            spec = self.registry.get(tool_name)
        except Exception as exc:  # noqa: BLE001 - validator reports registry boundary errors.
            errors.append(f"tool spec lookup failed for {tool_name}: {exc}")
            return args

        try:
            validated = spec.input_model.model_validate(args or {})
        except ValidationError as exc:
            errors.append(f"invalid args for {tool_name}: {exc.errors()}")
            return args

        return validated.model_dump(mode="json")


def normalize_budget(budget: dict[str, Any], step_count: int) -> dict[str, Any]:
    normalized = dict(budget or {})
    normalized["max_tool_calls"] = min(int(normalized.get("max_tool_calls") or step_count), step_count)
    return normalized
