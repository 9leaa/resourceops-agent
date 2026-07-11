from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from agent.llm_client import LlmClient
from agent.plan_validator import PlanValidator
from app.schemas import PlannedToolCall, PlannerMode, ResourceType, ToolCatalog, ToolPermissionLevel, ToolPlan
from trace.llm_calls import build_llm_call_record


SYSTEM_PROMPT = """你是 ResourceOps 的工具规划器。
你只能根据工具目录选择工具，输出必须是 JSON。
你不能编造工具名，不能要求执行命令，不能选择 write/dangerous/approval-required 工具。
你只负责提出诊断采集计划，不负责生成报告，不负责下结论。"""


@dataclass(frozen=True)
class LlmPlanResult:
    """LLM planner 的结果。

    tool_plan 是最终会被执行的计划。只有 LLM 候选计划通过 PlanValidator 时，
    tool_plan 才会来自 LLM；否则 tool_plan 是 deterministic fallback。
    """

    tool_plan: ToolPlan
    source: str
    status: str
    llm_called: bool
    fallback_reason: str | None = None
    prompt_length: int = 0
    response_length: int = 0
    response_preview: str | None = None
    latency_ms: int = 0
    error_type: str | None = None
    error: str | None = None
    validation_errors: list[str] | None = None
    candidate_plan: dict[str, Any] | None = None
    llm_call: dict[str, Any] | None = None

    @property
    def used_llm_plan(self) -> bool:
        return self.source == "llm" and self.status == "success"

    @property
    def preview(self) -> str:
        if self.used_llm_plan:
            return f"llm plan accepted; steps={len(self.tool_plan.steps)}"
        if self.fallback_reason:
            return f"fallback to deterministic plan: {self.fallback_reason}"
        return "fallback to deterministic plan"

    def model_dump(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "llm_called": self.llm_called,
            "used_llm_plan": self.used_llm_plan,
            "fallback_reason": self.fallback_reason,
            "prompt_length": self.prompt_length,
            "response_length": self.response_length,
            "response_preview": self.response_preview,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error": self.error,
            "validation_errors": self.validation_errors or [],
            "candidate_plan": self.candidate_plan,
            "selected_plan": self.tool_plan.model_dump(mode="json"),
            "llm_call": self.llm_call,
        }


