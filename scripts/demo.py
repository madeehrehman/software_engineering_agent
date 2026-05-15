"""End-to-end demo for the SDLC Deep Agent (Phase 5 deliverable).

Drives a non-trivial fixture ticket (`DEMO-42` — a TokenBucket rate limiter)
through INTAKE → DONE with all three real subagents wired up: skills loaded
from ``skills/``, every LLM call archived to ``.deepagent/trajectories/``,
and curated facts compounding into ``project_memory.json``.

The demo uses a *canned* LLM so it runs deterministically without an
``OPENAI_API_KEY``. To run against the real OpenAI API, swap the canned
client at the marked seam.

Run:

    python scripts/demo.py

Optional flags:

    --target-repo PATH   target repo into which ``.deepagent/`` is initialized
                         (default: a fresh temp dir)
    --keep                do not delete the temp target repo / sandbox at exit
    --quiet               print only the final summary
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sdlc_agent.contracts import SubagentName  # noqa: E402
from sdlc_agent.llm.openai_client import OpenAIClient  # noqa: E402
from sdlc_agent.mcp.git import LocalGitClient  # noqa: E402
from sdlc_agent.mcp.jira import FixtureJiraMCP  # noqa: E402
from sdlc_agent.memory import TrajectoryRecorder, initialize_deepagent  # noqa: E402
from sdlc_agent.memory.stores import MemoryStores  # noqa: E402
from sdlc_agent.orchestrator import SDLCPhase  # noqa: E402
from sdlc_agent.orchestrator.dispatcher import Orchestrator  # noqa: E402
from sdlc_agent.sandbox import LocalSubprocessSandbox  # noqa: E402
from sdlc_agent.skills import SkillLoader  # noqa: E402
from sdlc_agent.subagents import BacklogAnalyzer, Developer, PRReviewer  # noqa: E402


# --------------------------------------------------------------- canned LLM
class _FakeChatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeChatMessage(content)


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, queue: list[str]) -> None:
        self._queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeChatResponse:
        self.calls.append(kwargs)
        if not self._queue:
            raise RuntimeError("canned LLM queue exhausted")
        return _FakeChatResponse(self._queue.pop(0))


class _FakeChat:
    def __init__(self, queue: list[str]) -> None:
        self.completions = _FakeCompletions(queue)


class _FakeOpenAI:
    def __init__(self, queue: list[str]) -> None:
        self.chat = _FakeChat(queue)


def _make_canned_client(responses: list[dict[str, Any]]) -> OpenAIClient:
    queue = [json.dumps(r) for r in responses]
    fake = _FakeOpenAI(queue)
    return OpenAIClient(api_key="sk-demo", model="gpt-demo", client=fake)  # type: ignore[arg-type]


# --------------------------------------------------------- canned narrative
_TEST_FILE = '''\
import unittest
from token_bucket import TokenBucket


class TestTokenBucket(unittest.TestCase):
    def test_first_n_calls_allowed(self):
        bucket = TokenBucket(capacity=3, refill_per_second=1, now=lambda: 0.0)
        self.assertTrue(bucket.allow("ip-1"))
        self.assertTrue(bucket.allow("ip-1"))
        self.assertTrue(bucket.allow("ip-1"))
        self.assertFalse(bucket.allow("ip-1"))

    def test_refill_restores_tokens(self):
        t = [0.0]
        bucket = TokenBucket(capacity=2, refill_per_second=1, now=lambda: t[0])
        self.assertTrue(bucket.allow("k"))
        self.assertTrue(bucket.allow("k"))
        self.assertFalse(bucket.allow("k"))
        t[0] = 1.0
        self.assertTrue(bucket.allow("k"))

    def test_capacity_clamps_refill(self):
        t = [0.0]
        bucket = TokenBucket(capacity=2, refill_per_second=1, now=lambda: t[0])
        t[0] = 100.0
        self.assertTrue(bucket.allow("k"))
        self.assertTrue(bucket.allow("k"))
        self.assertFalse(bucket.allow("k"))

    def test_keys_are_isolated(self):
        bucket = TokenBucket(capacity=1, refill_per_second=1, now=lambda: 0.0)
        self.assertTrue(bucket.allow("a"))
        self.assertFalse(bucket.allow("a"))
        self.assertTrue(bucket.allow("b"))
'''

_IMPL_FILE = '''\
"""Pure-Python token-bucket rate limiter (DEMO-42)."""

from __future__ import annotations

from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        self.capacity = capacity
        self.refill_per_second = float(refill_per_second)
        self._now = now or (lambda: __import__("time").monotonic())
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = self._now()
        tokens, last = self._state.get(key, (float(self.capacity), now))
        elapsed = max(0.0, now - last)
        tokens = min(float(self.capacity), tokens + elapsed * self.refill_per_second)
        if tokens >= 1.0:
            tokens -= 1.0
            self._state[key] = (tokens, now)
            return True
        self._state[key] = (tokens, now)
        return False
'''


def _backlog_response() -> dict[str, Any]:
    return {
        "artifact": {
            "ticket_key": "DEMO-42",
            "summary": "Add a TokenBucket(capacity, refill_per_second).allow(key) rate-limiter helper.",
            "acceptance_criteria": [
                "TokenBucket(capacity=N, refill_per_second=R).allow(key) returns True for first N calls then False until refill",
                "Refill happens at refill_per_second tokens/sec, clamped to capacity",
                "Tests pass with an injected now() — no sleep",
            ],
            "ambiguities": [],
            "missing_info": [],
            "out_of_scope": [
                "thread safety (Lock) — confirmed deferred in ticket comments",
            ],
            "ready_for_development": True,
            "notes": "Comments confirm unittest style and out-of-scope items; ready.",
        },
        "proposed_memory": [
            {
                "scope": "project_fact",
                "claim": "this project's test runner is unittest (not pytest)",
                "evidence": "DEMO-42 comment from dan@example.com explicitly requests unittest style",
                "confidence": "high",
            }
        ],
    }


def _developer_steps() -> list[dict[str, Any]]:
    return [
        {
            "action": "write_test",
            "file_path": "test_token_bucket.py",
            "content": _TEST_FILE,
            "rationale": "RED: pin all 4 acceptance behaviors before writing impl.",
        },
        {
            "action": "run_tests",
            "file_path": "",
            "content": "",
            "rationale": "expect ImportError (token_bucket module missing) — RED.",
        },
        {
            "action": "write_code",
            "file_path": "token_bucket.py",
            "content": _IMPL_FILE,
            "rationale": "GREEN: minimal token-bucket implementing the 4 tests.",
        },
        {
            "action": "run_tests",
            "file_path": "",
            "content": "",
            "rationale": "expect GREEN now.",
        },
        {
            "action": "complete",
            "file_path": "",
            "content": "",
            "rationale": "all 4 ACs covered; final test run is green.",
        },
    ]


def _developer_summary() -> dict[str, Any]:
    return {
        "artifact": {
            "implementation_summary": (
                "TokenBucket(capacity, refill_per_second, now=) with per-key state; "
                "refill clamped to capacity. 4 unittest cases covering allow, refill, "
                "clamping, and key isolation. Test passes a fake now() so the run is "
                "deterministic (no sleep)."
            ),
            "impl_files": ["token_bucket.py"],
            "test_files": ["test_token_bucket.py"],
            "iterations_used": 5,
            "final_tests_green": True,
            "acceptance_criteria_addressed": [
                "TokenBucket(capacity=N, refill_per_second=R).allow(key) returns True for first N calls then False until refill",
                "Refill happens at refill_per_second tokens/sec, clamped to capacity",
                "Tests pass with an injected now() — no sleep",
            ],
        },
        "proposed_memory": [
            {
                "scope": "subagent_lore",
                "claim": "clock-injecting via `now=` keeps rate-limit tests deterministic",
                "evidence": "test_refill_restores_tokens advances t[0] manually; suite completes in <50ms",
                "confidence": "high",
            }
        ],
    }


def _master_agent_plan() -> dict[str, Any]:
    return {
        "goal": "Implement DEMO-42 TokenBucket rate limiter with deterministic unittest coverage",
        "current_focus": "Requirements analysis from Jira DEMO-42",
        "phase_checklist": [
            "Analyze DEMO-42 and confirm acceptance criteria",
            "TDD implementation in sandbox",
            "Independent PR review of git diff",
        ],
        "risks": ["thread safety explicitly out of scope for v1"],
        "notes": "Supervisor plan; gates owned by orchestrator master agent.",
    }


def _master_agent_gate_proceed() -> dict[str, Any]:
    return {
        "decision": "proceed",
        "rationale": "Phase gate criteria satisfied; advancing SDLC.",
        "retry_guidance": "",
    }


def _reviewer_response() -> dict[str, Any]:
    return {
        "artifact": {
            "verdict": "approve",
            "summary": (
                "TokenBucket is a clean, side-effect-free implementation with deterministic "
                "tests via an injected clock. AC coverage is complete; the deferred "
                "thread-safety scope is correctly out of v1."
            ),
            "issues": [],
            "strengths": [
                "tests injected `now()` so the suite is deterministic and instant",
                "capacity-clamping correctness exercised by `test_capacity_clamps_refill`",
                "key isolation explicitly tested",
            ],
        },
        "proposed_memory": [],
    }


# ------------------------------------------------------------ demo plumbing
@contextmanager
def _ephemeral_target_repo(keep: bool) -> Iterator[Path]:
    tmp = Path(tempfile.mkdtemp(prefix="sdlc-agent-demo-"))
    try:
        yield tmp
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def _init_sample_git_repo(parent: Path) -> dict[str, Any]:
    """Create a tiny 2-commit git repo; used by the PR Reviewer as 'the diff'."""
    repo = parent / "sample_git_repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Demo Bot",
        "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "Demo Bot",
        "GIT_COMMITTER_EMAIL": "demo@example.com",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**env, "PATH": __import__("os").environ.get("PATH", "")},
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "demo@example.com")
    git("config", "user.name", "Demo Bot")
    git("config", "commit.gpgsign", "false")

    (repo / "README.md").write_text("# Sample repo for SDLC agent demo\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-q", "-m", "base: initial commit")
    git("tag", "base")

    git("checkout", "-q", "-b", "feat/demo-42")
    (repo / "token_bucket.py").write_text(_IMPL_FILE, encoding="utf-8")
    (repo / "test_token_bucket.py").write_text(_TEST_FILE, encoding="utf-8")
    git("add", "token_bucket.py", "test_token_bucket.py")
    git("commit", "-q", "-m", "feat(DEMO-42): add TokenBucket helper")

    return {"repo": repo, "base_ref": "base", "head_ref": "HEAD"}


def _box(title: str) -> str:
    bar = "=" * max(len(title), 65)
    return f"{bar}\n{title}\n{bar}"


def _run_demo(target_repo: Path, *, quiet: bool) -> int:
    say = (lambda *a, **k: None) if quiet else print

    say(_box("SDLC Deep Agent -- End-to-end demo (DEMO-42)"))

    say(f"\n[1/4] Initializing .deepagent/ at {target_repo}")
    paths = initialize_deepagent(target_repo, project_name="demo-project")
    say(f"      created: {paths.root.name}/ + config.yaml + 3 stores + log.jsonl")

    sample_repo = _init_sample_git_repo(target_repo)
    say(f"      sample git repo:    {sample_repo['repo'].name}/ (base..HEAD = the 'PR')")

    sandbox_root = target_repo / "dev_sandbox"
    sandbox_root.mkdir()
    sandbox = LocalSubprocessSandbox(
        root=sandbox_root,
        default_test_command=[sys.executable, "-m", "unittest", "discover"],
    )

    say("\n[2/4] Wiring subagents (skills + trajectory recorder)")
    skills = SkillLoader()
    say(f"      skills loader:      {skills.skills_dir}")
    say(f"      available skills:   {', '.join(skills.available())}")

    canned_llm = _make_canned_client(
        [
            _master_agent_plan(),
            _backlog_response(),
            _master_agent_gate_proceed(),
            *_developer_steps(),
            _developer_summary(),
            _master_agent_gate_proceed(),
            _reviewer_response(),
            _master_agent_gate_proceed(),
        ]
    )

    # NOTE: real-LLM seam — swap the line above for:
    #   canned_llm = OpenAIClient()  # reads OPENAI_API_KEY / OPENAI_MODEL from env
    # and the orchestrator + subagents will hit the real API instead.

    fixture_dir = REPO_ROOT / "scripts" / "demo_fixtures" / "jira"
    orch_session = "demo-42"
    recorder = TrajectoryRecorder(paths, session_id=orch_session)

    registry = {
        SubagentName.BACKLOG_ANALYZER: BacklogAnalyzer(
            llm=canned_llm,
            jira=FixtureJiraMCP(fixture_dir=fixture_dir),
            skills=skills,
            recorder=recorder,
        ),
        SubagentName.DEVELOPER: Developer(
            llm=canned_llm,
            sandbox=sandbox,
            max_iterations=8,
            skills=skills,
            recorder=recorder,
        ),
        SubagentName.PR_REVIEWER: PRReviewer(
            llm=canned_llm,
            git=LocalGitClient(repo_root=sample_repo["repo"]),
            skills=skills,
            recorder=recorder,
        ),
    }
    say(f"      session_id:         {orch_session}")
    say("      subagents wired:    BacklogAnalyzer, Developer, PRReviewer")

    say("\n[3/4] Running orchestrator (master agent + subagents) -> run_to_completion()")
    orch = Orchestrator(
        paths=paths,
        registry=registry,
        session_id=orch_session,
        llm=canned_llm,
        recorder=recorder,
        skills=skills,
    )
    state = orch.intake(
        "DEMO-42",
        ticket_inputs={
            "jira_key": "DEMO-42",
            "base_ref": sample_repo["base_ref"],
            "head_ref": sample_repo["head_ref"],
        },
    )
    final = orch.run_to_completion("DEMO-42")

    history = [
        f"{r.from_phase} -> {r.to_phase}"
        + (f" [{r.decision.value}]" if r.decision is not None else "")
        for r in final.history
    ]
    say(f"      transitions: {len(history)}")
    if not quiet:
        for h in history:
            say(f"        - {h}")

    say("\n[4/4] Results")
    say(f"  Final phase:           {final.current_phase.value}")
    assert final.current_phase is SDLCPhase.DONE, "demo expected to terminate at DONE"
    if final.plan:
        say(f"  Orchestrator plan:     {final.plan.get('goal', '')[:70]}...")
    stores = MemoryStores(paths)
    facts = stores.read_project_facts()
    say(f"  Project facts:         {len(facts)} promoted")
    for f in facts:
        say(
            f"    - {f.get('claim')!r}  [{f.get('scope')}, {f.get('confidence')}; "
            f"corroborations={f.get('corroborations', 0)}]"
        )
    dev_lore = stores.read_subagent_lore(SubagentName.DEVELOPER)
    ba_lore = stores.read_subagent_lore(SubagentName.BACKLOG_ANALYZER)
    pr_lore = stores.read_subagent_lore(SubagentName.PR_REVIEWER)
    say(
        f"  Subagent lore:         backlog={len(ba_lore)}  developer={len(dev_lore)}  "
        f"pr_reviewer={len(pr_lore)}"
    )
    if dev_lore:
        for entry in dev_lore:
            say(f"    developer: {entry.get('claim')!r}")

    episodes = list(stores.read_episodes())
    by_kind: dict[str, int] = {}
    for e in episodes:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    say(f"  Episodic events:       {len(episodes)}  ({by_kind})")

    session_dir = paths.trajectories_dir / orch_session
    traj_files = sorted(session_dir.glob("*.jsonl")) if session_dir.is_dir() else []
    total_calls = 0
    for tf in traj_files:
        total_calls += sum(1 for _ in tf.open("r", encoding="utf-8") if _)
    say(
        f"  Trajectories:          {len(traj_files)} task files at "
        f".deepagent/trajectories/{orch_session}/  ({total_calls} LLM calls archived)"
    )
    for tf in traj_files:
        kinds: dict[str, int] = {}
        for line in tf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                k = json.loads(line)["kind"]
                kinds[k] = kinds.get(k, 0) + 1
        say(f"    - {tf.name}  {sum(kinds.values())} call(s)  {kinds}")

    impl = stores.load_artifact("DEMO-42", SDLCPhase.DEVELOPMENT)
    review = stores.load_artifact("DEMO-42", SDLCPhase.PR_REVIEW)
    if impl is not None:
        say(
            f"  Implementation:        files={impl.artifact['impl_files']} + "
            f"tests={impl.artifact['test_files']}, "
            f"final_tests_green={impl.artifact['final_tests_green']}"
        )
    if review is not None:
        say(
            f"  Review verdict:        {review.artifact['verdict']}  "
            f"({len(review.artifact['issues'])} issue(s), "
            f"{len(review.artifact['strengths'])} strength(s))"
        )

    say("")
    say(_box(f"DONE. .deepagent/ left at {paths.root}"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-repo", type=Path, default=None)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.target_repo is not None:
        args.target_repo.mkdir(parents=True, exist_ok=True)
        return _run_demo(args.target_repo, quiet=args.quiet)

    with _ephemeral_target_repo(args.keep) as repo:
        return _run_demo(repo, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
