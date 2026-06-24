import os

from tools.process import InspectProcessInput, inspect_process


def test_inspect_current_process() -> None:
    result = inspect_process(InspectProcessInput(pid=os.getpid()))
    assert result["available"] is True
    assert result["pid"] == os.getpid()
    assert "memory_info" in result