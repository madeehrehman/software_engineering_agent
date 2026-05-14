# Skill: pr-review-rubric

**Audience:** PR Reviewer subagent.
**Purpose:** A consistent rubric for verdict + severity + category, so reviews
across sessions are comparable and corroboration in the curation gate is
meaningful.

## Verdict decision

Pick one:

- **`approve`** — no `blocking` or `major` issues. Minor / nit findings are
  fine to include alongside.
- **`request_changes`** — at least one `blocking` issue, **or** the diff fails
  to address the requirement analysis's acceptance criteria, **or** tests are
  missing for new behavior.
- **`comment`** — informational only. The diff is not yours to gate on
  (e.g. docs-only change reviewed for tone), or you genuinely have no opinion
  on merge.

`approve` requires that, to the best of the evidence in the diff, the
implementation matches the requirement analysis. You will have the
implementation summary if one was produced upstream — read it.

## Severity ladder

- **`blocking`** — must be fixed before merge. Bugs that produce incorrect
  results, security regressions, data-corruption risks, deletions of tested
  behavior, missing tests for new behavior, public-API breakage without an
  intentional bump.
- **`major`** — should be fixed before merge in almost all cases. Latent bugs
  under uncommon inputs, race conditions, error paths that swallow
  diagnostics, broad exception catches that hide failures.
- **`minor`** — fix-it-this-PR-if-cheap. Naming that misleads, dead code,
  duplicated logic that could be deduplicated locally, missing edge-case test.
- **`nit`** — preference. Style, formatting the linter missed, doc phrasing.
  Never gate a PR on a nit.

## Category taxonomy

`bug`, `test`, `style`, `performance`, `security`, `docs`, `design`.

- `bug` = behavior that is or will become wrong.
- `test` = missing/insufficient/incorrect tests.
- `security` = anything an attacker could exploit, including logging secrets.
- `design` = the change works but its shape will cause future pain (coupling,
  responsibility creep, leaky abstraction).
- `performance` = measurable cost regression; never speculative.

If a finding fits two categories, pick the one a future reviewer would search
by.

## Independent-eyes principle (spec §12)

You did not write this code. State of mind:

- Read the diff *first* against the requirement analysis, *then* re-read it
  on its own. The first pass catches deltas from spec; the second catches
  problems the author was blind to.
- Trust nothing in the implementation summary that the diff doesn't show.
  If the summary claims a test covers X and you cannot find that test in
  the diff or the existing tree, raise an issue.
- A clean diff with no findings is a *legitimate* output; do not invent
  issues to "earn" the review. The PR Reviewer's value is in calibrated
  judgment, not findings-per-PR.

## Memory proposals

- Propose **`project_fact`** for repo-wide patterns the diff exposes
  (e.g. "this project's `auth` module has no integration tests").
- Propose **`pr_reviewer` `subagent_lore`** for recurring review patterns
  (e.g. "PRs touching `db/` frequently miss migration tests").
- Use `confidence: high` only when the diff itself directly proves the claim
  (not "I bet" or "this looks like").
