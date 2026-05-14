"""Phase 4: HITL approvers translate into the right gate decisions."""

from __future__ import annotations

from pathlib import Path

from sdlc_agent.config import GateConfig
from sdlc_agent.contracts import SubagentName
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.orchestrator.hitl import (
    AutoApprove,
    AutoReject,
    HaltForHuman,
    ScriptedApprover,
)
from sdlc_agent.subagents.mocks import CannedSubagent, canned_successful_artifact


def _registry() -> dict[SubagentName, CannedSubagent]:
    return {
        SubagentName.BACKLOG_ANALYZER: CannedSubagent(
            name=SubagentName.BACKLOG_ANALYZER,
            artifact_kwargs=canned_successful_artifact(artifact={"acceptance_criteria": ["ac"]}),
        ),
        SubagentName.DEVELOPER: CannedSubagent(
            name=SubagentName.DEVELOPER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"implementation_summary": "...", "tests": ["t"]}
            ),
        ),
        SubagentName.PR_REVIEWER: CannedSubagent(
            name=SubagentName.PR_REVIEWER,
            artifact_kwargs=canned_successful_artifact(artifact={"verdict": "approve"}),
        ),
    }


def test_auto_approve_drives_to_done(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(
        paths=paths,
        registry=_registry(),
        gates=GateConfig(hitl_requirements_gate=True, hitl_review_gate=True),
        approver=AutoApprove(),
    )

    final = orch.run_to_completion("T-1")

    assert final.current_phase is SDLCPhase.DONE
    kinds = [e["kind"] for e in MemoryStores(paths).read_episodes()]
    assert kinds.count("hitl_approval") == 2


def test_halt_for_human_default_is_needs_human(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(
        paths=paths,
        registry=_registry(),
        gates=GateConfig(hitl_requirements_gate=True),
        approver=HaltForHuman(),
    )

    final = orch.run_to_completion("T-2")

    assert final.current_phase is SDLCPhase.NEEDS_HUMAN


def test_auto_reject_retries_then_blocks(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    orch = Orchestrator(
        paths=paths,
        registry=_registry(),
        gates=GateConfig(hitl_requirements_gate=True),
        approver=AutoReject(),
        max_attempts_per_phase=2,
    )

    final = orch.run_to_completion("T-3")

    assert final.current_phase is SDLCPhase.BLOCKED
    kinds = [e["kind"] for e in MemoryStores(paths).read_episodes()]
    assert kinds.count("hitl_rejection") == 2


def test_scripted_approver_reject_then_approve_completes(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    approver = ScriptedApprover([False, True])
    orch = Orchestrator(
        paths=paths,
        registry=_registry(),
        gates=GateConfig(hitl_requirements_gate=True),
        approver=approver,
        max_attempts_per_phase=2,
    )

    final = orch.run_to_completion("T-4")

    assert final.current_phase is SDLCPhase.DONE
    assert [c[0] for c in approver.calls] == [
        SDLCPhase.REQUIREMENTS_GATE,
        SDLCPhase.REQUIREMENTS_GATE,
    ]


def test_review_gate_hitl_only_fires_at_review_gate(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    approver = ScriptedApprover([True])
    orch = Orchestrator(
        paths=paths,
        registry=_registry(),
        gates=GateConfig(hitl_review_gate=True),
        approver=approver,
    )

    final = orch.run_to_completion("T-5")

    assert final.current_phase is SDLCPhase.DONE
    assert [c[0] for c in approver.calls] == [SDLCPhase.REVIEW_GATE]
