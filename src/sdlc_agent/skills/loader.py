"""SkillLoader: resolve named skills to markdown content (spec §10).

Skill names are lower-kebab-case strings (e.g. ``"tdd-discipline"``) that map
to ``skills/<name>.md`` under the system repo. Loading is lazy and cached
in-process — the same file is read at most once per loader instance.

The loader is **read-only** and **safe to share across subagents** in a
single session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

DEFAULT_SKILLS_DIR: Path = Path(__file__).resolve().parents[3] / "skills"

_VALID_NAME = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$")


class SkillNotFoundError(FileNotFoundError):
    """Raised when a named skill cannot be resolved from the skills directory."""


class SkillLoader:
    """Resolves skill names to markdown content from a skills directory.

    Validates names (kebab-case, no path separators) so a malformed name can
    never escape the skills directory.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = (skills_dir or DEFAULT_SKILLS_DIR).resolve()
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        """Return the markdown text for ``name``. Raises if missing/invalid."""
        self._validate_name(name)
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        path = self.skills_dir / f"{name}.md"
        if not path.is_file():
            raise SkillNotFoundError(
                f"skill '{name}' not found at {path}. Available: "
                f"{', '.join(self.available()) or '(none)'}"
            )
        content = path.read_text(encoding="utf-8")
        self._cache[name] = content
        return content

    def load_many(self, names: Iterable[str]) -> list[tuple[str, str]]:
        """Return ``[(name, content), ...]`` preserving input order."""
        return [(n, self.load(n)) for n in names]

    def available(self) -> list[str]:
        """List skill names available on disk (sorted, sans ``.md``)."""
        if not self.skills_dir.is_dir():
            return []
        return sorted(
            p.stem
            for p in self.skills_dir.glob("*.md")
            if p.stem.lower() != "readme"
        )

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not _VALID_NAME.match(name):
            raise SkillNotFoundError(
                f"invalid skill name {name!r}: must be lower-kebab-case "
                "(matching [a-z0-9]([a-z0-9-]*[a-z0-9])?)"
            )


_SKILL_SECTION_HEADER = "--- LOADED SKILLS ---"


def assemble_system_prompt(
    base: str,
    *,
    loader: SkillLoader | None,
    skill_names: Sequence[str],
) -> str:
    """Prepend resolved skill markdown to a subagent's base system prompt.

    Returns ``base`` unchanged when no loader or no names are supplied. Skills
    are rendered under a clearly marked section so the model can distinguish
    static know-how from per-task context.
    """
    if loader is None or not skill_names:
        return base
    loaded = loader.load_many(skill_names)
    skills_block = "\n\n".join(
        f"## Skill: {name}\n\n{content.strip()}" for name, content in loaded
    )
    return (
        f"{base}\n\n"
        f"{_SKILL_SECTION_HEADER}\n\n"
        f"The following skills were loaded by the orchestrator for this task. "
        f"They are durable, project-agnostic know-how that constrains how you work.\n\n"
        f"{skills_block}"
    )
