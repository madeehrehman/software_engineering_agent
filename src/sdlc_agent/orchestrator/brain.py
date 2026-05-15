"""Orchestrator brain: the deep-agent supervisor (spec §3.1).

The orchestrator is the long-horizon reasoning layer — planning, gate decisions,
and dispatch briefs. Subagents remain stateless workers with narrow prompts;
this module holds the system prompt and LLM calls that make the supervisor
the brain of the system.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sdlc_agent.contracts import ArtifactReturn, SubagentName
from sdlc_agent.llm import OpenAIClient
from sdlc_agent.memory.trajectories import TrajectoryRecorder

if TYPE_CHECKING:
    from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator.state_machine import (
    GATE_PHASES,
    GateDecision,
    SDLCPhase,
    TicketState,
    evaluate_default_gate,
    work_before,
)
from sdlc_agent.skills import SkillLoader, assemble_system_prompt
from sdlc_agent.subagents.base import call_llm_with_schema

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the Orchestrator — the deep agent supervisor for a project-scoped SDLC system.

You own the long-horizon plan and every gate decision. You do NOT write code, run
tests, or review diffs directly. You delegate to specialized subagents and judge
their verified artifacts against the ticket goal, curated project memory, and
phase-specific gate criteria.

Responsibilities:
  * Maintain a durable ticket plan (goal, phase checklist, risks, current focus).
  * At each SDLC gate, choose exactly one route: proceed, retry, blocked, or
    needs_human — with a concrete rationale grounded in the artifact body.
  * On retry, supply retry_guidance the next subagent dispatch can act on
    (what failed, what must change, what to ignore).
  * Respect max_attempts: if attempts are exhausted, blocked is appropriate
    even when a retry might otherwise help.

Gate criteria (apply the gate named in the user message):
  * REQUIREMENTS_GATE — acceptance criteria present and testable; no blocking
    ambiguities or missing_info; ready_for_development consistent with findings.
  * DEVELOPMENT_GATE — implementation exists; tests exist for new behavior;
    final_tests_green is true and self-checks passed; claims match evidence.
  * REVIEW_GATE — verdict and issues are consistent; blocking issues only when
    request_changes; review addresses the requirement analysis / implementation.

You receive subagent self-verification but you are the final judge at the gate.
Respond ONLY in the JSON shape required by the structured-output schema.
"""

DEFAULT_ORCHESTRATOR_SKILLS: tuple[str, ...] = ("orchestrator-supervisor",)

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "current_focus": {"type": "string"},
        "phase_checklist": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["goal", "current_focus", "phase_checklist", "risks", "notes"],
    "additionalProperties": False,
}

_GATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["proceed", "retry", "blocked", "needs_human"],
        },
        "rationale": {"type": "string"},
        "retry_guidance": {"type": "string"},
    },
    "required": ["decision", "rationale", "retry_guidance"],
    "additionalProperties": False,
}

_GATE_CRITERIA: dict[SDLCPhase, str] = {
    SDLCPhase.REQUIREMENTS_GATE: (
        "REQUIREMENTS_GATE: requirement analysis complete; acceptance criteria "
        "testable; ambiguities/missing_info resolved or escalated."
    ),
    SDLCPhase.DEVELOPMENT_GATE: (
        "DEVELOPMENT_GATE: code + tests delivered together; tests green; "
        "self-checks passed; implementation matches acceptance criteria."
    ),
    SDLCPhase.REVIEW_GATE: (
        "REVIEW_GATE: structured review complete; verdict consistent with issues; "
        "ready to merge or clear retry path."
    ),
}


