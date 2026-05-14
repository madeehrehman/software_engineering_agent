# Skill: tdd-discipline

**Audience:** Developer subagent.
**Purpose:** Enforce test-first by construction. This is the load-bearing
property that justifies merging code + test generation into one subagent
(spec §12): the same loop that writes the code writes its tests, so tests
shape the code rather than being bolted on after.

## The loop (one acceptance criterion at a time)

For each acceptance criterion in the requirement analysis:

1. **RED.** `write_test` — a test that pins the desired behavior of the
   *smallest* unit that would satisfy the criterion. The test must reference
   functions or modules that may not exist yet. Then `run_tests` and verify
   the failure mode matches your intent (NameError / ImportError / wrong
   value). If the test passes immediately, the test is wrong — it doesn't
   exercise the criterion. Fix the test.
2. **GREEN.** `write_code` — the *minimum* implementation that turns this
   specific test green. No speculative branches. No "while I'm here"
   refactors. `run_tests` and confirm green.
3. **REFACTOR.** Only when green. Tighten naming, deduplicate, simplify.
   `run_tests` again to confirm still green.
4. **NEXT.** Move to the next acceptance criterion. Do not interleave.

Use `complete` **only** when:
- Every acceptance criterion has at least one corresponding test, and
- The most recent `run_tests` was green (exit code 0).

## Sandbox rules

- Paths in `file_path` are relative to the sandbox root. The sandbox is
  flat — place tests at the top level (e.g. `test_<feature>.py`) alongside
  implementation modules. Do not create a `tests/` subdirectory; the default
  test runner discovers `test_*.py` at the sandbox root.
- Always write the **full** file content; partial diffs are not supported.
- No network. No shell access beyond the test runner.

## Anti-patterns

- **Writing all tests first, then all code.** That is *test-first batch
  writing*, not TDD. The RED→GREEN cycle is per-test.
- **Padding the artifact.** Don't claim files you didn't write. Self-checks
  verify every file in `impl_files` / `test_files` exists on disk.
- **Marking `final_tests_green` optimistically.** It is set strictly from
  the latest `run_tests` exit code. If the last run was non-zero, the
  answer is `false` — and the gate will route to retry.
- **Skipping `run_tests`.** Self-checks require the last test result to be
  green; if you never ran the suite, the check fails.

## Memory proposals

- Propose **`project_fact`** when the loop reveals a repo-wide truth (e.g.
  "this project uses `unittest`, not `pytest`," or "imports use absolute
  paths from the package root").
- Propose **`developer` `subagent_lore`** for patterns useful next time
  (e.g. "the `auth` module's tests assume a clean DB fixture").
- Use `confidence: high` only when evidence comes from a test runner result
  or a file you wrote in this loop.
