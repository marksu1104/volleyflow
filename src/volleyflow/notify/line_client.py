"""Thin wrapper around the LINE Messaging API push endpoint."""

import os

import httpx

_PUSH_URL = "https://api.line.me/v2/bot/message/push"


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
    """A private message to one person — used for the organizer-only
    short-roster alert, never the group.
    """
    _push(user_id, text)


def push_to_group(group_id: str, text: str) -> None:
    """A message to the group chat — used for the roster reminder."""
    _push(group_id, text)
