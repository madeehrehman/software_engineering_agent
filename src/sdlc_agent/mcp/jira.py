"""Jira MCP clients.

Two implementations:

* :class:`JiraMCPStub`    — Phase 0 in-process handshake stub.
* :class:`FixtureJiraMCP` — Phase 2 fixture-backed client. Loads issues from a
  directory of JSON files; the surface (``get_issue``, ``handshake``) mirrors
  what a real Jira MCP server would expose, so swapping in a real server later
  is a constructor change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from sdlc_agent.mcp.client import HandshakeResult


class JiraMCPError(RuntimeError):
    """Base for Jira MCP failures."""


class IssueNotFound(JiraMCPError):
    pass


class JiraComment(BaseModel):
    author: str
    body: str
    created: str | None = None


class JiraIssue(BaseModel):
    """Subset of Jira's issue shape the Backlog Analyzer actually needs."""

    key: str
    summary: str
    description: str = ""
    issue_type: str = "Story"
    status: str = "To Do"
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    comments: list[JiraComment] = Field(default_factory=list)
    reporter: str | None = None
    assignee: str | None = None


@dataclass
class JiraMCPStub:
    """Phase 0 stub — handshake only, no issue data."""

    server_name: str = "jira-mcp-stub"

    def handshake(self) -> HandshakeResult:
        return HandshakeResult(
            ok=True,
            server=self.server_name,
            transport="in-process",
            detail="stub Jira MCP; real client deferred to Phase 2",
        )


@dataclass
class FixtureJiraMCP:
    """Fixture-backed Jira MCP. Loads issues from ``<fixture_dir>/<KEY>.json``.

    Shape mirrors what a real Jira MCP server would return so subagents do not
    need to know which backend they're talking to.
    """

    fixture_dir: Path
    server_name: str = "jira-fixture-mcp"

    def handshake(self) -> HandshakeResult:
        ok = self.fixture_dir.is_dir()
        return HandshakeResult(
            ok=ok,
            server=self.server_name,
            transport="fixture",
            detail=(
                f"loading from {self.fixture_dir}" if ok
                else f"fixture dir missing: {self.fixture_dir}"
            ),
        )

    def get_issue(self, key: str) -> JiraIssue:
        path = self.fixture_dir / f"{key}.json"
        if not path.exists():
            raise IssueNotFound(f"no fixture for issue {key} at {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise JiraMCPError(f"fixture {path} is not valid JSON: {e}") from e
        return JiraIssue.model_validate(data)

    def list_issues(self) -> list[str]:
        """List issue keys available as fixtures. Convenience for tests/demos."""
        if not self.fixture_dir.is_dir():
            return []
        return sorted(p.stem for p in self.fixture_dir.glob("*.json"))
