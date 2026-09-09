"""Which origins the browser is told may call this API.

The LAN entry exists so a real phone can reach the laptop's server over
wifi — a desktop browser can't show Safari's rendering, touch targets or
the keyboard, and three bugs have reached a phone that every static check
passed. It is also the only origin rule here that is a pattern rather
than a fixed string, which is why it gets its own tests.
"""

import importlib
import os
import re
from collections.abc import Iterator

import pytest

from volleyflow.api import main

_FLAG = "VOLLEYFLOW_DEV_LOGIN"


@pytest.fixture(autouse=True)
def _module_left_as_found() -> Iterator[None]:
    """importlib.reload rebinds the module object every other test in the
    suite imports, so whatever happens here has to be put back — without
    this, passing depends on which order pytest happens to run in."""
    before = os.environ.get(_FLAG)
    yield
    if before is None:
        os.environ.pop(_FLAG, None)
    else:
        os.environ[_FLAG] = before
    importlib.reload(main)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://192.168.1.101:5500",
        "http://10.0.0.5:5500",
        "http://172.16.0.1:5500",
        "http://172.31.255.254:8000",
        "http://192.168.1.101",  # no port
    ],
)
def test_a_machine_on_the_local_network_is_allowed(origin: str) -> None:
    assert re.fullmatch(main._PRIVATE_LAN_ORIGIN, origin)


@pytest.mark.parametrize(
    "origin",
    [
        # Just outside the private blocks.
        "http://172.15.0.1:5500",
        "http://172.32.0.1:5500",
        "http://11.0.0.5:5500",
        "http://193.168.1.1:5500",
        # The one that matters: a hostname that merely *starts* with a
        # private address. Starlette matches with fullmatch, so this
        # cannot pass — the test is here so a later edit can't quietly
        # turn that into a prefix match.
        "http://192.168.1.101.evil.com",
        "http://localhost.evil.com",
        # https is the deployed site's business, not the LAN's.
        "https://192.168.1.101:5500",
    ],
)
def test_anything_outside_the_local_network_is_refused(origin: str) -> None:
    assert re.fullmatch(main._PRIVATE_LAN_ORIGIN, origin) is None


def test_production_offers_no_lan_origin_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The pattern is only wired in when local sign-in is on. Production
    # sets neither, so the app it builds has no regex to match against.
    monkeypatch.delenv("VOLLEYFLOW_DEV_LOGIN", raising=False)

    reloaded = importlib.reload(main)

    assert reloaded._dev_login is False
    cors = next(
        m for m in reloaded.app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert cors.kwargs["allow_origin_regex"] is None
    assert cors.kwargs["allow_origins"] == ["https://marksu1104.github.io"]


def test_turning_on_local_sign_in_wires_the_pattern_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    reloaded = importlib.reload(main)

    cors = next(
        m for m in reloaded.app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert cors.kwargs["allow_origin_regex"] == reloaded._PRIVATE_LAN_ORIGIN
