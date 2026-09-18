"""Whether the caller can be reached by a LINE push.

The organizer's 總覽 asks this so somebody who never added the Official
Account finds out from the app, rather than from a short-handed game
nobody was told about. `line_client.is_reachable` is replaced here so no
test ever calls LINE.
"""

import pytest
from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify
from volleyflow.notify import line_client


def test_somebody_who_never_added_the_account_is_reported_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = identify(client, "周恆")
    monkeypatch.setattr(line_client, "is_reachable", lambda user_id: False)

    response = client.get(
        "/players/me/line-reachable", headers=auth_headers(person["token"])
    )

    assert response.status_code == 200
    assert response.json()["reachable"] is False


def test_a_friend_of_the_account_is_reported_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = identify(client, "周恆")
    monkeypatch.setattr(line_client, "is_reachable", lambda user_id: True)

    response = client.get(
        "/players/me/line-reachable", headers=auth_headers(person["token"])
    )

    assert response.json()["reachable"] is True


def test_an_unanswerable_check_is_reported_as_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Passes None straight through rather than collapsing it to false.
    # The screen shows the warning only on a definite false, so this is
    # what keeps a LINE outage from telling every organizer they are not
    # a friend.
    person = identify(client, "周恆")
    monkeypatch.setattr(line_client, "is_reachable", lambda user_id: None)

    response = client.get(
        "/players/me/line-reachable", headers=auth_headers(person["token"])
    )

    assert response.json()["reachable"] is None


def test_it_asks_about_the_caller_and_nobody_else(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reason this endpoint is about the caller alone: reporting one
    # organizer's friend status to another tells A something only B can
    # fix. A token here *is* the line_user_id (see tests/api/conftest),
    # so the recorded argument is directly comparable.
    person = identify(client, "周恆")
    other = identify(client, "林書妤")
    asked: list[str] = []

    def record(user_id: str) -> bool:
        asked.append(user_id)
        return True

    monkeypatch.setattr(line_client, "is_reachable", record)

    client.get("/players/me/line-reachable", headers=auth_headers(person["token"]))

    assert asked == [person["token"]]
    assert other["token"] not in asked


def test_it_refuses_an_unsigned_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(user_id: str) -> bool:
        raise AssertionError("must not ask LINE about an unauthenticated caller")

    monkeypatch.setattr(line_client, "is_reachable", explode)

    response = client.get("/players/me/line-reachable", headers={"Authorization": ""})

    assert response.status_code == 401
