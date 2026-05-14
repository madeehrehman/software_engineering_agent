"""Phase 0 acceptance: `.deepagent/` is created with the §5.1 layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc_agent.config import DeepAgentConfig
from sdlc_agent.memory import SUBAGENT_LORE_FILES, DeepAgentPaths, initialize_deepagent


def test_initializer_creates_full_layout(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)

    assert paths.root.is_dir()
    assert paths.config_yaml.is_file()
    assert paths.project_memory_json.is_file()
    assert paths.subagent_lore_dir.is_dir()
    for lore_name in SUBAGENT_LORE_FILES:
        assert paths.subagent_lore_file(lore_name).is_file()
    assert paths.episodic_dir.is_dir()
    assert paths.episodic_log.is_file()
    assert paths.artifacts_dir.is_dir()
    assert paths.state_dir.is_dir()
    assert paths.trajectories_dir.is_dir()


def test_initializer_empty_stores_have_valid_json(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)

    assert json.loads(paths.project_memory_json.read_text()) == {"facts": []}
    for lore_name in SUBAGENT_LORE_FILES:
        assert json.loads(paths.subagent_lore_file(lore_name).read_text()) == {"lore": []}
    assert paths.episodic_log.read_text() == ""


def test_initializer_writes_loadable_config(tmp_repo: Path) -> None:
    initialize_deepagent(tmp_repo, project_name="demo-project")

    cfg = DeepAgentConfig.from_yaml(DeepAgentPaths(repo_root=tmp_repo).config_yaml)
    assert cfg.project.name == "demo-project"


def test_initializer_is_idempotent(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    paths.project_memory_json.write_text('{"facts": [{"id": "f1"}]}\n', encoding="utf-8")

    initialize_deepagent(tmp_repo)

    assert "f1" in paths.project_memory_json.read_text()


def test_initializer_overwrite_resets_stores(tmp_repo: Path) -> None:
    paths = initialize_deepagent(tmp_repo)
    paths.project_memory_json.write_text('{"facts": [{"id": "f1"}]}\n', encoding="utf-8")

    initialize_deepagent(tmp_repo, overwrite=True)

    assert json.loads(paths.project_memory_json.read_text()) == {"facts": []}


def test_initializer_rejects_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        initialize_deepagent(missing)
