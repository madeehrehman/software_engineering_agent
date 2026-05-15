# SDLC Deep Agent — Architecture Decisions

This document is the "here's the tradeoff I considered, here's why it broke
this way" companion to the spec (`sdlc-deep-agent-spec.md`). It documents
the load-bearing decisions and the alternatives I rejected at each branch.

The defensible spine, in one line:

> **Disk is memory, context is the working set, retrieval is the bridge.
> There is exactly one orchestrator because exactly one component must own
> the SDLC state machine *and* the memory curation gate. Subagents are
> stateless workers with least-privilege tool scopes; their assignments are
> made stateful by injection. Recursion depth is fixed at one. Persistent
> project knowledge compounds across sessions as curated artifacts on disk,
> never as replayed trajectories, and never rots, because every durable
> write passes an orchestrator-owned promotion gate that distinguishes
> observation from fact.**

What follows are the seven decisions that spine actually produces.

---

## 1. One orchestrator, specialized subagents (recursion depth = 1)

**Decision.** Exactly one agent (the Orchestrator) owns the long-horizon
plan. Subagents are deep in *capability* (sandbox, filesystem write, LLM
access) but short-horizon — one task in, one verified artifact out, return.
Subagents cannot spawn subagents.

**Why this is load-bearing.** Multi-agent systems fail at two recurring
shapes: (a) two agents racing to mutate the same state, and (b) recursion
chains that no longer terminate because each level's "I'll just delegate"
hides the actual decision boundary. Fixing recursion at 1 and concentrating
state authority in one component removes both failure modes by construction.

**Alternative I considered.** A two-tier orchestrator (a "session" agent
above a "ticket" agent) for parallel multi-ticket work. **Rejected** for
this scope — the existing FSM is per-ticket, and parallel multi-ticket is a
queue-scheduling problem above this layer, not an agent-architecture one.
Once the multi-ticket layer exists, it sits *above* the Orchestrator with
no change to anything below.

---

## 2. The orchestrator owns two things, and only those two

**Decision.** The Orchestrator owns (a) the SDLC state machine and (b) the
memory curation gate. Everything else — code edits, diff analysis, ticket
parsing — lives in subagents.

**Why.** Both are single-owner problems. The FSM is single-owner because
"what phase is this ticket in?" must have exactly one answer. The curation
gate is single-owner because "should this become a durable fact?" must
have exactly one decider; if every subagent could promote its own
proposals, durable memory would drift toward whichever subagent talked
most.

**Concretely.** See `src/sdlc_agent/orchestrator/state_machine.py` (FSM)
and `src/sdlc_agent/orchestrator/curation.py` (gate). The dispatcher
(`dispatcher.py`) is glue: it pulls from the FSM, dispatches to a subagent
in the registry, runs returned proposals through the gate, then transitions.

**Supervisor reasoning (when wired).** The spec's "planning tool" for the
orchestrator is implemented as **`MasterAgent`** in
`src/sdlc_agent/orchestrator/master_agent.py`. Pass `llm=` (and optionally
`recorder=` / `skills=`) into `Orchestrator`, or inject a custom
`MasterAgent` via `master_agent=`. When present, the master agent:

- **`create_plan()`** at intake — persists `TicketState.plan` (goal,
  phase checklist, risks, current focus).
- **`evaluate_gate()`** at each SDLC gate — LLM chooses
  `proceed` / `retry` / `blocked` / `needs_human` with rationale; retry
  guidance lands in `TicketState.retry_notes` for the next dispatch.
- **`build_task_description()`** — master-agent-authored dispatch brief
  (plan + retry notes), not a generic stub.

Without `llm` / `master_agent`, gates fall back to deterministic
`evaluate_default_gate()` in `state_machine.py` (self-checks + attempt
limits). That path keeps Phase 1–5 tests and minimal wiring fast; the
demo and production-shaped runs pass `llm=` so the orchestrator acts as
the deep-agent supervisor described in spec §3.1.

---

## 3. Subagents are stateless; assignments are stateful by injection

**Decision.** Subagents own no files, persist nothing, and hold no memory
across tasks. The Orchestrator decides what slice of project memory is
relevant for *this* task and injects it into the `TaskAssignment`
(`injected_context.project_facts`, `injected_context.subagent_lore`, and —
crucially — the content of prior-phase artifacts inlined into `inputs`).

**Why.** Stateless subagents are testable, swappable, and parallel-safe.
"Stateful by injection" gives them the long-horizon context they need
without granting them write access to anything. The PR Reviewer doesn't
need filesystem access to `.deepagent/`; it needs the relevant project
facts and the diff — both arrive in the assignment.

