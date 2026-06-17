from __future__ import annotations
"""
把所有工具统一注册、统一校验参数、统一执行、统一处理超时/错误/审批拦截，并统一返回 ToolExecutionResult。
1. 工具怎么注册？
2. 工具参数怎么校验？
3. 工具怎么执行？
4. 工具超时怎么办？
5. 工具报错怎么办？
6. 危险工具是否允许直接执行？
7. 工具结果如何标准化返回？
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from app.schemas import StrictBaseModel, ToolCallStatus, ToolPermissionLevel

#每个工具handler都应该接受应该pydantic （basemodel）参数，然后返回任意结果
ToolHandler = Callable[[BaseModel], Any]

#frozen=t 表示创建后不可修改
@dataclass(frozen=True)
class ToolSpec:
    """工具说明书"""
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler #真正执行工具逻辑的函数
    permission_level: ToolPermissionLevel = ToolPermissionLevel.SAFE
    timeout_seconds: float = 5.0 #工具超时时间
    retry: int = 0  #重试次数
    tags: list[str] = field(default_factory=list) #工具标签：[cpu,snapshot,safe]


class ToolExecutionResult(StrictBaseModel):
    """工具执行结果"""
    tool_name: str
    permission_level: ToolPermissionLevel
    status: ToolCallStatus
    data: Any | None = None
    preview: str | None = None
    summary: str | None = None
    error: str | None = None
    latency_ms: int = Field(..., ge=0)
    validated_args: dict[str, Any] = Field(default_factory=dict)


class EmptyToolInput(BaseModel):
    """不需要参数的工具输入"""
    pass


class ToolRegistry:
    """Execution boundary for all ResourceOps tools.

    P0 keeps the registry usable without real resource tools. P1 will register
    GPU/CPU/Memory/Process collectors behind this same boundary.
    注册、列出、查找、执行、拦截危险工具、处理参数校验、处理超时、处理重试、统一返回结果
    """

    def __init__(self, allow_dangerous: bool = False, workspace_root: Path | str | None = None) -> None:
        self.allow_dangerous = allow_dangerous#是否允许直接执行危险工具
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
        self._tools: dict[str, ToolSpec] = {}#内部工具字典

    def register(self, spec: ToolSpec) -> None:
        """注册工具"""
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permission_level": spec.permission_level.value,
                "input_schema": spec.input_model.model_json_schema(),
                "timeout_seconds": spec.timeout_seconds,
                "retry": spec.retry,
                "tags": list(spec.tags),
            }
            for spec in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def get(self, name: str) -> ToolSpec:
        """根据工具名查找工具说明书"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolExecutionResult:
        started = perf_counter()

        try:
            spec = self.get(name)
        except KeyError as exc:
            #失败依然返回一个ToolExecutionResult类型
            return self._error_result(name, ToolPermissionLevel.SAFE, str(exc), started)

        try:
            #用该工具对应的pydantic输入模型来检查并转换传来的参数
            validated = spec.input_model.model_validate(args or {})
        except ValidationError as exc:
            return self._error_result(name, spec.permission_level, exc.errors(), started)

        validated_args = validated.model_dump(mode="json")
        if spec.permission_level == ToolPermissionLevel.DANGEROUS and not self.allow_dangerous:
            return ToolExecutionResult(
                tool_name=name,
                permission_level=spec.permission_level,
                status=ToolCallStatus.BLOCKED_FOR_APPROVAL,
                data={
                    "approval_required": True,
                    "action": name,
                    "args": validated_args,
                    "reason": "dangerous tool requires human approval",
                },
                preview="blocked for human approval",
                summary="dangerous tool requires human approval",
                error="dangerous tool requires human approval",
                latency_ms=self._latency_ms(started),
                validated_args=validated_args,
            )

        last_error: Any = None
        for _attempt in range(spec.retry + 1):
            try:
                data = self._run_with_timeout(spec, validated)
                return ToolExecutionResult(
                    tool_name=name,
                    permission_level=spec.permission_level,
                    status=ToolCallStatus.SUCCESS,
                    data=data,
                    preview=preview_data(data),
                    summary=summary_data(data),
                    latency_ms=self._latency_ms(started),
                    validated_args=validated_args,
                )
            except Exception as exc:  # noqa: BLE001 - tool boundary normalizes failures.
                last_error = f"{exc.__class__.__name__}: {exc}"

        return self._error_result(name, spec.permission_level, last_error, started, validated_args)

    def _run_with_timeout(self, spec: ToolSpec, validated: BaseModel) -> Any:
        """把工具 handler 放到单独线程执行
            ↓
            等待 timeout_seconds 秒
            ↓
            如果按时返回，返回工具结果
            ↓
            如果超时，取消 future，并抛出 TimeoutError
        """
        with ThreadPoolExecutor(max_workers=1) as executor:

            future = executor.submit(spec.handler, validated)
            try:
                return future.result(timeout=spec.timeout_seconds)
            except TimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"tool timed out after {spec.timeout_seconds}s") from exc

    def _error_result(
        self,
        tool_name: str,
        permission_level: ToolPermissionLevel,
        error: Any,
        started: float,
        validated_args: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=tool_name,
            permission_level=permission_level,
            status=ToolCallStatus.ERROR,
            error=str(error),
            preview="tool error",
            summary=str(error),
            latency_ms=self._latency_ms(started),
            validated_args=validated_args or {},
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, int((perf_counter() - started) * 1000))


def preview_data(data: Any) -> str:
    if isinstance(data, dict):
        if "preview" in data and isinstance(data["preview"], str):
            return data["preview"]
        keys = ", ".join(sorted(str(key) for key in data.keys())[:5])
        return f"dict keys: {keys}" if keys else "empty dict"
    if isinstance(data, list):
        return f"{len(data)} items"
    return str(data)[:200]


def summary_data(data: Any) -> str:
    if isinstance(data, dict):
        if "summary" in data and isinstance(data["summary"], str):
            return data["summary"]
        return preview_data(data)
    return preview_data(data)


def default_registry(allow_dangerous: bool = False) -> ToolRegistry:
    return ToolRegistry(allow_dangerous=allow_dangerous)
