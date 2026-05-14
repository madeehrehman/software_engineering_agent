"""Phase 4: curation gate unit tests (spec §5.3)."""

from __future__ import annotations

from pathlib import Path

from sdlc_agent.contracts import (
    ArtifactReturn,
    MemoryConfidence,
    MemoryScope,
    ProposedMemory,
    SubagentName,
    TaskStatus,
    VerificationBlock,
)
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator.curation import (
    CurationGate,
    PromotionDecision,
    is_promoted,
)


def _gate(tmp_repo: Path) -> tuple[CurationGate, MemoryStores]:
    paths = initialize_deepagent(tmp_repo)
    stores = MemoryStores(paths)
    return CurationGate(stores), stores


def _artifact_with(*proposals: ProposedMemory) -> ArtifactReturn:
    return ArtifactReturn(
        task_id="t-1",
        status=TaskStatus.COMPLETED,
        verification=VerificationBlock(passed=True),
        proposed_memory=list(proposals),
    )


def test_empty_evidence_is_rejected(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    art = _artifact_with(
        ProposedMemory(
            scope=MemoryScope.PROJECT_FACT,
            claim="repo uses pytest",
            evidence="   ",
            confidence=MemoryConfidence.HIGH,
        )
    )

    [result] = gate.evaluate(art, subagent=SubagentName.BACKLOG_ANALYZER, ticket_id="T-1")

    assert result.decision is PromotionDecision.REJECTED_NO_EVIDENCE
    assert stores.read_project_facts() == []


def test_high_confidence_promotes_on_first_sighting(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    art = _artifact_with(
        ProposedMemory(
            scope=MemoryScope.PROJECT_FACT,
            claim="repo uses pytest",
            evidence="pyproject.toml lists pytest",
            confidence=MemoryConfidence.HIGH,
        )
    )

    [result] = gate.evaluate(art, subagent=SubagentName.BACKLOG_ANALYZER, ticket_id="T-1")

    assert result.decision is PromotionDecision.PROMOTED_NEW
    assert is_promoted(result.decision)
    facts = stores.read_project_facts()
    assert len(facts) == 1
    assert facts[0]["claim"] == "repo uses pytest"
    assert facts[0]["corroborations"] == 1
    assert facts[0]["confidence"] == "high"


def test_medium_confidence_first_sighting_is_pending(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    art = _artifact_with(
        ProposedMemory(
            scope=MemoryScope.PROJECT_FACT,
            claim="auth tests are flaky",
            evidence="failed twice this run",
            confidence=MemoryConfidence.MEDIUM,
        )
    )

    [result] = gate.evaluate(art, subagent=SubagentName.PR_REVIEWER, ticket_id="T-1")

    assert result.decision is PromotionDecision.RECORDED_PENDING
    assert stores.read_project_facts() == []


def test_medium_confidence_corroborated_promotes(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    proposal = ProposedMemory(
        scope=MemoryScope.PROJECT_FACT,
        claim="auth tests are flaky",
        evidence="failed twice in CI",
        confidence=MemoryConfidence.MEDIUM,
    )
    stores.append_episode(
        {"kind": "proposal_received", "claim": "auth tests are flaky"}
    )

    [result] = gate.evaluate(
        _artifact_with(proposal), subagent=SubagentName.PR_REVIEWER, ticket_id="T-2"
    )

    assert result.decision is PromotionDecision.PROMOTED_CORROBORATED
    facts = stores.read_project_facts()
    assert len(facts) == 1
    assert facts[0]["corroborations"] == 2


def test_duplicate_promoted_claim_bumps_corroboration(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    high = ProposedMemory(
        scope=MemoryScope.PROJECT_FACT,
        claim="CI is GitHub Actions",
        evidence=".github/workflows/ci.yml",
        confidence=MemoryConfidence.HIGH,
    )
    gate.evaluate(_artifact_with(high), subagent=SubagentName.BACKLOG_ANALYZER, ticket_id="T-1")

    second = ProposedMemory(
        scope=MemoryScope.PROJECT_FACT,
        claim="CI is GitHub Actions",
        evidence="README references Actions",
        confidence=MemoryConfidence.MEDIUM,
    )
    [result] = gate.evaluate(
        _artifact_with(second), subagent=SubagentName.PR_REVIEWER, ticket_id="T-2"
    )

    assert result.decision is PromotionDecision.UPDATED_EXISTING
    facts = stores.read_project_facts()
    assert len(facts) == 1
    assert facts[0]["corroborations"] == 2
    assert "README references Actions" in facts[0]["evidence"]
    assert len(facts[0]["sources"]) == 2


def test_subagent_lore_is_isolated_per_subagent(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    proposal = ProposedMemory(
        scope=MemoryScope.SUBAGENT_LORE,
        claim="auth module flagged fragile",
        evidence="3 prior reviews noted regressions",
        confidence=MemoryConfidence.HIGH,
    )

    gate.evaluate(
        _artifact_with(proposal), subagent=SubagentName.PR_REVIEWER, ticket_id="T-1"
    )

    assert len(stores.read_subagent_lore(SubagentName.PR_REVIEWER)) == 1
    assert stores.read_subagent_lore(SubagentName.BACKLOG_ANALYZER) == []
    assert stores.read_project_facts() == []


def test_claim_normalization_dedups_whitespace_and_case(tmp_repo: Path) -> None:
    gate, stores = _gate(tmp_repo)
    first = ProposedMemory(
        scope=MemoryScope.PROJECT_FACT,
        claim="Repo Uses Pytest",
        evidence="pyproject.toml",
        confidence=MemoryConfidence.HIGH,
    )
    gate.evaluate(_artifact_with(first), subagent=SubagentName.BACKLOG_ANALYZER, ticket_id="T-1")

    second = ProposedMemory(
        scope=MemoryScope.PROJECT_FACT,
        claim="  repo   uses    pytest  ",
        evidence="conftest.py uses pytest fixtures",
        confidence=MemoryConfidence.MEDIUM,
    )
    [result] = gate.evaluate(
        _artifact_with(second), subagent=SubagentName.PR_REVIEWER, ticket_id="T-2"
    )

    assert result.decision is PromotionDecision.UPDATED_EXISTING
    assert len(stores.read_project_facts()) == 1
