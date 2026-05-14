"""Memory layer: `.deepagent/` paths + (Phase 1) read/write stores."""

from sdlc_agent.memory.initializer import initialize_deepagent
from sdlc_agent.memory.paths import DeepAgentPaths, SUBAGENT_LORE_FILES
from sdlc_agent.memory.trajectories import TrajectoryRecorder

__all__ = [
    "DeepAgentPaths",
    "SUBAGENT_LORE_FILES",
    "TrajectoryRecorder",
    "initialize_deepagent",
]
