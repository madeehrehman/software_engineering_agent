"""PR Reviewer subagent (spec §3.2).

Fetches a unified diff via a local git client and produces a structured review
under strict JSON-schema output. The reviewer never wrote the code — independent
eyes are preserved even though code + test generation are merged in the Developer
(spec §12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from sdlc_agent.contracts import (
    ArtifactReturn,
    SubagentName,
    TaskAssignment,
    VerificationCheck,
)
from sdlc_agent.llm import OpenAIClient
from sdlc_agent.mcp.git import LocalGitClient
from sdlc_agent.memory.trajectories import TrajectoryRecorder
from sdlc_agent.skills import SkillLoader, assemble_system_prompt
from sdlc_agent.subagents.base import (
    PROPOSED_MEMORY_SCHEMA,
    build_artifact_return,
    call_llm_with_schema,
    parse_proposed_memory,
    render_injected_context,
)


_REVIEWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifact": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["approve", "request_changes", "comment"],
                },
                "summary": {"type": "string"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "severity": {
                                "type": "string",
                                "enum": ["blocking", "major", "minor", "nit"],
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "bug",
                                    "test",
                                    "style",
                                    "performance",
                                    "security",
                                    "docs",
                                    "design",
                                ],
                            },
                            "comment": {"type": "string"},
                        },
                        "required": ["file", "severity", "category", "comment"],
                        "additionalProperties": False,
                    },
                },
                "strengths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "summary", "issues", "strengths"],
            "additionalProperties": False,
        },
        "proposed_memory": PROPOSED_MEMORY_SCHEMA,
    },
    "required": ["artifact", "proposed_memory"],
    "additionalProperties": False,
}


_SYSTEM_PROMPT = """\
You are the PR Reviewer subagent in an SDLC orchestration system.

Your job:
  * Read one code diff plus curated project context and prior reviewer lore.
  * Produce a *structured review*:
      - verdict: approve | request_changes | comment
      - summary: a one-paragraph overall take
      - issues: zero or more findings with file path, severity, category, comment.
        Use severity=blocking only for things that should block merge.
      - strengths: zero or more things this PR does well.
  * Optionally propose durable memory (project_fact or pr_reviewer subagent_lore).
    Always include concrete evidence. Use HIGH confidence only when the diff
    itself directly proves the claim.

Constraints:
  * You did NOT write this code; review it as an independent reader.
  * You have READ access to the diff via your inputs; you have NO write access,
    no sandbox, and no other tools.
  * Respond ONLY in the JSON shape required by the structured-output schema.
"""


@dataclass
class PRReviewer:
    """Stateless PR Reviewer (spec §3.2)."""

    DEFAULT_SKILLS: ClassVar[tuple[str, ...]] = ("pr-review-rubric",)

    llm: OpenAIClient
    git: LocalGitClient
    name: SubagentName = SubagentName.PR_REVIEWER
    max_diff_chars: int = 16_000
    skills: SkillLoader | None = None
    recorder: TrajectoryRecorder | None = None

    def run(self, assignment: TaskAssignment) -> ArtifactReturn:
        if assignment.subagent is not SubagentName.PR_REVIEWER:
            raise ValueError(
                f"PRReviewer received assignment for {assignment.subagent}"
            )

        base_ref = str(assignment.inputs.get("base_ref") or "main")
        head_ref = str(assignment.inputs.get("head_ref") or "HEAD")

        diff_text = self.git.diff(base_ref=base_ref, head_ref=head_ref)
        files_changed = self.git.files_changed(base_ref=base_ref, head_ref=head_ref)
        truncated = False
        if len(diff_text) > self.max_diff_chars:
            diff_text = diff_text[: self.max_diff_chars]
            truncated = True

        system_prompt = assemble_system_prompt(
            _SYSTEM_PROMPT,
            loader=self.skills,
            skill_names=self.DEFAULT_SKILLS,
        )
        user_prompt = self._build_user_prompt(
            assignment,
            base_ref=base_ref,
            head_ref=head_ref,
            diff_text=diff_text,
            files_changed=files_changed,
            truncated=truncated,
        )
        response = call_llm_with_schema(
            self.llm,
            system=system_prompt,
            user=user_prompt,
            schema_name="pr_reviewer_response",
            schema=_REVIEWER_SCHEMA,
            recorder=self.recorder,
            task_id=assignment.task_id,
            kind="pr_reviewer.run",
            metadata={
                "base_ref": base_ref,
                "head_ref": head_ref,
                "files_changed": files_changed,
                "diff_truncated": truncated,
            },
        )

        artifact_body = response["artifact"]
        proposals = parse_proposed_memory(response.get("proposed_memory"))
        checks = self._self_check(artifact_body, files_changed)

        return build_artifact_return(
            task_id=assignment.task_id,
            artifact_body=artifact_body,
            proposed_memory=proposals,
            self_checks=checks,
            notes=(
                "pr_reviewer self-verified"
                + (" (diff truncated)" if truncated else "")
            ),
        )

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _build_user_prompt(
        assignment: TaskAssignment,
        *,
        base_ref: str,
        head_ref: str,
        diff_text: str,
        files_changed: list[str],
        truncated: bool,
    ) -> str:
        context = render_injected_context(assignment.injected_context)
        files = "\n".join(f"  - {f}" for f in files_changed) or "  (none)"
        trunc_note = (
            "\nNote: the diff was truncated to fit the prompt budget; review what is shown."
            if truncated
            else ""
        )
        return f"""\
{context}

PR diff for ticket {assignment.ticket_id}
  base: {base_ref}
  head: {head_ref}

Files changed:
{files}

Unified diff:
```
{diff_text}
```
{trunc_note}

Produce the structured review."""

    @staticmethod
    def _self_check(
        artifact: dict[str, Any], files_changed: list[str]
    ) -> list[VerificationCheck]:
        verdict = artifact.get("verdict")
        issues = artifact.get("issues") or []
        return [
            VerificationCheck(
                check="verdict is one of approve/request_changes/comment",
                passed=verdict in {"approve", "request_changes", "comment"},
            ),
            VerificationCheck(
                check="summary is non-empty",
                passed=bool((artifact.get("summary") or "").strip()),
            ),
            VerificationCheck(
                check="blocking issues only present when verdict is request_changes",
                passed=(
                    verdict == "request_changes"
                    or not any(i.get("severity") == "blocking" for i in issues)
                ),
            ),
            VerificationCheck(
                check="issue files reference actual changed files (if any)",
                passed=(
                    not issues
                    or not files_changed
                    or all(i.get("file") in files_changed for i in issues)
                ),
            ),
        ]
