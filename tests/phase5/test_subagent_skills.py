"""Phase 5: each subagent prepends its DEFAULT_SKILLS to the system prompt."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from sdlc_agent.contracts import (
    Constraints,
    InjectedContext,
    SubagentName,
    TaskAssignment,
)
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.sandbox import LocalSubprocessSandbox
from sdlc_agent.skills import SkillLoader
from sdlc_agent.subagents import BacklogAnalyzer, Developer, PRReviewer


def _backlog_canned() -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": "TICKET-12",
            "summary": "noop",
            "acceptance_criteria": ["ac-1"],
            "ambiguities": [],
            "missing_info": [],
            "out_of_scope": [],
            "ready_for_development": True,
            "notes": "",
        },
        "proposed_memory": [],
    }


def _reviewer_canned() -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": "approve",
            "summary": "noop review",
            "issues": [],
            "strengths": [],
        },
        "proposed_memory": [],
    }


def _developer_canned_summary() -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": "noop",
            "impl_files": [],
            "test_files": [],
            "iterations_used": 1,
            "final_tests_green": False,
            "acceptance_criteria_addressed": [],
        },
        "proposed_memory": [],
    }


def _developer_step(action: str = "complete") -> dict[str, Any]:
    return {"action": action, "file_path": "", "content": "", "rationale": ""}


# -------------------------------------------------------------- backlog
def test_backlog_analyzer_injects_skill(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_backlog_canned()])
    analyzer = BacklogAnalyzer(
        llm=llm,
        jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir),
        skills=SkillLoader(),
    )
    analyzer.run(
        TaskAssignment(
            task_id="t1",
            ticket_id="TICKET-12",
            subagent=SubagentName.BACKLOG_ANALYZER,
            task="x",
            inputs={},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )
    call = llm._client.chat.completions.calls[-1]  # type: ignore[attr-defined]
    system = call["messages"][0]["content"]
    assert "--- LOADED SKILLS ---" in system
    assert "## Skill: requirement-ambiguity-checklist" in system


def test_backlog_analyzer_without_loader_has_clean_prompt(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_backlog_canned()])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )
    analyzer.run(
        TaskAssignment(
            task_id="t1",
            ticket_id="TICKET-12",
            subagent=SubagentName.BACKLOG_ANALYZER,
            task="x",
            inputs={},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )
    call = llm._client.chat.completions.calls[-1]  # type: ignore[attr-defined]
    assert "--- LOADED SKILLS ---" not in call["messages"][0]["content"]


# -------------------------------------------------------------- reviewer
def test_pr_reviewer_injects_skill(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_reviewer_canned()])
    reviewer = PRReviewer(
        llm=llm,
        git=LocalGitClient(repo_root=small_git_repo["repo"]),
        skills=SkillLoader(),
    )
    reviewer.run(
        TaskAssignment(
            task_id="t1",
            ticket_id="TICKET-12",
            subagent=SubagentName.PR_REVIEWER,
            task="x",
            inputs={"base_ref": "base", "head_ref": "HEAD"},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )
    call = llm._client.chat.completions.calls[-1]  # type: ignore[attr-defined]
    system = call["messages"][0]["content"]
    assert "## Skill: pr-review-rubric" in system
    assert "request_changes" in system


# -------------------------------------------------------------- developer
@pytest.fixture()
def py_sandbox(tmp_path: Path) -> LocalSubprocessSandbox:
    root = tmp_path / "sb"
    root.mkdir()
    return LocalSubprocessSandbox(
        root=root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )


def test_developer_injects_skill_on_both_step_and_summary(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_developer_step("complete"), _developer_canned_summary()])
    dev = Developer(
        llm=llm, sandbox=py_sandbox, max_iterations=3, skills=SkillLoader()
    )
    dev.run(
        TaskAssignment(
            task_id="dev-1",
            ticket_id="TICKET-12",
            subagent=SubagentName.DEVELOPER,
            task="x",
            inputs={"requirement_analysis": {"acceptance_criteria": ["ac"]}},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )
    step_system = llm._client.chat.completions.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
    summary_system = llm._client.chat.completions.calls[-1]["messages"][0]["content"]  # type: ignore[attr-defined]
    assert "## Skill: tdd-discipline" in step_system
    assert "## Skill: tdd-discipline" in summary_system
    assert step_system != summary_system, "step and summary keep distinct system prompts"


# -------------------------------------------------------- declared defaults
def test_static_default_skills_per_role() -> None:
    """The static-per-role choice: each subagent class declares its skills."""
    assert BacklogAnalyzer.DEFAULT_SKILLS == ("requirement-ambiguity-checklist",)
    assert Developer.DEFAULT_SKILLS == ("tdd-discipline",)
    assert PRReviewer.DEFAULT_SKILLS == ("pr-review-rubric",)
