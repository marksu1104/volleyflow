"""Problem reports: sent by anybody signed in, read by the developer.

Stored rather than pushed over LINE, so they do not spend the free
tier's messages; reports.html and the unread count on the developer's
profile are the signal. The caller's identity, club, screen and
browser are attached automatically and stored as text, so a report
neither blocks deleting a club nor changes when it is renamed.
"""

import base64
import binascii
import os
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import _now, get_current_player, is_developer
from volleyflow.api.schemas import ProblemReport, ProblemReportOut
from volleyflow.db.models import ClubRow, PlayerRow, ProblemReportRow

router = APIRouter()

# Long enough to be read, short enough not to fill a free-tier database with
# old screenshots.
_KEEP_FOR = timedelta(days=90)
_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
_MAX_MESSAGE_CHARS = 4000


def _require_developer(player: PlayerRow) -> None:
    # Fails closed, like the invite secret: unset means nobody can read,
    # never that everybody can.
    if not os.environ.get("DEVELOPER_LINE_USER_ID"):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reading reports isn't configured on this server",
        )
    if not is_developer(player):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the developer can read reports"
        )


@router.post("/reports", status_code=204)
def report_a_problem(
    payload: ProblemReport,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Stores a problem report for the developer to read."""
    text = (payload.message or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to report")

    club_name = None
    if payload.club_id is not None:
        club = db.get(ClubRow, payload.club_id)
        if club is not None:
            club_name = club.name

    image, content_type = _decode_screenshot(payload.screenshot)

    db.query(ProblemReportRow).filter(
        ProblemReportRow.created_at < _now() - _KEEP_FOR
    ).delete(synchronize_session=False)
    db.add(
        ProblemReportRow(
            id=secrets.token_urlsafe(24),
            message=text[:_MAX_MESSAGE_CHARS],
            reporter=f"{current_player.name}（#{current_player.id}）",
            club=club_name,
            page=payload.page[:200] if payload.page else None,
            user_agent=payload.user_agent[:300] if payload.user_agent else None,
            image=image,
            content_type=content_type,
            created_at=_now(),
        )
    )
    db.commit()


@router.get("/reports", response_model=list[ProblemReportOut])
def list_reports(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ProblemReportOut]:
    """Newest first. Developer only."""
    _require_developer(current_player)
    rows = (
        db.query(ProblemReportRow)
        # Old rows with only a screenshot have nothing to read.
        .filter(ProblemReportRow.message.is_not(None))
        .order_by(ProblemReportRow.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        ProblemReportOut(
            id=row.id,
            message=row.message or "",
            reporter=row.reporter or "",
            club=row.club,
            page=row.page,
            user_agent=row.user_agent,
            created_at=row.created_at,
            has_screenshot=row.image is not None,
            read=row.read_at is not None,
        )
        for row in rows
    ]


@router.post("/reports/read", status_code=204)
def mark_reports_read(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Marks every unread report as read. Called by the reading page
    after it has drawn them.
    """
    _require_developer(current_player)
    db.query(ProblemReportRow).filter(ProblemReportRow.read_at.is_(None)).update(
        {ProblemReportRow.read_at: _now()}, synchronize_session=False
    )
    db.commit()


@router.get("/reports/{token}/image")
def problem_report_image(
    token: str,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> Response:
    """A report's screenshot. Developer only: a screenshot can show a
    name and a balance.
    """
    _require_developer(current_player)
    row = db.get(ProblemReportRow, token)
    if row is None or row.image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such screenshot")
    return Response(
        content=row.image, media_type=row.content_type or "application/octet-stream"
    )


def _decode_screenshot(data_url: str | None) -> tuple[bytes | None, str | None]:
    """The picture's bytes and type, or (None, None) when there is none.
    The page shrinks it before sending.
    """
    if not data_url:
        return (None, None)

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
    return (raw, content_type)
