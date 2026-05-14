"""Phase 0: OpenAI wrapper construction.

We do not hit the network here; we verify the key-resolution contract and that a
caller can inject a fake client for unit testing of subagents in Phase 2.
"""

from __future__ import annotations

from typing import Any

import pytest

from sdlc_agent.llm import OpenAIClient
from sdlc_agent.llm.openai_client import ChatMessage, OpenAIConfigError


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIConfigError):
        OpenAIClient()


def test_explicit_api_key_initializes() -> None:
    client = OpenAIClient(api_key="sk-test", model="gpt-4o-mini")
    assert client.model == "gpt-4o-mini"


def test_env_api_key_initializes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    client = OpenAIClient()
    assert client.model == "gpt-4o"


def test_complete_uses_injected_client() -> None:
    class _FakeMessage:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeChoice:
        def __init__(self, content: str) -> None:
            self.message = _FakeMessage(content)

    class _FakeResponse:
        def __init__(self, content: str) -> None:
            self.choices = [_FakeChoice(content)]

    class _FakeCompletions:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, Any] | None = None

        def create(self, **kwargs: Any) -> _FakeResponse:
            self.last_kwargs = kwargs
            return _FakeResponse("hello world")

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    fake = _FakeOpenAI()
    client = OpenAIClient(model="gpt-test", client=fake)  # type: ignore[arg-type]

    out = client.complete([ChatMessage(role="user", content="hi")])

    assert out == "hello world"
    assert fake.chat.completions.last_kwargs is not None
    assert fake.chat.completions.last_kwargs["model"] == "gpt-test"
    assert fake.chat.completions.last_kwargs["messages"] == [
        {"role": "user", "content": "hi"}
    ]
