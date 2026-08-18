"""Tests for the LINE webhook receiver."""

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_webhook_accepts_a_correctly_signed_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    body = b'{"events": []}'
    signature = _sign(body, "test-secret")

    response = client.post(
        "/line/webhook",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_webhook_rejects_an_incorrectly_signed_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    body = b'{"events": []}'

    response = client.post(
        "/line/webhook",
        content=body,
        headers={
            "X-Line-Signature": "wrong-signature",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 400


def test_webhook_logs_the_group_id_from_a_join_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    body = (
        b'{"events": [{"type": "join", '
        b'"source": {"type": "group", "groupId": "Cabc123"}}]}'
    )
    signature = _sign(body, "test-secret")

    response = client.post(
        "/line/webhook",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert "Cabc123" in capsys.readouterr().out
