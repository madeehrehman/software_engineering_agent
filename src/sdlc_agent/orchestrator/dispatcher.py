"""The orchestrator's main loop: dispatch → gate → transition → persist.

Owns the SDLC state machine and the per-ticket memory writes. Subagents (real or
mocked) implement the same minimal protocol: ``run(assignment) -> ArtifactReturn``.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from sdlc_agent.config import GateConfig
from sdlc_agent.contracts import (
    ArtifactReturn,
    Constraints,
    InjectedContext,
    Permissions,
    SubagentName,
    TaskAssignment,
)
from sdlc_agent.memory.paths import DeepAgentPaths
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator.curation import (
    CurationGate,
    CurationResult,
    PromotionDecision,
    is_promoted,
)
from sdlc_agent.orchestrator.hitl import GateApprover, HaltForHuman
from sdlc_agent.orchestrator.state_machine import (
    GATE_PHASES,
    GateDecision,
    SDLCPhase,
    TicketState,
    TransitionRecord,
    WORK_PHASES,
    evaluate_default_gate,
    gate_after,
    next_phase_for_decision,
    phase_to_subagent,
    work_before,
)


@runtime_checkable
class Subagent(Protocol):
    """Subagents are stateless workers with a single entry point."""

    name: SubagentName

    def run(self, assignment: TaskAssignment) -> ArtifactReturn:
        ...


SubagentRegistry = dict[SubagentName, Subagent]


_DEFAULT_PERMISSIONS: dict[SubagentName, Permissions] = {
    SubagentName.BACKLOG_ANALYZER: Permissions(filesystem="none", git="none", jira="read"),
    SubagentName.DEVELOPER: Permissions(filesystem="read-write", git="read", jira="none"),
    SubagentName.PR_REVIEWER: Permissions(filesystem="read-only", git="review", jira="none"),
}


class OrchestratorError(RuntimeError):
    pass


class Orchestrator:
    """Drives a ticket through the SDLC, persisting state at every transition."""

    def __init__(
        self,
        *,
        paths: DeepAgentPaths,
        registry: SubagentRegistry,
        gates: GateConfig | None = None,
        max_attempts_per_phase: int = 2,
        approver: GateApprover | None = None,
        curation: CurationGate | None = None,
        session_id: str | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.gates = gates or GateConfig()
        self.max_attempts_per_phase = max_attempts_per_phase
        self.memory = MemoryStores(paths)
        self.approver: GateApprover = approver or HaltForHuman()
        self.curation = curation or CurationGate(self.memory)
        self.session_id = session_id or uuid.uuid4().hex[:12]

    # ---------------------------------------------------------------- intake
    def intake(
        self,
        ticket_id: str,
        *,
        ticket_inputs: dict[str, object] | None = None,
    ) -> TicketState:
        """Create (or load) the working state for ``ticket_id`` at INTAKE.

        ``ticket_inputs`` is stamped onto the TicketState and flowed into every
        downstream assignment's ``inputs`` dict — that's how per-ticket metadata
        (Jira issue key, git refs, etc.) reaches the subagents.
        """
        existing = self.memory.load_ticket_state(ticket_id)
        if existing is not None:
            return existing

        state = TicketState(
            ticket_id=ticket_id,
            current_phase=SDLCPhase.INTAKE,
            ticket_inputs=dict(ticket_inputs or {}),
        )
        state.record_transition(SDLCPhase.REQUIREMENTS_ANALYSIS, rationale="intake")
        self.memory.save_ticket_state(state)
        self._log_episode("transition", state, state.history[-1])
        return state

    # ----------------------------------------------------------------- loop
    def run_to_completion(self, ticket_id: str, *, max_steps: int = 20) -> TicketState:
        """Loop ``advance()`` until terminal or ``max_steps`` reached."""
        state = self.memory.load_ticket_state(ticket_id) or self.intake(ticket_id)
        for _ in range(max_steps):
            if state.is_terminal:
                return state
            state = self.advance(ticket_id)
        if not state.is_terminal:
            raise OrchestratorError(
                f"ticket {ticket_id} did not reach terminal within {max_steps} steps"
            )
        return state

    def advance(self, ticket_id: str) -> TicketState:
        """Perform one atomic step from the current phase and persist.

        * Work phase  → dispatch subagent, save artifact, transition to its gate.
        * Gate phase  → evaluate, transition to next work phase / retry / terminal.
        * INTAKE      → transition to first work phase.
        * Terminal    → no-op.
        """
        state = self.memory.load_ticket_state(ticket_id)
        if state is None:
            state = self.intake(ticket_id)

        if state.is_terminal:
            return state

        phase = state.current_phase
        if phase is SDLCPhase.INTAKE:
            state.record_transition(SDLCPhase.REQUIREMENTS_ANALYSIS, rationale="intake")
        elif phase in WORK_PHASES:
            self._dispatch_work_phase(state, phase)
        elif phase in GATE_PHASES:
            self._evaluate_gate(state, phase)
        else:
            raise OrchestratorError(f"unhandled phase: {phase}")

        self.memory.save_ticket_state(state)
        return state

    # --------------------------------------------------------- work dispatch
    def _dispatch_work_phase(self, state: TicketState, phase: SDLCPhase) -> None:
        subagent_name = phase_to_subagent(phase)
        subagent = self.registry.get(subagent_name)
        if subagent is None:
            raise OrchestratorError(
                f"no subagent registered for {subagent_name} (phase {phase})"
            )

        attempt = state.bump_attempts(phase)
        assignment = self._build_assignment(state, phase, subagent_name, attempt)
        artifact = subagent.run(assignment)

        self.memory.save_artifact(state.ticket_id, phase, artifact)
        self._log_episode(
            "dispatch",
            state,
            extra={
                "phase": phase.value,
                "subagent": subagent_name.value,
                "task_id": assignment.task_id,
                "attempt": attempt,
                "verification_passed": artifact.verification.passed,
                "status": artifact.status.value,
                "proposed_memory_count": len(artifact.proposed_memory),
            },
        )

        self._run_curation(state, artifact, subagent_name)

        gate = gate_after(phase)
        record = state.record_transition(
            gate,
            rationale=f"{phase} → {gate} after subagent return",
        )
        self._log_episode("transition", state, record)

    # ----------------------------------------------------------- gate logic
    def _evaluate_gate(self, state: TicketState, gate: SDLCPhase) -> None:
        work_phase = work_before(gate)
        artifact = self.memory.load_artifact(state.ticket_id, work_phase)
        if artifact is None:
            raise OrchestratorError(
                f"no artifact for ticket {state.ticket_id} at {work_phase}; "
                "gate cannot evaluate"
            )

        attempts = state.attempts.get(work_phase, 0)

        if self._gate_requires_human(gate):
            approval = self.approver.approve(gate, artifact, state)
            decision, rationale = self._decision_from_approval(
                gate, approval, attempts_in_phase=attempts
            )
            self._log_hitl_event(state, gate, approval)
        else:
            decision, rationale = evaluate_default_gate(
                gate,
                artifact,
                attempts_in_phase=attempts,
                max_attempts=self.max_attempts_per_phase,
                require_human=False,
            )

        next_phase = next_phase_for_decision(gate, decision)
        if next_phase is SDLCPhase.BLOCKED:
            state.blocked_reason = rationale

        record = state.record_transition(next_phase, decision=decision, rationale=rationale)
        self._log_episode("gate", state, record)

    def _gate_requires_human(self, gate: SDLCPhase) -> bool:
        if gate is SDLCPhase.REQUIREMENTS_GATE and self.gates.hitl_requirements_gate:
            return True
        if gate is SDLCPhase.REVIEW_GATE and self.gates.hitl_review_gate:
            return True
        return False

    def _decision_from_approval(
        self,
        gate: SDLCPhase,
        approval: bool | None,
        *,
        attempts_in_phase: int,
    ) -> tuple[GateDecision, str]:
        if approval is True:
            return GateDecision.PROCEED, f"{gate}: human approved"
        if approval is False:
            if attempts_in_phase >= self.max_attempts_per_phase:
                return (
                    GateDecision.BLOCKED,
                    f"{gate}: human rejected after {attempts_in_phase} attempts",
                )
            return GateDecision.RETRY, f"{gate}: human rejected; retrying"
        return GateDecision.NEEDS_HUMAN, f"{gate}: HITL approval required by config"

    def _log_hitl_event(
        self,
        state: TicketState,
        gate: SDLCPhase,
        approval: bool | None,
    ) -> None:
        if approval is True:
            self._log_episode("hitl_approval", state, extra={"gate": gate.value})
        elif approval is False:
            self._log_episode("hitl_rejection", state, extra={"gate": gate.value})
        else:
            self._log_episode("hitl_halt", state, extra={"gate": gate.value})

    # -------------------------------------------------------- curation gate
    def _run_curation(
        self,
        state: TicketState,
        artifact: ArtifactReturn,
        subagent: SubagentName,
    ) -> list[CurationResult]:
        results = self.curation.evaluate(
            artifact, subagent=subagent, ticket_id=state.ticket_id
        )
        for r in results:
            self._log_episode(
                "proposal_received",
                state,
                extra={
                    "claim": r.proposal.claim,
                    "scope": r.proposal.scope.value,
                    "confidence": r.proposal.confidence.value,
                    "decision": r.decision.value,
                    "rationale": r.rationale,
                    "stored_id": r.stored_id,
                    "subagent": subagent.value,
                },
            )
            if is_promoted(r.decision):
                self._log_episode(
                    "promotion",
                    state,
                    extra={
                        "claim": r.proposal.claim,
                        "scope": r.proposal.scope.value,
                        "stored_id": r.stored_id,
                        "decision": r.decision.value,
                    },
                )
            elif r.decision is PromotionDecision.REJECTED_NO_EVIDENCE:
                self._log_episode(
                    "rejection",
                    state,
                    extra={
                        "claim": r.proposal.claim,
                        "rationale": r.rationale,
                    },
                )
        return results

    # ---------------------------------------------------- assignment builder
    def _build_assignment(
        self,
        state: TicketState,
        phase: SDLCPhase,
        subagent: SubagentName,
        attempt: int,
    ) -> TaskAssignment:
        injected = self._inject_context(state, phase, subagent)
        permissions = _DEFAULT_PERMISSIONS.get(subagent, Permissions())
        inputs: dict[str, object] = {
            **state.ticket_inputs,
            "phase": phase.value,
            "attempt": attempt,
        }
        inputs.update(self._prior_artifact_inputs(state, phase))
        return TaskAssignment(
            task_id=f"{state.ticket_id}-{phase.value.lower()}-a{attempt}-{uuid.uuid4().hex[:6]}",
            ticket_id=state.ticket_id,
            subagent=subagent,
            task=f"Execute {phase.value} for ticket {state.ticket_id} (attempt {attempt}).",
            inputs=inputs,
            injected_context=injected,
            constraints=Constraints(
                allowed_tools=list(injected.relevant_artifacts and ["filesystem:read"] or []),
                permissions=permissions,
            ),
        )

    def _prior_artifact_inputs(
        self, state: TicketState, phase: SDLCPhase
    ) -> dict[str, object]:
        """Inline prior-phase artifact bodies into the assignment.

        Subagents do not need filesystem access to ``.deepagent/`` — the
        orchestrator hands them the content they need. The injected_context
        still carries *paths* for audit, but content is in ``inputs``.
        """
        out: dict[str, object] = {}
        if phase is SDLCPhase.DEVELOPMENT:
            ra = self.memory.load_artifact(state.ticket_id, SDLCPhase.REQUIREMENTS_ANALYSIS)
            if ra is not None:
                out["requirement_analysis"] = ra.artifact
        elif phase is SDLCPhase.PR_REVIEW:
            dev = self.memory.load_artifact(state.ticket_id, SDLCPhase.DEVELOPMENT)
            if dev is not None:
                out["implementation_summary"] = dev.artifact
        return out

    def _inject_context(
        self,
        state: TicketState,
        phase: SDLCPhase,
        subagent: SubagentName,
    ) -> InjectedContext:
        """Hand the subagent the slice of memory the orchestrator deems relevant.

        Phase 1 uses an "everything curated" slice (all project facts, all lore for
        this subagent, all prior artifacts for this ticket). Smarter slicing is a
        Phase 4 concern, not load-bearing for the FSM walk-through.
        """
        project_facts = [
            f.get("claim", "") for f in self.memory.read_project_facts() if f.get("claim")
        ]
        lore = [
            f.get("claim", "") for f in self.memory.read_subagent_lore(subagent) if f.get("claim")
        ]
        prior_artifacts: list[str] = []
        ticket_dir = self.paths.ticket_artifacts_dir(state.ticket_id)
        if ticket_dir.exists():
            for p in sorted(ticket_dir.iterdir()):
                if p.is_file() and p.suffix == ".json":
                    prior_artifacts.append(str(p.relative_to(self.paths.repo_root)))

        return InjectedContext(
            project_facts=project_facts,
            subagent_lore=lore,
            relevant_artifacts=prior_artifacts,
        )

    # -------------------------------------------------------------- episodic
    def _log_episode(
        self,
        kind: str,
        state: TicketState,
        record: TransitionRecord | None = None,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "kind": kind,
            "session_id": self.session_id,
            "ticket_id": state.ticket_id,
            "current_phase": state.current_phase.value,
        }
        if record is not None:
            payload["transition"] = record.model_dump(mode="json")
        if extra:
            payload.update(extra)
        self.memory.append_episode(payload)
