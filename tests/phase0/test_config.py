"""Phase 0: config schema round-trips through YAML."""

from __future__ import annotations

from pathlib import Path

from sdlc_agent.config import DeepAgentConfig


def test_default_for_repo_uses_dir_name(tmp_repo: Path) -> None:
    cfg = DeepAgentConfig.default_for_repo(tmp_repo)
    assert cfg.project.name == tmp_repo.name
    assert cfg.project.repo_root == tmp_repo.resolve()
    assert cfg.model.provider == "openai"
    assert cfg.mcp.git.type == "stub"
    assert cfg.mcp.jira.type == "stub"
    assert cfg.gates.hitl_requirements_gate is False


def test_yaml_round_trip(tmp_repo: Path) -> None:
    cfg = DeepAgentConfig.default_for_repo(tmp_repo, project_name="alpha")
    yaml_path = tmp_repo / "config.yaml"
    cfg.to_yaml(yaml_path)

    reloaded = DeepAgentConfig.from_yaml(yaml_path)
    assert reloaded.project.name == "alpha"
    assert reloaded.project.repo_root == tmp_repo.resolve()


def test_explicit_project_name_override(tmp_repo: Path) -> None:
    cfg = DeepAgentConfig.default_for_repo(tmp_repo, project_name="custom-name")
    assert cfg.project.name == "custom-name"