def build_llm_tool_plan_result(
    *,
    description: str,
    resource_type: ResourceType,
    tool_catalog: ToolCatalog,
    fallback_plan: ToolPlan,
    validator: PlanValidator,
    llm_client: LlmClient | None,
    max_steps: int = 8,
) -> LlmPlanResult:
    fallback = fallback_tool_plan(fallback_plan)

    if llm_client is None:
        return LlmPlanResult(
            tool_plan=fallback,
            source="deterministic",
            status="fallback",
            llm_called=False,
            fallback_reason="no_llm_client",
        )

    prompt = build_planner_prompt(
        description=description,
        resource_type=resource_type,
        tool_catalog=tool_catalog,
        max_steps=max_steps,
    )
    response = ""
    candidate_plan: ToolPlan | None = None
    started = time.perf_counter()

    try:
        response = call_llm_planner(llm_client, prompt)
        payload = parse_planner_json(response)
        candidate_plan = tool_plan_from_llm_payload(
            payload=payload,
            description=description,
            resource_type=resource_type,
            tool_catalog=tool_catalog,
            fallback_plan=fallback_plan,
        )
        validation = validator.validate(candidate_plan, expected_resource_type=resource_type)
        scope_errors = plan_resource_scope_errors(candidate_plan, tool_catalog, resource_type)
        if not validation.valid or validation.normalized_plan is None or scope_errors:
            latency_ms = max(1, int((time.perf_counter() - started) * 1000))
            return LlmPlanResult(
                tool_plan=fallback,
                source="deterministic",
                status="fallback",
                llm_called=True,
                fallback_reason="plan_validation_failed",
                prompt_length=len(prompt),
                response_length=len(response),
                response_preview=preview_text(response),
                latency_ms=latency_ms,
                validation_errors=[*validation.errors, *scope_errors],
                candidate_plan=candidate_plan.model_dump(mode="json"),
                llm_call=build_llm_call_record(
                    purpose="planner",
                    client=llm_client,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    response=response,
                    status="validation_failed",
                    latency_ms=latency_ms,
                ),
            )

        accepted_plan = validation.normalized_plan.model_copy(
            update={"fallback_plan": fallback_plan.steps}
        )
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return LlmPlanResult(
            tool_plan=accepted_plan,
            source="llm",
            status="success",
            llm_called=True,
            prompt_length=len(prompt),
            response_length=len(response),
            response_preview=preview_text(response),
            latency_ms=latency_ms,
            candidate_plan=candidate_plan.model_dump(mode="json"),
            llm_call=build_llm_call_record(
                purpose="planner",
                client=llm_client,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                response=response,
                status="success",
                latency_ms=latency_ms,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - LLM boundary must fallback safely.
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return LlmPlanResult(
            tool_plan=fallback,
            source="deterministic",
            status="fallback",
            llm_called=True,
            fallback_reason=exc.__class__.__name__,
            prompt_length=len(prompt),
            response_length=len(response),
            response_preview=preview_text(response) if response else None,
            latency_ms=latency_ms,
            error_type=exc.__class__.__name__,
            error=str(exc),
            candidate_plan=candidate_plan.model_dump(mode="json") if candidate_plan else None,
            llm_call=build_llm_call_record(
                purpose="planner",
                client=llm_client,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                response=response,
                status="failed",
                latency_ms=latency_ms,
                error_type=exc.__class__.__name__,
                error=str(exc),
            ),
        )


def build_planner_prompt(
    *,
    description: str,
    resource_type: ResourceType,
    tool_catalog: ToolCatalog,
    max_steps: int,
) -> str:
    tools = scoped_catalog_tools(tool_catalog, resource_type)
    catalog_payload = {
        "catalog_version": tool_catalog.catalog_version,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "permission_level": tool.permission_level,
                "requires_approval": tool.requires_approval,
                "input_schema": compact_input_schema(tool.input_schema),
                "tags": tool.tags,
                "resource_types": tool.resource_types,
            }
            for tool in tools
        ],
    }
    return f"""请为 ResourceOps 生成一个工具调用计划。

用户问题：
{description}

诊断资源类型：
{resource_type.value}

计划约束：
1. 最多 {max_steps} 步。
2. 只能选择工具目录里存在的工具。
3. 只能选择 permission_level=safe 且 requires_approval=false 的工具。
4. args 必须符合 input_schema；没有参数就写 {{}}。
5. 不要输出 Markdown，不要解释，只输出 JSON 对象。
6. JSON 顶层必须包含 steps 数组。
7. 每个 step 只能包含 tool_name、args、reason、expected_result。
8. 当前资源类型不是 mixed 时，只能选择 resource_types 包含当前资源类型的工具。

输出格式：
{{
  "steps": [
    {{
      "tool_name": "get_cpu_snapshot",
      "args": {{}},
      "reason": "为什么需要这个工具",
      "expected_result": "期望拿到什么信息"
    }}
  ]
}}

工具目录 JSON：
```json
{json.dumps(catalog_payload, ensure_ascii=False, indent=2)}
```
"""


