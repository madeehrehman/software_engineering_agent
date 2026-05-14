"""Initialize the project-local `.deepagent/` folder (spec §5.1, Phase 0)."""

from __future__ import annotations

import json
from pathlib import Path

from sdlc_agent.config import DeepAgentConfig
from sdlc_agent.memory.paths import SUBAGENT_LORE_FILES, DeepAgentPaths

EMPTY_PROJECT_MEMORY: dict = {"facts": []}
EMPTY_SUBAGENT_LORE: dict = {"lore": []}


def initialize_deepagent(
    repo_root: Path,
    *,
    project_name: str | None = None,
    config: DeepAgentConfig | None = None,
    overwrite: bool = False,
) -> DeepAgentPaths:
    """Create the `.deepagent/` skeleton inside ``repo_root`` and return its paths.

    Idempotent by default: existing files are left alone unless ``overwrite=True``.

    The skeleton matches the layout in spec §5.1: config.yaml, project_memory.json,
    subagent_lore/{backlog_analyzer,developer,pr_reviewer}.json, episodic/log.jsonl,
    artifacts/, state/, trajectories/.
    """
    repo_root = repo_root.resolve()
    if not repo_root.exists():
        raise FileNotFoundError(f"target repo root does not exist: {repo_root}")
    if not repo_root.is_dir():
        raise NotADirectoryError(f"target repo root is not a directory: {repo_root}")

    paths = DeepAgentPaths(repo_root=repo_root)

    for d in (
        paths.root,
        paths.subagent_lore_dir,
        paths.episodic_dir,
        paths.artifacts_dir,
        paths.state_dir,
        paths.trajectories_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    cfg = config or DeepAgentConfig.default_for_repo(repo_root, project_name=project_name)
    if overwrite or not paths.config_yaml.exists():
        cfg.to_yaml(paths.config_yaml)

    _write_json_if_absent(paths.project_memory_json, EMPTY_PROJECT_MEMORY, overwrite)
    for lore_name in SUBAGENT_LORE_FILES:
        _write_json_if_absent(paths.subagent_lore_file(lore_name), EMPTY_SUBAGENT_LORE, overwrite)

    if overwrite or not paths.episodic_log.exists():
        paths.episodic_log.touch()

    return paths


def _write_json_if_absent(path: Path, payload: dict, overwrite: bool) -> None:
    if overwrite or not path.exists():
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
