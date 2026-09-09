"""The dev-login escape hatch, and the locks on it.

`verify_id_token` is the only place a token becomes an identity, so it is
the only place this hole can exist — but it is still a hole in
authentication, and these are the tests that say when it opens and when
it must not.
"""

from typing import Any

import pytest

from volleyflow.api.auth import verify_id_token


class _Verified:
    """What LINE's verify endpoint returns for a good token."""

    status_code = 200

    @staticmethod
    def json() -> dict[str, str]:
        return {"sub": "U-real-line-account"}


def test_a_dev_token_is_refused_when_the_flag_is_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one that matters. Production does not set this variable, so a
    # dev token there has to fall through to LINE and be rejected —
    # never quietly accepted.
    monkeypatch.delenv("VOLLEYFLOW_DEV_LOGIN", raising=False)
    monkeypatch.setenv("LINE_LIFF_CHANNEL_ID", "x")

    def went_to_line(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("reached LINE, which is correct")

    monkeypatch.setattr("volleyflow.api.auth.httpx.post", went_to_line)

    with pytest.raises(AssertionError, match="reached LINE"):
        verify_id_token("dev:蘇懂")


def test_the_flag_alone_does_not_short_circuit_a_real_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The second lock. Even with dev mode on, anything not shaped like
    # dev:<name> still goes to LINE.
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")
    monkeypatch.setenv("LINE_LIFF_CHANNEL_ID", "x")
    calls: list[int] = []

    def record(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return _Verified()

    monkeypatch.setattr("volleyflow.api.auth.httpx.post", record)

    assert verify_id_token("eyJhbGciOi.a.real.looking.token") == "U-real-line-account"
    assert calls == [1]


def test_a_dev_token_with_the_flag_set_identifies_that_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    assert verify_id_token("dev:蘇懂") == "dev:蘇懂"


def test_a_dev_identity_can_never_collide_with_a_real_line_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # LINE user ids start with "U" followed by hex. Keeping the prefix on
    # the returned id keeps the two populations apart in the players
    # table, so local testing can't attach itself to a real person's
    # ledger.
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    assert verify_id_token("dev:蘇懂").startswith("dev:")


def test_a_name_arrives_percent_encoded_because_headers_are_ascii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A browser refuses to send "Bearer dev:蘇懂" — header values are
    # ASCII — so the frontend encodes the name and this decodes it. Found
    # by actually calling the running server, not by reading the code.
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    assert verify_id_token("dev:%E8%98%87%E6%87%82") == "dev:蘇懂"


def test_a_plain_ascii_name_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    assert verify_id_token("dev:Ricky") == "dev:Ricky"


def test_a_dev_token_with_no_name_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    with pytest.raises(ValueError, match="needs a name"):
        verify_id_token("dev:   ")
