"""Filesystem layout for `.deepagent/` (spec §5.1).

Centralized so any module that touches memory uses the same paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEEPAGENT_DIRNAME = ".deepagent"

SUBAGENT_LORE_FILES: tuple[str, ...] = (
    "backlog_analyzer.json",
    "developer.json",
    "pr_reviewer.json",
)


@dataclass(frozen=True)
class DeepAgentPaths:
    """All `.deepagent/` paths, anchored at a target repo root."""

    repo_root: Path

    @property
    def root(self) -> Path:
        return self.repo_root / DEEPAGENT_DIRNAME

    @property
    def config_yaml(self) -> Path:
        return self.root / "config.yaml"

    @property
    def project_memory_json(self) -> Path:
        return self.root / "project_memory.json"

    @property
    def subagent_lore_dir(self) -> Path:
        return self.root / "subagent_lore"

    def subagent_lore_file(self, name: str) -> Path:
        return self.subagent_lore_dir / name

    @property
    def episodic_dir(self) -> Path:
        return self.root / "episodic"

    @property
    def episodic_log(self) -> Path:
        return self.episodic_dir / "log.jsonl"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def trajectories_dir(self) -> Path:
        return self.root / "trajectories"

    def state_file(self, ticket_id: str) -> Path:
        return self.state_dir / f"{ticket_id}.json"

    def ticket_artifacts_dir(self, ticket_id: str) -> Path:
        return self.artifacts_dir / ticket_id
