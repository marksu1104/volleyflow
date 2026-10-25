"""Unguessable tokens for a club's invite link.

Finding a club and joining it both require its token, so clubs cannot
be discovered or joined by trying ids. The token is an HMAC of the
club id, verified by recomputing it, so it needs no database column;
the cost is that it can only be revoked by changing the secret, which
changes every club's link.
"""

import hashlib
import hmac
import os

from fastapi import HTTPException, status

# Local development only. It is in a public repository, so a token signed
# with it proves nothing to anybody who has read this file.
_DEV_FALLBACK = "volleyflow-dev-invite-secret"


def _secret() -> bytes:
    """The key invite tokens are signed with: INVITE_TOKEN_SECRET.

    Falls back to a fixed string only under local sign-in
    (VOLLEYFLOW_DEV_LOGIN=1, never set in production); otherwise a
    missing secret is a 503, so a broken setup is visible instead of
    silently unprotected.
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
    """The club id a token names, or None if it is malformed or forged.
    Compared with hmac.compare_digest, which takes the same time
    however many characters match.
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
