"""Local subprocess-based sandbox.

Phase 3 ships a temp-directory-scoped sandbox: file writes are refused outside
``root``, command execution is bounded by a timeout, and the sandbox can be
seeded from a template directory. A Docker-backed sandbox is the natural next
implementation when "real" isolation is required (per spec §9); this class is
designed to be a drop-in target for the same Protocol.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class SandboxError(RuntimeError):
    """Raised on sandbox misuse (path escape, missing root, timeout, etc.)."""


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class Sandbox(Protocol):
    """Minimal sandbox surface the Developer subagent needs.

    All paths are *relative* to ``root``; implementations enforce containment.
    """

    root: Path
    default_test_command: list[str]

    def write_file(self, relative_path: str, content: str) -> None: ...
    def read_file(self, relative_path: str) -> str: ...
    def file_exists(self, relative_path: str) -> bool: ...
    def list_files(self, glob: str = "**/*") -> list[str]: ...
    def run(self, command: list[str], *, timeout: int = 60) -> SandboxResult: ...
    def run_tests(self, command: list[str] | None = None) -> SandboxResult: ...


@dataclass
class LocalSubprocessSandbox:
    """Subprocess sandbox rooted at a temp directory."""

    root: Path
    default_test_command: list[str] = field(
        default_factory=lambda: ["python", "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    )
    default_timeout_seconds: int = 60

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.root.exists():
            raise SandboxError(f"sandbox root does not exist: {self.root}")
        if not self.root.is_dir():
            raise SandboxError(f"sandbox root is not a directory: {self.root}")

    # ------------------------------------------------------- construction
    @classmethod
    def from_template(
        cls,
        template_dir: Path,
        *,
        dest: Path,
        default_test_command: list[str] | None = None,
    ) -> "LocalSubprocessSandbox":
        """Copy ``template_dir`` into ``dest`` and return a sandbox at ``dest``."""
        if dest.exists():
            raise SandboxError(f"destination already exists: {dest}")
        shutil.copytree(template_dir, dest)
        kwargs: dict = {}
        if default_test_command is not None:
            kwargs["default_test_command"] = default_test_command
        return cls(root=dest, **kwargs)

    # -------------------------------------------------------------- I/O
    def write_file(self, relative_path: str, content: str) -> None:
        target = self._resolve_within(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, relative_path: str) -> str:
        target = self._resolve_within(relative_path)
        if not target.is_file():
            raise SandboxError(f"no such file in sandbox: {relative_path}")
        return target.read_text(encoding="utf-8")

    def file_exists(self, relative_path: str) -> bool:
        try:
            target = self._resolve_within(relative_path)
        except SandboxError:
            return False
        return target.is_file()

    def list_files(self, glob: str = "**/*") -> list[str]:
        return sorted(
            str(p.relative_to(self.root).as_posix())
            for p in self.root.glob(glob)
            if p.is_file()
        )

    # ------------------------------------------------------------- exec
    def run(self, command: list[str], *, timeout: int | None = None) -> SandboxResult:
        if not command:
            raise SandboxError("empty command")
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.default_timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxError(
                f"command timed out after {e.timeout}s: {' '.join(command)}"
            ) from e
        except FileNotFoundError as e:
            raise SandboxError(f"executable not found: {command[0]}") from e
        return SandboxResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def run_tests(self, command: list[str] | None = None) -> SandboxResult:
        return self.run(command or self.default_test_command)

    # -------------------------------------------------------- internal
    def _resolve_within(self, relative_path: str) -> Path:
        if not relative_path:
            raise SandboxError("empty path")
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as e:
            raise SandboxError(
                f"path escapes sandbox root: {relative_path!r} -> {candidate}"
            ) from e
        return candidate
