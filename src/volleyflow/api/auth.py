"""Turning a LINE ID token into a verified identity.

Every LIFF page gets a signed ID token from liff.getIDToken(); the API
trusts only what LINE says that token belongs to, never a user id in a
request body. Verification is delegated to LINE's endpoint rather than
done locally, trading one network call per request for no JWT library
or key cache to maintain.
"""

import os
from urllib.parse import unquote

import httpx

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

_DEV_PREFIX = "dev:"
_DEV_FLAG = "VOLLEYFLOW_DEV_LOGIN"


def verify_id_token(id_token: str) -> str:
    """Returns the verified LINE user id (the token's `sub`).

    Raises ValueError if LINE rejects the token. A network failure is
    left to surface as a 5xx: treating an outage as "invalid" would
    sign everyone out at once.
    """
    # Local sign-in without LINE, for development only. It needs both an
    # environment variable production never sets and a token no LINE login
    # could produce.
    if id_token.startswith(_DEV_PREFIX) and os.environ.get(_DEV_FLAG) == "1":
        # Percent-decoded: HTTP header values are ASCII, so the frontend
        # encodes the name.
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
