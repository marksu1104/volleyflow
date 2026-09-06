"""Turning a LINE ID token into a verified identity.

Every LIFF page gets a fresh signed JWT from liff.getIDToken() — this
module is what stops the API from just trusting whatever line_user_id a
client claims in a request body, which is what every endpoint did before
this existed. Verification is delegated to LINE's own endpoint rather
than checked locally (JWKS + signature verification), trading one
network call per request for not needing a JWT/crypto dependency or a
public-key cache to keep correct — an easy trade at this app's traffic.
"""

import os

import httpx

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def verify_id_token(id_token: str) -> str:
    """Returns the verified line_user_id (the token's `sub` claim).

    Raises ValueError if LINE rejects the token — expired, wrong
    audience, tampered, or just malformed. Never raises for "not
    verifiable due to a network error"; that's left to bubble up as a
    5xx, since silently treating a verification outage as "invalid" would
    lock everyone out at once for an unrelated reason.
    """
    channel_id = os.environ["LINE_LIFF_CHANNEL_ID"]
    response = httpx.post(
        _VERIFY_URL,
        data={"id_token": id_token, "client_id": channel_id},
        timeout=10,
    )
    if response.status_code != 200:
        raise ValueError("LINE rejected this ID token")
    sub = response.json().get("sub")
    if not isinstance(sub, str):
        raise ValueError("LINE's verify response had no sub claim")
    return sub
