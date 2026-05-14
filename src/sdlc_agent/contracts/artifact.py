"""Artifact return contract: subagent → orchestrator (spec §7)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"


class MemoryScope(StrEnum):
    PROJECT_FACT = "project_fact"
    SUBAGENT_LORE = "subagent_lore"


class MemoryConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationCheck(BaseModel):
    check: str
    passed: bool


class VerificationBlock(BaseModel):
    """Self-verification before return — orchestrator should not second-guess raw output."""

    self_checks: list[VerificationCheck] = Field(default_factory=list)
    passed: bool
    notes: str = ""


class ProposedMemory(BaseModel):
    """Subagents *propose* durable facts; only the orchestrator can promote (§5.3)."""

    scope: MemoryScope
    claim: str
    evidence: str
    confidence: MemoryConfidence


class ArtifactReturn(BaseModel):
    """Spec §7: structured return from a subagent."""

    task_id: str
    status: TaskStatus
    artifact: dict[str, Any] = Field(default_factory=dict)
    verification: VerificationBlock
    proposed_memory: list[ProposedMemory] = Field(default_factory=list)
