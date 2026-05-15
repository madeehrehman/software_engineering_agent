# Skills library (spec §10)

Skills are **shared, versioned, project-agnostic units of know-how** loaded on
demand by subagents. They live in *this* repo (the system repo), not in any
target project's `.deepagent/`. Project-specific knowledge stays under
`.deepagent/`; skills are repo-portable.

## Files

| File                                  | Loaded by         | Purpose                                                |
|---------------------------------------|-------------------|--------------------------------------------------------|
| `requirement-ambiguity-checklist.md`  | Backlog Analyzer  | What "ambiguous" means; how to phrase missing-info.    |
| `tdd-discipline.md`                   | Developer         | The red/green/refactor loop and self-verification.     |
| `pr-review-rubric.md`                 | PR Reviewer       | Severity/category taxonomy and verdict criteria.       |
| `master-agent.md`                     | Master agent      | Supervisor planning, gating, and dispatch doctrine.  |

## How loading works

Each subagent class has a class-level `DEFAULT_SKILLS` tuple. When the subagent
runs, the orchestrator-supplied `SkillLoader` reads the matching `.md` files
and the subagent prepends them to its system prompt under a clearly marked
section header (`--- LOADED SKILLS ---`).

Skills are loaded lazily (only when a subagent runs) and cached in-process so
the same file is not re-read across dispatches.

## Adding a new skill

1. Add `skills/<kebab-case-name>.md`. Keep it project-agnostic — no repo paths,
   no team names, no language-version specifics. If you find yourself writing
   "in this repo we…", it belongs in `.deepagent/project_memory.json`, not here.
2. Add the name to a subagent's `DEFAULT_SKILLS` (or, when per-task resolution
   is wired through `TaskAssignment.skills`, name it in the assignment).
3. Add a row to the table above.
