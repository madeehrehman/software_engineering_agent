"""Phase 5 integration: full SDLC walk with skills + trajectory archiving wired.

Every subagent loads its DEFAULT_SKILLS; every LLM call lands in
``.deepagent/trajectories/<session-id>/<task-id>.jsonl``. The orchestrator
stamps ``session_id`` into every episodic event for correlation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from sdlc_agent.contracts import SubagentName
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.memory import TrajectoryRecorder, initialize_deepagent
from sdlc_agent.orchestrator import SDLCPhase
from sdlc_agent.orchestrator.dispatcher import Orchestrator
from sdlc_agent.sandbox import LocalSubprocessSandbox
from sdlc_agent.skills import SkillLoader
from sdlc_agent.subagents import BacklogAnalyzer, Developer, PRReviewer


_TEST_FILE = (
    "import unittest\n"
    "from greet import greet\n\n\n"
    "class TestGreet(unittest.TestCase):\n"
    "    def test_basic(self):\n"
    "        self.assertEqual(greet('world'), 'hello, world')\n"
)
_IMPL_FILE = "def greet(name: str) -> str:\n    return f'hello, {name}'\n"


def _backlog() -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": "TICKET-12",
            "summary": "Add greet(name)",
            "acceptance_criteria": ["greet('world') returns 'hello, world'"],
            "ambiguities": [],
            "missing_info": [],
            "out_of_scope": [],
            "ready_for_development": True,
            "notes": "ready",
        },
        "proposed_memory": [],
    }


def _dev_steps() -> list[dict[str, Any]]:
    return [
        {"action": "write_test", "file_path": "test_greet.py", "content": _TEST_FILE, "rationale": "RED"},
        {"action": "run_tests", "file_path": "", "content": "", "rationale": "expect RED"},
        {"action": "write_code", "file_path": "greet.py", "content": _IMPL_FILE, "rationale": "GREEN"},
        {"action": "run_tests", "file_path": "", "content": "", "rationale": "expect GREEN"},
        {"action": "complete", "file_path": "", "content": "", "rationale": "done"},
    ]


def _dev_summary() -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": "TDD: 1 test, 1 impl.",
            "impl_files": ["greet.py"],
            "test_files": ["test_greet.py"],
            "iterations_used": 5,
            "final_tests_green": True,
            "acceptance_criteria_addressed": ["greet('world') returns 'hello, world'"],
        },
        "proposed_memory": [],
    }


def _reviewer() -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": "approve",
            "summary": "lgtm",
            "issues": [],
            "strengths": ["test-first"],
        },
        "proposed_memory": [],
    }


def test_full_sdlc_with_skills_and_trajectories(
    tmp_path: Path,
    tmp_repo: Path,
    jira_fixture_dir: Path,
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)

    canned = [
        _backlog(),
        *_dev_steps(),
        _dev_summary(),
        _reviewer(),
    ]
    llm = fake_llm_factory(canned)

    sandbox_root = tmp_path / "dev_sandbox"
    sandbox_root.mkdir()
    sandbox = LocalSubprocessSandbox(
        root=sandbox_root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )

    skills = SkillLoader()
    recorder = TrajectoryRecorder(paths, session_id="phase5-e2e")

    registry = {
        SubagentName.BACKLOG_ANALYZER: BacklogAnalyzer(
            llm=llm,
            jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir),
            skills=skills,
            recorder=recorder,
        ),
        SubagentName.DEVELOPER: Developer(
            llm=llm,
            sandbox=sandbox,
            max_iterations=8,
            skills=skills,
            recorder=recorder,
        ),
        SubagentName.PR_REVIEWER: PRReviewer(
            llm=llm,
            git=LocalGitClient(repo_root=small_git_repo["repo"]),
            skills=skills,
            recorder=recorder,
        ),
    }

    orch = Orchestrator(paths=paths, registry=registry, session_id="phase5-e2e")
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

    session_dir = paths.trajectories_dir / "phase5-e2e"
    assert session_dir.is_dir()
    trajectories = sorted(session_dir.glob("*.jsonl"))
    assert len(trajectories) == 3, (
        f"one trajectory file per task (3 subagent dispatches); got {len(trajectories)}"
    )

    total_events = 0
    by_kind: dict[str, int] = {}
    for path in trajectories:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            total_events += 1
            by_kind[ev["kind"]] = by_kind.get(ev["kind"], 0) + 1
            assert ev["session_id"] == "phase5-e2e"

    assert by_kind == {
        "backlog_analyzer.run": 1,
        "developer.step": 5,
        "developer.summary": 1,
        "pr_reviewer.run": 1,
    }
    assert total_events == 8


def test_orchestrator_stamps_session_id_into_episodes(
    tmp_path: Path,
    tmp_repo: Path,
    jira_fixture_dir: Path,
    small_git_repo: dict[str, Any],
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    paths = initialize_deepagent(tmp_repo)
    llm = fake_llm_factory([_backlog(), *_dev_steps(), _dev_summary(), _reviewer()])

    sandbox_root = tmp_path / "sb"
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
    orch = Orchestrator(paths=paths, registry=registry, session_id="sess-XYZ")
    orch.intake(
        "TICKET-12",
        ticket_inputs={
            "jira_key": "TICKET-12",
            "base_ref": small_git_repo["base_ref"],
            "head_ref": small_git_repo["head_ref"],
        },
    )
    orch.run_to_completion("TICKET-12")

    lines = paths.episodic_log.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    assert events, "episodic log should not be empty"
    assert all(e.get("session_id") == "sess-XYZ" for e in events)


def test_orchestrator_session_id_auto_generated_when_omitted(
    tmp_repo: Path,
) -> None:
    paths = initialize_deepagent(tmp_repo)
    o1 = Orchestrator(paths=paths, registry={})
    o2 = Orchestrator(paths=paths, registry={})
    assert o1.session_id and o2.session_id
    assert o1.session_id != o2.session_id, "auto-generated ids must be unique per orchestrator"
