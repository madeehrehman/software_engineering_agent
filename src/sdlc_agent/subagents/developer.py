"""Developer subagent (spec §3.2, §12).

A stateless TDD loop: the LLM proposes one step at a time (write_test /
write_code / run_tests / complete); the Developer applies it in its sandbox and
feeds the test runner's output back into the next prompt. After the loop a
single ``summarize`` LLM call produces the artifact body + proposed memory.
Self-checks enforce: final tests green, ≥1 test file written, ≥1 impl file
written, and every claimed file actually exists in the sandbox.

The spec's "code + tests in one TDD loop" tradeoff (§12) is the load-bearing
property: testability is enforced *by construction* because the same loop that
writes the code writes the tests that exercise it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from sdlc_agent.contracts import (
    ArtifactReturn,
    SubagentName,
    TaskAssignment,
    VerificationCheck,
)
from sdlc_agent.llm import OpenAIClient
from sdlc_agent.memory.trajectories import TrajectoryRecorder
from sdlc_agent.sandbox import Sandbox, SandboxResult
from sdlc_agent.skills import SkillLoader, assemble_system_prompt
from sdlc_agent.subagents.base import (
    PROPOSED_MEMORY_SCHEMA,
    build_artifact_return,
    call_llm_with_schema,
    parse_proposed_memory,
    render_injected_context,
)


_DEV_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["write_test", "write_code", "run_tests", "complete"],
        },
        "file_path": {"type": "string"},
        "content": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["action", "file_path", "content", "rationale"],
    "additionalProperties": False,
}


_DEV_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifact": {
            "type": "object",
            "properties": {
                "implementation_summary": {"type": "string"},
                "impl_files": {"type": "array", "items": {"type": "string"}},
                "test_files": {"type": "array", "items": {"type": "string"}},
                "iterations_used": {"type": "integer"},
                "final_tests_green": {"type": "boolean"},
                "acceptance_criteria_addressed": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "implementation_summary",
                "impl_files",
                "test_files",
                "iterations_used",
                "final_tests_green",
                "acceptance_criteria_addressed",
            ],
            "additionalProperties": False,
        },
        "proposed_memory": PROPOSED_MEMORY_SCHEMA,
    },
    "required": ["artifact", "proposed_memory"],
    "additionalProperties": False,
}


_STEP_SYSTEM_PROMPT = """\
You are the Developer subagent. You implement against a requirement analysis,
strictly test-first: the SAME loop that writes the code writes its tests.

Discipline:
  * Write a failing test FIRST, then run tests to confirm it fails (RED).
  * Then write minimal code to make it pass, run tests, confirm green (GREEN).
  * Iterate per acceptance criterion. Refactor only when green.
  * Use `complete` only when all acceptance criteria are addressed AND the
    final test run was green.

For each turn return ONE step:
  * write_test: file_path under tests/, content is the full test file.
  * write_code: file_path under the source tree, content is the full source file.
  * run_tests: file_path/content empty strings.
  * complete:   file_path/content empty strings.

Constraints:
  * Sandboxed filesystem — paths are relative to the sandbox root.
  * No network. No tool calls. Only this step schema.
  * Respond ONLY in the JSON shape required by the structured-output schema.
"""


_SUMMARY_SYSTEM_PROMPT = """\
You are the Developer subagent producing the final return artifact after a TDD
loop. Be honest about what you wrote and whether tests are green.

Set `final_tests_green` strictly from the latest test-runner result reported in
the loop trace. List every test file under `test_files` and every impl file
under `impl_files`. Optionally propose durable memory with concrete evidence
drawn from the loop trace (e.g. "this project uses unittest, not pytest").

