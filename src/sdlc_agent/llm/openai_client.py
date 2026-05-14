"""Thin OpenAI ChatGPT wrapper used by orchestrator + subagents.

Phase 0 only requires construction + a callable `complete()` surface; real prompt
engineering for each subagent lands in Phase 2.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


class OpenAIConfigError(RuntimeError):
    """Raised when the OpenAI client can't find its API key."""


@dataclass
class ChatMessage:
    role: str
    content: str

    def to_openai(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class OpenAIClient:
    """Wrapper around the official ``openai`` SDK.

    Resolution order for the API key: explicit ``api_key`` arg → ``OPENAI_API_KEY``
    env var → :class:`OpenAIConfigError`.
    """

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL") or self.DEFAULT_MODEL
        self.temperature = temperature

        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None and not resolved_key:
            raise OpenAIConfigError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in your key, "
                "or pass api_key= explicitly."
            )

        self._client = client or OpenAI(api_key=resolved_key)

    def complete(
        self,
        messages: list[ChatMessage | dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Run a chat completion and return the assistant's text content.

        ``response_format`` accepts the OpenAI structured-output dict (e.g.
        ``{"type": "json_schema", "json_schema": {...}}``); subagents in Phase 2
        will use it to enforce artifact schemas.
        """
        payload_messages = [
            m.to_openai() if isinstance(m, ChatMessage) else m for m in messages
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
