"""Config schema + loader for `.deepagent/config.yaml` (spec §5.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    """Identifies the target repository the agent attaches to."""

    name: str
    repo_root: Path = Field(default=Path("."))


class ModelConfig(BaseModel):
    """LLM model selection. Only OpenAI/ChatGPT is wired in Phase 0."""

    provider: Literal["openai"] = "openai"
    name: str = "gpt-4o-mini"
    temperature: float = 0.0


class MCPEndpointConfig(BaseModel):
    """A single MCP server endpoint. `stub` means use the in-process stub."""

    type: Literal["stub", "http"] = "stub"
    url: str | None = None


class MCPConfig(BaseModel):
    git: MCPEndpointConfig = Field(default_factory=MCPEndpointConfig)
    jira: MCPEndpointConfig = Field(default_factory=MCPEndpointConfig)


class GateConfig(BaseModel):
    """Per spec §4 + §11 Phase 4: human-in-the-loop is opt-in per gate."""

    hitl_requirements_gate: bool = False
    hitl_review_gate: bool = False


class DeepAgentConfig(BaseModel):
    """Top-level config persisted at `.deepagent/config.yaml`."""

    project: ProjectConfig
    model: ModelConfig = Field(default_factory=ModelConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    gates: GateConfig = Field(default_factory=GateConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "DeepAgentConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        data = self.model_dump(mode="json")
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    @classmethod
    def default_for_repo(cls, repo_root: Path, project_name: str | None = None) -> "DeepAgentConfig":
        return cls(
            project=ProjectConfig(
                name=project_name or repo_root.resolve().name,
                repo_root=repo_root.resolve(),
            )
        )
