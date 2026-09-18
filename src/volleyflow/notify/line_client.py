"""Thin wrapper around the LINE Messaging API push endpoint."""

import os

import httpx

_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_PROFILE_URL = "https://api.line.me/v2/bot/profile/{user_id}"


def is_reachable(user_id: str) -> bool | None:
    """Whether a push to this person would actually arrive.

    LINE delivers only to somebody who has added the Official Account
    as a friend and hasn't blocked it, and refuses everyone else — which
    is why send_game_reminder wraps each organizer in its own try. That
    refusal happens at the moment it matters least: the night a game is
    short-handed, in a log line nobody reads. This asks the same
    question in advance, so an organizer can be told while there is
    still time to fix it.

    The profile endpoint answers without sending anything: 200 for a
    friend, 404 for everyone else. That 404 is the answer and not a
    failure, so this is the one call in this module that deliberately
    does not raise_for_status.

    Returns None for "cannot say" — no token, LINE unreachable, or any
    other status. A caller must treat that as neither yes nor no:
    telling somebody to add a friend they added months ago is worse
    than staying quiet. Five seconds, not the ten the pushes get,
    because a page waits on this one and a nightly job does not.
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
    """A private message to one person — used for the organizer-only
    short-roster alert, never the group.
    """
    _push(user_id, text)


def push_image_to_user(user_id: str, image_url: str) -> None:
    """An image message. LINE renders it by fetching the URL from its own
    servers, so the address has to be publicly reachable over HTTPS —
    which is why a screenshot gets stored and served rather than being
    handed to the chat directly.
    """
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    response = httpx.post(
        _PUSH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "to": user_id,
            "messages": [
                {
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": image_url,
                }
            ],
        },
        timeout=10,
    )
    response.raise_for_status()


# There is deliberately no push_to_group. One existed, for a nightly
# roster message to the club's LINE group, and the organizer asked for
# it to be dropped — the group already talks about the game in the
# group, so a bot repeating the roster into it was noise. Deleted rather
# than left behind an unused import, so nothing can quietly start using
# it again. See notify/reminders.send_game_reminder.
