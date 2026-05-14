"""Phase 5: TrajectoryRecorder writes one JSONL per task per session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc_agent.memory import DeepAgentPaths, TrajectoryRecorder, initialize_deepagent


@pytest.fixture()
def paths(tmp_repo: Path) -> DeepAgentPaths:
    return initialize_deepagent(tmp_repo)


def test_records_event_to_session_task_jsonl(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="session-A")
    rec.record(
        task_id="task-1",
        kind="developer.step",
        prompt=[{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        response='{"action":"complete","file_path":"","content":"","rationale":""}',
        metadata={"iteration": 1},
    )
    p = paths.trajectories_dir / "session-A" / "task-1.jsonl"
    assert p.is_file()
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["session_id"] == "session-A"
    assert event["task_id"] == "task-1"
    assert event["kind"] == "developer.step"
    assert event["prompt"][0]["content"] == "S"
    assert "complete" in event["response"]
    assert event["metadata"]["iteration"] == 1
    assert "timestamp" in event


def test_appends_subsequent_events_to_same_file(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="s1")
    for i in range(3):
        rec.record(
            task_id="t1",
            kind=f"call-{i}",
            prompt=[{"role": "user", "content": f"u{i}"}],
            response=f"r{i}",
        )
    events = rec.read("t1")
    assert len(events) == 3
    assert [e["kind"] for e in events] == ["call-0", "call-1", "call-2"]


def test_each_task_gets_its_own_file(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="s1")
    rec.record(task_id="A", kind="x", prompt=[], response="a")
    rec.record(task_id="B", kind="x", prompt=[], response="b")
    assert rec.read("A")[0]["response"] == "a"
    assert rec.read("B")[0]["response"] == "b"
    assert not (paths.trajectories_dir / "s1" / "A.jsonl").samefile(
        paths.trajectories_dir / "s1" / "B.jsonl"
    )


def test_sessions_are_isolated_by_directory(paths: DeepAgentPaths) -> None:
    r1 = TrajectoryRecorder(paths, session_id="alpha")
    r2 = TrajectoryRecorder(paths, session_id="beta")
    r1.record(task_id="t1", kind="x", prompt=[], response="from-alpha")
    r2.record(task_id="t1", kind="x", prompt=[], response="from-beta")
    assert (paths.trajectories_dir / "alpha" / "t1.jsonl").is_file()
    assert (paths.trajectories_dir / "beta" / "t1.jsonl").is_file()
    assert r1.read("t1")[0]["response"] == "from-alpha"
    assert r2.read("t1")[0]["response"] == "from-beta"


def test_read_missing_task_returns_empty(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="s")
    assert rec.read("does-not-exist") == []


def test_invalid_session_id_rejected(paths: DeepAgentPaths) -> None:
    with pytest.raises(ValueError):
        TrajectoryRecorder(paths, session_id="")
    with pytest.raises(ValueError):
        TrajectoryRecorder(paths, session_id="bad/slash")
    with pytest.raises(ValueError):
        TrajectoryRecorder(paths, session_id="bad\\slash")


def test_invalid_task_id_rejected(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="s")
    with pytest.raises(ValueError):
        rec.record(task_id="", kind="x", prompt=[], response="")
    with pytest.raises(ValueError):
        rec.record(task_id="bad/slash", kind="x", prompt=[], response="")


def test_jsonl_is_one_event_per_line(paths: DeepAgentPaths) -> None:
    rec = TrajectoryRecorder(paths, session_id="s")
    rec.record(task_id="t", kind="x", prompt=[], response="line one\nstill line one")
    rec.record(task_id="t", kind="x", prompt=[], response="line two")
    text = rec.trajectory_path("t").read_text(encoding="utf-8")
    assert text.count("\n") == 2, "exactly two newlines (one per event), not embedded ones"
