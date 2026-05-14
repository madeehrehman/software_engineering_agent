"""Stateful-by-injection task assignment + verified artifact return contracts.

Spec §6 (orchestrator → subagent) and §7 (subagent → orchestrator).
"""

from sdlc_agent.contracts.artifact import (
    ArtifactReturn,
    MemoryConfidence,
    MemoryScope,
    ProposedMemory,
    TaskStatus,
    VerificationBlock,
    VerificationCheck,
)
from sdlc_agent.contracts.assignment import (
    Constraints,
    InjectedContext,
    Permissions,
    SubagentName,
    TaskAssignment,
)

__all__ = [
    "ArtifactReturn",
    "Constraints",
    "InjectedContext",
    "MemoryConfidence",
    "MemoryScope",
    "Permissions",
    "ProposedMemory",
    "SubagentName",
    "TaskAssignment",
    "TaskStatus",
    "VerificationBlock",
    "VerificationCheck",
]
