"""Phase 6: orchestrator brain — supervisor LLM (planning, gates, dispatch briefs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sdlc_agent.contracts import (
    ArtifactReturn,
    SubagentName,
    TaskStatus,
    VerificationBlock,
)
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.brain import OrchestratorBrain
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.orchestrator.state_machine import GateDecision, TicketState
from sdlc_agent.subagents.mocks import CannedSubagent, canned_successful_artifact


def _plan_response() -> dict[str, Any]:
    return {
        "goal": "Deliver DEMO-42 TokenBucket rate limiter",
        "current_focus": "Requirements analysis",
        "phase_checklist": [
            "requirements",
            "TDD implementation",
            "PR review",
        ],
        "risks": ["ambiguous AC"],
        "notes": "unittest per ticket comments",
    }


def _gate_proceed() -> dict[str, Any]:
    return {
        "decision": "proceed",
        "rationale": "gate criteria satisfied",
        "retry_guidance": "",
    }


def _gate_retry(guidance: str = "add missing tests") -> dict[str, Any]:
    return {
        "decision": "retry",
        "rationale": "verification incomplete",
        "retry_guidance": guidance,
    }


def _ok_registry() -> dict[SubagentName, CannedSubagent]:
    return {
        SubagentName.BACKLOG_ANALYZER: CannedSubagent(
            name=SubagentName.BACKLOG_ANALYZER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"acceptance_criteria": ["ac1"]}
            ),
        ),
        SubagentName.DEVELOPER: CannedSubagent(
            name=SubagentName.DEVELOPER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"implementation_summary": "ok", "tests": ["t1"]}
            ),
        ),
        SubagentName.PR_REVIEWER: CannedSubagent(
            name=SubagentName.PR_REVIEWER,
            artifact_kwargs=canned_successful_artifact(artifact={"verdict": "approve"}),
        ),
    }


def test_brain_create_plan_persists_on_state(
    tmp_repo: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    stores = MemoryStores(paths)
    llm = fake_llm_factory([_plan_response()])
    brain = OrchestratorBrain(stores, llm)
    state = TicketState(ticket_id="T-1", ticket_inputs={"jira_key": "T-1"})

    plan = brain.create_plan(state)

    assert plan["goal"] == "Deliver DEMO-42 TokenBucket rate limiter"
    assert state.plan is not None
    assert state.plan["current_focus"] == "Requirements analysis"


def test_brain_gate_retry_stores_guidance(
    tmp_repo: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    stores = MemoryStores(paths)
    llm = fake_llm_factory([_gate_retry("fix acceptance criteria")])
    brain = OrchestratorBrain(stores, llm)
    state = TicketState(ticket_id="T-1")
    artifact = ArtifactReturn(
        task_id="t",
        status=TaskStatus.COMPLETED,
        verification=VerificationBlock(passed=False),
    )

    decision, _ = brain.evaluate_gate(
        SDLCPhase.REQUIREMENTS_GATE,
        artifact,
        state,
        attempts_in_phase=1,
        max_attempts=2,
    )

    assert decision is GateDecision.RETRY
    assert "fix acceptance criteria" in state.retry_notes[SDLCPhase.REQUIREMENTS_ANALYSIS.value]


def test_brain_dispatch_brief_includes_plan_and_retry(
    tmp_repo: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    stores = MemoryStores(paths)
    llm = fake_llm_factory([_plan_response()])
    brain = OrchestratorBrain(stores, llm)
    state = TicketState(
        ticket_id="T-1",
        plan=_plan_response(),
        retry_notes={SDLCPhase.DEVELOPMENT.value: "tests must be green"},
    )

    brief = brain.build_task_description(
        state,
        SDLCPhase.DEVELOPMENT,
        SubagentName.DEVELOPER,
        attempt=2,
    )

    assert "Deliver DEMO-42" in brief
    assert "tests must be green" in brief


def test_orchestrator_with_brain_runs_full_ticket(
    tmp_repo: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    canned = [
        _plan_response(),
        _gate_proceed(),
        _gate_proceed(),
        _gate_proceed(),
    ]
    llm = fake_llm_factory(canned)
    orch = Orchestrator(paths=paths, registry=_ok_registry(), llm=llm)

    state = orch.intake("T-BRAIN", ticket_inputs={"jira_key": "T-BRAIN"})
    assert state.plan is not None
    assert state.plan["goal"]

    final = orch.run_to_completion("T-BRAIN")
    assert final.current_phase is SDLCPhase.DONE

    episodes = [e["kind"] for e in MemoryStores(paths).read_episodes()]
    assert "orchestrator_plan" in episodes


def test_orchestrator_without_llm_uses_legacy_gates(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(paths=paths, registry=_ok_registry())
    assert orch.brain is None

    final = orch.run_to_completion("T-LEGACY")
    assert final.current_phase is SDLCPhase.DONE


def test_assignment_uses_brain_brief_not_generic_stub(
    tmp_repo: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    registry = _ok_registry()
    llm = fake_llm_factory([_plan_response(), _gate_proceed(), _gate_proceed(), _gate_proceed()])
    orch = Orchestrator(paths=paths, registry=registry, llm=llm)
    orch.intake("T-2", ticket_inputs={"jira_key": "T-2"})
    orch.advance("T-2")

    assignment = registry[SubagentName.BACKLOG_ANALYZER].last_assignment
    assert assignment is not None
    assert "Plan goal:" in assignment.task
    assert "Execute REQUIREMENTS_ANALYSIS for ticket" not in assignment.task
