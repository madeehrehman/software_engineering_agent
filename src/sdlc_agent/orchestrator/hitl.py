"""Human-in-the-loop approvers for spec §11 Phase 4 (REQUIREMENTS_GATE / REVIEW_GATE).

The orchestrator delegates HITL-enabled gates to an injected :class:`GateApprover`.
Return values are translated by the orchestrator into ``GateDecision``:

* ``True``  → PROCEED          (human approved)
* ``False`` → RETRY (or BLOCKED at max attempts; human rejected)
* ``None``  → NEEDS_HUMAN      (halt; default fallback when no automated approver)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sdlc_agent.contracts import ArtifactReturn
from sdlc_agent.orchestrator.state_machine import SDLCPhase, TicketState


@runtime_checkable
class GateApprover(Protocol):
    def approve(
        self,
        gate: SDLCPhase,
        artifact: ArtifactReturn,
        state: TicketState,
    ) -> bool | None:
        ...


class HaltForHuman:
    """Default: pause workflow at NEEDS_HUMAN so a human can resume out-of-band."""

    def approve(
        self, gate: SDLCPhase, artifact: ArtifactReturn, state: TicketState
    ) -> bool | None:
        return None


class AutoApprove:
    """Always approve. For tests and fully-automated runs."""

    def approve(
        self, gate: SDLCPhase, artifact: ArtifactReturn, state: TicketState
    ) -> bool | None:
        return True


class AutoReject:
    """Always reject. Drives the RETRY path under HITL config."""

    def approve(
        self, gate: SDLCPhase, artifact: ArtifactReturn, state: TicketState
    ) -> bool | None:
        return False


class ScriptedApprover:
    """Returns answers from a queue in order. Useful for mixed-decision tests."""

    def __init__(self, decisions: list[bool | None]) -> None:
        self._queue: list[bool | None] = list(decisions)
        self.calls: list[tuple[SDLCPhase, str]] = []

    def approve(
        self, gate: SDLCPhase, artifact: ArtifactReturn, state: TicketState
    ) -> bool | None:
        self.calls.append((gate, state.ticket_id))
        if not self._queue:
            raise RuntimeError("ScriptedApprover ran out of decisions")
        return self._queue.pop(0)
