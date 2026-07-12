from agent.llm_report import build_llm_report, build_llm_report_result
from app.schemas import ResourceType


class FakeLlmClient:
    def __init__(self, output: str | None = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.last_prompt: str | None = None

    def generate_report(self, prompt: str) -> str:
        self.last_prompt = prompt
        if self.error:
            raise self.error
        return self.output or ""


class StreamingFakeLlmClient:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.last_prompt: str | None = None

    def generate_report(self, prompt: str) -> str:
        raise AssertionError("stream_report should be used when a stream callback is provided")

    def stream_report(self, prompt: str):
        self.last_prompt = prompt
        yield from self.chunks


VALID_REPORT = """## 问题概览
用户反馈内存压力。

## 关键证据
当前已有 detector 证据。

## 诊断发现
存在 memory_pressure。

## 建议操作
先检查内存占用进程。

## 审批状态
appr_test 当前为 pending，危险操作尚未执行。

## 风险说明
危险操作必须人工审批。
"""


def test_llm_report_uses_client_output() -> None:
    client = FakeLlmClient(VALID_REPORT)

    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[{"approval_id": "appr_test", "status": "pending"}],
        llm_client=client,
    )

    assert report == VALID_REPORT.strip()
    assert client.last_prompt is not None
    assert "诊断数据" in client.last_prompt
    assert "diagnosis" in client.last_prompt
    assert "tool_context" not in client.last_prompt


def test_llm_report_falls_back_without_client() -> None:
    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[],
        llm_client=None,
    )

    assert report == "fallback report"


def test_llm_report_falls_back_on_client_error() -> None:
    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[],
        llm_client=FakeLlmClient(error=RuntimeError("llm failed")),
    )

    assert report == "fallback report"


def test_llm_report_falls_back_on_missing_sections() -> None:
    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[],
        llm_client=FakeLlmClient("只有一句话，没有章节。"),
    )

    assert report == "fallback report"


def test_llm_report_falls_back_when_pending_approval_is_marked_executed() -> None:
    bad_report = VALID_REPORT.replace(
        "appr_test 当前为 pending，危险操作尚未执行",
        "appr_test 当前审批状态为已执行，危险操作已执行",
    )

    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[{"approval_id": "appr_test", "status": "pending"}],
        llm_client=FakeLlmClient(bad_report),
    )

    assert report == "fallback report"


def test_llm_report_allows_explicit_pending_with_no_executed_actions() -> None:
    report_text = VALID_REPORT.replace(
        "危险操作尚未执行。",
        "危险操作尚未执行；当前没有已执行操作。",
    )

    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[{"approval_id": "appr_test", "status": "pending"}],
        llm_client=FakeLlmClient(report_text),
    )

    assert report == report_text.strip()


def test_llm_report_streams_chunks_to_callback() -> None:
    chunks = [VALID_REPORT[:40], VALID_REPORT[40:]]
    streamed: list[str] = []
    client = StreamingFakeLlmClient(chunks)

    result = build_llm_report_result(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[{"approval_id": "appr_test", "status": "pending"}],
        llm_client=client,
        stream_callback=streamed.append,
    )

    assert result.used_llm
    assert result.final_report == VALID_REPORT.strip()
    assert streamed == chunks
    assert client.last_prompt is not None


def test_llm_report_strips_meta_prose_before_report_body() -> None:
    report_with_meta = """我先按你给的字段重组报告，只保留输入里的事实。
**问题概览**
用户反馈内存压力。

**关键证据**
当前已有 detector 证据。

**诊断发现**
存在 memory_pressure。

**建议操作**
先检查内存占用进程。

**审批状态**
appr_test 当前为 pending，危险操作尚未执行。

**风险说明**
危险操作必须人工审批。
"""

    report = build_llm_report(
        deterministic_report="fallback report",
        description="为什么内存快满了？",
        resource_type=ResourceType.MEMORY,
        tool_results=[],
        evidence_items=[],
        findings=[],
        approvals=[{"approval_id": "appr_test", "status": "pending"}],
        llm_client=FakeLlmClient(report_with_meta),
    )

    assert report.startswith("## 问题概览")
    assert "我先按" not in report
