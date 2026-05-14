"""Phase 3: Developer TDD loop with mocked LLM + real subprocess test runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from sdlc_agent.contracts import (
    Constraints,
    InjectedContext,
    SubagentName,
    TaskAssignment,
    TaskStatus,
)
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.sandbox import LocalSubprocessSandbox
from sdlc_agent.subagents import Developer


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


def _step(
    action: str,
    *,
    file_path: str = "",
    content: str = "",
    rationale: str = "",
) -> dict[str, Any]:
    return {
        "action": action,
        "file_path": file_path,
        "content": content,
        "rationale": rationale or f"{action} step",
    }


def _wrap(d: dict[str, Any]) -> dict[str, Any]:
    return d


def _summary(
    *,
    impl_files: list[str],
    test_files: list[str],
    final_tests_green: bool,
    iterations_used: int,
    acceptance: list[str] | None = None,
    proposed_memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": "Added greet(name) under TDD discipline.",
            "impl_files": impl_files,
            "test_files": test_files,
            "iterations_used": iterations_used,
            "final_tests_green": final_tests_green,
            "acceptance_criteria_addressed": acceptance or ["greet returns 'hello, <name>'"],
        },
        "proposed_memory": proposed_memory or [],
    }


@pytest.fixture()
def py_sandbox(tmp_path: Path) -> LocalSubprocessSandbox:
    """Sandbox whose test command uses the *current* Python interpreter.

    Default ``unittest discover`` (start=., top=., pattern=test_*.py) finds any
    ``test_*.py`` file at root — keeps the test fixture's layout simple. Real
    projects with a ``tests/`` package would override the test command.
    """
    root = tmp_path / "sb"
    root.mkdir()
    return LocalSubprocessSandbox(
        root=root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )


def _assignment(*, acceptance_criteria: list[str] | None = None) -> TaskAssignment:
    return TaskAssignment(
        task_id="dev-1",
        ticket_id="TICKET-12",
        subagent=SubagentName.DEVELOPER,
        task="Implement greet()",
        inputs={
            "phase": "DEVELOPMENT",
            "attempt": 1,
            "requirement_analysis": {
                "summary": "Add greet(name) returning 'hello, <name>'.",
                "acceptance_criteria": acceptance_criteria
                or ["greet returns 'hello, <name>'"],
                "ambiguities": [],
                "missing_info": [],
            },
        },
        injected_context=InjectedContext(),
        constraints=Constraints(),
    )


def test_full_tdd_loop_red_then_green(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    canned = [
        _wrap(_step("write_test", file_path="test_greet.py", content=_TEST_FILE)),
        _wrap(_step("run_tests", rationale="expect RED")),
        _wrap(_step("write_code", file_path="greet.py", content=_IMPL_FILE)),
        _wrap(_step("run_tests", rationale="expect GREEN")),
        _wrap(_step("complete", rationale="AC covered, tests green")),
        _summary(
            impl_files=["greet.py"],
            test_files=["test_greet.py"],
            final_tests_green=True,
            iterations_used=5,
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox, max_iterations=8)

    out = dev.run(_assignment())

    assert out.status is TaskStatus.COMPLETED
    assert out.verification.passed is True
    assert all(c.passed for c in out.verification.self_checks)
    assert out.artifact["final_tests_green"] is True
    assert py_sandbox.file_exists("greet.py")
    assert py_sandbox.file_exists("test_greet.py")


def test_red_first_step_actually_fails(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    """Confirms the TDD loop's RED step is real: first test run returns nonzero."""
    canned = [
        _wrap(_step("write_test", file_path="test_greet.py", content=_TEST_FILE)),
        _wrap(_step("run_tests")),
        _wrap(_step("complete")),
        _summary(
            impl_files=[],
            test_files=["test_greet.py"],
            final_tests_green=False,
            iterations_used=3,
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox, max_iterations=5)

    out = dev.run(_assignment())

    assert out.verification.passed is False
    assert out.status is TaskStatus.NEEDS_HUMAN
    failing_checks = [c for c in out.verification.self_checks if not c.passed]
    assert any("test run is green" in c.check for c in failing_checks)
    assert any("at least one impl file" in c.check for c in failing_checks)


def test_self_check_catches_lying_about_green(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    canned = [
        _wrap(_step("write_test", file_path="test_greet.py", content=_TEST_FILE)),
        _wrap(_step("run_tests")),
        _wrap(_step("complete")),
        _summary(
            impl_files=[],
            test_files=["test_greet.py"],
            final_tests_green=True,
            iterations_used=3,
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox)

    out = dev.run(_assignment())

    assert out.verification.passed is False
    assert any(
        not c.passed and "matches actual" in c.check for c in out.verification.self_checks
    )


def test_self_check_catches_claiming_nonexistent_file(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    canned = [
        _wrap(_step("write_test", file_path="test_greet.py", content=_TEST_FILE)),
        _wrap(_step("write_code", file_path="greet.py", content=_IMPL_FILE)),
        _wrap(_step("run_tests")),
        _wrap(_step("complete")),
        _summary(
            impl_files=["greet.py", "ghost/nope.py"],
            test_files=["test_greet.py"],
            final_tests_green=True,
            iterations_used=4,
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox)

    out = dev.run(_assignment())

    assert out.verification.passed is False
    assert any(
        not c.passed and "claimed files exist" in c.check
        for c in out.verification.self_checks
    )


def test_proposed_memory_carried_through(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    canned = [
        _wrap(_step("write_test", file_path="test_greet.py", content=_TEST_FILE)),
        _wrap(_step("write_code", file_path="greet.py", content=_IMPL_FILE)),
        _wrap(_step("run_tests")),
        _wrap(_step("complete")),
        _summary(
            impl_files=["greet.py"],
            test_files=["test_greet.py"],
            final_tests_green=True,
            iterations_used=4,
            proposed_memory=[
                {
                    "scope": "project_fact",
                    "claim": "this project uses unittest (not pytest)",
                    "evidence": "loop ran `python -m unittest discover` and tests passed",
                    "confidence": "high",
                }
            ],
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox)

    out = dev.run(_assignment())

    assert out.status is TaskStatus.COMPLETED
    assert len(out.proposed_memory) == 1
    assert out.proposed_memory[0].claim == "this project uses unittest (not pytest)"


def test_max_iterations_exits_loop(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    """Loop must terminate at max_iterations even if LLM never says 'complete'."""
    step = _wrap(_step("run_tests"))
    canned = [step, step, step, _summary(
        impl_files=[],
        test_files=[],
        final_tests_green=False,
        iterations_used=3,
    )]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox, max_iterations=3)

    out = dev.run(_assignment())

    assert out.artifact["iterations_used"] == 3
    assert out.verification.passed is False


def test_step_prompt_includes_requirement_analysis(
    py_sandbox: LocalSubprocessSandbox,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    canned = [
        _wrap(_step("complete")),
        _summary(
            impl_files=[],
            test_files=[],
            final_tests_green=False,
            iterations_used=1,
        ),
    ]
    llm = fake_llm_factory(canned)
    dev = Developer(llm=llm, sandbox=py_sandbox, max_iterations=3)
    dev.run(_assignment(acceptance_criteria=["AC-xyz: greet returns greeting"]))

    first_call = llm._client.chat.completions.calls[0]  # type: ignore[attr-defined]
    user_msg = first_call["messages"][1]["content"]
    assert "AC-xyz" in user_msg
    assert "Add greet(name)" in user_msg
