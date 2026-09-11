"""Opaque tokens for a club's invite link.

A club's join link used to carry its raw numeric id (`?club=12`), and the
endpoint behind it — `POST /clubs/{id}/join` — accepts a join from any
identified caller who supplies a valid id, invited or not. A stranger
trying small integers in order could read a club's name and self-enrol.

The token here fixes the reconnaissance half of that: the id in a shared
link is now unguessable, so finding a club still requires actually having
its link. See `docs/backlog.md` for the join-endpoint half this
deliberately leaves open, and why.

No new database column — the token is a deterministic HMAC of the club
id, verified by recomputing it. That also means it can't be revoked or
rotated per club; only by changing the shared secret, which invalidates
every club's link at once. Acceptable for a single-organizer club with no
history of abuse; revisit if that changes.
"""

import hashlib
import hmac
import os


def _secret() -> bytes:
    # Falls back to a fixed string rather than failing outright, so a
    # freshly cloned dev environment isn't blocked by a secret nobody
    # told it about — the token is still unguessable in practice, just
    # not against someone who has read this file. Production sets
    # INVITE_TOKEN_SECRET for real.
    fallback = "volleyflow-dev-invite-secret"
    return os.environ.get("INVITE_TOKEN_SECRET", fallback).encode()


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
