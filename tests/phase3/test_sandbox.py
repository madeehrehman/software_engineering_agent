"""Phase 3: LocalSubprocessSandbox unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sdlc_agent.sandbox import LocalSubprocessSandbox, SandboxError


@pytest.fixture()
def sandbox(tmp_path: Path) -> LocalSubprocessSandbox:
    root = tmp_path / "sb"
    root.mkdir()
    return LocalSubprocessSandbox(root=root)


def test_write_and_read_file(sandbox: LocalSubprocessSandbox) -> None:
    sandbox.write_file("src/hello.py", "print('hi')\n")
    assert sandbox.read_file("src/hello.py") == "print('hi')\n"
    assert sandbox.file_exists("src/hello.py")
    assert "src/hello.py" in sandbox.list_files()


def test_write_creates_nested_dirs(sandbox: LocalSubprocessSandbox) -> None:
    sandbox.write_file("a/b/c/d.txt", "x")
    assert sandbox.file_exists("a/b/c/d.txt")


def test_path_escape_is_refused(sandbox: LocalSubprocessSandbox) -> None:
    with pytest.raises(SandboxError):
        sandbox.write_file("../escape.txt", "x")
    with pytest.raises(SandboxError):
        sandbox.read_file("../etc/passwd")


def test_run_command_captures_output(sandbox: LocalSubprocessSandbox) -> None:
    sandbox.write_file("hello.py", "print('hello sandbox')\n")
    result = sandbox.run([sys.executable, "hello.py"])

    assert result.ok
    assert "hello sandbox" in result.stdout


def test_run_command_propagates_nonzero_exit(sandbox: LocalSubprocessSandbox) -> None:
    sandbox.write_file("boom.py", "import sys; sys.exit(7)\n")
    result = sandbox.run([sys.executable, "boom.py"])

    assert not result.ok
    assert result.exit_code == 7


def test_run_command_timeout(sandbox: LocalSubprocessSandbox) -> None:
    sandbox.write_file("sleeper.py", "import time; time.sleep(10)\n")
    with pytest.raises(SandboxError):
        sandbox.run([sys.executable, "sleeper.py"], timeout=1)


def test_missing_executable_raises(sandbox: LocalSubprocessSandbox) -> None:
    with pytest.raises(SandboxError):
        sandbox.run(["definitely-not-a-real-binary-xyz"])


def test_from_template_seeds_directory(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "README.md").write_text("hello", encoding="utf-8")

    sb = LocalSubprocessSandbox.from_template(template, dest=tmp_path / "sb")

    assert sb.file_exists("README.md")
    assert sb.read_file("README.md") == "hello"


def test_from_template_refuses_existing_destination(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    dest = tmp_path / "sb"
    dest.mkdir()
    with pytest.raises(SandboxError):
        LocalSubprocessSandbox.from_template(template, dest=dest)
