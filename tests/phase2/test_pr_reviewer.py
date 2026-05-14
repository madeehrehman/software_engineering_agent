"""Phase 2: PR Reviewer end-to-end against a real local git repo + mocked LLM."""

from __future__ import annotations

from typing import Any, Callable

from sdlc_agent.contracts import (
    Constraints,
    InjectedContext,
    SubagentName,
    TaskAssignment,
    TaskStatus,
)
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.subagents import PRReviewer


def _assignment(
    ticket_id: str = "TICKET-12",
    *,
    base_ref: str = "base",
    head_ref: str = "HEAD",
    lore: list[str] | None = None,
) -> TaskAssignment:
    return TaskAssignment(
        task_id="task-2",
        ticket_id=ticket_id,
        subagent=SubagentName.PR_REVIEWER,
        task="Review PR",
        inputs={"base_ref": base_ref, "head_ref": head_ref},
        injected_context=InjectedContext(subagent_lore=lore or []),
        constraints=Constraints(),
    )


def _canned_response(
    *,
    verdict: str = "approve",
    issues: list[dict[str, Any]] | None = None,
    strengths: list[str] | None = None,
    proposed_memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": verdict,
            "summary": "Adds a small greet() utility; tests acceptable.",
            "issues": issues or [],
            "strengths": strengths or ["clean signature"],
        },
        "proposed_memory": proposed_memory or [],
    }


def test_reviewer_happy_path(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_canned_response()])
    reviewer = PRReviewer(
        llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
    )

    out = reviewer.run(_assignment())

    assert out.status is TaskStatus.COMPLETED
    assert out.verification.passed is True
    assert out.artifact["verdict"] == "approve"


def test_reviewer_flags_inconsistent_blocking_with_approve(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    bad = _canned_response(
        verdict="approve",
        issues=[
            {
                "file": "src/feature.py",
                "severity": "blocking",
                "category": "bug",
                "comment": "missing input validation",
            }
        ],
    )
    llm = fake_llm_factory([bad])
    reviewer = PRReviewer(
        llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
    )

    out = reviewer.run(_assignment())

    assert out.verification.passed is False
    assert out.status is TaskStatus.NEEDS_HUMAN


def test_reviewer_rejects_issues_outside_diff(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    bad = _canned_response(
        verdict="request_changes",
        issues=[
            {
                "file": "src/totally_unrelated.py",
                "severity": "major",
                "category": "bug",
                "comment": "doesn't exist in diff",
            }
        ],
    )
    llm = fake_llm_factory([bad])
    reviewer = PRReviewer(
        llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
    )

    out = reviewer.run(_assignment())

    assert out.verification.passed is False
    file_check_failed = any(
        not c.passed and "issue files reference" in c.check
        for c in out.verification.self_checks
    )
    assert file_check_failed


def test_prompt_carries_diff_and_lore(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_canned_response()])
    reviewer = PRReviewer(
        llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
    )

    reviewer.run(_assignment(lore=["auth module flagged fragile"]))

    last_call = llm._client.chat.completions.calls[-1]  # type: ignore[attr-defined]
    user_msg = last_call["messages"][1]["content"]
    assert "+def greet" in user_msg
    assert "auth module flagged fragile" in user_msg
    assert "src/feature.py" in user_msg
