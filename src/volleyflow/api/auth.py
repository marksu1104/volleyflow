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
from urllib.parse import unquote

import httpx

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

_DEV_PREFIX = "dev:"
_DEV_FLAG = "VOLLEYFLOW_DEV_LOGIN"


def verify_id_token(id_token: str) -> str:
    """Returns the verified line_user_id (the token's `sub` claim).

    Raises ValueError if LINE rejects the token — expired, wrong
    audience, tampered, or just malformed. Never raises for "not
    verifiable due to a network error"; that's left to bubble up as a
    5xx, since silently treating a verification outage as "invalid" would
    lock everyone out at once for an unrelated reason.
    """
    # A way to be somebody without LINE, for local work only. Nothing
    # about the app is reachable without a verified identity, so on a
    # laptop — no LIFF, no ID token — every page stops at "open this in
    # LINE" and the only way to see a member's view was to pick up a
    # phone and use the live data.
    #
    # This is a hole in authentication, so it is deliberately awkward to
    # open: it needs an environment variable that production does not
    # set, *and* a token shaped like nothing LINE would ever issue. Two
    # conditions, either of which alone does nothing, and a value that is
    # obvious in a log if it ever appears somewhere it shouldn't.
    if id_token.startswith(_DEV_PREFIX) and os.environ.get(_DEV_FLAG) == "1":
        # Percent-decoded because this arrives in an Authorization
        # header, and HTTP header values are ASCII — a browser refuses
        # to send "Bearer dev:蘇懂" at all. The frontend encodes the
        # name; this is the other half.
        name = unquote(id_token.removeprefix(_DEV_PREFIX)).strip()
        if not name:
            raise ValueError("dev login needs a name after 'dev:'")
        return f"{_DEV_PREFIX}{name}"

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