Respond ONLY in the JSON shape required by the structured-output schema.
"""


@dataclass
class _LoopStep:
    iteration: int
    action: str
    file_path: str = ""
    content: str = ""
    rationale: str = ""
    test_exit_code: int | None = None
    test_stdout_tail: str = ""
    test_stderr_tail: str = ""

    def for_prompt(self) -> str:
        if self.action == "run_tests":
            return (
                f"  - it {self.iteration}: run_tests → "
                f"exit={self.test_exit_code} "
                f"(stdout tail: {self.test_stdout_tail[-300:]!r}; "
                f"stderr tail: {self.test_stderr_tail[-300:]!r})"
            )
        if self.action in ("write_test", "write_code"):
            return (
                f"  - it {self.iteration}: {self.action} {self.file_path} "
                f"({len(self.content)} chars) — {self.rationale[:120]}"
            )
        return f"  - it {self.iteration}: {self.action} — {self.rationale[:120]}"


@dataclass
class Developer:
    """Stateless TDD-loop Developer (spec §3.2 / §12 merged-loop tradeoff)."""

    DEFAULT_SKILLS: ClassVar[tuple[str, ...]] = ("tdd-discipline",)

    llm: OpenAIClient
    sandbox: Sandbox
    max_iterations: int = 8
    name: SubagentName = SubagentName.DEVELOPER
    skills: SkillLoader | None = None
    recorder: TrajectoryRecorder | None = None
    _last_test_result: SandboxResult | None = field(default=None, init=False, repr=False)

    def run(self, assignment: TaskAssignment) -> ArtifactReturn:
        if assignment.subagent is not SubagentName.DEVELOPER:
            raise ValueError(
                f"Developer received assignment for {assignment.subagent}"
            )

        requirement_analysis = self._requirement_analysis(assignment)
        history: list[_LoopStep] = []
        test_files: list[str] = []
        impl_files: list[str] = []

        for iteration in range(1, self.max_iterations + 1):
            step = self._llm_next_step(
                assignment=assignment,
                requirement_analysis=requirement_analysis,
                history=history,
            )
            action = step["action"]
            record = _LoopStep(
                iteration=iteration,
                action=action,
                file_path=step.get("file_path", ""),
                content=step.get("content", ""),
                rationale=step.get("rationale", ""),
            )

            if action == "complete":
                history.append(record)
                break

            if action == "write_test":
                self.sandbox.write_file(record.file_path, record.content)
                if record.file_path not in test_files:
                    test_files.append(record.file_path)
            elif action == "write_code":
                self.sandbox.write_file(record.file_path, record.content)
                if record.file_path not in impl_files:
                    impl_files.append(record.file_path)
            elif action == "run_tests":
                result = self.sandbox.run_tests()
                self._last_test_result = result
                record.test_exit_code = result.exit_code
                record.test_stdout_tail = result.stdout
                record.test_stderr_tail = result.stderr
            else:
                raise ValueError(f"unknown action: {action}")

            history.append(record)

        if self._last_test_result is None:
            self._last_test_result = self.sandbox.run_tests()

        summary = self._llm_summarize(
            assignment=assignment,
            requirement_analysis=requirement_analysis,
            history=history,
            test_files=test_files,
            impl_files=impl_files,
        )

        artifact_body = summary["artifact"]
        proposals = parse_proposed_memory(summary.get("proposed_memory"))
        checks = self._self_check(
            artifact_body=artifact_body,
            test_files=test_files,
            impl_files=impl_files,
        )

        return build_artifact_return(
            task_id=assignment.task_id,
            artifact_body=artifact_body,
            proposed_memory=proposals,
            self_checks=checks,
            notes=f"developer TDD loop: {len(history)} steps, final exit={self._last_test_result.exit_code}",
        )

    # ----------------------------------------------------------- prompts
    def _llm_next_step(
        self,
        *,
        assignment: TaskAssignment,
        requirement_analysis: dict[str, Any] | None,
        history: list[_LoopStep],
    ) -> dict[str, Any]:
        user = self._build_step_prompt(
            assignment=assignment,
            requirement_analysis=requirement_analysis,
            history=history,
        )
        system = assemble_system_prompt(
            _STEP_SYSTEM_PROMPT,
            loader=self.skills,
            skill_names=self.DEFAULT_SKILLS,
        )
        return call_llm_with_schema(
            self.llm,
            system=system,
            user=user,
            schema_name="developer_step",
            schema=_DEV_STEP_SCHEMA,
            recorder=self.recorder,
            task_id=assignment.task_id,
            kind="developer.step",
            metadata={"iteration": len(history) + 1},
        )

    def _llm_summarize(
        self,
        *,
        assignment: TaskAssignment,
        requirement_analysis: dict[str, Any] | None,
        history: list[_LoopStep],
        test_files: list[str],
        impl_files: list[str],
    ) -> dict[str, Any]:
        files_on_disk = self.sandbox.list_files()
        last = self._last_test_result
        user = (
            f"Loop summary for ticket {assignment.ticket_id}:\n\n"
            f"Iterations used: {len(history)}\n"
            f"Test files written: {test_files}\n"
            f"Impl files written: {impl_files}\n"
            f"All files in sandbox: {files_on_disk}\n"
            f"Final test exit code: {last.exit_code if last else 'n/a'}\n"
            f"Final test stdout tail: {(last.stdout if last else '')[-1500:]}\n"
            f"Final test stderr tail: {(last.stderr if last else '')[-500:]}\n\n"
            "Trace:\n" + "\n".join(s.for_prompt() for s in history) + "\n\n"
            f"Requirement analysis: {requirement_analysis}\n"
            "Produce the final artifact + any proposed memory."
        )
        system = assemble_system_prompt(
            _SUMMARY_SYSTEM_PROMPT,
            loader=self.skills,
            skill_names=self.DEFAULT_SKILLS,
        )
        return call_llm_with_schema(
            self.llm,
            system=system,
            user=user,
            schema_name="developer_summary",
            schema=_DEV_SUMMARY_SCHEMA,
            recorder=self.recorder,
            task_id=assignment.task_id,
            kind="developer.summary",
            metadata={
                "iterations": len(history),
                "final_exit_code": last.exit_code if last else None,
            },
        )

    @staticmethod
    def _build_step_prompt(
        *,
        assignment: TaskAssignment,
        requirement_analysis: dict[str, Any] | None,
        history: list[_LoopStep],
    ) -> str:
        context = render_injected_context(assignment.injected_context)
        ra = requirement_analysis or {}
        ac = ra.get("acceptance_criteria") or []
        trace = "\n".join(s.for_prompt() for s in history) or "  (no steps yet)"
        return f"""\
{context}

