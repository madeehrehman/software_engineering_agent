"""Phase 1 acceptance: ticket walks INTAKE → DONE against mocked subagents;
state persists and resumes mid-lifecycle (spec §11 Phase 1 test)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc_agent.config import GateConfig
from sdlc_agent.contracts import SubagentName
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.subagents.mocks import (
    CannedSubagent,
    canned_failing_artifact,
    canned_successful_artifact,
)


def _ok_registry() -> dict[SubagentName, CannedSubagent]:
    return {
        SubagentName.BACKLOG_ANALYZER: CannedSubagent(
            name=SubagentName.BACKLOG_ANALYZER,
            artifact_kwargs=canned_successful_artifact(artifact={"acceptance_criteria": ["ac1"]}),
        ),
        SubagentName.DEVELOPER: CannedSubagent(
            name=SubagentName.DEVELOPER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"implementation_summary": "...", "tests": ["t1"]}
            ),
        ),
        SubagentName.PR_REVIEWER: CannedSubagent(
            name=SubagentName.PR_REVIEWER,
            artifact_kwargs=canned_successful_artifact(artifact={"verdict": "approve"}),
        ),
    }


def test_intake_to_done_walkthrough(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    registry = _ok_registry()
    orch = Orchestrator(paths=paths, registry=registry)

    final = orch.run_to_completion("TICKET-1")

    assert final.current_phase is SDLCPhase.DONE
    assert registry[SubagentName.BACKLOG_ANALYZER].call_count == 1
    assert registry[SubagentName.DEVELOPER].call_count == 1
    assert registry[SubagentName.PR_REVIEWER].call_count == 1

    assert paths.ticket_artifacts_dir("TICKET-1").joinpath("requirement_analysis.json").exists()
    assert paths.ticket_artifacts_dir("TICKET-1").joinpath("implementation_summary.json").exists()
    assert paths.ticket_artifacts_dir("TICKET-1").joinpath("review.json").exists()


def test_state_persists_after_each_step(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(paths=paths, registry=_ok_registry())

    orch.intake("T-2")
    state_file = paths.state_file("T-2")
    assert state_file.exists()
    on_disk = json.loads(state_file.read_text())
    assert on_disk["current_phase"] == SDLCPhase.REQUIREMENTS_ANALYSIS.value


def test_resume_mid_lifecycle(tmp_repo: Path) -> None:
    """Two separate Orchestrator instances; the second picks up where the first left off."""
    paths = initialize_deepagent(tmp_repo)
    registry_a = _ok_registry()
    orch_a = Orchestrator(paths=paths, registry=registry_a)

    state = orch_a.intake("T-3")
    state = orch_a.advance("T-3")
    state = orch_a.advance("T-3")
    assert state.current_phase is SDLCPhase.DEVELOPMENT

    registry_b = _ok_registry()
    orch_b = Orchestrator(paths=paths, registry=registry_b)

    final = orch_b.run_to_completion("T-3")
    assert final.current_phase is SDLCPhase.DONE

    assert registry_a[SubagentName.BACKLOG_ANALYZER].call_count == 1
    assert registry_b[SubagentName.BACKLOG_ANALYZER].call_count == 0
    assert registry_b[SubagentName.DEVELOPER].call_count == 1
    assert registry_b[SubagentName.PR_REVIEWER].call_count == 1


def test_retry_on_first_failure_then_proceeds(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    registry = _ok_registry()
    flaky = CannedSubagent(
        name=SubagentName.DEVELOPER,
        artifact_kwargs=canned_failing_artifact("first attempt failed"),
        artifact_kwargs_after_first_call=canned_successful_artifact(
            artifact={"implementation_summary": "ok", "tests": ["t1"]}
        ),
    )
    registry[SubagentName.DEVELOPER] = flaky
    orch = Orchestrator(paths=paths, registry=registry, max_attempts_per_phase=2)

    final = orch.run_to_completion("T-4")

    assert final.current_phase is SDLCPhase.DONE
    assert flaky.call_count == 2
    decisions = [t.decision for t in final.history if t.decision is not None]
    assert any(d.value == "retry" for d in decisions)
    assert any(d.value == "proceed" for d in decisions)


def test_blocks_after_max_attempts(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    registry = _ok_registry()
    always_fail = CannedSubagent(
        name=SubagentName.DEVELOPER,
        artifact_kwargs=canned_failing_artifact("perma-failed"),
    )
    registry[SubagentName.DEVELOPER] = always_fail
    orch = Orchestrator(paths=paths, registry=registry, max_attempts_per_phase=2)

    final = orch.run_to_completion("T-5")

    assert final.current_phase is SDLCPhase.BLOCKED
    assert always_fail.call_count == 2
    assert final.blocked_reason is not None and "DEVELOPMENT_GATE" in final.blocked_reason


def test_hitl_gate_escalates_to_needs_human(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(
        paths=paths,
        registry=_ok_registry(),
        gates=GateConfig(hitl_requirements_gate=True),
    )

    final = orch.run_to_completion("T-6")

    assert final.current_phase is SDLCPhase.NEEDS_HUMAN


def test_episodic_log_records_dispatch_and_gate_events(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(paths=paths, registry=_ok_registry())

    orch.run_to_completion("T-7")

    events = list(MemoryStores(paths).read_episodes())
    kinds = [e["kind"] for e in events]
    assert kinds.count("dispatch") == 3
    assert kinds.count("gate") == 3
    assert "transition" in kinds


def test_run_without_intake_auto_intakes(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(paths=paths, registry=_ok_registry())

    final = orch.run_to_completion("T-8")
    assert final.current_phase is SDLCPhase.DONE


def test_assignment_carries_injected_facts(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    stores = MemoryStores(paths)
    stores.append_project_fact({"claim": "repo uses pytest", "evidence": "pyproject.toml"})
    stores.append_subagent_lore(SubagentName.PR_REVIEWER, {"claim": "auth module fragile"})

    registry = _ok_registry()
    orch = Orchestrator(paths=paths, registry=registry)
    orch.run_to_completion("T-9")

    reviewer_assignment = registry[SubagentName.PR_REVIEWER].last_assignment
    assert reviewer_assignment is not None
    assert "repo uses pytest" in reviewer_assignment.injected_context.project_facts
    assert "auth module fragile" in reviewer_assignment.injected_context.subagent_lore
    assert any(
        "requirement_analysis.json" in a for a in reviewer_assignment.injected_context.relevant_artifacts
    )


@pytest.mark.parametrize("ticket_id", ["TICKET-A", "TICKET-B"])
def test_state_file_named_by_ticket(tmp_repo: Path, ticket_id: str) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(paths=paths, registry=_ok_registry())
    orch.intake(ticket_id)
    assert paths.state_file(ticket_id).exists()
