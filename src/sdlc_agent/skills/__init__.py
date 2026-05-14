"""Skill library (spec §10).

Skills are shared, versioned, project-agnostic markdown units of know-how.
The :class:`SkillLoader` resolves names like ``"pr-review-rubric"`` to the
text of ``skills/pr-review-rubric.md`` in the system repo and caches them
in-process.

Subagents do not own skill files — they declare their `DEFAULT_SKILLS` and
the loader (handed in by the orchestrator/owner) resolves them on demand.
"""

from sdlc_agent.skills.loader import (
    DEFAULT_SKILLS_DIR,
    SkillLoader,
    SkillNotFoundError,
    assemble_system_prompt,
)

__all__ = [
    "DEFAULT_SKILLS_DIR",
    "SkillLoader",
    "SkillNotFoundError",
    "assemble_system_prompt",
]
