"""Tests for the LINE Messaging API client wrapper.

httpx.post is monkeypatched so nothing here ever hits the real LINE API.
"""

from typing import Any

import httpx
import pytest

from volleyflow.notify import line_client


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


def test_push_to_user_sends_the_recipient_and_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    line_client.push_to_user("Uabc", "hello")

    assert captured["url"] == "https://api.line.me/v2/bot/message/push"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["to"] == "Uabc"
    assert captured["json"]["messages"] == [{"type": "text", "text": "hello"}]


def test_push_to_group_sends_the_group_id_as_the_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    line_client.push_to_group("Cgroup", "hi group")

    assert captured["json"]["to"] == "Cgroup"
