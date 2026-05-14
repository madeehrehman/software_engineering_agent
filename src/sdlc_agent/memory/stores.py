"""Read/write layer for the three memory stores (spec §5.2).

* **Working state** — ``state/<ticket-id>.json`` (loaded on resume).
* **Project memory** — ``project_memory.json`` + ``subagent_lore/`` (loaded every session).
* **Episodic log** — ``episodic/log.jsonl`` (queried, not loaded into context).

Plus per-ticket artifact persistence (``artifacts/<ticket-id>/<phase>.json``).

Only the orchestrator writes via this layer. Subagents never reach the disk;
they propose memory and the orchestrator's curation gate decides what to promote.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sdlc_agent.contracts import ArtifactReturn, SubagentName
from sdlc_agent.memory.paths import DeepAgentPaths
from sdlc_agent.orchestrator.state_machine import SDLCPhase, TicketState


_PHASE_ARTIFACT_FILENAMES: dict[SDLCPhase, str] = {
    SDLCPhase.REQUIREMENTS_ANALYSIS: "requirement_analysis.json",
    SDLCPhase.DEVELOPMENT: "implementation_summary.json",
    SDLCPhase.PR_REVIEW: "review.json",
}


def _subagent_lore_filename(subagent: SubagentName) -> str:
    return f"{subagent.value}.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStores:
    """Single owner of memory I/O. Held by the orchestrator only."""

    def __init__(self, paths: DeepAgentPaths) -> None:
        self.paths = paths

    # ------------------------------------------------------------------ state
    def load_ticket_state(self, ticket_id: str) -> TicketState | None:
        path = self.paths.state_file(ticket_id)
        if not path.exists():
            return None
        return TicketState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_ticket_state(self, state: TicketState) -> None:
        path = self.paths.state_file(state.ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    # -------------------------------------------------------- project memory
    def read_project_facts(self) -> list[dict[str, Any]]:
        return self._read_json(self.paths.project_memory_json).get("facts", [])

    def append_project_fact(self, fact: dict[str, Any]) -> None:
        data = self._read_json(self.paths.project_memory_json)
        facts = data.setdefault("facts", [])
        facts.append(fact)
        self._write_json(self.paths.project_memory_json, data)

    def overwrite_project_facts(self, facts: list[dict[str, Any]]) -> None:
        """Replace the project_memory facts list wholesale (curation gate use)."""
        self._write_json(self.paths.project_memory_json, {"facts": facts})

    # ---------------------------------------------------------- subagent lore
    def read_subagent_lore(self, subagent: SubagentName) -> list[dict[str, Any]]:
        path = self.paths.subagent_lore_file(_subagent_lore_filename(subagent))
        return self._read_json(path).get("lore", [])

    def append_subagent_lore(self, subagent: SubagentName, entry: dict[str, Any]) -> None:
        path = self.paths.subagent_lore_file(_subagent_lore_filename(subagent))
        data = self._read_json(path)
        lore = data.setdefault("lore", [])
        lore.append(entry)
        self._write_json(path, data)

    def overwrite_subagent_lore(
        self, subagent: SubagentName, lore: list[dict[str, Any]]
    ) -> None:
        """Replace one subagent's lore list wholesale (curation gate use)."""
        path = self.paths.subagent_lore_file(_subagent_lore_filename(subagent))
        self._write_json(path, {"lore": lore})

    # ----------------------------------------------------------- episodic log
    def append_episode(self, event: dict[str, Any]) -> None:
        path = self.paths.episodic_log
        path.parent.mkdir(parents=True, exist_ok=True)
        enriched = {"timestamp": _utcnow_iso(), **event}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched) + "\n")

    def read_episodes(self) -> Iterator[dict[str, Any]]:
        path = self.paths.episodic_log
        if not path.exists():
            return iter(())
        return self._iter_jsonl(path)

    # ------------------------------------------------------------- artifacts
    def save_artifact(
        self,
        ticket_id: str,
        phase: SDLCPhase,
        artifact: ArtifactReturn,
    ) -> Path:
        ticket_dir = self.paths.ticket_artifacts_dir(ticket_id)
        ticket_dir.mkdir(parents=True, exist_ok=True)
        filename = _PHASE_ARTIFACT_FILENAMES.get(phase, f"{phase.value.lower()}.json")
        path = ticket_dir / filename
        path.write_text(
            artifact.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def load_artifact(self, ticket_id: str, phase: SDLCPhase) -> ArtifactReturn | None:
        ticket_dir = self.paths.ticket_artifacts_dir(ticket_id)
        filename = _PHASE_ARTIFACT_FILENAMES.get(phase, f"{phase.value.lower()}.json")
        path = ticket_dir / filename
        if not path.exists():
            return None
        return ArtifactReturn.model_validate_json(path.read_text(encoding="utf-8"))

    # ----------------------------------------------------------------- utils
    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8") or "{}")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
