"""Phase 2 acceptance: orchestrator drives real subagents (mocked LLM) end-to-end.

We mock the Developer with a canned subagent because Phase 3 (real Developer +
sandbox) is out of scope per spec §12. The full SDLC walk still verifies that
the Backlog Analyzer and PR Reviewer plug into the orchestrator unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sdlc_agent.contracts import SubagentName
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.subagents import (
    BacklogAnalyzer,
    CannedSubagent,
    PRReviewer,
    canned_successful_artifact,
)


def _backlog_response() -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": "TICKET-12",
            "summary": "Add per-IP rate limiting to /api/login",
            "acceptance_criteria": [
                "HTTP 429 after threshold",
                "limit configurable per env",
            ],
            "ambiguities": [],
            "missing_info": [],
            "out_of_scope": [],
            "ready_for_development": True,
            "notes": "ready",
        },
        "proposed_memory": [
            {
                "scope": "project_fact",
                "claim": "security tickets carry priority High",
                "evidence": "TICKET-12 labeled 'security' and priority=High",
                "confidence": "high",
            }
        ],
    }


def _reviewer_response() -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": "approve",
            "summary": "Small, well-scoped change. Tests included.",
            "issues": [],
            "strengths": ["clean signature", "good error handling"],
        },
        "proposed_memory": [
            {
                "scope": "subagent_lore",
                "claim": "team prefers explicit return types on public functions",
                "evidence": "approved this PR which uses explicit return type",
                "confidence": "medium",
            }
        ],
    }


def test_full_lifecycle_with_real_subagents(
    tmp_repo: Path,
    jira_fixture_dir: Path,
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    llm = fake_llm_factory([_backlog_response(), _reviewer_response()])

    backlog = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )
    reviewer = PRReviewer(
        llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
    )
    developer = CannedSubagent(
        name=SubagentName.DEVELOPER,
        artifact_kwargs=canned_successful_artifact(
            artifact={"implementation_summary": "...", "tests": ["t1"]}
        ),
    )
    registry = {
        SubagentName.BACKLOG_ANALYZER: backlog,
        SubagentName.DEVELOPER: developer,
        SubagentName.PR_REVIEWER: reviewer,
    }

    orch = Orchestrator(paths=paths, registry=registry)
    orch.intake(
        "TICKET-12",
        ticket_inputs={
            "jira_key": "TICKET-12",
            "base_ref": small_git_repo["base_ref"],
            "head_ref": small_git_repo["head_ref"],
        },
    )
    final = orch.run_to_completion("TICKET-12")

    assert final.current_phase is SDLCPhase.DONE

    stores = MemoryStores(paths)
    facts = {f["claim"] for f in stores.read_project_facts()}
    assert "security tickets carry priority High" in facts

    backlog_artifact_path = paths.ticket_artifacts_dir(
        "TICKET-12"
    ) / "requirement_analysis.json"
    review_artifact_path = paths.ticket_artifacts_dir("TICKET-12") / "review.json"
    assert backlog_artifact_path.is_file()
    assert review_artifact_path.is_file()
