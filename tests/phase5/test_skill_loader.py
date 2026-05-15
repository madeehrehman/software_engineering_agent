"""Phase 5: skill resolution and system-prompt assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdlc_agent.skills import (
    DEFAULT_SKILLS_DIR,
    SkillLoader,
    SkillNotFoundError,
    assemble_system_prompt,
)


def test_default_loader_lists_repo_skills() -> None:
    """The default skills directory at repo root resolves and lists the 3 skills."""
    loader = SkillLoader()
    available = set(loader.available())
    assert {
        "tdd-discipline",
        "pr-review-rubric",
        "requirement-ambiguity-checklist",
        "master-agent",
    } <= available


def test_load_returns_markdown_content() -> None:
    loader = SkillLoader()
    text = loader.load("tdd-discipline")
    assert "RED" in text and "GREEN" in text
    assert text == loader.load("tdd-discipline"), "second read should hit the cache"


def test_load_caches(tmp_path: Path) -> None:
    skill = tmp_path / "demo.md"
    skill.write_text("v1", encoding="utf-8")
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.load("demo") == "v1"
    skill.write_text("v2", encoding="utf-8")
    assert loader.load("demo") == "v1", "cached value must not be invalidated by disk change"


def test_missing_skill_raises_with_available_list(tmp_path: Path) -> None:
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    (tmp_path / "beta.md").write_text("b", encoding="utf-8")
    loader = SkillLoader(skills_dir=tmp_path)
    with pytest.raises(SkillNotFoundError) as excinfo:
        loader.load("gamma")
    msg = str(excinfo.value)
    assert "gamma" in msg and "alpha" in msg and "beta" in msg


def test_invalid_skill_names_rejected(tmp_path: Path) -> None:
    loader = SkillLoader(skills_dir=tmp_path)
    for bad in ["", "Foo", "foo_bar", "../etc/passwd", "foo/bar", "foo.bar"]:
        with pytest.raises(SkillNotFoundError):
            loader.load(bad)


def test_load_many_preserves_order(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta", encoding="utf-8")
    (tmp_path / "c.md").write_text("gamma", encoding="utf-8")
    loader = SkillLoader(skills_dir=tmp_path)
    loaded = loader.load_many(["c", "a", "b"])
    assert [n for n, _ in loaded] == ["c", "a", "b"]
    assert [c for _, c in loaded] == ["gamma", "alpha", "beta"]


def test_available_excludes_readme() -> None:
    loader = SkillLoader()
    assert "README" not in loader.available()
    assert "readme" not in loader.available()


def test_assemble_returns_base_when_loader_none() -> None:
    assert assemble_system_prompt("BASE", loader=None, skill_names=["x"]) == "BASE"


def test_assemble_returns_base_when_no_skill_names() -> None:
    loader = SkillLoader()
    assert assemble_system_prompt("BASE", loader=loader, skill_names=[]) == "BASE"


def test_assemble_prepends_skills_section() -> None:
    loader = SkillLoader()
    out = assemble_system_prompt(
        "BASE",
        loader=loader,
        skill_names=["tdd-discipline"],
    )
    assert out.startswith("BASE")
    assert "--- LOADED SKILLS ---" in out
    assert "## Skill: tdd-discipline" in out
    assert "RED" in out  # actual skill content present


def test_assemble_includes_every_named_skill_in_order() -> None:
    loader = SkillLoader()
    out = assemble_system_prompt(
        "BASE",
        loader=loader,
        skill_names=["pr-review-rubric", "tdd-discipline"],
    )
    idx_rubric = out.index("## Skill: pr-review-rubric")
    idx_tdd = out.index("## Skill: tdd-discipline")
    assert idx_rubric < idx_tdd


def test_default_skills_dir_points_at_repo_skills_folder() -> None:
    assert DEFAULT_SKILLS_DIR.name == "skills"
    assert (DEFAULT_SKILLS_DIR / "tdd-discipline.md").is_file()
