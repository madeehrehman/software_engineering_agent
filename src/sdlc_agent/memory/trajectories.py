"""Trajectory cold-storage archiving (spec §5.1, §5.2).

Subagents make multiple LLM calls per task; the full prompt+response trace is
too large to fit in the episodic log and not useful as durable memory, but is
the *single most useful artifact when debugging "why did the agent do that?"*

This module writes one JSONL file per task under
``.deepagent/trajectories/<session-id>/<task-id>.jsonl``. Each line is one
LLM call (or sandbox event you choose to record). The orchestrator's episodic
log stays small and queryable; full traces are retrievable by session+task ID.

Trajectories are **cold storage**: never auto-loaded into context; only
retrieved on demand for debugging.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sdlc_agent.memory.paths import DeepAgentPaths


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryRecorder:
    """Append-only JSONL writer for raw LLM reasoning traces.

    Bound to a single ``session_id`` for its lifetime. One file is written
    per ``task_id`` under ``.deepagent/trajectories/<session-id>/``.

    Construction is cheap; the directory is created lazily on first record.
    Safe to share across subagents within one session.
    """

    def __init__(self, paths: DeepAgentPaths, *, session_id: str) -> None:
        if not session_id or "/" in session_id or "\\" in session_id:
            raise ValueError(
                f"invalid session_id {session_id!r}: must be non-empty and "
                "contain no path separators"
            )
        self.paths = paths
        self.session_id = session_id

    @property
    def session_dir(self) -> Path:
        return self.paths.trajectories_dir / self.session_id

    def trajectory_path(self, task_id: str) -> Path:
        if not task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(
                f"invalid task_id {task_id!r}: must be non-empty and contain "
                "no path separators"
            )
        return self.session_dir / f"{task_id}.jsonl"

    def record(
        self,
        *,
        task_id: str,
        kind: str,
        prompt: Sequence[Mapping[str, Any]],
        response: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Append one trace event for ``task_id``. Returns the JSONL path.

        ``kind`` distinguishes call sites within a task (e.g. ``"step"`` and
        ``"summary"`` for the Developer's loop steps vs. its final summary).
        """
        path = self.trajectory_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        event: dict[str, Any] = {
            "timestamp": _utcnow_iso(),
            "session_id": self.session_id,
            "task_id": task_id,
            "kind": kind,
            "prompt": list(prompt),
            "response": response,
            "metadata": dict(metadata or {}),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path

    def read(self, task_id: str) -> list[dict[str, Any]]:
        """Read back all events for a task (debugging / tests)."""
        path = self.trajectory_path(task_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
        return events
