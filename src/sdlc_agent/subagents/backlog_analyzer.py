"""Backlog Analyzer subagent (spec §3.2).

Pulls a Jira ticket via :mod:`sdlc_agent.mcp.jira`, analyzes it with an LLM under
strict JSON-schema output, and returns a verified requirement analysis artifact
plus any proposed memory entries.
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
from sdlc_agent.mcp.jira import FixtureJiraMCP, JiraIssue
from sdlc_agent.memory.trajectories import TrajectoryRecorder
from sdlc_agent.skills import SkillLoader, assemble_system_prompt
from sdlc_agent.subagents.base import (
    PROPOSED_MEMORY_SCHEMA,
    build_artifact_return,
    call_llm_with_schema,
    parse_proposed_memory,
    render_injected_context,
)


_BACKLOG_ANALYZER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifact": {
            "type": "object",
            "properties": {
                "ticket_key": {"type": "string"},
                "summary": {"type": "string"},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "ambiguities": {"type": "array", "items": {"type": "string"}},
                "missing_info": {"type": "array", "items": {"type": "string"}},
                "out_of_scope": {"type": "array", "items": {"type": "string"}},
                "ready_for_development": {"type": "boolean"},
                "notes": {"type": "string"},
            },
            "required": [
                "ticket_key",
                "summary",
                "acceptance_criteria",
                "ambiguities",
                "missing_info",
                "out_of_scope",
                "ready_for_development",
                "notes",
            ],
            "additionalProperties": False,
        },
        "proposed_memory": PROPOSED_MEMORY_SCHEMA,
    },
    "required": ["artifact", "proposed_memory"],
    "additionalProperties": False,
}


_SYSTEM_PROMPT = """\
You are the Backlog Analyzer subagent in an SDLC orchestration system.

Your job:
  * Read one Jira ticket plus the curated project context the orchestrator gave you.
  * Produce a *requirement analysis* that identifies:
      - acceptance criteria (clear, testable; carry forward any provided by the reporter)
      - ambiguities (anything a developer could interpret two ways)
      - missing info (facts a developer would need that aren't in the ticket)
      - out of scope (work the ticket implicitly excludes)
  * Decide `ready_for_development`: true ONLY if there are no blocking ambiguities
    or missing info.
  * Optionally propose durable memory entries (project_fact or backlog_analyzer
    subagent_lore). Always include concrete evidence. Use HIGH confidence only
    when the evidence is direct (e.g. quoted from the ticket).

Constraints:
  * You have READ access to Jira via your assignment's inputs; you have NO other
    tools, filesystem access, or write access.
  * Respond ONLY in the JSON shape required by the structured-output schema.
"""


@dataclass
class BacklogAnalyzer:
    """Stateless Backlog Analyzer (spec §3.2)."""

    DEFAULT_SKILLS: ClassVar[tuple[str, ...]] = ("requirement-ambiguity-checklist",)

    llm: OpenAIClient
    jira: FixtureJiraMCP
    name: SubagentName = SubagentName.BACKLOG_ANALYZER
    skills: SkillLoader | None = None
    recorder: TrajectoryRecorder | None = None

    def run(self, assignment: TaskAssignment) -> ArtifactReturn:
        if assignment.subagent is not SubagentName.BACKLOG_ANALYZER:
            raise ValueError(
                f"BacklogAnalyzer received assignment for {assignment.subagent}"
            )

        issue_key = self._issue_key(assignment)
        issue = self.jira.get_issue(issue_key)

        system_prompt = assemble_system_prompt(
            _SYSTEM_PROMPT,
            loader=self.skills,
            skill_names=self.DEFAULT_SKILLS,
        )
        user_prompt = self._build_user_prompt(assignment, issue)
        response = call_llm_with_schema(
            self.llm,
            system=system_prompt,
            user=user_prompt,
            schema_name="backlog_analyzer_response",
            schema=_BACKLOG_ANALYZER_SCHEMA,
            recorder=self.recorder,
            task_id=assignment.task_id,
            kind="backlog_analyzer.run",
            metadata={"issue_key": issue.key},
        )

        artifact_body = response["artifact"]
        proposals = parse_proposed_memory(response.get("proposed_memory"))
        checks = self._self_check(issue, artifact_body)

        return build_artifact_return(
            task_id=assignment.task_id,
            artifact_body=artifact_body,
            proposed_memory=proposals,
            self_checks=checks,
            notes="backlog_analyzer self-verified",
        )

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _issue_key(assignment: TaskAssignment) -> str:
        return str(assignment.inputs.get("jira_key") or assignment.ticket_id)

    @staticmethod
    def _build_user_prompt(assignment: TaskAssignment, issue: JiraIssue) -> str:
        context = render_injected_context(assignment.injected_context)
        comments = (
            "\n".join(f"  - {c.author}: {c.body}" for c in issue.comments)
            if issue.comments
            else "  (none)"
        )
        ac_provided = (
            "\n".join(f"  - {ac}" for ac in issue.acceptance_criteria)
            if issue.acceptance_criteria
            else "  (none provided)"
        )
        return f"""\
{context}

Jira ticket {issue.key}:
  Summary:     {issue.summary}
  Type:        {issue.issue_type}
  Status:      {issue.status}
  Priority:    {issue.priority or "(unset)"}
  Labels:      {", ".join(issue.labels) or "(none)"}
  Components:  {", ".join(issue.components) or "(none)"}

Description:
{issue.description or "(empty)"}

Acceptance criteria already in the ticket:
{ac_provided}

Comments:
{comments}

Produce the requirement analysis."""

    @staticmethod
    def _self_check(issue: JiraIssue, artifact: dict[str, Any]) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = [
            VerificationCheck(
                check="ticket_key matches requested issue",
                passed=artifact.get("ticket_key") == issue.key,
            ),
            VerificationCheck(
                check="at least one acceptance criterion present",
                passed=len(artifact.get("acceptance_criteria") or []) >= 1,
            ),
            VerificationCheck(
                check="ready_for_development is consistent with ambiguities/missing_info",
                passed=(
                    not artifact.get("ready_for_development")
                    or (
                        not (artifact.get("ambiguities") or [])
                        and not (artifact.get("missing_info") or [])
                    )
                ),
            ),
        ]
        return checks
