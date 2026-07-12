from __future__ import annotations

import logging

from app.api import configured_api_worker_count, warn_if_multiple_api_workers


def test_api_worker_guard_warns_for_multiple_workers(monkeypatch, caplog) -> None:
    monkeypatch.setenv("RESOURCEOPS_API_WORKERS", "2")

    with caplog.at_level(logging.WARNING):
        warn_if_multiple_api_workers()

    assert configured_api_worker_count() == 2
    assert "single API worker" in caplog.text