Ticket: {assignment.ticket_id}
Requirement analysis:
  Summary: {ra.get("summary", "(none)")}
  Acceptance criteria:
{chr(10).join(f"    - {c}" for c in ac) or "    (none)"}
  Ambiguities: {ra.get("ambiguities") or "(none)"}
  Missing info: {ra.get("missing_info") or "(none)"}

Loop trace so far ({len(history)} step(s), max {assignment.inputs.get("max_iterations", "?")}):
{trace}

Choose the next step. Remember: tests first (RED), then code (GREEN), then iterate.
"""

    # ---------------------------------------------------------- helpers
    @staticmethod
    def _requirement_analysis(assignment: TaskAssignment) -> dict[str, Any] | None:
        ra = assignment.inputs.get("requirement_analysis")
        if isinstance(ra, dict):
            return ra
        return None

    def _self_check(
        self,
        *,
        artifact_body: dict[str, Any],
        test_files: list[str],
        impl_files: list[str],
    ) -> list[VerificationCheck]:
        last = self._last_test_result
        actual_green = last is not None and last.ok
        claimed_green = bool(artifact_body.get("final_tests_green"))

        claimed_impl = artifact_body.get("impl_files") or []
        claimed_tests = artifact_body.get("test_files") or []
        all_claimed_on_disk = all(
            self.sandbox.file_exists(p) for p in (*claimed_impl, *claimed_tests)
        )

        return [
            VerificationCheck(
                check="final test run is green",
                passed=actual_green,
            ),
            VerificationCheck(
                check="claimed final_tests_green matches actual",
                passed=claimed_green == actual_green,
            ),
            VerificationCheck(
                check="at least one test file was written",
                passed=len(test_files) >= 1,
            ),
            VerificationCheck(
                check="at least one impl file was written",
                passed=len(impl_files) >= 1,
            ),
            VerificationCheck(
                check="all claimed files exist in the sandbox",
                passed=all_claimed_on_disk,
            ),
        ]
