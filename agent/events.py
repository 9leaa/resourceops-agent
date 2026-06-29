from __future__ import annotations

from typing import Protocol

from app.schemas import DiagnosisTodo


class AgentEventSink(Protocol):
    def on_phase_snapshot(self, phases: list[DiagnosisTodo]) -> None:
        ...

    def on_phase_updated(self, phase: DiagnosisTodo, phases: list[DiagnosisTodo]) -> None:
        ...

    def on_todo_snapshot(self, todos: list[DiagnosisTodo]) -> None:
        ...

    def on_todo_updated(self, todo: DiagnosisTodo, todos: list[DiagnosisTodo]) -> None:
        ...


class NoopAgentEventSink:
    def on_phase_snapshot(self, phases: list[DiagnosisTodo]) -> None:
        pass

    def on_phase_updated(self, phase: DiagnosisTodo, phases: list[DiagnosisTodo]) -> None:
        pass

    def on_todo_snapshot(self, todos: list[DiagnosisTodo]) -> None:
        pass

    def on_todo_updated(self, todo: DiagnosisTodo, todos: list[DiagnosisTodo]) -> None:
        pass