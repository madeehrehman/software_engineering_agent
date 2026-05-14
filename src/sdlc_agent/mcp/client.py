"""Base protocol for MCP clients.

Phase 0 only requires a ``handshake()`` so the orchestrator can verify it has the
servers it expects before running a ticket. Real method surfaces (Jira issue read,
git diff fetch, etc.) land alongside the subagents that need them in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class HandshakeResult:
    """Returned by every MCP client's ``handshake()``.

    ``ok=False`` should be surfaced to the orchestrator so it can refuse to start
    a ticket against a server that didn't come up.
    """

    ok: bool
    server: str
    transport: str
    detail: str = ""


@runtime_checkable
class MCPClient(Protocol):
    """Minimal MCP client surface for Phase 0."""

    server_name: str

    def handshake(self) -> HandshakeResult:
        ...
