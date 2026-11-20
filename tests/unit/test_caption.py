"""Unit tests for app.ingestion.caption with a stub client (no network, no cost)."""

from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from anthropic import APIConnectionError, AuthenticationError

from app.exceptions import UpstreamUnavailableError
from app.ingestion.caption import ClaudeCaptioner, FakeCaptioner


class StubMessages:
    """Records the request and returns or raises a prepared result."""

    def __init__(self, result: Any) -> None:
        """Store what ``create`` should return (or raise, if an exception)."""
        self.result = result
        self.request: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        """Pretend to call the Messages API."""
        self.request = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_captioner(result: Any) -> tuple[ClaudeCaptioner, StubMessages]:
    """Build a captioner around a stub client."""
    messages = StubMessages(result)
    client = SimpleNamespace(messages=messages)
    return ClaudeCaptioner(client=client), messages  # type: ignore[arg-type]


def response(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    """Build a minimal Messages API response."""
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


async def test_caption_sends_image_and_returns_text() -> None:
    captioner, messages = make_captioner(response("QUICK HELP\nScreen description: tiles."))
    text = await captioner.caption(b"png-bytes", 3, "iris.pdf")
    assert text == "QUICK HELP\nScreen description: tiles."
    image, prompt = messages.request["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert "page 3" in prompt["text"] and "iris.pdf" in prompt["text"]
    # Current models reject sampling parameters, so none may be sent.
    assert "temperature" not in messages.request


async def test_refusal_returns_empty_caption() -> None:
    captioner, _ = make_captioner(response("", stop_reason="refusal"))
    assert await captioner.caption(b"png", 1, "x.pdf") == ""


async def test_connection_failure_becomes_upstream_unavailable() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    captioner, _ = make_captioner(APIConnectionError(request=request))
    with pytest.raises(UpstreamUnavailableError):
        await captioner.caption(b"png", 1, "x.pdf")


async def test_rejected_key_is_logged_as_setup_problem(caplog: pytest.LogCaptureFixture) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    captioner, _ = make_captioner(error)
    with pytest.raises(UpstreamUnavailableError):
        await captioner.caption(b"png", 1, "x.pdf")
    assert "check ANTHROPIC_API_KEY" in caplog.text


async def test_fake_captioner_records_calls() -> None:
    fake = FakeCaptioner()
    text = await fake.caption(b"png", 2, "iris.pdf")
    assert fake.calls == [(2, "iris.pdf")] and "page 2" in text
