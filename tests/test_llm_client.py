"""Tests for the LLM client's handling of reasoning-model responses."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm.client import LLMClient, LLMError  # noqa: E402


def _client():
    return LLMClient(
        base_url="https://openrouter.ai/api/v1",
        model="inclusionai/ling-3.0-flash",
        api_key="test",
        max_retries=1,
    )


def _response(choice_message, finish_reason):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": choice_message, "finish_reason": finish_reason}]
    }
    return resp


def test_returns_content_when_present():
    """The answer lives in `content`, even when reasoning is also present."""
    msg = {"content": "[[YES]]", "reasoning": "let me think..."}
    with patch("src.llm.client.requests.post", return_value=_response(msg, "stop")):
        assert _client().chat([{"role": "user", "content": "hi"}]) == "[[YES]]"


def test_truncated_completion_raises_instead_of_returning_reasoning():
    """A length-truncated reply has no answer; do not return partial reasoning."""
    msg = {"content": None, "reasoning": "Let me analyze this form against the rul"}
    with patch("src.llm.client.requests.post", return_value=_response(msg, "length")):
        with pytest.raises(LLMError, match="truncated"):
            _client().chat([{"role": "user", "content": "hi"}])


def test_response_format_retried_without_when_provider_rejects():
    """Providers without structured output return 400; retry without it."""
    rejected = MagicMock()
    rejected.status_code = 400
    rejected.raise_for_status = MagicMock()

    ok_msg = {"content": '{"qualifies": true, "reason": "ok"}'}
    accepted = _response(ok_msg, "stop")

    sent_payloads = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent_payloads.append(dict(json or {}))
        return rejected if len(sent_payloads) == 1 else accepted

    with patch("src.llm.client.requests.post", side_effect=fake_post):
        result = _client().chat(
            [{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )

    assert "qualifies" in result
    assert len(sent_payloads) == 2
    assert "response_format" in sent_payloads[0]
    assert "response_format" not in sent_payloads[1]


def test_falls_back_to_reasoning_when_content_missing_but_complete():
    """If a model only populates `reasoning` and finishes cleanly, use it."""
    msg = {"content": None, "reasoning": "[[NO]] not relevant"}
    with patch("src.llm.client.requests.post", return_value=_response(msg, "stop")):
        assert "[[NO]]" in _client().chat([{"role": "user", "content": "hi"}])
