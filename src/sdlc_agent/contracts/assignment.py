"""Task assignment contract: orchestrator → subagent (spec §6)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SubagentName(StrEnum):
    BACKLOG_ANALYZER = "backlog_analyzer"
    DEVELOPER = "developer"
    PR_REVIEWER = "pr_reviewer"


class InjectedContext(BaseModel):
    """The relevant slice of project memory the orchestrator hands the subagent.

    Subagents are stateless workers; this is how their task is made *stateful*.
    """

    project_facts: list[str] = Field(default_factory=list)
    subagent_lore: list[str] = Field(default_factory=list)
    relevant_artifacts: list[str] = Field(default_factory=list)


class Permissions(BaseModel):
    """Per-subagent permission grants. Defaults to nothing — opt in explicitly."""

    filesystem: str = "none"
    git: str = "none"
    jira: str = "none"


class Constraints(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    permissions: Permissions = Field(default_factory=Permissions)


class TaskAssignment(BaseModel):
    """Spec §6: assignment passed from orchestrator to subagent."""

    task_id: str
    ticket_id: str
    subagent: SubagentName
    task: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    injected_context: InjectedContext = Field(default_factory=InjectedContext)
    constraints: Constraints = Field(default_factory=Constraints)
    expected_artifact_schema: dict[str, Any] | None = None
