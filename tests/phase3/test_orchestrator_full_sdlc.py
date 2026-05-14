"""Phase 3 acceptance: full SDLC INTAKE → DONE with all three real subagents
(LLM mocked, MCP fixture/local, real subprocess sandbox).

Spec §11 Phase 3 test: "full INTAKE → DONE on a trivial real ticket, all three
subagents live; verify the returned artifact contains both implementation and
passing tests."
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from sdlc_agent.contracts import SubagentName
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.memory.stores import MemoryStores
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.sandbox import LocalSubprocessSandbox
from sdlc_agent.subagents import BacklogAnalyzer, Developer, PRReviewer


_TEST_FILE = """\
import unittest
from greet import greet


class TestGreet(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(greet("world"), "hello, world")
"""

_IMPL_FILE = """\
def greet(name: str) -> str:
    return f"hello, {name}"
"""


def _backlog_response() -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": "TICKET-12",
            "summary": "Add greet(name) returning a hello-world string",
            "acceptance_criteria": ["greet('world') returns 'hello, world'"],
            "ambiguities": [],
            "missing_info": [],
            "out_of_scope": [],
            "ready_for_development": True,
            "notes": "ready",
        },
        "proposed_memory": [],
    }


def _developer_steps() -> list[dict[str, Any]]:
    return [
        {
            "action": "write_test",
            "file_path": "test_greet.py",
            "content": _TEST_FILE,
            "rationale": "RED: write failing test first",
        },
        {
            "action": "run_tests",
            "file_path": "",
            "content": "",
            "rationale": "expect RED",
        },
        {
            "action": "write_code",
            "file_path": "greet.py",
            "content": _IMPL_FILE,
            "rationale": "GREEN: minimal impl",
        },
        {
            "action": "run_tests",
            "file_path": "",
            "content": "",
            "rationale": "expect GREEN",
        },
        {
            "action": "complete",
            "file_path": "",
            "content": "",
            "rationale": "AC covered",
        },
    ]


def _developer_summary() -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": "Implemented greet() via TDD; one test, one impl file.",
            "impl_files": ["greet.py"],
            "test_files": ["test_greet.py"],
            "iterations_used": 5,
            "final_tests_green": True,
            "acceptance_criteria_addressed": ["greet('world') returns 'hello, world'"],
        },
        "proposed_memory": [
            {
                "scope": "project_fact",
                "claim": "this project's test runner is unittest",
                "evidence": "Developer ran `python -m unittest discover` and tests passed",
                "confidence": "high",
            }
        ],
    }


def _reviewer_response() -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": "approve",
            "summary": "Tiny greet() with a clean test; ship it.",
            "issues": [],
            "strengths": ["test-first", "minimal impl"],
        },
        "proposed_memory": [],
    }


def test_full_sdlc_all_three_real_subagents(
    tmp_path: Path,
    tmp_repo: Path,
    jira_fixture_dir: Path,
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)

    canned = [
        _backlog_response(),
        *_developer_steps(),
        _developer_summary(),
        _reviewer_response(),
    ]
    llm = fake_llm_factory(canned)

    sandbox_root = tmp_path / "dev_sandbox"
    sandbox_root.mkdir()
    sandbox = LocalSubprocessSandbox(
        root=sandbox_root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )

    registry = {
        SubagentName.BACKLOG_ANALYZER: BacklogAnalyzer(
            llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
        ),
        SubagentName.DEVELOPER: Developer(llm=llm, sandbox=sandbox, max_iterations=8),
        SubagentName.PR_REVIEWER: PRReviewer(
            llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
        ),
    }

    orch = Orchestrator(paths=paths, registry=registry)
    orch.intake(
        "TICKET-12",
        ticket_inputs={
            "jira_key": "TICKET-12",
            "base_ref": small_git_repo["base_ref"],
            "head_ref": small_git_repo["head_ref"],
        },
    )
    final = orch.run_to_completion("TICKET-12")

    assert final.current_phase is SDLCPhase.DONE

    stores = MemoryStores(paths)
    impl_artifact = stores.load_artifact("TICKET-12", SDLCPhase.DEVELOPMENT)
    assert impl_artifact is not None
    assert impl_artifact.artifact["final_tests_green"] is True
    assert "greet.py" in impl_artifact.artifact["impl_files"]
    assert "test_greet.py" in impl_artifact.artifact["test_files"]
    assert impl_artifact.verification.passed is True

    facts = {f["claim"] for f in stores.read_project_facts()}
    assert "this project's test runner is unittest" in facts

    assert sandbox.file_exists("greet.py")
    assert sandbox.file_exists("test_greet.py")


def test_developer_assignment_carries_requirement_analysis(
    tmp_path: Path,
    tmp_repo: Path,
    jira_fixture_dir: Path,
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    """The orchestrator injects the prior phase's artifact content into inputs,
    so the Developer can read the RA without filesystem access to .deepagent/."""
    paths = initialize_deepagent(tmp_repo)
    canned = [
        _backlog_response(),
        *_developer_steps(),
        _developer_summary(),
        _reviewer_response(),
    ]
    llm = fake_llm_factory(canned)

    sandbox_root = tmp_path / "dev_sandbox"
    sandbox_root.mkdir()
    sandbox = LocalSubprocessSandbox(
        root=sandbox_root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )

    captured_developer = Developer(llm=llm, sandbox=sandbox, max_iterations=8)
    orig_run = captured_developer.run
    last_assignment: dict[str, Any] = {}

    def capture(assignment):  # type: ignore[no-untyped-def]
        last_assignment["a"] = assignment
        return orig_run(assignment)

    captured_developer.run = capture  # type: ignore[method-assign]

    registry = {
        SubagentName.BACKLOG_ANALYZER: BacklogAnalyzer(
            llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
        ),
        SubagentName.DEVELOPER: captured_developer,
        SubagentName.PR_REVIEWER: PRReviewer(
            llm=llm, git=LocalGitClient(repo_root=small_git_repo["repo"])
        ),
    }

    orch = Orchestrator(paths=paths, registry=registry)
    orch.intake(
        "TICKET-12",
        ticket_inputs={
            "jira_key": "TICKET-12",
            "base_ref": small_git_repo["base_ref"],
            "head_ref": small_git_repo["head_ref"],
        },
    )
    orch.run_to_completion("TICKET-12")

    a = last_assignment["a"]
    ra = a.inputs["requirement_analysis"]
    assert ra["ticket_key"] == "TICKET-12"
    assert "greet('world') returns 'hello, world'" in ra["acceptance_criteria"]
