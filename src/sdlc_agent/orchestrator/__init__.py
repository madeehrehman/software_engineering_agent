"""Orchestrator: SDLC state machine + dispatcher + (Phase 4) curation gate.

Only state-machine types are re-exported here. The ``Orchestrator`` class lives
in :mod:`sdlc_agent.orchestrator.dispatcher` and must be imported from there to
avoid a circular import via :mod:`sdlc_agent.memory.stores`.

The orchestrator **brain** (supervisor LLM) lives in :mod:`sdlc_agent.orchestrator.brain`
(import from there — not re-exported here to avoid a circular import with
:mod:`sdlc_agent.memory.stores`).
"""

from sdlc_agent.orchestrator.state_machine import (
    GATE_PHASES,
    GateDecision,
    SDLCPhase,
    TERMINAL_PHASES,
    TicketState,
    TransitionRecord,
    WORK_PHASES,
    evaluate_default_gate,
    gate_after,
    next_phase_for_decision,
    phase_to_subagent,
    work_before,
)

__all__ = [
    "GATE_PHASES",
    "GateDecision",
    "SDLCPhase",
    "TERMINAL_PHASES",
    "TicketState",
    "TransitionRecord",
    "WORK_PHASES",
    "evaluate_default_gate",
    "gate_after",
    "next_phase_for_decision",
    "phase_to_subagent",
    "work_before",
]
