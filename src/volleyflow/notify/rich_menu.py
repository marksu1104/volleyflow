"""The fixed panel under the chat in the club's LINE Official Account.

A rich menu is the app's front door: members already live in LINE, and
without one the only way back into VolleyFlow is to scroll the chat
looking for a link somebody posted weeks ago.

NOTHING HERE RUNS ON IMPORT, AND NOTHING PUBLISHES BY ACCIDENT. Setting
a default rich menu changes what every member of the Official Account
sees, so `publish` is reachable only by running this module with an
explicit `--publish`. Building the definition and rendering the artwork
are safe to do any number of times; sending it is one deliberate act.

Three calls, in this order:

  1. POST /v2/bot/richmenu                     -> richMenuId
  2. POST /v2/bot/richmenu/{id}/content        the PNG
  3. POST /v2/bot/user/all/richmenu/{id}       make it everyone's default

Step 2 goes to **api-data.line.me**, not api.line.me. That is the one
part of this API that is easy to get quietly wrong: the wrong host
answers 404 and the menu ends up published with no image on it.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

import httpx

_API = "https://api.line.me/v2/bot"
_DATA_API = "https://api-data.line.me/v2/bot"

# LINE accepts 2500x1686 (tall, up to six areas) or 2500x843 (compact,
# up to three). Compact, because three honest destinations beat six
# where half open the same page — and it takes far less of the screen.
MENU_WIDTH = 2500
MENU_HEIGHT = 843

logger = logging.getLogger("volleyflow.rich_menu")


def _token() -> str:
    return os.environ["LINE_CHANNEL_ACCESS_TOKEN"]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def three_column_areas(urls: list[str]) -> list[dict[str, Any]]:
    """Three equal columns across the compact menu, left to right.

    The widths are 833 / 834 / 833 rather than three times 833: LINE
    rejects a definition whose areas do not tile the image exactly, and
    2500 does not divide by three.
    """
    if len(urls) != 3:
        raise ValueError("a compact menu has room for exactly three areas")
    widths = [833, 834, 833]
    areas: list[dict[str, Any]] = []
    x = 0
    for width, url in zip(widths, urls, strict=True):
        areas.append(
            {
                "bounds": {"x": x, "y": 0, "width": width, "height": MENU_HEIGHT},
                "action": {"type": "uri", "uri": url},
            }
        )
        x += width
    return areas


def build_definition(
    urls: list[str], chat_bar_text: str = "打開 VolleyFlow"
) -> dict[str, Any]:
    """The menu LINE stores. `selected: True` means it starts open, which
    is what makes it a front door rather than something to discover."""
    return {
        "size": {"width": MENU_WIDTH, "height": MENU_HEIGHT},
        "selected": True,
        "name": "VolleyFlow",
        "chatBarText": chat_bar_text,
        "areas": three_column_areas(urls),
    }


def create(definition: dict[str, Any]) -> str:
    response = httpx.post(
        f"{_API}/richmenu",
        headers={**_headers(), "Content-Type": "application/json"},
        json=definition,
        timeout=10,
    )
    response.raise_for_status()
    rich_menu_id: str = response.json()["richMenuId"]
    return rich_menu_id


def upload_image(rich_menu_id: str, png: bytes) -> None:
    """The artwork. api-data.line.me, not api.line.me — see the module
    docstring."""
    response = httpx.post(
        f"{_DATA_API}/richmenu/{rich_menu_id}/content",
        headers={**_headers(), "Content-Type": "image/png"},
        content=png,
        timeout=30,
    )
    response.raise_for_status()


def set_default(rich_menu_id: str) -> None:
    """Every member of the Official Account sees this from now on."""
    response = httpx.post(
        f"{_API}/user/all/richmenu/{rich_menu_id}",
        headers=_headers(),
        timeout=10,
    )
    response.raise_for_status()


def list_menus() -> list[dict[str, Any]]:
    response = httpx.get(f"{_API}/richmenu/list", headers=_headers(), timeout=10)
    response.raise_for_status()
    menus: list[dict[str, Any]] = response.json().get("richmenus", [])
    return menus


def delete(rich_menu_id: str) -> None:
    """Menus are kept per channel and there is a cap, so replacing one
    without deleting the old leaves litter that eventually refuses new
    ones."""
    response = httpx.delete(
        f"{_API}/richmenu/{rich_menu_id}", headers=_headers(), timeout=10
    )
    response.raise_for_status()


def publish(png_path: Path, urls: list[str], replace: bool = True) -> str:
    """Create, upload, and make default — the whole outward-facing act.

    `replace` deletes every menu that existed before, which is almost
    always what is wanted: the alternative is a channel slowly filling
    with unreferenced menus until LINE refuses to store another.
    """
    png = png_path.read_bytes()
    existing = [m["richMenuId"] for m in list_menus()] if replace else []

    rich_menu_id = create(build_definition(urls))
    upload_image(rich_menu_id, png)
    set_default(rich_menu_id)
    logger.info("published rich menu %s", rich_menu_id)

    for old in existing:
        delete(old)
        logger.info("deleted previous rich menu %s", old)
    return rich_menu_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path, help="the 2500x843 PNG")
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        dest="urls",
        help="destination for one column, left to right; give it three times",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="actually send it. Without this the definition is printed and "
        "nothing reaches LINE.",
    )
    args = parser.parse_args()

    definition = build_definition(args.urls)
    if not args.publish:
        print("Not published — pass --publish to send this to LINE:")
        print(definition)
        return 0

    rich_menu_id = publish(args.image, args.urls)
    print(f"published {rich_menu_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