def tool_plan_from_llm_payload(
    *,
    payload: dict[str, Any],
    description: str,
    resource_type: ResourceType,
    tool_catalog: ToolCatalog,
    fallback_plan: ToolPlan,
) -> ToolPlan:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("planner JSON must contain a steps array")

    catalog_by_name = {tool.name: tool for tool in tool_catalog.tools}
    steps: list[PlannedToolCall] = []

    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise ValueError(f"step must be an object: index={index}")

        tool_name = str(raw_step.get("tool_name") or raw_step.get("action") or "").strip()
        args = raw_step.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"step args must be an object: index={index}")

        catalog_item = catalog_by_name.get(tool_name)
        permission_level = catalog_item.permission_level if catalog_item else ToolPermissionLevel.SAFE
        requires_approval = catalog_item.requires_approval if catalog_item else False
        tags = list(catalog_item.tags) if catalog_item else []

        steps.append(
            PlannedToolCall(
                step_index=index,
                tool_name=tool_name,
                args=args,
                reason=str(raw_step.get("reason") or "LLM planner selected this tool").strip(),
                expected_result=str(raw_step.get("expected_result") or "").strip() or None,
                permission_level=permission_level,
                requires_approval=requires_approval,
                required=True,
                tags=tags,
            )
        )

    return ToolPlan(
        planner_mode=PlannerMode.LLM,
        resource_type=resource_type,
        user_question=description,
        steps=steps,
        max_steps=len(steps),
        budget={"max_tool_calls": len(steps)},
        fallback_plan=fallback_plan.steps,
        tool_catalog_version=tool_catalog.catalog_version,
    )


def parse_planner_json(response: str) -> dict[str, Any]:
    json_text = extract_json_object(response)
    payload = json.loads(json_text)
    if not isinstance(payload, dict):
        raise ValueError("planner response must be a JSON object")
    return payload


def extract_json_object(text: str) -> str:
    normalized = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", normalized, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = normalized.find("{")
    end = normalized.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("planner response does not contain a JSON object")
    return normalized[start : end + 1]


def call_llm_planner(llm_client: LlmClient, prompt: str) -> str:
    generate_text = getattr(llm_client, "generate_text", None)
    if callable(generate_text):
        return str(generate_text(system_prompt=SYSTEM_PROMPT, user_prompt=prompt)).strip()

    generate_plan = getattr(llm_client, "generate_plan", None)
    if callable(generate_plan):
        return str(generate_plan(prompt)).strip()

    return str(llm_client.generate_report(f"{SYSTEM_PROMPT}\n\n{prompt}")).strip()


def fallback_tool_plan(plan: ToolPlan) -> ToolPlan:
    return ToolPlan(
        plan_id=plan.plan_id,
        planner_mode=PlannerMode.FALLBACK,
        resource_type=plan.resource_type,
        user_question=plan.user_question,
        steps=plan.steps,
        max_steps=plan.max_steps,
        budget=plan.budget,
        fallback_plan=[],
        tool_catalog_version=plan.tool_catalog_version,
        created_at=plan.created_at,
    )


def compact_input_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": input_schema.get("type", "object"),
        "properties": input_schema.get("properties", {}),
        "required": input_schema.get("required", []),
    }


def scoped_catalog_tools(tool_catalog: ToolCatalog, resource_type: ResourceType) -> list[Any]:
    if resource_type in {ResourceType.MIXED, ResourceType.UNKNOWN}:
        return list(tool_catalog.tools)
    return [
        tool
        for tool in tool_catalog.tools
        if not tool.resource_types or resource_type in tool.resource_types
    ]


def plan_resource_scope_errors(
    plan: ToolPlan,
    tool_catalog: ToolCatalog,
    resource_type: ResourceType,
) -> list[str]:
    if resource_type in {ResourceType.MIXED, ResourceType.UNKNOWN}:
        return []
    catalog_by_name = {tool.name: tool for tool in tool_catalog.tools}
    errors: list[str] = []
    for step in plan.steps:
        tool = catalog_by_name.get(step.tool_name)
        if tool is not None and tool.resource_types and resource_type not in tool.resource_types:
            errors.append(
                f"tool is outside resource scope: {step.tool_name} not in {resource_type.value}"
            )
    return errors


def preview_text(text: str, limit: int = 500) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
