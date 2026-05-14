"""MCP client abstractions + stub / fixture / local implementations."""

from sdlc_agent.mcp.client import HandshakeResult, MCPClient
from sdlc_agent.mcp.git import GitMCPStub, GitMCPError, LocalGitClient
from sdlc_agent.mcp.jira import (
    FixtureJiraMCP,
    JiraIssue,
    JiraMCPError,
    JiraMCPStub,
)

__all__ = [
    "FixtureJiraMCP",
    "GitMCPError",
    "GitMCPStub",
    "HandshakeResult",
    "JiraIssue",
    "JiraMCPError",
    "JiraMCPStub",
    "LocalGitClient",
    "MCPClient",
]
