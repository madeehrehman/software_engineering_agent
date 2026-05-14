"""Shared LLM-prompting helpers for real subagents.

Each subagent is a small class whose ``run(assignment)`` does four things:

1. Gather external inputs (Jira issue / git diff) via its MCP client.
2. Build a prompt that includes the assignment's ``injected_context`` slice.
3. Call the LLM with a strict JSON-schema ``response_format``.
4. Self-verify the parsed artifact and return :class:`ArtifactReturn`.

This module factors out the cross-subagent boilerplate.
"""

from __future__ import annotations

import json
from typing import Any

from sdlc_agent.contracts import (
    ArtifactReturn,
    InjectedContext,
    ProposedMemory,
    TaskStatus,
    VerificationBlock,
    VerificationCheck,
)
from sdlc_agent.llm import OpenAIClient
from sdlc_agent.memory.trajectories import TrajectoryRecorder


PROPOSED_MEMORY_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["project_fact", "subagent_lore"]},
            "claim": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["scope", "claim", "evidence", "confidence"],
        "additionalProperties": False,
    },
}


def render_injected_context(ic: InjectedContext) -> str:
    """Format injected context for inclusion in a user-prompt string."""
    sections: list[str] = []
    if ic.project_facts:
        sections.append(
            "Project facts (curated, durable):\n"
            + "\n".join(f"  - {f}" for f in ic.project_facts)
        )
    if ic.subagent_lore:
        sections.append(
            "Prior lore for this subagent role:\n"
            + "\n".join(f"  - {ln}" for ln in ic.subagent_lore)
        )
    if ic.relevant_artifacts:
        sections.append(
            "Relevant prior artifacts for this ticket (paths only):\n"
            + "\n".join(f"  - {a}" for a in ic.relevant_artifacts)
        )
    return "\n\n".join(sections) if sections else "(no prior context provided)"


def call_llm_with_schema(
    llm: OpenAIClient,
    *,
    system: str,
    user: str,
    schema_name: str,
    schema: dict[str, Any],
    recorder: TrajectoryRecorder | None = None,
    task_id: str | None = None,
    kind: str = "llm_call",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a chat completion with strict JSON-schema enforcement and parse.

    When ``recorder`` is supplied (and ``task_id`` is set), the full prompt and
    raw response are appended to the task's trajectory JSONL for cold-storage
    debugging (spec §5.1, Phase 5).
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    raw = llm.complete(
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        },
    )
    if recorder is not None and task_id:
        recorder.record(
            task_id=task_id,
            kind=kind,
            prompt=messages,
            response=raw,
            metadata={"schema_name": schema_name, **(metadata or {})},
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM response was not valid JSON: {e}\n---\n{raw}") from e


def parse_proposed_memory(raw: list[dict[str, Any]] | None) -> list[ProposedMemory]:
    if not raw:
        return []
    return [ProposedMemory.model_validate(p) for p in raw]


def build_artifact_return(
    *,
    task_id: str,
    artifact_body: dict[str, Any],
    proposed_memory: list[ProposedMemory],
    self_checks: list[VerificationCheck],
    notes: str = "",
) -> ArtifactReturn:
    """Wrap a verified artifact body into the canonical :class:`ArtifactReturn`.

    Status mapping: all checks passed → COMPLETED; any check failed → NEEDS_HUMAN.
    """
    passed = all(c.passed for c in self_checks) if self_checks else True
    return ArtifactReturn(
        task_id=task_id,
        status=TaskStatus.COMPLETED if passed else TaskStatus.NEEDS_HUMAN,
        artifact=artifact_body,
        verification=VerificationBlock(
            self_checks=self_checks,
            passed=passed,
            notes=notes,
        ),
        proposed_memory=proposed_memory,
    )
