"""A screenshot of something going wrong, sent to the developer."""

import base64
import binascii
import os
import re
import secrets
from datetime import timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import (
    _now,
    get_current_player,
)
from volleyflow.api.schemas import (
    ProblemReport,
)
from volleyflow.db.models import (
    ClubRow,
    PlayerRow,
    ProblemReportRow,
)
from volleyflow.notify import line_client

router = APIRouter()


@router.post("/reports", status_code=204)
def report_a_problem(
    payload: ProblemReport,
    request: Request,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Sends a problem report straight to the developer over LINE.

    Delivered rather than stored: an unread row in a table nobody has a
    screen for is the same as no report at all, and this project has no
    admin surface to grow one on. LINE is where the developer already
    is. The consequence is deliberate — if the push fails (LINE's free
    monthly quota is shared with game reminders), this fails loudly and
    the reporter is told, instead of the report quietly disappearing.

    Most of what makes a report actionable isn't the sentence someone
    types, it's who and where — so the caller's identity, screen and
    browser are attached here rather than asked for.
    """
    text = (payload.message or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to report")

    club_name = ""
    if payload.club_id is not None:
        club = db.get(ClubRow, payload.club_id)
        if club is not None:
            club_name = club.name

    lines = [
        "🐞 VolleyFlow 問題回報",
        "",
        text,
        "",
        f"回報者：{current_player.name}（#{current_player.id}）",
    ]
    if club_name:
        lines.append(f"球隊：{club_name}")
    if payload.page:
        lines.append(f"畫面：{payload.page}")
    if payload.user_agent:
        lines.append(f"裝置：{payload.user_agent[:180]}")
    lines.append(f"時間：{_now().isoformat(timespec='seconds')} UTC")

    developer_id = os.environ.get("LINE_ORGANIZER_USER_ID")
    if not developer_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Problem reporting isn't configured yet",
        )
    image_url = _store_screenshot(db, request, payload.screenshot)

    try:
        line_client.push_to_user(developer_id, "\n".join(lines))
        if image_url is not None:
            line_client.push_image_to_user(developer_id, image_url)
    except Exception as e:  # noqa: BLE001 - any failure means undelivered
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't send the report — please tell the organizer directly",
        ) from e


_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024


def _store_screenshot(
    db: Session, request: Request, data_url: str | None
) -> str | None:
    """Keeps a screenshot just long enough for LINE to come and fetch it.

    An image message has to name an HTTPS URL that LINE's own servers can
    read, so the picture needs somewhere public to live, and this project
    has no file storage. It goes in the database under an unguessable id
    and is served back by the endpoint below.

    Anything older than a month goes at the same time: nobody revisits a
    screenshot of a bug fixed weeks ago, and a free-tier database
    shouldn't quietly fill with them.
    """
    if not data_url:
        return None

    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,(.+)", data_url, re.S)
    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Screenshot must be a PNG, JPEG or WebP"
        )
    content_type, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Screenshot isn't valid base64"
        ) from e
    if len(raw) > _MAX_SCREENSHOT_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Screenshot is too large"
        )

    db.query(ProblemReportRow).filter(
        ProblemReportRow.created_at < _now() - timedelta(days=30)
    ).delete(synchronize_session=False)

    token = secrets.token_urlsafe(24)
    db.add(
        ProblemReportRow(
            id=token, image=raw, content_type=content_type, created_at=_now()
        )
    )
    db.commit()
    return str(request.url_for("problem_report_image", token=token))


@router.get("/reports/{token}/image", name="problem_report_image")
def problem_report_image(token: str, db: Session = Depends(get_db)) -> Response:
    """Deliberately public: LINE's servers fetch this to render the image
    message and arrive with none of our credentials. The id is 24 random
    bytes, so holding one tells you nothing about any other, and all that
    sits behind it is a screenshot its own author just sent.
    """
    row = db.get(ProblemReportRow, token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such screenshot")
    return Response(content=row.image, media_type=row.content_type)
