# Skill: requirement-ambiguity-checklist

**Audience:** Backlog Analyzer subagent.
**Purpose:** Detect underspecified tickets *before* code is written. A failed
detection here costs an entire DEVELOPMENT phase.

A ticket is **ambiguous** when two competent engineers, reading it
independently, could ship two different implementations and both believe they
satisfied the request.

## Checklist (run every item before deciding `ready_for_development`)

1. **Behavior boundary.** Is there at least one concrete input → output example
   in the ticket, comments, or referenced docs? If only abstract behavior is
   described, the ticket is ambiguous.
2. **Failure modes.** What should happen for: empty input, oversized input,
   malformed input, the unhappy path generally? If the ticket is silent, flag.
3. **Side effects.** Does the change touch persistence, external systems,
   feature flags, telemetry, or auth? If yes, are those side effects spelled
   out? "Don't break existing X" is *not* a spec.
4. **Scope edges.** What is explicitly *not* in scope? If the ticket
   description could plausibly drag in adjacent work, the boundary must be
   stated. List those out-of-scope items in `out_of_scope`.
5. **Acceptance criteria are testable.** Each criterion must be phraseable as
   a unit or integration test. "Improves performance" is not a criterion; "p95
   latency on /search drops below 200 ms with the existing benchmark suite" is.
6. **Vocabulary alignment.** Are domain terms ("user," "session," "tenant,"
   "draft") defined or unambiguously sourced from prior project memory? When
   in doubt, flag as ambiguity.
7. **Authority of the reporter.** If the reporter has a track record of
   underspecifying (per `backlog_analyzer` subagent lore), demand stronger
   evidence before marking ready.

## Output rules

- Every entry in `ambiguities` and `missing_info` must be **one specific,
  answerable question** ("What status code on duplicate submit?"), not a
  vague concern ("Error handling is unclear").
- `ready_for_development = true` requires `ambiguities == [] AND missing_info == []`.
  The self-check enforces this; don't try to bypass it.
- `out_of_scope` is for things the ticket *implies* are excluded, not for
  things you wish were excluded.

## Memory proposals

- Propose **`project_fact`** only when a new fact about the repo would help
  every future backlog analysis (e.g. "this project gates merges on a
  `BREAKING CHANGES` label").
- Propose **`backlog_analyzer` `subagent_lore`** when you notice a recurring
  reporter pattern, ticket-format quirk, or class of ambiguity (e.g. "tickets
  filed under the `infra` component frequently omit the runtime version").
- Use `confidence: high` only when evidence is **directly quoted from the
  ticket or a prior promoted fact**.
