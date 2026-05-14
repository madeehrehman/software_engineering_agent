"""Phase 2: Backlog Analyzer end-to-end with a mocked LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sdlc_agent.contracts import (
    Constraints,
    InjectedContext,
    SubagentName,
    TaskAssignment,
    TaskStatus,
)
from sdlc_agent.llm.openai_client import OpenAIClient
from sdlc_agent.mcp.jira import FixtureJiraMCP
from sdlc_agent.subagents import BacklogAnalyzer


def _assignment(
    ticket_id: str = "TICKET-12",
    *,
    project_facts: list[str] | None = None,
) -> TaskAssignment:
    return TaskAssignment(
        task_id="task-1",
        ticket_id=ticket_id,
        subagent=SubagentName.BACKLOG_ANALYZER,
        task="Analyze backlog",
        inputs={"phase": "REQUIREMENTS_ANALYSIS", "attempt": 1},
        injected_context=InjectedContext(project_facts=project_facts or []),
        constraints=Constraints(),
    )


def _canned_response(
    ticket_key: str = "TICKET-12",
    *,
    ambiguities: list[str] | None = None,
    missing_info: list[str] | None = None,
    proposed_memory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": ticket_key,
            "summary": "Add per-IP rate limiting to /api/login",
            "acceptance_criteria": [
                "HTTP 429 returned after threshold",
                "limit configurable per env",
            ],
            "ambiguities": ambiguities or [],
            "missing_info": missing_info or [],
            "out_of_scope": [],
            "ready_for_development": not (ambiguities or missing_info),
            "notes": "mocked",
        },
        "proposed_memory": proposed_memory or [],
    }


def test_backlog_analyzer_returns_well_formed_artifact(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_canned_response()])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )

    out = analyzer.run(_assignment())

    assert out.status is TaskStatus.COMPLETED
    assert out.verification.passed is True
    assert out.artifact["ticket_key"] == "TICKET-12"
    assert out.artifact["acceptance_criteria"]
    assert all(c.passed for c in out.verification.self_checks)


def test_inconsistent_ready_flag_fails_self_check(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    bad = _canned_response(ambiguities=["what does 'fast' mean"])
    bad["artifact"]["ready_for_development"] = True
    llm = fake_llm_factory([bad])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )

    out = analyzer.run(_assignment())

    assert out.verification.passed is False
    assert out.status is TaskStatus.NEEDS_HUMAN
    failed = [c for c in out.verification.self_checks if not c.passed]
    assert any("ready_for_development" in c.check for c in failed)


def test_wrong_ticket_key_fails_self_check(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    bad = _canned_response(ticket_key="TICKET-XXX")
    llm = fake_llm_factory([bad])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )

    out = analyzer.run(_assignment())

    assert out.verification.passed is False


def test_proposed_memory_passed_through(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    response = _canned_response(
        proposed_memory=[
            {
                "scope": "project_fact",
                "claim": "auth endpoints are HIGH priority",
                "evidence": "TICKET-12 labeled 'security' and priority High",
                "confidence": "high",
            }
        ]
    )
    llm = fake_llm_factory([response])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )

    out = analyzer.run(_assignment())

    assert len(out.proposed_memory) == 1
    assert out.proposed_memory[0].confidence.value == "high"


def test_prompt_includes_injected_context_and_ticket_body(
    jira_fixture_dir: Path,
    fake_llm_factory: Callable[[list[Any]], OpenAIClient],
) -> None:
    llm = fake_llm_factory([_canned_response()])
    analyzer = BacklogAnalyzer(
        llm=llm, jira=FixtureJiraMCP(fixture_dir=jira_fixture_dir)
    )

    analyzer.run(_assignment(project_facts=["repo uses pytest"]))

    last_call = llm._client.chat.completions.calls[-1]  # type: ignore[attr-defined]
    user_msg = last_call["messages"][1]["content"]
    assert "repo uses pytest" in user_msg
    assert "TICKET-12" in user_msg
    assert "Add rate limiting to /api/login" in user_msg
    assert last_call["response_format"]["type"] == "json_schema"
