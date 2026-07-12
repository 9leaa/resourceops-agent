from __future__ import annotations

import argparse
import json
from pathlib import Path

from approval.store import ApprovalStore
from app.schemas import Approval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate legacy approvals.jsonl into ResourceOps SQLite.")
    parser.add_argument("--input", default="var/approvals.jsonl", help="Legacy approvals JSONL path.")
    parser.add_argument("--db", default=None, help="Target SQLite database. Defaults to RESOURCEOPS_TRACE_DB.")
    return parser


def load_approvals(path: Path) -> list[Approval]:
    approvals: list[Approval] = []
    if not path.exists():
        return approvals

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                approvals.append(Approval.model_validate(json.loads(line)))
            except Exception as exc:  # noqa: BLE001 - migration should report bad legacy rows.
                raise ValueError(f"invalid approval JSONL row {line_number}: {exc}") from exc
    return approvals


def migrate(input_path: Path, db_path: str | None = None) -> int:
    store = ApprovalStore(db_path)
    migrated = 0
    for approval in load_approvals(input_path):
        store.save(approval)
        migrated += 1
    return migrated


def main() -> int:
    args = build_parser().parse_args()
    count = migrate(Path(args.input), args.db)
    print(f"migrated_approvals={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
