"""Thin wrapper around the LINE Messaging API push endpoint."""

import os

import httpx

_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_PROFILE_URL = "https://api.line.me/v2/bot/profile/{user_id}"


def is_reachable(user_id: str) -> bool | None:
    """Whether a push to this person would arrive: LINE delivers only to
    somebody who has added the Official Account and not blocked it.

    Asks the profile endpoint, which sends nothing: 200 for a friend,
    404 for anyone else (an answer, not an error). Returns None when
    it cannot say (no token, LINE unreachable), which callers must
    treat as unknown. Five-second timeout, because a page waits on it.
    """
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        return None
    try:
        response = httpx.get(
            _PROFILE_URL.format(user_id=user_id),
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    return None


def _push(to: str, text: str) -> None:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    response = httpx.post(
        _PUSH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"to": to, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )
    response.raise_for_status()


def push_to_user(user_id: str, text: str) -> None:
    """Sends a private text message to one person."""
    _push(user_id, text)


# Deliberately no push_to_group: nothing this app sends goes to a group
# chat.
