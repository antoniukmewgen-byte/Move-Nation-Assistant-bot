"""Tests for `/auth/*` (app/api/routes/auth.py).

The phone/code/password state-machine logic itself is already exercised
thoroughly against a fake Telethon client in `tests/test_telethon_auth.py`.
These tests only cover the route layer's own job: wiring the verified user
(id, username, full_name) and request payload through to the right
`app.services.telethon_auth` function, and translating its `AuthStepResult`
into the response model — so `telethon_auth`'s functions are monkeypatched
directly rather than re-driving the real flow.
"""

import pytest

from app.api.routes import auth as auth_routes
from app.api.schemas import CodeRequest, PasswordRequest, PhoneRequest
from app.services import telethon_auth
from app.services.telegram_auth import TelegramWebAppUser
from app.services.telethon_auth import AuthStepResult

pytestmark = pytest.mark.asyncio

ALICE = TelegramWebAppUser(id=1, username="alice", full_name="Alice A.")


async def test_submit_phone_delegates_with_user_details(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_start_phone_auth(user_id, username, full_name, phone):
        seen["args"] = (user_id, username, full_name, phone)
        return AuthStepResult(status="code_sent")

    monkeypatch.setattr(telethon_auth, "start_phone_auth", fake_start_phone_auth)

    result = await auth_routes.submit_phone(PhoneRequest(phone="+380123456789"), user=ALICE)

    assert seen["args"] == (1, "alice", "Alice A.", "+380123456789")
    assert result.status == "code_sent"
    assert result.error is None


async def test_submit_phone_propagates_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_phone_auth(*_args):
        return AuthStepResult(status="error", error="Некоректний номер телефону.")

    monkeypatch.setattr(telethon_auth, "start_phone_auth", fake_start_phone_auth)

    result = await auth_routes.submit_phone(PhoneRequest(phone="123456"), user=ALICE)

    assert result.status == "error"
    assert result.error == "Некоректний номер телефону."


async def test_submit_code_delegates_to_user_id_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    async def fake_submit_code(user_id, code):
        seen["args"] = (user_id, code)
        return AuthStepResult(status="connected")

    monkeypatch.setattr(telethon_auth, "submit_code", fake_submit_code)

    result = await auth_routes.submit_code(CodeRequest(code="12345"), user_id=1)

    assert seen["args"] == (1, "12345")
    assert result.status == "connected"


async def test_submit_code_password_required(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_submit_code(*_args):
        return AuthStepResult(status="password_required")

    monkeypatch.setattr(telethon_auth, "submit_code", fake_submit_code)

    result = await auth_routes.submit_code(CodeRequest(code="12345"), user_id=1)

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
