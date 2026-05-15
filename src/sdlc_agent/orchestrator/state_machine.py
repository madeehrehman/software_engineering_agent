"""SDLC state machine types + pure transition logic (spec §4).

The orchestrator owns this. Subagents have no awareness of it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sdlc_agent.contracts import ArtifactReturn, SubagentName, TaskStatus


class SDLCPhase(StrEnum):
    """All states the SDLC FSM can occupy (spec §4)."""

    INTAKE = "INTAKE"
    REQUIREMENTS_ANALYSIS = "REQUIREMENTS_ANALYSIS"
    REQUIREMENTS_GATE = "REQUIREMENTS_GATE"
    DEVELOPMENT = "DEVELOPMENT"
    DEVELOPMENT_GATE = "DEVELOPMENT_GATE"
    PR_REVIEW = "PR_REVIEW"
    REVIEW_GATE = "REVIEW_GATE"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


WORK_PHASES: tuple[SDLCPhase, ...] = (
    SDLCPhase.REQUIREMENTS_ANALYSIS,
    SDLCPhase.DEVELOPMENT,
    SDLCPhase.PR_REVIEW,
)

GATE_PHASES: tuple[SDLCPhase, ...] = (
    SDLCPhase.REQUIREMENTS_GATE,
    SDLCPhase.DEVELOPMENT_GATE,
    SDLCPhase.REVIEW_GATE,
)

TERMINAL_PHASES: tuple[SDLCPhase, ...] = (
    SDLCPhase.DONE,
    SDLCPhase.BLOCKED,
    SDLCPhase.NEEDS_HUMAN,
)


_WORK_TO_SUBAGENT: dict[SDLCPhase, SubagentName] = {
    SDLCPhase.REQUIREMENTS_ANALYSIS: SubagentName.BACKLOG_ANALYZER,
    SDLCPhase.DEVELOPMENT: SubagentName.DEVELOPER,
    SDLCPhase.PR_REVIEW: SubagentName.PR_REVIEWER,
}

_WORK_TO_GATE: dict[SDLCPhase, SDLCPhase] = {
    SDLCPhase.REQUIREMENTS_ANALYSIS: SDLCPhase.REQUIREMENTS_GATE,
    SDLCPhase.DEVELOPMENT: SDLCPhase.DEVELOPMENT_GATE,
    SDLCPhase.PR_REVIEW: SDLCPhase.REVIEW_GATE,
}

_GATE_TO_WORK: dict[SDLCPhase, SDLCPhase] = {v: k for k, v in _WORK_TO_GATE.items()}

_GATE_TO_NEXT_WORK: dict[SDLCPhase, SDLCPhase] = {
    SDLCPhase.REQUIREMENTS_GATE: SDLCPhase.DEVELOPMENT,
    SDLCPhase.DEVELOPMENT_GATE: SDLCPhase.PR_REVIEW,
    SDLCPhase.REVIEW_GATE: SDLCPhase.DONE,
}


def phase_to_subagent(phase: SDLCPhase) -> SubagentName:
    """Which subagent owns this work phase? Raises for non-work phases."""
    try:
        return _WORK_TO_SUBAGENT[phase]
    except KeyError as e:
        raise ValueError(f"{phase} has no associated subagent") from e


def gate_after(phase: SDLCPhase) -> SDLCPhase:
    """The gate that follows a work phase."""
    return _WORK_TO_GATE[phase]


def work_before(gate: SDLCPhase) -> SDLCPhase:
    """The work phase a gate evaluates."""
    return _GATE_TO_WORK[gate]


def next_work_after_gate(gate: SDLCPhase) -> SDLCPhase:
    """What comes after a gate when the decision is PROCEED."""
    return _GATE_TO_NEXT_WORK[gate]


class GateDecision(StrEnum):
    """A gate is not a boolean — it's one of four routes (spec §4)."""

    PROCEED = "proceed"
    RETRY = "retry"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


def next_phase_for_decision(gate: SDLCPhase, decision: GateDecision) -> SDLCPhase:
    """Pure routing: where does the FSM go from ``gate`` given ``decision``?"""
    if decision is GateDecision.PROCEED:
        return next_work_after_gate(gate)
    if decision is GateDecision.RETRY:
        return work_before(gate)
    if decision is GateDecision.BLOCKED:
        return SDLCPhase.BLOCKED
    if decision is GateDecision.NEEDS_HUMAN:
        return SDLCPhase.NEEDS_HUMAN
    raise ValueError(f"unknown decision: {decision}")


class TransitionRecord(BaseModel):
    """A single FSM transition, persisted with the ticket state and audit log."""

    from_phase: SDLCPhase
    to_phase: SDLCPhase
    decision: GateDecision | None = None
    rationale: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TicketState(BaseModel):
    """Current working state for one ticket (spec §5.2 store #1).

    Persisted to ``.deepagent/state/<ticket-id>.json`` after every transition so
    a session can resume mid-lifecycle.
    """

    ticket_id: str
    current_phase: SDLCPhase = SDLCPhase.INTAKE
    attempts: dict[SDLCPhase, int] = Field(default_factory=dict)
    history: list[TransitionRecord] = Field(default_factory=list)
    blocked_reason: str | None = None
    ticket_inputs: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] | None = None
    """Long-horizon plan authored by the orchestrator master agent (spec §3.1)."""
    retry_notes: dict[str, str] = Field(default_factory=dict)
    """Per work-phase guidance from the last RETRY gate decision (phase value → text)."""

    def record_transition(
        self,
        to_phase: SDLCPhase,
        *,
        decision: GateDecision | None = None,
        rationale: str = "",
    ) -> TransitionRecord:
        record = TransitionRecord(
            from_phase=self.current_phase,
            to_phase=to_phase,
            decision=decision,
            rationale=rationale,
        )
        self.history.append(record)
        self.current_phase = to_phase
        return record

    def bump_attempts(self, phase: SDLCPhase) -> int:
        self.attempts[phase] = self.attempts.get(phase, 0) + 1
        return self.attempts[phase]

    @property
    def is_terminal(self) -> bool:
        return self.current_phase in TERMINAL_PHASES


def evaluate_default_gate(
    gate: SDLCPhase,
    artifact: ArtifactReturn,
    *,
    attempts_in_phase: int,
    max_attempts: int = 2,
    require_human: bool = False,
) -> tuple[GateDecision, str]:
    """Default Phase-1 gate logic suitable for the mocked-subagent walk-through.

    Real subagent-aware gate logic for each phase (e.g. acceptance criteria check
    at REQUIREMENTS_GATE, tests-exist check at DEVELOPMENT_GATE) lands when each
    real subagent does, in Phase 2+ and Phase 4.

    Routing rules:
      * ``status == NEEDS_HUMAN`` → NEEDS_HUMAN.
      * ``require_human`` (HITL configured for this gate) → NEEDS_HUMAN.
      * ``verification.passed`` → PROCEED.
      * otherwise → RETRY if under ``max_attempts`` else BLOCKED.
    """
    if artifact.status is TaskStatus.NEEDS_HUMAN:
        return GateDecision.NEEDS_HUMAN, f"{gate}: subagent escalated"
    if require_human:
        return GateDecision.NEEDS_HUMAN, f"{gate}: HITL approval required by config"
    if artifact.verification.passed and artifact.status is TaskStatus.COMPLETED:
        return GateDecision.PROCEED, f"{gate}: verification passed"
    if attempts_in_phase >= max_attempts:
        return GateDecision.BLOCKED, (
            f"{gate}: verification failed after {attempts_in_phase} attempts"
        )
    return GateDecision.RETRY, f"{gate}: verification failed; retrying"