**Alternative I considered.** Granting subagents read access to
`.deepagent/` so they could "pull what they need." **Rejected** because
that inverts least-privilege: the security story collapses when subagents
discover memory rather than receive curated slices, and the path
"discover → use → propose" creates strong pressure for subagents to
*write* back. Pushing all discovery up to the Orchestrator keeps the
gate honest.

---

## 4. Merge code + test generation into one Developer (test-first)

**Decision.** The Developer is a single subagent running a TDD loop:
write a failing test → run tests (RED) → write minimal code → run tests
(GREEN) → iterate per acceptance criterion. The same loop that writes the
code writes its tests.

**The case for splitting** (what I rejected) was that an independent
adversarial Tester finds bugs the author is blind to. That's a real value,
but it's already preserved elsewhere: the PR Reviewer never wrote the
code and reviews independently. The case *for merging* — which won — is
that TDD enforces testability **by construction**. When code and tests
come out of separate subagents, tests get bolted onto whatever shape the
code already has. When the same loop produces both, the code is shaped
by the tests that exercise it.

**Operationally.** This is why `DEVELOPMENT_GATE` is a single gate
checking *both* code and tests — there's no separate test phase. The gate
condition "tests exist for new code" is itself a routing rule: if the
Developer returns code without tests, the gate fails and routes to RETRY,
not to a fictional missing-tester phase.

---

## 5. Every gate is a router, not a boolean

**Decision.** Each SDLC gate (`REQUIREMENTS_GATE`, `DEVELOPMENT_GATE`,
`REVIEW_GATE`) emits one of four decisions: `PROCEED`, `RETRY`,
`BLOCKED`, `NEEDS_HUMAN`. The next state is determined purely by `(gate,
decision)` (`next_phase_for_decision` in `state_machine.py`); routing has
no hidden global state.

