"""Phase 5: subagents record every LLM call to the trajectory store."""

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
from sdlc_agent.memory import DeepAgentPaths, TrajectoryRecorder, initialize_deepagent
from sdlc_agent.sandbox import LocalSubprocessSandbox
from sdlc_agent.subagents import BacklogAnalyzer, Developer, PRReviewer


@pytest.fixture()
def paths(tmp_repo: Path) -> DeepAgentPaths:
    return initialize_deepagent(tmp_repo)


@pytest.fixture()
def recorder(paths: DeepAgentPaths) -> TrajectoryRecorder:
    return TrajectoryRecorder(paths, session_id="phase5-test")


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
            "summary": "noop",
            "issues": [],
            "strengths": [],
        },
        "proposed_memory": [],
    }


def _step(action: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "action": action,
        "file_path": kwargs.get("file_path", ""),
        "content": kwargs.get("content", ""),
        "rationale": kwargs.get("rationale", ""),
    }


def _summary(**kwargs: Any) -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": kwargs.get("summary", "noop"),
            "impl_files": kwargs.get("impl_files", []),
            "test_files": kwargs.get("test_files", []),
            "iterations_used": kwargs.get("iterations_used", 1),
            "final_tests_green": kwargs.get("final_tests_green", False),
            "acceptance_criteria_addressed": kwargs.get("acceptance", []),
        },
        "proposed_memory": [],
    }


def test_backlog_analyzer_records_one_event(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
    recorder: TrajectoryRecorder,
) -> None:
    llm = fake_llm_factory([_backlog_canned()])
    analyzer = BacklogAnalyzer(
        llm=llm,
        jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir),
        recorder=recorder,
    )
    assignment = TaskAssignment(
        task_id="backlog-task-abc",
        ticket_id="TICKET-12",
        subagent=SubagentName.BACKLOG_ANALYZER,
        task="x",
        inputs={},
        injected_context=InjectedContext(),
        constraints=Constraints(),
    )
    analyzer.run(assignment)

    events = recorder.read("backlog-task-abc")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "backlog_analyzer.run"
    assert e["metadata"]["schema_name"] == "backlog_analyzer_response"
    assert e["metadata"]["issue_key"] == "TICKET-12"
    assert any(m["role"] == "system" for m in e["prompt"])
    assert any(m["role"] == "user" for m in e["prompt"])


def test_pr_reviewer_records_one_event(
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
    recorder: TrajectoryRecorder,
) -> None:
    llm = fake_llm_factory([_reviewer_canned()])
    reviewer = PRReviewer(
        llm=llm,
        git=LocalGitClient(repo_root=small_git_repo["repo"]),
        recorder=recorder,
    )
    reviewer.run(
        TaskAssignment(
            task_id="review-xyz",
            ticket_id="TICKET-12",
            subagent=SubagentName.PR_REVIEWER,
            task="x",
            inputs={"base_ref": "base", "head_ref": "HEAD"},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )

    events = recorder.read("review-xyz")
    assert len(events) == 1
    assert events[0]["kind"] == "pr_reviewer.run"
    assert events[0]["metadata"]["base_ref"] == "base"
    assert "src/feature.py" in events[0]["metadata"]["files_changed"]


@pytest.fixture()
def py_sandbox(tmp_path: Path) -> LocalSubprocessSandbox:
    root = tmp_path / "sb"
    root.mkdir()
    return LocalSubprocessSandbox(
        root=root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )


_TEST_FILE = (
    "import unittest\n"
    "from greet import greet\n\n\n"
    "class TestGreet(unittest.TestCase):\n"
    "    def test_basic(self):\n"
    "        self.assertEqual(greet('world'), 'hello, world')\n"
)
_IMPL_FILE = "def greet(name: str) -> str:\n    return f'hello, {name}'\n"


def test_developer_records_step_and_summary_in_same_file(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
    recorder: TrajectoryRecorder,
) -> None:
    canned = [
        _step("write_test", file_path="test_greet.py", content=_TEST_FILE),
        _step("run_tests"),
        _step("write_code", file_path="greet.py", content=_IMPL_FILE),
        _step("run_tests"),
        _step("complete"),
        _summary(
            impl_files=["greet.py"],
            test_files=["test_greet.py"],
            final_tests_green=True,
            iterations_used=5,
            acceptance=["greet returns 'hello, <name>'"],
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(
        llm=llm, sandbox=py_sandbox, max_iterations=8, recorder=recorder
    )
    dev.run(
        TaskAssignment(
            task_id="dev-traj-1",
            ticket_id="TICKET-12",
            subagent=SubagentName.DEVELOPER,
            task="x",
            inputs={"requirement_analysis": {"acceptance_criteria": ["ac"]}},
            injected_context=InjectedContext(),
            constraints=Constraints(),
        )
    )

    events = recorder.read("dev-traj-1")
    kinds = [e["kind"] for e in events]
    assert kinds.count("developer.step") == 5, "one event per non-terminal LLM-driven step"
    assert kinds[-1] == "developer.summary"
    iterations = [e["metadata"]["iteration"] for e in events if e["kind"] == "developer.step"]
    assert iterations == [1, 2, 3, 4, 5]
    final_meta = events[-1]["metadata"]
    assert final_meta["iterations"] == 5
    assert final_meta["final_exit_code"] == 0


def test_recorder_optional_subagent_runs_without_it(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
    paths: DeepAgentPaths,
) -> None:
    llm = fake_llm_factory([_backlog_canned()])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )
    out = analyzer.run(
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
    assert out.verification.passed is True
    assert list(paths.trajectories_dir.iterdir()) == []
