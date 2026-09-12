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


def test_there_is_no_way_to_push_to_a_group(monkeypatch: pytest.MonkeyPatch) -> None:
    # The organizer asked for the group message to be dropped, so the
    # client has no function that could send one — see line_client's own
    # note where push_to_group used to be.
    assert not hasattr(line_client, "push_to_group")
