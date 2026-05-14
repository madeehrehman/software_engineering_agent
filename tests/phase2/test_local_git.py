"""Phase 2: LocalGitClient against a real on-disk git repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdlc_agent.mcp.git import GitMCPError, LocalGitClient


def test_handshake_ok_on_real_repo(small_git_repo: dict) -> None:
    client = LocalGitClient(repo_root=small_git_repo["repo"])
    result = client.handshake()
    assert result.ok is True
    assert result.transport == "subprocess"


def test_handshake_fails_when_not_a_repo(tmp_path: Path) -> None:
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    result = LocalGitClient(repo_root=not_repo).handshake()
    assert result.ok is False


def test_diff_contains_added_file(small_git_repo: dict) -> None:
    client = LocalGitClient(repo_root=small_git_repo["repo"])
    diff = client.diff(base_ref="base", head_ref="HEAD")

    assert "src/feature.py" in diff
    assert "+def greet" in diff


def test_files_changed(small_git_repo: dict) -> None:
    client = LocalGitClient(repo_root=small_git_repo["repo"])
    files = client.files_changed(base_ref="base", head_ref="HEAD")
    assert files == ["src/feature.py"]


def test_current_branch(small_git_repo: dict) -> None:
    client = LocalGitClient(repo_root=small_git_repo["repo"])
    assert client.current_branch() == "feat/sample"


def test_bad_ref_raises(small_git_repo: dict) -> None:
    client = LocalGitClient(repo_root=small_git_repo["repo"])
    with pytest.raises(GitMCPError):
        client.diff(base_ref="does-not-exist", head_ref="HEAD")
