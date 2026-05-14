# SDLC Deep Agent

A **project-scoped orchestrator** that drives tickets through an explicit SDLC state machine—from backlog analysis through test-first development and structured PR review—while keeping **disk as memory**, **subagents stateless**, and **every durable fact behind a curation gate**.

Built in Python around **OpenAI** (strict JSON-schema outputs), **fixture-backed Jira** and **local `git`** for integration demos, and a **skills** library plus **trajectory archiving** for observable, debuggable runs.

---

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Project memory (`.deepagent/`)](#project-memory-deepagent)
- [Testing](#testing)
- [Documentation](#documentation)
- [Extending the system](#extending-the-system)

---

## Overview

The system attaches to a **target repository**, initializes a `.deepagent/` workspace, and runs each ticket through:

`INTAKE` → **Requirements analysis** (Backlog Analyzer) → gate → **Development** (Developer, TDD in a sandbox) → gate → **PR review** (PR Reviewer) → gate → `DONE`

A single **Orchestrator** owns the FSM and the **memory curation gate**; three **subagents** each implement `run(TaskAssignment) -> ArtifactReturn`, self-verify structured outputs, and may emit **proposed memory** that only the orchestrator can promote.

Design authority lives in **`sdlc-deep-agent-spec.md`**. Rationale for major tradeoffs (TDD-merge vs split tester, why no LangGraph in-tree, MCP swap points) lives in **`ARCHITECTURE.md`**.

---

## Features

- **Explicit SDLC FSM** with `PROCEED` / `RETRY` / `BLOCKED` / `NEEDS_HUMAN` routing; state persisted after every transition for resume.
- **Three production-shaped subagents:** Jira-backed requirement analysis, sandboxed TDD developer, git-diff-based structured review—all with schema-constrained LLM I/O and self-checks.
- **Stateful-by-injection assignments:** curated project facts + per-role lore + **inlined prior artifact bodies** (subagents never need read access to `.deepagent/artifacts/`).
- **Curation gate:** evidence-aware promotion, corroboration for medium/low confidence, deduplication—sole path to durable `project_memory.json` / `subagent_lore/`.
- **Human-in-the-loop:** `GateApprover` protocol (`HaltForHuman`, `AutoApprove`, `AutoReject`, `ScriptedApprover`) wired at requirements and review gates via config.
- **Skills:** versioned Markdown in `skills/`, loaded by `SkillLoader` and merged into each subagent’s system prompt via per-role `DEFAULT_SKILLS`.
- **Cold trajectories:** append-only `.deepagent/trajectories/<session-id>/<task-id>.jsonl` records every LLM prompt/response when a `TrajectoryRecorder` is wired; episodic log events carry matching `session_id`.
- **Deterministic demo:** `scripts/demo.py` runs a non-trivial ticket with a canned LLM (no API key required).

---

## Requirements

- **Python** 3.11 or newer (see `pyproject.toml`).
- **Git** available on `PATH` for PR Reviewer tests and demos that use `LocalGitClient`.
- **OpenAI API key** only if you run subagents or live tests against the real API (optional for unit tests and the stock demo).

---

## Installation

Clone the repository, create a virtual environment, and install in editable mode with dev dependencies:

```powershell
cd software_engineering_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On macOS or Linux, use `source .venv/bin/activate` and the same `pip install` line.

---

## Configuration

1. Copy the example environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Set at least:

   | Variable | Purpose |
   |----------|---------|
   | `OPENAI_API_KEY` | Required for real Chat Completions calls (not required for default `pytest` or `scripts/demo.py` canned path). |
   | `OPENAI_MODEL` | Optional; defaults to a sensible model in `OpenAIClient` if unset. |

3. **Gate / model / MCP endpoints** can be tuned via `.deepagent/config.yaml` after you run `initialize_deepagent` or the demo against a target repo (see `src/sdlc_agent/config.py` for the schema).

---

## Quick start

```powershell
# Full automated test suite (mocked LLM; no API key needed)
python -m pytest

# End-to-end walkthrough: INIT → DONE on fixture ticket DEMO-42 (canned LLM, seconds)
python scripts\demo.py

# Optional: hit the live OpenAI API in marked tests
python -m pytest --run-live -m live
```

---

## Usage

### End-to-end demo

`scripts/demo.py` initializes `.deepagent/` in a temporary target repo (or use `--target-repo`), wires **BacklogAnalyzer**, **Developer**, and **PRReviewer** with shared **skills** and **trajectories**, and prints phase transitions, promoted memory, episodic summaries, and per-task trajectory files.

```powershell
python scripts\demo.py                 # stdout narrative; temp dir discarded
python scripts\demo.py --keep            # preserve temp workspace for inspection
python scripts\demo.py --quiet           # exit 0 smoke only (CI-friendly)
python scripts\demo.py --target-repo C:\path\to\your\repo  # persistent .deepagent/
```

Fixture Jira issues for the demo live under `scripts/demo_fixtures/jira/`.

### Programmatic use

Import the orchestrator from the dispatcher module (package `__init__` avoids eager-loading the full loop):

```python
from pathlib import Path
from sdlc_agent.contracts import SubagentName
from sdlc_agent.memory import initialize_deepagent
from sdlc_agent.orchestrator.dispatcher import Orchestrator
# Construct registry: dict[SubagentName, Subagent], paths: DeepAgentPaths, then:
# orch = Orchestrator(paths=paths, registry=registry, session_id="my-session")
# orch.intake("TICKET-1", ticket_inputs={"jira_key": "TICKET-1", ...})
# orch.run_to_completion("TICKET-1")
```

Subagents (`BacklogAnalyzer`, `Developer`, `PRReviewer`) accept optional `skills: SkillLoader | None` and `recorder: TrajectoryRecorder | None`. See `tests/phase5/test_orchestrator_full_sdlc_with_skills.py` for a full wiring example.

### Skills

- Add Markdown files under `skills/<kebab-name>.md`.
- Point a subagent’s `DEFAULT_SKILLS` at those names or (future) extend `TaskAssignment` with an explicit skill list—see **`sdlc-deep-agent-spec.md`** §10 and **`ARCHITECTURE.md`**.

---

## Architecture

| Principle | Meaning in this codebase |
|-----------|---------------------------|
| One orchestrator | `Orchestrator` in `orchestrator/dispatcher.py`—only component that transitions the FSM, writes `.deepagent/`, and runs the curation gate. |
| Recursion depth 1 | Subagents do not delegate; they return one verified `ArtifactReturn` per dispatch. |
| Disk is memory | `MemoryStores`, JSON/JSONL under `.deepagent/`; context carries a working subset via `TaskAssignment`. |
| Curation gate | `orchestrator/curation.py`; subagents emit `ProposedMemory` only. |
| Least privilege | Permissions on assignments; artifact **content** injected in `inputs`, not handed as raw filesystem access to lore paths. |

**SDLC phases** (high level): `INTAKE` → `REQUIREMENTS_ANALYSIS` → `REQUIREMENTS_GATE` → `DEVELOPMENT` → `DEVELOPMENT_GATE` → `PR_REVIEW` → `REVIEW_GATE` → `DONE` (or `BLOCKED` / `NEEDS_HUMAN`).

For diagrams, gate decisions, and “why not LangGraph / why merge TDD,” read **`ARCHITECTURE.md`** and **`sdlc-deep-agent-spec.md`** §4–§5 and §12–§13.

---

## Repository layout

| Path | Role |
|------|------|
| `src/sdlc_agent/` | Installable package: contracts, LLM client, MCP clients, memory, orchestrator, sandbox, skills loader, subagents. |
| `skills/` | Shared Markdown skills (project-agnostic doctrine). |
| `scripts/demo.py` | Runnable end-to-end scenario with deterministic canned LLM. |
| `scripts/demo_fixtures/jira/` | Jira JSON fixtures used only by the demo. |
| `tests/phase0` … `tests/phase5` | Phase-aligned pytest modules (initializer through skills + trajectories integration). |
| `sdlc-deep-agent-spec.md` | Canonical architecture + contracts + phased build checklist (updated to match this repo). |
| `ARCHITECTURE.md` | Decision log and extension seams. |

---

## Project memory (`.deepagent/`)

Created under a chosen **repository root** (the “target project”):

| Path | Contents |
|------|----------|
| `config.yaml` | Repo metadata, model hints, gate / MCP settings. |
| `project_memory.json` | Curated **project_fact** entries after promotion. |
| `subagent_lore/*.json` | Per-role durable lore after promotion. |
| `episodic/log.jsonl` | Append-only audit stream (`transition`, `dispatch`, `gate`, `promotion`, HITL, …) with **`session_id`**. |
| `artifacts/<ticket-id>/` | Persisted `ArtifactReturn` payloads per phase (`requirement_analysis.json`, `implementation_summary.json`, `review.json`). |
| `state/<ticket-id>.json` | Resume point for that ticket’s FSM. |
| `trajectories/<session-id>/<task-id>.jsonl` | Cold LLM transcripts when recording is enabled. |

`.deepagent/` is listed in `.gitignore`—it belongs to each target workspace, not necessarily to this system repo’s own git history.

---

## Testing

```powershell
python -m pytest              # default: fast, mocked LLM, 135 tests
python -m pytest -v           # verbose per test
python -m pytest tests/phase4 # focus one phase
python -m pytest --run-live -m live   # optional live OpenAI (needs key + flag)
```

**Markers:** `@pytest.mark.live` tests are skipped unless `--run-live` **and** `OPENAI_API_KEY` are present (`tests/conftest.py`).

---

## Documentation

| Document | Audience |
|----------|----------|
| **README.md** (this file) | Operators and new contributors—setup, usage, navigation. |
| **sdlc-deep-agent-spec.md** | Full system design, contracts §6–§7, memory §5, phased build §11—**aligned with the reference implementation.** |
| **ARCHITECTURE.md** | Technical lead / reviewer—tradeoffs, swaps, intentional non-goals. |
| **`skills/README.md`** | How to add skills and how `SkillLoader` composes prompts. |

---

## Extending the system

Straightforward swaps (architecture unchanged):

- **Real Jira / MCP** — replace `FixtureJiraMCP` with a client that satisfies the same call surface used by `BacklogAnalyzer`.
- **Docker sandbox** — implement `Sandbox` like `LocalSubprocessSandbox`; inject into `Developer`.
- **LangGraph / other supervisor** — wrap `Orchestrator.advance` as a node subgraph; persistence already exists via `MemoryStores`.
- **CLI** — not shipped; compose `initialize_deepagent` + `Orchestrator` + your registry under a `[project.scripts]` entry in `pyproject.toml` when needed.

Contributions should keep **single-owner FSM + curation**, **least-privilege assignments**, and **tests** passing for phases you touch.

---

## Origin

Originally shaped as an Accenture tech-lead style assessment (**`sdlc-deep-agent-spec.md`** §12 describes the historical *minimum* scope). **This repository implements the full phased design** (0–5), including the third subagent, skills, and trajectory storage, for reproducible study and production-oriented iteration.