**Why.** Pass/fail loses irrecoverable information. A failed gate that
can plausibly be retried with an enriched assignment (e.g. "ambiguities
remain") is operationally different from one that warrants escalation
("the diff deletes a tested behavior") or termination ("max retries
exceeded"). The 4-route model preserves that distinction in code; the
2-route model would force it into prompts or comments where it rots.

**Human-in-the-loop fits cleanly here.** When a gate is configured for
HITL approval, the gate emits `NEEDS_HUMAN`, and a separate `GateApprover`
protocol (default: `HaltForHuman`) decides what to do next. The FSM never
short-circuits on the approver's behalf, which is what lets us swap
`HaltForHuman` for `AutoApprove` (CI), `AutoReject` (audit smoke), or
`ScriptedApprover` (tests) without touching the dispatcher.

**Who picks the route.** With `MasterAgent` wired, gate decisions are
LLM-judged against phase criteria and the artifact body, then passed through
**safety rails** (subagent `NEEDS_HUMAN` → `needs_human`; max attempts →
`blocked`; malformed LLM output → fallback to `evaluate_default_gate`).
Without the master agent, `evaluate_default_gate()` alone applies the same
routing table from self-checks — no semantic judgment, but the FSM and
episodic log shape are identical.

---

## 6. Memory has three stores with three lifetimes; curation is gated

**Decision.** `.deepagent/` separates:

| Store                              | Lifetime          | Loaded                                            |
|-----------------------------------|-------------------|---------------------------------------------------|
| `state/<ticket-id>.json`          | per ticket run    | on resume                                         |
| `project_memory.json` + `subagent_lore/` | across sessions   | every session, injected into every assignment     |
| `episodic/log.jsonl`              | append-only audit | queried for "have we seen this before?"           |
| `trajectories/<session>/`         | cold storage      | never auto-loaded; retrievable by ID              |

**Curation.** Subagents *propose* durable facts via `proposed_memory[]`
in their `ArtifactReturn`. The orchestrator's `CurationGate` is the **sole
writer**. The rules:

- Empty evidence → rejected.
- `confidence: high` + evidence → promoted on first sighting.
- `confidence: medium|low` → recorded as "pending" until a prior sighting
  in the episodic log corroborates the same normalized claim, at which
  point it is promoted.
- Duplicate of an already-promoted claim → corroboration counter bumped,
  evidence/sources accumulated, no duplicate entry.

**Why this is the heart of the system.** Without a gate, "the agent
learned something" decays into either prompt rot (the model hallucinated a
fact, wrote it, now reads it back as ground truth) or a write-amplification
spiral (every run grows the store with near-duplicates). Memory that
compounds requires that durable writes pass an orchestrator-owned filter
distinguishing *observation* from *fact*. Without that filter you do not
have memory; you have noise.

**Alternative I considered.** A "vector store everything, retrieve by
similarity" approach. **Rejected** because it solves a different problem
— retrieval over a large corpus — and the operative bottleneck here is
*write hygiene*, not retrieval cardinality. The store is small (curated
facts only) and is loaded entirely into every assignment. When it grows
past that threshold, retrieval can be swapped underneath without changing
the gate.

---

## 7. Skills are shared infrastructure; trajectories are cold storage

**Decision.** Skills (`skills/*.md`) are versioned, project-agnostic units
of know-how — `tdd-discipline`, `pr-review-rubric`,
`requirement-ambiguity-checklist`, and **`master-agent`** (orchestrator
supervisor doctrine). They live in the *system* repo, are loaded lazily by
a shared `SkillLoader`, and are prepended to each role's system prompt
under a clearly-marked section (subagents and `MasterAgent` alike).

Skill resolution is **static per-role** in this build: each subagent
class declares `DEFAULT_SKILLS: tuple[str, ...]`; `MasterAgent` uses
`DEFAULT_MASTER_AGENT_SKILLS = ("master-agent",)`. This is the minimum
viable plumbing; the spec contemplates skills being "named in or resolved
from the task assignment," which is an additive change — add a
`skills: list[str]` field to `TaskAssignment` and resolve dynamically when
the orchestrator should override.

**Trajectories** (`.deepagent/trajectories/<session>/<task>.jsonl`) capture
every LLM prompt+response for cold-storage debugging. The episodic log
records *that* a dispatch happened, with a `task_id`; the trajectory file
records *why* the LLM said what it said. Trajectories are append-only
JSONL, one file per task, never auto-loaded — they exist for debugging,
not for replay.

**Alternative I considered for skills.** Pulling skills via retrieval over
a vector index. **Rejected** for this scope because the cardinality is
tiny (≤ 10 skills, ≤ 100 lines each) and the value of explicit, named,
versioned skills outweighs any flexibility from semantic search. Skills
are doctrine, not knowledge; doctrine is named.

---

## What is intentionally not built

These are deferrals, not gaps — they don't change the architecture, they
swap an implementation under an existing seam.

- **Real Jira / git MCP servers.** The `FixtureJiraMCP` and `LocalGitClient`
  implementations satisfy the same `MCPClient`-shaped surface a real MCP
  server would; swapping is a one-import change.
- **Docker sandbox for the Developer.** `LocalSubprocessSandbox` implements
  the `Sandbox` Protocol; a `DockerSandbox` is a drop-in replacement when
  the deployment target moves off the developer's laptop.
- **LangGraph supervisor wrapper.** The current `Orchestrator` is a hand-
  rolled FSM with an optional in-process **`MasterAgent`** supervisor LLM
  (not LangGraph). LangGraph would add graphical introspection and built-in
  checkpointing; the existing per-transition `save_ticket_state` already
  provides the durability guarantee LangGraph's checkpointing would.
- **CLI entrypoints** (`sdlc-agent init|run|status`). `scripts/demo.py`
  shows the full wire-up; promoting that to an installed console-script is
  a `[project.scripts]` line.
- **Per-task skill resolution.** `TaskAssignment.skills: list[str]`
  augments the current static-per-role mapping; the loader and assembly
  helper are already written to consume an arbitrary name list.

---

## How to read the codebase

Start at the seams, not the implementations:

1. `src/sdlc_agent/contracts/` — `TaskAssignment` and `ArtifactReturn` are
   the only two messages crossing the orchestrator/subagent boundary. If
   you can read these two files, you understand the wire protocol.
2. `src/sdlc_agent/orchestrator/state_machine.py` — pure FSM. No I/O, no
   LLM, no MCP. The phase graph in spec §4 is implemented here verbatim.
3. `src/sdlc_agent/orchestrator/dispatcher.py` — the only file that does
   real work. Dispatch → curation → gate → transition → persist, with HITL
   plugged in via a Protocol. Optional `MasterAgent` for plan + gate + briefs.
4. `src/sdlc_agent/orchestrator/master_agent.py` — supervisor system prompt,
   structured LLM calls (`master_agent.plan`, `master_agent.gate` trajectory
   kinds), and dispatch brief assembly. Read after the dispatcher if you care
   why the orchestrator is a "deep agent" and not only a router.
5. `src/sdlc_agent/subagents/` — each subagent is ~150–350 LOC of prompt +
   self-checks. They're interchangeable.
6. `scripts/demo.py` — wires `llm`, `recorder`, and `skills` on both
   subagents and `Orchestrator` (master agent included) against fixture
   ticket DEMO-42.
