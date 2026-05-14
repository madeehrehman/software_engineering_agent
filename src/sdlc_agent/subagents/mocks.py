"""Canned-artifact subagents for Phase 1 (testing the state machine end-to-end).

A real subagent is just a class with ``run(assignment) -> ArtifactReturn``. The
mocks here satisfy that contract by returning fixed artifacts so we can verify
the orchestrator's dispatch + gate routing without standing up real model calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdlc_agent.contracts import (
    ArtifactReturn,
    ProposedMemory,
    SubagentName,
    TaskAssignment,
    TaskStatus,
    VerificationBlock,
    VerificationCheck,
)


def canned_successful_artifact(
    *,
    artifact: dict[str, Any] | None = None,
    notes: str = "mock: ok",
    proposed_memory: list[ProposedMemory] | None = None,
) -> dict[str, Any]:
    """Build the kwargs for a passing ArtifactReturn (task_id filled by the mock)."""
    kwargs: dict[str, Any] = {
        "status": TaskStatus.COMPLETED,
        "artifact": artifact or {"mock": True},
        "verification": VerificationBlock(
            self_checks=[VerificationCheck(check="all required outputs present", passed=True)],
            passed=True,
            notes=notes,
        ),
    }
    if proposed_memory is not None:
        kwargs["proposed_memory"] = proposed_memory
    return kwargs


def canned_failing_artifact(reason: str = "mock failure") -> dict[str, Any]:
    """Build kwargs for a failing ArtifactReturn — drives the RETRY/BLOCKED routes."""
    return {
        "status": TaskStatus.FAILED,
        "artifact": {"error": reason},
        "verification": VerificationBlock(
            self_checks=[VerificationCheck(check="all required outputs present", passed=False)],
            passed=False,
            notes=reason,
        ),
    }


@dataclass
class CannedSubagent:
    """A stateless subagent that returns a pre-baked artifact.

    Tracks call count so tests can verify the orchestrator invoked it the right
    number of times (e.g. once per retry).
    """

    name: SubagentName
    artifact_kwargs: dict[str, Any] = field(default_factory=canned_successful_artifact)
    artifact_kwargs_after_first_call: dict[str, Any] | None = None
    call_count: int = 0
    last_assignment: TaskAssignment | None = None

    def run(self, assignment: TaskAssignment) -> ArtifactReturn:
        self.call_count += 1
        self.last_assignment = assignment

        kwargs = self.artifact_kwargs
        if self.call_count > 1 and self.artifact_kwargs_after_first_call is not None:
            kwargs = self.artifact_kwargs_after_first_call

        return ArtifactReturn(task_id=assignment.task_id, **kwargs)
