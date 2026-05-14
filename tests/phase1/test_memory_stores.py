"""Phase 1: memory stores read/write."""

from __future__ import annotations

import json
from pathlib import Path

from sdlc_agent.contracts import (
    ArtifactReturn,
    SubagentName,
    TaskStatus,
    VerificationBlock,
)
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator.state_machine import SDLCPhase, TicketState


def _stores(tmp_repo: Path) -> MemoryStores:
    paths = initialize_deepagent(tmp_repo)
    return MemoryStores(paths)


def test_state_round_trip(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    state = TicketState(ticket_id="T-1", current_phase=SDLCPhase.REQUIREMENTS_ANALYSIS)
    state.bump_attempts(SDLCPhase.REQUIREMENTS_ANALYSIS)

    stores.save_ticket_state(state)
    loaded = stores.load_ticket_state("T-1")

    assert loaded is not None
    assert loaded.current_phase is SDLCPhase.REQUIREMENTS_ANALYSIS
    assert loaded.attempts[SDLCPhase.REQUIREMENTS_ANALYSIS] == 1


def test_state_missing_returns_none(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    assert stores.load_ticket_state("never-existed") is None


def test_project_fact_append(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    stores.append_project_fact({"claim": "uses pytest"})
    stores.append_project_fact({"claim": "uses GitHub Actions"})

    facts = stores.read_project_facts()
    assert [f["claim"] for f in facts] == ["uses pytest", "uses GitHub Actions"]


def test_subagent_lore_isolation(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    stores.append_subagent_lore(SubagentName.PR_REVIEWER, {"claim": "auth module fragile"})

    assert [e["claim"] for e in stores.read_subagent_lore(SubagentName.PR_REVIEWER)] == [
        "auth module fragile"
    ]
    assert stores.read_subagent_lore(SubagentName.BACKLOG_ANALYZER) == []


def test_episodic_log_appends(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    stores.append_episode({"kind": "transition", "ticket_id": "T-1"})
    stores.append_episode({"kind": "dispatch", "ticket_id": "T-1"})

    events = list(stores.read_episodes())
    assert [e["kind"] for e in events] == ["transition", "dispatch"]
    assert "timestamp" in events[0]


def test_artifact_save_load(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    art = ArtifactReturn(
        task_id="t-1",
        status=TaskStatus.COMPLETED,
        artifact={"requirement_summary": "build X"},
        verification=VerificationBlock(passed=True),
    )

    path = stores.save_artifact("T-1", SDLCPhase.REQUIREMENTS_ANALYSIS, art)
    assert path.name == "requirement_analysis.json"
    assert json.loads(path.read_text())["artifact"]["requirement_summary"] == "build X"

    loaded = stores.load_artifact("T-1", SDLCPhase.REQUIREMENTS_ANALYSIS)
    assert loaded is not None
    assert loaded.task_id == "t-1"


def test_artifact_load_missing(tmp_repo: Path) -> None:
    stores = _stores(tmp_repo)
    assert stores.load_artifact("T-X", SDLCPhase.REQUIREMENTS_ANALYSIS) is None
