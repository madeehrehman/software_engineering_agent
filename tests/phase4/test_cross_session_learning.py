"""Phase 4 acceptance test (spec §11 Phase 4):

    Run two tickets in sequence; verify session 2 receives injected facts
    learned in session 1; verify a false observation is *not* promoted.

The two sessions share the same `.deepagent/` folder but use distinct Orchestrator
instances, simulating a process restart between tickets.
"""

from __future__ import annotations

from pathlib import Path

from sdlc_agent.contracts import (
    MemoryConfidence,
    MemoryScope,
    ProposedMemory,
    SubagentName,
)
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.subagents.mocks import CannedSubagent, canned_successful_artifact


_HIGH = MemoryConfidence.HIGH
_MEDIUM = MemoryConfidence.MEDIUM
_FACT = MemoryScope.PROJECT_FACT
_LORE = MemoryScope.SUBAGENT_LORE


def _session1_registry() -> dict[SubagentName, CannedSubagent]:
    return {
        SubagentName.BACKLOG_ANALYZER: CannedSubagent(
            name=SubagentName.BACKLOG_ANALYZER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"acceptance_criteria": ["ac1"]},
                proposed_memory=[
                    ProposedMemory(
                        scope=_FACT,
                        claim="repo uses pytest",
                        evidence="pyproject.toml lists pytest under dev deps",
                        confidence=_HIGH,
                    ),
                    ProposedMemory(
                        scope=_FACT,
                        claim="auth tests are slow",
                        evidence="",
                        confidence=_MEDIUM,
                    ),
                ],
            ),
        ),
        SubagentName.DEVELOPER: CannedSubagent(
            name=SubagentName.DEVELOPER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"implementation_summary": "...", "tests": ["t1"]},
                proposed_memory=[
                    ProposedMemory(
                        scope=_FACT,
                        claim="CI is GitHub Actions",
                        evidence="hint observed once",
                        confidence=_MEDIUM,
                    ),
                ],
            ),
        ),
        SubagentName.PR_REVIEWER: CannedSubagent(
            name=SubagentName.PR_REVIEWER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"verdict": "approve"},
                proposed_memory=[
                    ProposedMemory(
                        scope=_LORE,
                        claim="auth module flagged fragile",
                        evidence="this PR touched auth/ and 3 prior reviews flagged it",
                        confidence=_HIGH,
                    ),
                ],
            ),
        ),
    }


def _session2_registry() -> dict[SubagentName, CannedSubagent]:
    return {
        SubagentName.BACKLOG_ANALYZER: CannedSubagent(
            name=SubagentName.BACKLOG_ANALYZER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"acceptance_criteria": ["ac2"]},
            ),
        ),
        SubagentName.DEVELOPER: CannedSubagent(
            name=SubagentName.DEVELOPER,
            artifact_kwargs=canned_successful_artifact(
                artifact={"implementation_summary": "...", "tests": ["t2"]},
                proposed_memory=[
                    ProposedMemory(
                        scope=_FACT,
                        claim="CI is GitHub Actions",
                        evidence="second sighting in .github/workflows/ci.yml",
                        confidence=_MEDIUM,
                    ),
                ],
            ),
        ),
        SubagentName.PR_REVIEWER: CannedSubagent(
            name=SubagentName.PR_REVIEWER,
            artifact_kwargs=canned_successful_artifact(artifact={"verdict": "approve"}),
        ),
    }


def test_session_two_inherits_promoted_facts_and_rejects_false_observations(
    tmp_repo: Path,
) -> None:
    paths = initialize_deepagent(tmp_repo)

    Orchestrator(paths=paths, registry=_session1_registry()).run_to_completion("TICKET-1")

    stores = MemoryStores(paths)
    facts_after_s1 = {f["claim"] for f in stores.read_project_facts()}
    assert "repo uses pytest" in facts_after_s1, "HIGH+evidence should promote on first sighting"
    assert "auth tests are slow" not in facts_after_s1, (
        "no-evidence proposal must NOT be promoted (spec §5.3)"
    )
    assert "CI is GitHub Actions" not in facts_after_s1, (
        "MEDIUM-confidence single sighting must NOT be promoted yet"
    )
    pr_lore_s1 = {e["claim"] for e in stores.read_subagent_lore(SubagentName.PR_REVIEWER)}
    assert "auth module flagged fragile" in pr_lore_s1

    s2_registry = _session2_registry()
    Orchestrator(paths=paths, registry=s2_registry).run_to_completion("TICKET-2")

    facts_after_s2 = {f["claim"] for f in stores.read_project_facts()}
    assert "CI is GitHub Actions" in facts_after_s2, (
        "second MEDIUM sighting across sessions should promote (corroborated)"
    )
    assert "auth tests are slow" not in facts_after_s2, (
        "false observation must remain unpromoted across sessions"
    )

    backlog_assignment = s2_registry[SubagentName.BACKLOG_ANALYZER].last_assignment
    assert backlog_assignment is not None
    injected = backlog_assignment.injected_context.project_facts
    assert "repo uses pytest" in injected, (
        "session 2 backlog analyzer must see session 1's promoted fact"
    )

    pr_assignment = s2_registry[SubagentName.PR_REVIEWER].last_assignment
    assert pr_assignment is not None
    assert "auth module flagged fragile" in pr_assignment.injected_context.subagent_lore


def test_episodic_log_records_promotions_and_rejections(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    Orchestrator(paths=paths, registry=_session1_registry()).run_to_completion("T-1")

    stores = MemoryStores(paths)
    events = list(stores.read_episodes())
    kinds = [e["kind"] for e in events]

    assert "proposal_received" in kinds
    assert "promotion" in kinds
    assert "rejection" in kinds
    promotion_events = [e for e in events if e["kind"] == "promotion"]
    claims = {e["claim"] for e in promotion_events}
    assert "repo uses pytest" in claims
    assert "auth module flagged fragile" in claims

    rejection_events = [e for e in events if e["kind"] == "rejection"]
    assert any(e["claim"] == "auth tests are slow" for e in rejection_events)
