"""LINE Messaging API webhook receiver.

For now this only logs incoming events — the immediate need is capturing
the volleyball group's LINE group id (visible in the logged event) once
the bot is added to it. No auto-reply logic yet; that's a stretch goal,
never the billing engine, per CLAUDE.md 2.5.
"""

import base64
import hashlib
import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Request, status

router = APIRouter()


def _verify_signature(body: bytes, signature: str) -> None:
    channel_secret = os.environ["LINE_CHANNEL_SECRET"]
    expected = base64.b64encode(
        hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid LINE signature")


@router.post("/line/webhook")
async def line_webhook(
    request: Request, x_line_signature: str = Header(...)
) -> dict[str, str]:
    body = await request.body()
    _verify_signature(body, x_line_signature)

    payload = await request.json()
    for event in payload.get("events", []):
        source = event.get("source", {})
        print(
            f"LINE webhook event: type={event.get('type')} "
            f"source_type={source.get('type')} "
            f"group_id={source.get('groupId')} "
            f"user_id={source.get('userId')}"
        )

    return {"status": "ok"}
