"""Phase 0: MCP stub clients handshake cleanly (no external infra)."""

from __future__ import annotations

from sdlc_agent.mcp import GitMCPStub, HandshakeResult, JiraMCPStub, MCPClient


def test_git_stub_handshake() -> None:
    result = GitMCPStub().handshake()
    assert isinstance(result, HandshakeResult)
    assert result.ok is True
    assert result.server == "git-mcp-stub"


def test_jira_stub_handshake() -> None:
    result = JiraMCPStub().handshake()
    assert result.ok is True
    assert result.server == "jira-mcp-stub"


def test_stubs_satisfy_protocol() -> None:
    assert isinstance(GitMCPStub(), MCPClient)
    assert isinstance(JiraMCPStub(), MCPClient)
