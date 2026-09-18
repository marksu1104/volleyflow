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


class _FakeStatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_is_reachable_is_true_for_somebody_who_added_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeStatusResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeStatusResponse(200)

    monkeypatch.setattr(httpx, "get", fake_get)

    assert line_client.is_reachable("Uabc") is True
    assert captured["url"] == "https://api.line.me/v2/bot/profile/Uabc"
    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_is_reachable_is_false_when_line_has_no_profile_for_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 404 is the answer, not a failure: LINE only serves a profile
    # to a bot the person has added, so "no profile" means "not a
    # friend" and a push to them would be refused.
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeStatusResponse(404))

    assert line_client.is_reachable("Uabc") is False


def test_is_reachable_says_it_cannot_tell_when_line_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # None, never False. A timeout is not evidence that somebody hasn't
    # added the account, and returning False here would put a "you
    # haven't added us" warning in front of every organizer the day
    # LINE's profile endpoint is slow.
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")

    def fake_get(url: str, **kwargs: Any) -> _FakeStatusResponse:
        raise httpx.ConnectTimeout("no route to LINE")

    monkeypatch.setattr(httpx, "get", fake_get)

    assert line_client.is_reachable("Uabc") is None


def test_is_reachable_says_it_cannot_tell_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing token is a deployment that cannot send pushes at all.
    # The pushes themselves raise KeyError on it, which is right for a
    # nightly job; this one is asked while somebody waits for a page, so
    # it answers "cannot say" instead of turning a management screen
    # into an error.
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)

    def explode(url: str, **kwargs: Any) -> _FakeStatusResponse:
        raise AssertionError("must not call LINE without a token")

    monkeypatch.setattr(httpx, "get", explode)

    assert line_client.is_reachable("Uabc") is None


def test_is_reachable_says_it_cannot_tell_when_line_answers_some_other_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 500 from LINE is not "not a friend". Only a plain 404 means
    # that; anything else leaves the question open, and the screen stays
    # quiet rather than accusing somebody of not having added an account
    # they added long ago. Without this the three states collapse to two
    # the first time LINE has a bad afternoon.
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeStatusResponse(500))

    assert line_client.is_reachable("Uabc") is None


def test_there_is_no_way_to_push_to_a_group(monkeypatch: pytest.MonkeyPatch) -> None:
    # The organizer asked for the group message to be dropped, so the
    # client has no function that could send one — see line_client's own
    # note where push_to_group used to be.
    assert not hasattr(line_client, "push_to_group")