class OrchestratorBrain:
    """LLM-backed supervisor: planning, gating, and dispatch briefs."""

    def __init__(
        self,
        memory: "MemoryStores",
        llm: OpenAIClient,
        *,
        recorder: TrajectoryRecorder | None = None,
        skills: SkillLoader | None = None,
        skill_names: tuple[str, ...] = DEFAULT_ORCHESTRATOR_SKILLS,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.recorder = recorder
        self._system = assemble_system_prompt(
            ORCHESTRATOR_SYSTEM_PROMPT,
            loader=skills,
            skill_names=skill_names,
        )

    # -------------------------------------------------------------- planning
    def create_plan(self, state: TicketState) -> dict[str, Any]:
        """Produce and persist a long-horizon plan on ``state.plan``."""
        user = self._build_plan_prompt(state)
        task_id = f"{state.ticket_id}-orchestrator-plan"
        plan_body = call_llm_with_schema(
            self.llm,
            system=self._system,
            user=user,
            schema_name="orchestrator_plan",
            schema=_PLAN_SCHEMA,
            recorder=self.recorder,
            task_id=task_id,
            kind="orchestrator.plan",
            metadata={"ticket_id": state.ticket_id},
        )
        state.plan = plan_body
        return plan_body

    # ----------------------------------------------------------- gate logic
    def evaluate_gate(
        self,
        gate: SDLCPhase,
        artifact: ArtifactReturn,
        state: TicketState,
        *,
        attempts_in_phase: int,
        max_attempts: int,
    ) -> tuple[GateDecision, str]:
        """LLM gate evaluation with deterministic safety rails."""
        user = self._build_gate_prompt(
            gate,
            artifact,
            state,
            attempts_in_phase=attempts_in_phase,
            max_attempts=max_attempts,
        )
        task_id = f"{state.ticket_id}-gate-{gate.value.lower()}"
        try:
            raw = call_llm_with_schema(
                self.llm,
                system=self._system,
                user=user,
                schema_name="orchestrator_gate",
                schema=_GATE_SCHEMA,
                recorder=self.recorder,
                task_id=task_id,
                kind="orchestrator.gate",
                metadata={"gate": gate.value, "ticket_id": state.ticket_id},
            )
            decision = _parse_gate_decision(raw["decision"])
            rationale = str(raw.get("rationale", "")).strip() or f"{gate}: orchestrator decision"
            retry_guidance = str(raw.get("retry_guidance", "")).strip()
        except (ValueError, KeyError):
            return evaluate_default_gate(
                gate,
                artifact,
                attempts_in_phase=attempts_in_phase,
                max_attempts=max_attempts,
            )

        decision, rationale = _apply_gate_safety_rails(
            gate,
            artifact,
            decision,
            rationale,
            attempts_in_phase=attempts_in_phase,
            max_attempts=max_attempts,
        )

        if decision is GateDecision.RETRY and retry_guidance:
            work = work_before(gate)
            state.retry_notes[work.value] = retry_guidance
        elif decision is GateDecision.PROCEED:
            work = work_before(gate)
            state.retry_notes.pop(work.value, None)

        return decision, rationale

    # -------------------------------------------------------- dispatch brief
    def build_task_description(
        self,
        state: TicketState,
        phase: SDLCPhase,
        subagent: SubagentName,
        attempt: int,
    ) -> str:
        """Orchestrator-authored brief for the subagent (not a generic stub)."""
        parts: list[str] = [
            f"Ticket {state.ticket_id} — {phase.value} via {subagent.value} "
            f"(attempt {attempt}).",
        ]
        if state.plan:
            parts.append(f"Plan goal: {state.plan.get('goal', '')}")
            focus = state.plan.get("current_focus", "")
            if focus:
                parts.append(f"Current focus: {focus}")
        retry = state.retry_notes.get(phase.value, "")
        if retry:
            parts.append(f"Orchestrator retry guidance:\n{retry}")
        parts.append(
            "Deliver a verified artifact matching your role contract. "
            "Propose durable memory only with evidence."
        )
        return "\n\n".join(parts)

    # ----------------------------------------------------------- prompts
    def _build_plan_prompt(self, state: TicketState) -> str:
        facts = [
            f.get("claim", "")
            for f in self.memory.read_project_facts()
            if f.get("claim")
        ]
        inputs = json.dumps(state.ticket_inputs, indent=2, default=str)
        facts_block = "\n".join(f"  - {c}" for c in facts) or "  (none yet)"
        return f"""\
Create the long-horizon plan for ticket {state.ticket_id}.

Ticket inputs:
{inputs}

Curated project facts:
{facts_block}

SDLC phases ahead: REQUIREMENTS_ANALYSIS → DEVELOPMENT → PR_REVIEW (each with a gate).

Set current_focus to the first concrete work item (requirements analysis).
"""

    def _build_gate_prompt(
        self,
        gate: SDLCPhase,
        artifact: ArtifactReturn,
        state: TicketState,
        *,
        attempts_in_phase: int,
        max_attempts: int,
    ) -> str:
        work_phase = work_before(gate)
        criteria = _GATE_CRITERIA.get(gate, gate.value)
        facts = [
            f.get("claim", "")
            for f in self.memory.read_project_facts()
            if f.get("claim")
        ]
        plan_block = json.dumps(state.plan, indent=2, default=str) if state.plan else "(no plan)"
        artifact_block = json.dumps(
            {
                "status": artifact.status.value,
                "verification": artifact.verification.model_dump(mode="json"),
                "artifact": artifact.artifact,
            },
            indent=2,
            default=str,
        )
        return f"""\
Evaluate {criteria}

Ticket: {state.ticket_id}
Work phase: {work_phase.value}
Attempts in this phase: {attempts_in_phase} (max {max_attempts})

Ticket plan:
{plan_block}

Project facts:
{chr(10).join(f"  - {c}" for c in facts) or "  (none)"}

Subagent return:
{artifact_block}

Choose proceed, retry, blocked, or needs_human. If retry, retry_guidance must
tell the subagent exactly what to fix on the next attempt.
"""


def _parse_gate_decision(raw: str) -> GateDecision:
    try:
        return GateDecision(raw)
    except ValueError as e:
        raise ValueError(f"unknown gate decision: {raw!r}") from e


def _apply_gate_safety_rails(
    gate: SDLCPhase,
    artifact: ArtifactReturn,
    decision: GateDecision,
    rationale: str,
    *,
    attempts_in_phase: int,
    max_attempts: int,
) -> tuple[GateDecision, str]:
    """Enforce spec invariants the LLM might violate."""
    from sdlc_agent.contracts import TaskStatus

    if artifact.status is TaskStatus.NEEDS_HUMAN:
        return GateDecision.NEEDS_HUMAN, f"{gate}: subagent escalated"
    if decision is GateDecision.RETRY and attempts_in_phase >= max_attempts:
        return (
            GateDecision.BLOCKED,
            f"{gate}: max attempts ({max_attempts}) exceeded; {rationale}",
        )
    if gate not in GATE_PHASES:
        return evaluate_default_gate(
            gate, artifact, attempts_in_phase=attempts_in_phase, max_attempts=max_attempts
        )
    return decision, rationale
