"""Phase 1: contract types validate as expected."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sdlc_agent.contracts import (
    ArtifactReturn,
    InjectedContext,
    MemoryConfidence,
    MemoryScope,
    Permissions,
    ProposedMemory,
    SubagentName,
    TaskAssignment,
    TaskStatus,
    VerificationBlock,
    VerificationCheck,
)


def test_task_assignment_defaults() -> None:
    a = TaskAssignment(
        task_id="t-1",
        ticket_id="TICKET-1",
        subagent=SubagentName.BACKLOG_ANALYZER,
        task="analyze",
    )
    assert a.injected_context == InjectedContext()
    assert a.constraints.permissions == Permissions()


def test_artifact_return_with_proposals() -> None:
    art = ArtifactReturn(
        task_id="t-1",
        status=TaskStatus.COMPLETED,
        artifact={"k": "v"},
        verification=VerificationBlock(
            self_checks=[VerificationCheck(check="x", passed=True)],
            passed=True,
        ),
        proposed_memory=[
            ProposedMemory(
                scope=MemoryScope.PROJECT_FACT,
                claim="repo uses pytest",
                evidence="pyproject has pytest",
                confidence=MemoryConfidence.HIGH,
            )
        ],
    )
    dumped = art.model_dump(mode="json")
    assert dumped["status"] == "completed"
    assert dumped["proposed_memory"][0]["scope"] == "project_fact"


def test_invalid_subagent_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskAssignment(
            task_id="t",
            ticket_id="T",
            subagent="qa_bot",  # type: ignore[arg-type]
            task="x",
        )
