"""Shared pytest fixtures + the `live` marker plumbing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from sdlc_agent.llm.openai_client import OpenAIClient


# ----------------------------------------------------------------- live opt-in
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked `live` (hits the real OpenAI API)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-live") and os.environ.get("OPENAI_API_KEY"):
        return
    if config.getoption("--run-live"):
        skip = pytest.mark.skip(reason="--run-live set but OPENAI_API_KEY missing")
    else:
        skip = pytest.mark.skip(reason="live tests opt-in via --run-live + OPENAI_API_KEY")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------- common dirs
@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway 'target repo' directory the agent can initialize into."""
    repo = tmp_path / "target_repo"
    repo.mkdir()
    return repo


JIRA_FIXTURE_DIR = Path(__file__).parent / "phase2" / "fixtures" / "jira"


@pytest.fixture()
def jira_fixture_dir() -> Path:
    return JIRA_FIXTURE_DIR


# ----------------------------------------------------------------- fake LLM
class _FakeChatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeChatMessage(content)


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, queue: list[str]) -> None:
        self._queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeChatResponse:
        self.calls.append(kwargs)
        if not self._queue:
            raise RuntimeError("FakeLLM queue exhausted; provide more canned responses")
        return _FakeChatResponse(self._queue.pop(0))


class _FakeChat:
    def __init__(self, queue: list[str]) -> None:
        self.completions = _FakeCompletions(queue)


class _FakeOpenAI:
    """Drop-in fake matching the slice of openai.OpenAI() the wrapper uses."""

    def __init__(self, queue: list[str]) -> None:
        self.chat = _FakeChat(queue)


@pytest.fixture()
def fake_llm_factory() -> Callable[[list[dict[str, Any] | str]], OpenAIClient]:
    """Factory: list of canned responses (dicts → JSON or raw strings) → OpenAIClient."""

    def make(responses: list[dict[str, Any] | str]) -> OpenAIClient:
        as_strings = [r if isinstance(r, str) else json.dumps(r) for r in responses]
        fake = _FakeOpenAI(as_strings)
        return OpenAIClient(api_key="sk-fake", model="gpt-test", client=fake)  # type: ignore[arg-type]

    return make


# -------------------------------------------------------------- git fixtures
def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture()
def small_git_repo(tmp_path: Path) -> dict[str, Any]:
    """Create a small two-commit git repo and return refs/paths the tests need.

    layout::

        repo/
          README.md           (added in base commit)
          src/feature.py      (added in head commit)
    """
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test User", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)

    (repo / "README.md").write_text("# Sample\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "base: initial commit", cwd=repo)
    _git("tag", "base", cwd=repo)

    _git("checkout", "-q", "-b", "feat/sample", cwd=repo)
    src = repo / "src"
    src.mkdir()
    (src / "feature.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'hello, {name}'\n",
        encoding="utf-8",
    )
    _git("add", "src/feature.py", cwd=repo)
    _git("commit", "-q", "-m", "feat: add greet()", cwd=repo)

    return {"repo": repo, "base_ref": "base", "head_ref": "HEAD"}
