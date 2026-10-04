"""Tests for `/auth/*` (app/api/routes/auth.py).

The QR/password state-machine logic itself is already exercised thoroughly
against a fake Telethon client in `tests/test_telethon_auth.py`. These tests
only cover the route layer's own job: wiring the verified user (id,
username, full_name) and request payload through to the right
`app.services.telethon_auth` function, and translating its `AuthStepResult`
into the response model (including rendering `qr_image` from `qr_url`) — so
`telethon_auth`'s functions are monkeypatched directly rather than
re-driving the real flow.
"""

import pytest

from app.api.routes import auth as auth_routes
from app.api.schemas import PasswordRequest
from app.services import telethon_auth
from app.services.telegram_auth import TelegramWebAppUser
from app.services.telethon_auth import AuthStepResult

pytestmark = pytest.mark.asyncio

ALICE = TelegramWebAppUser(id=1, username="alice", full_name="Alice A.")


async def test_start_qr_delegates_with_user_details(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_start_qr_auth(user_id, username, full_name):
        seen["args"] = (user_id, username, full_name)
        return AuthStepResult(status="qr_pending", qr_url="tg://login?token=abc")

    monkeypatch.setattr(telethon_auth, "start_qr_auth", fake_start_qr_auth)

    result = await auth_routes.start_qr(user=ALICE)

    assert seen["args"] == (1, "alice", "Alice A.")
    assert result.status == "qr_pending"
    assert result.qr_url == "tg://login?token=abc"
    assert result.qr_image is not None
    assert result.qr_image.startswith("data:image/png;base64,")


async def test_start_qr_propagates_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_qr_auth(*_args):
        return AuthStepResult(status="error", error="Не вдалося згенерувати QR-код.")

    monkeypatch.setattr(telethon_auth, "start_qr_auth", fake_start_qr_auth)

    result = await auth_routes.start_qr(user=ALICE)

    assert result.status == "error"
    assert result.error == "Не вдалося згенерувати QR-код."
    assert result.qr_image is None


async def test_poll_qr_delegates_to_user_id_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_poll_qr_auth(user_id):
        seen["user_id"] = user_id
        return AuthStepResult(status="waiting")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    result = await auth_routes.poll_qr(user_id=1)

    assert seen["user_id"] == 1
    assert result.status == "waiting"
    assert result.qr_image is None


async def test_poll_qr_renewed_includes_a_fresh_qr_image(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="qr_renewed", qr_url="tg://login?token=new")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    result = await auth_routes.poll_qr(user_id=1)

    assert result.status == "qr_renewed"
    assert result.qr_url == "tg://login?token=new"
    assert result.qr_image is not None


async def test_poll_qr_password_required(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="password_required")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    result = await auth_routes.poll_qr(user_id=1)

    assert result.status == "password_required"
    assert result.error is None


async def test_submit_password_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_submit_password(user_id, password):
        seen["args"] = (user_id, password)
        return AuthStepResult(status="connected")

    monkeypatch.setattr(telethon_auth, "submit_password", fake_submit_password)

    result = await auth_routes.submit_password(PasswordRequest(password="hunter2"), user_id=1)

    assert seen["args"] == (1, "hunter2")
    assert result.status == "connected"


async def test_cancel_delegates_and_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_cancel_auth(user_id):
        seen["user_id"] = user_id

    monkeypatch.setattr(telethon_auth, "cancel_auth", fake_cancel_auth)

    result = await auth_routes.cancel(user_id=1)

    assert seen["user_id"] == 1
    assert result == {"ok": True}
