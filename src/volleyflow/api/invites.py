"""Opaque tokens for a club's invite link.

A club's join link used to carry its raw numeric id (`?club=12`), and the
endpoint behind it — `POST /clubs/{id}/join` — accepted a join from any
identified caller who supplied a valid id, invited or not. A stranger
trying small integers in order could read a club's name and self-enrol.

The token here closes both halves of that: the id in a shared link is
unguessable, so finding a club requires actually having its link, and
joining requires presenting that same token — an id alone is refused.

No new database column — the token is a deterministic HMAC of the club
id, verified by recomputing it. That also means it can't be revoked or
rotated per club; only by changing the shared secret, which invalidates
every club's link at once. Acceptable for a single-organizer club with no
history of abuse; revisit if that changes.
"""

import hashlib
import hmac
import os

from fastapi import HTTPException, status

# Local development only. It is in a public repository, so a token signed
# with it proves nothing to anybody who has read this file.
_DEV_FALLBACK = "volleyflow-dev-invite-secret"


def _secret() -> bytes:
    """The key every invite token is signed with.

    Falls back to a fixed string **only** under local sign-in
    (VOLLEYFLOW_DEV_LOGIN=1, which production never sets), and otherwise
    refuses with a 503 rather than signing anything.

    It used to fall back everywhere, on the assumption written right here
    that "production sets INVITE_TOKEN_SECRET for real". Nothing ever
    checked that, and on 2026-09-15 a token signed with this file's own
    fallback was sent to the production API and **accepted** — so the
    whole point of the token, that a club can't be found by anyone who
    doesn't have its link, had quietly not been true there. Failing
    closed means a missing secret is a clearly broken invite screen that
    somebody notices, instead of a working one that protects nothing.
    """
    configured = os.environ.get("INVITE_TOKEN_SECRET")
    if configured:
        return configured.encode()
    if os.environ.get("VOLLEYFLOW_DEV_LOGIN") == "1":
        return _DEV_FALLBACK.encode()
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Invite links aren't configured on this server",
    )


def invite_token(club_id: int) -> str:
    """A per-club token, stable for as long as the secret doesn't change."""
    mac = hmac.new(_secret(), str(club_id).encode(), hashlib.sha256).hexdigest()[:20]
    return f"{club_id}.{mac}"


def club_id_from_invite_token(token: str) -> int | None:
    """The club id a token names, or None if it's malformed or forged.

    `hmac.compare_digest` rather than `==`: a plain string comparison
    exits as soon as it finds a mismatched character, which leaks how
    many leading characters an attacker got right through how long the
    response takes. Constant-time comparison is the standard defence.
    """
    try:
        raw_id, mac = token.split(".", 1)
        club_id = int(raw_id)
    except ValueError:
        return None
    expected = invite_token(club_id).split(".", 1)[1]
    if not hmac.compare_digest(expected, mac):
        return None
    return club_id
