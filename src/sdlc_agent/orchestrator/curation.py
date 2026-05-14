"""Memory curation gate (spec §5.3).

> Subagents *propose* durable facts; the orchestrator decides what gets promoted.
> Distinguish *observation* ("this test failed this run") from *durable fact*
> ("this test is flaky"). Promotion threshold for "fact" status: corroborated
> more than once, or explicitly high-confidence with evidence.

This module is the single owner of writes to `project_memory.json` and
`subagent_lore/`. Subagents never reach the durable store directly; only this
gate's promotion path does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sdlc_agent.contracts import (
    ArtifactReturn,
    MemoryConfidence,
    MemoryScope,
    ProposedMemory,
    SubagentName,
)
from sdlc_agent.memory.stores import MemoryStores


class PromotionDecision(StrEnum):
    PROMOTED_NEW = "promoted_new"
    PROMOTED_CORROBORATED = "promoted_corroborated"
    UPDATED_EXISTING = "updated_existing"
    RECORDED_PENDING = "recorded_pending"
    REJECTED_NO_EVIDENCE = "rejected_no_evidence"


_PROMOTED = frozenset(
    {
        PromotionDecision.PROMOTED_NEW,
        PromotionDecision.PROMOTED_CORROBORATED,
        PromotionDecision.UPDATED_EXISTING,
    }
)


def is_promoted(decision: PromotionDecision) -> bool:
    return decision in _PROMOTED


@dataclass(frozen=True)
class CurationResult:
    proposal: ProposedMemory
    decision: PromotionDecision
    rationale: str
    stored_id: str | None = None


def _normalize(claim: str) -> str:
    return " ".join(claim.lower().split())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class CurationGate:
    """Orchestrator-owned: subagent proposals → durable memory writes.

    Rules:
      * Empty evidence            → REJECTED_NO_EVIDENCE.
      * Already-durable claim     → UPDATED_EXISTING (bump corroboration + evidence + source).
      * HIGH confidence + evidence on first sighting → PROMOTED_NEW.
      * MEDIUM/LOW confidence corroborated by a prior proposal (any session)
                                  → PROMOTED_CORROBORATED.
      * Otherwise                 → RECORDED_PENDING (visible in episodic log only).
    """

    def __init__(self, memory: MemoryStores) -> None:
        self.memory = memory

    def evaluate(
        self,
        artifact: ArtifactReturn,
        *,
        subagent: SubagentName,
        ticket_id: str,
    ) -> list[CurationResult]:
        return [
            self._evaluate_one(
                proposal,
                subagent=subagent,
                ticket_id=ticket_id,
                task_id=artifact.task_id,
            )
            for proposal in artifact.proposed_memory
        ]

    # ---------------------------------------------------------------- one
    def _evaluate_one(
        self,
        proposal: ProposedMemory,
        *,
        subagent: SubagentName,
        ticket_id: str,
        task_id: str,
    ) -> CurationResult:
        if not proposal.evidence.strip():
            return CurationResult(
                proposal=proposal,
                decision=PromotionDecision.REJECTED_NO_EVIDENCE,
                rationale="empty evidence; observation only",
            )

        existing = self._find_existing(proposal, subagent)
        if existing is not None:
            updated_id = self._update_existing(
                existing,
                proposal,
                subagent=subagent,
                task_id=task_id,
                ticket_id=ticket_id,
            )
            return CurationResult(
                proposal=proposal,
                decision=PromotionDecision.UPDATED_EXISTING,
                rationale="claim already durable; corroboration bumped",
                stored_id=updated_id,
            )

        if proposal.confidence is MemoryConfidence.HIGH:
            stored_id = self._promote_new(
                proposal,
                subagent=subagent,
                task_id=task_id,
                ticket_id=ticket_id,
            )
            return CurationResult(
                proposal=proposal,
                decision=PromotionDecision.PROMOTED_NEW,
                rationale="high-confidence with evidence; promoted on first sighting",
                stored_id=stored_id,
            )

        if self._has_prior_proposal(proposal.claim):
            stored_id = self._promote_new(
                proposal,
                subagent=subagent,
                task_id=task_id,
                ticket_id=ticket_id,
                initial_corroborations=2,
            )
            return CurationResult(
                proposal=proposal,
                decision=PromotionDecision.PROMOTED_CORROBORATED,
                rationale="corroborated by prior proposal; promoted",
                stored_id=stored_id,
            )

        return CurationResult(
            proposal=proposal,
            decision=PromotionDecision.RECORDED_PENDING,
            rationale="first observation; awaiting corroboration",
        )

    # ----------------------------------------------------------- helpers
    def _find_existing(
        self, proposal: ProposedMemory, subagent: SubagentName
    ) -> dict[str, Any] | None:
        store = (
            self.memory.read_project_facts()
            if proposal.scope is MemoryScope.PROJECT_FACT
            else self.memory.read_subagent_lore(subagent)
        )
        normalized = _normalize(proposal.claim)
        for entry in store:
            if _normalize(entry.get("claim", "")) == normalized:
                return entry
        return None

    def _update_existing(
        self,
        existing: dict[str, Any],
        proposal: ProposedMemory,
        *,
        subagent: SubagentName,
        task_id: str,
        ticket_id: str,
    ) -> str:
        if proposal.scope is MemoryScope.PROJECT_FACT:
            store_list = self.memory.read_project_facts()
        else:
            store_list = self.memory.read_subagent_lore(subagent)

        for entry in store_list:
            if entry.get("id") != existing.get("id"):
                continue
            entry["corroborations"] = entry.get("corroborations", 1) + 1
            entry["last_seen"] = _utcnow_iso()
            evidence = entry.setdefault("evidence", [])
            if proposal.evidence not in evidence:
                evidence.append(proposal.evidence)
            entry.setdefault("sources", []).append(
                {
                    "task_id": task_id,
                    "ticket_id": ticket_id,
                    "subagent": subagent.value,
                    "confidence": proposal.confidence.value,
                }
            )
            if proposal.scope is MemoryScope.PROJECT_FACT:
                self.memory.overwrite_project_facts(store_list)
            else:
                self.memory.overwrite_subagent_lore(subagent, store_list)
            return str(entry.get("id"))

        return str(existing.get("id", ""))

    def _promote_new(
        self,
        proposal: ProposedMemory,
        *,
        subagent: SubagentName,
        task_id: str,
        ticket_id: str,
        initial_corroborations: int = 1,
    ) -> str:
        now = _utcnow_iso()
        entry: dict[str, Any] = {
            "id": _new_id(),
            "claim": proposal.claim,
            "evidence": [proposal.evidence],
            "confidence": proposal.confidence.value,
            "scope": proposal.scope.value,
            "subagent": (
                subagent.value if proposal.scope is MemoryScope.SUBAGENT_LORE else None
            ),
            "corroborations": initial_corroborations,
            "first_seen": now,
            "last_seen": now,
            "sources": [
                {
                    "task_id": task_id,
                    "ticket_id": ticket_id,
                    "subagent": subagent.value,
                    "confidence": proposal.confidence.value,
                }
            ],
        }
        if proposal.scope is MemoryScope.PROJECT_FACT:
            self.memory.append_project_fact(entry)
        else:
            self.memory.append_subagent_lore(subagent, entry)
        return str(entry["id"])

    def _has_prior_proposal(self, claim: str) -> bool:
        normalized = _normalize(claim)
        for event in self.memory.read_episodes():
            if event.get("kind") != "proposal_received":
                continue
            if _normalize(event.get("claim", "")) == normalized:
                return True
        return False
