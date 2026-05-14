"""Phase 2: fixture-backed Jira MCP behaves like a real server's surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdlc_agent.mcp.jira import FixtureJiraMCP, IssueNotFound


def test_handshake_ok_when_fixture_dir_exists(jira_fixture_dir: Path) -> None:
    result = FixtureJiraMCP(fixture_dir=jira_fixture_dir).handshake()
    assert result.ok is True
    assert result.transport == "fixture"


def test_handshake_fails_when_fixture_dir_missing(tmp_path: Path) -> None:
    result = FixtureJiraMCP(fixture_dir=tmp_path / "nope").handshake()
    assert result.ok is False


def test_get_issue_round_trips_schema(jira_fixture_dir: Path) -> None:
    client = FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    issue = client.get_issue("TICKET-12")

    assert issue.key == "TICKET-12"
    assert issue.priority == "High"
    assert "security" in issue.labels
    assert len(issue.acceptance_criteria) == 2
    assert len(issue.comments) == 1
    assert issue.comments[0].author == "carol@example.com"


def test_missing_issue_raises(jira_fixture_dir: Path) -> None:
    with pytest.raises(IssueNotFound):
        FixtureJiraMCP(fixture_dir=jira_fixture_dir).get_issue("TICKET-9999")


def test_list_issues(jira_fixture_dir: Path) -> None:
    keys = FixtureJiraMCP(fixture_dir=jira_fixture_dir).list_issues()
    assert keys == ["TICKET-12", "TICKET-13"]
