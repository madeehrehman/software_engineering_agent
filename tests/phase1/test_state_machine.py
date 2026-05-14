"""Phase 1: pure FSM transition logic."""

from __future__ import annotations

from sdlc_agent.contracts import (
    ArtifactReturn,
    TaskStatus,
    VerificationBlock,
)
from sdlc_agent.orchestrator.state_machine import (
    GateDecision,
    SDLCPhase,
    evaluate_default_gate,
    gate_after,
    next_phase_for_decision,
    phase_to_subagent,
    work_before,
)


def test_work_to_gate_mapping() -> None:
    assert gate_after(SDLCPhase.REQUIREMENTS_ANALYSIS) is SDLCPhase.REQUIREMENTS_GATE
    assert gate_after(SDLCPhase.DEVELOPMENT) is SDLCPhase.DEVELOPMENT_GATE
    assert gate_after(SDLCPhase.PR_REVIEW) is SDLCPhase.REVIEW_GATE


def test_gate_to_work_mapping() -> None:
    assert work_before(SDLCPhase.REQUIREMENTS_GATE) is SDLCPhase.REQUIREMENTS_ANALYSIS
    assert work_before(SDLCPhase.DEVELOPMENT_GATE) is SDLCPhase.DEVELOPMENT
    assert work_before(SDLCPhase.REVIEW_GATE) is SDLCPhase.PR_REVIEW


def test_phase_to_subagent_mapping() -> None:
    assert phase_to_subagent(SDLCPhase.REQUIREMENTS_ANALYSIS).value == "backlog_analyzer"
    assert phase_to_subagent(SDLCPhase.DEVELOPMENT).value == "developer"
    assert phase_to_subagent(SDLCPhase.PR_REVIEW).value == "pr_reviewer"


def test_routing_proceed_advances() -> None:
    assert next_phase_for_decision(SDLCPhase.REQUIREMENTS_GATE, GateDecision.PROCEED) is SDLCPhase.DEVELOPMENT
    assert next_phase_for_decision(SDLCPhase.DEVELOPMENT_GATE, GateDecision.PROCEED) is SDLCPhase.PR_REVIEW
    assert next_phase_for_decision(SDLCPhase.REVIEW_GATE, GateDecision.PROCEED) is SDLCPhase.DONE


def test_routing_retry_goes_back_to_work() -> None:
    assert next_phase_for_decision(SDLCPhase.DEVELOPMENT_GATE, GateDecision.RETRY) is SDLCPhase.DEVELOPMENT


def test_routing_blocked_and_needs_human() -> None:
    assert next_phase_for_decision(SDLCPhase.REVIEW_GATE, GateDecision.BLOCKED) is SDLCPhase.BLOCKED
    assert next_phase_for_decision(SDLCPhase.REVIEW_GATE, GateDecision.NEEDS_HUMAN) is SDLCPhase.NEEDS_HUMAN


def _artifact(passed: bool, status: TaskStatus = TaskStatus.COMPLETED) -> ArtifactReturn:
    return ArtifactReturn(
        task_id="t",
        status=status,
        verification=VerificationBlock(passed=passed),
    )


def test_default_gate_proceeds_on_pass() -> None:
    decision, _ = evaluate_default_gate(
        SDLCPhase.REQUIREMENTS_GATE,
        _artifact(True),
        attempts_in_phase=1,
    )
    assert decision is GateDecision.PROCEED


def test_default_gate_retries_when_under_max() -> None:
    decision, _ = evaluate_default_gate(
        SDLCPhase.DEVELOPMENT_GATE,
        _artifact(False),
        attempts_in_phase=1,
        max_attempts=2,
    )
    assert decision is GateDecision.RETRY


def test_default_gate_blocks_at_max() -> None:
    decision, _ = evaluate_default_gate(
        SDLCPhase.DEVELOPMENT_GATE,
        _artifact(False),
        attempts_in_phase=2,
        max_attempts=2,
    )
    assert decision is GateDecision.BLOCKED


def test_default_gate_needs_human_when_required() -> None:
    decision, _ = evaluate_default_gate(
        SDLCPhase.REVIEW_GATE,
        _artifact(True),
        attempts_in_phase=1,
        require_human=True,
    )
    assert decision is GateDecision.NEEDS_HUMAN


def test_default_gate_needs_human_when_subagent_escalates() -> None:
    decision, _ = evaluate_default_gate(
        SDLCPhase.REQUIREMENTS_GATE,
        _artifact(False, status=TaskStatus.NEEDS_HUMAN),
        attempts_in_phase=1,
    )
    assert decision is GateDecision.NEEDS_HUMAN
