"""Live OpenAI smoke tests. Skipped unless `--run-live` AND OPENAI_API_KEY is set.

These exist so you can sanity-check that the real model returns a schema-valid
response for each subagent on a representative input.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdlc_agent.contracts import (
    Constraints,
    InjectedContext,
    SubagentName,
    TaskAssignment,
)
from sdlc_agent.llm import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.subagents import BacklogAnalyzer, PRReviewer


def _live_client() -> OpenAIClient:
    return OpenAIClient(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
    )


@pytest.mark.live
def test_live_backlog_analyzer_against_real_ticket(jira_fixture_dir: Path) -> None:
    analyzer = BacklogAnalyzer(
        llm=_live_client(), jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )
    out = analyzer.run(
        TaskAssignment(
            task_id="live-1",
            ticket_id="TICKET-12",
            subagent=SubagentName.BACKLOG_ANALYZER,
            task="live: analyze",
            inputs={},
            injected_context=InjectedContext(project_facts=["repo uses pytest"]),
            constraints=Constraints(),
        )
    )
    assert out.artifact["ticket_key"] == "TICKET-12"
    assert out.artifact["acceptance_criteria"]


@pytest.mark.live
def test_live_pr_reviewer_against_local_diff(small_git_repo: dict) -> None:
    reviewer = PRReviewer(
        llm=_live_client(), git=LocalGitClient(repo_root=small_git_repo["repo"])
    )
    out = reviewer.run(
        TaskAssignment(
            task_id="live-2",
            ticket_id="TICKET-12",
            subagent=SubagentName.PR_REVIEWER,
            task="live: review",
            inputs={"base_ref": small_git_repo["base_ref"], "head_ref": "HEAD"},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )
    assert out.artifact["verdict"] in {"approve", "request_changes", "comment"}
    assert out.artifact["summary"]
