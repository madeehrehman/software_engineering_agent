"""Phase 0 smoke: package imports + version exposed."""

from __future__ import annotations


def test_package_imports() -> None:
    import sdlc_agent

    assert sdlc_agent.__version__ == "0.1.0"


def test_subpackage_imports() -> None:
    from sdlc_agent import config, llm, mcp, memory  # noqa: F401
