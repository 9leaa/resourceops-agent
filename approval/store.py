from __future__ import annotations

import json
import os
from pathlib import Path

from app.schemas import Approval


DEFAULT_APPROVAL_STORE = Path(__file__).resolve().parents[1] / "var" / "approvals.jsonl"


def resolve_approval_store(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("RESOURCEOPS_APPROVAL_STORE", DEFAULT_APPROVAL_STORE))


class ApprovalStore:
    """负责审批数据怎么存怎么取"""
    def __init__(self, path: Path | str | None = None) -> None:
        """确定存储路径、确保父目录存在、确保文件存在"""
        self.path = resolve_approval_store(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def save(self, approval: Approval) -> Approval:
        """保存或更新一个审批"""
        approvals = {item.approval_id: item for item in self.list(status=None)}
        approvals[approval.approval_id] = approval
        with self.path.open("w", encoding="utf-8") as file:
            for item in approvals.values():
                file.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return approval

    def get(self, approval_id: str) -> Approval:
        """根据id读取审批"""
        for approval in self.list(status=None):
            if approval.approval_id == approval_id:
                return approval
        raise KeyError(f"approval not found: {approval_id}")

    def list(self, status: str | None = "pending") -> list[Approval]:
        """列出审批表，默认返回是pending，如果是status = None：则返回所有审批"""
        approvals: list[Approval] = []
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                approval = Approval.model_validate(json.loads(line))
                if status is None or approval.status == status:
                    approvals.append(approval)
        return approvals
