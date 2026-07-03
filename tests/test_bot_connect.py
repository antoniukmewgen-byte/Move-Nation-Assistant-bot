"""Tests for `/connect` (app/bot/handlers/connect.py).

The phone/code/password state-machine logic itself is already exercised
against a fake Telethon client in `tests/test_telethon_auth.py`; these tests
only cover the handler layer's own job — FSM state transitions and the
message sent back to the user for each `AuthStepResult`, so
`app.services.telethon_auth`'s functions are monkeypatched directly.
"""

import pytest

from app.bot.handlers import connect as connect_handlers
from app.bot.states import Connect
from app.services import telethon_auth
from app.services.telethon_auth import AuthStepResult
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser, make_fsm_context

pytestmark = pytest.mark.asyncio


async def test_cmd_connect_sets_waiting_for_phone_state() -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await connect_handlers.cmd_connect(message, state)

    assert await state.get_state() == Connect.waiting_for_phone
    assert len(message.answers) == 1


async def test_process_phone_success_moves_to_waiting_for_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_phone_auth(*_args):
        return AuthStepResult(status="code_sent")

    monkeypatch.setattr(telethon_auth, "start_phone_auth", fake_start_phone_auth)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="+380123456789")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_phone)

    await connect_handlers.process_phone(message, state)

    assert await state.get_state() == Connect.waiting_for_code
    assert "код" in message.answers[0].lower()


async def test_process_phone_error_clears_state_and_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_phone_auth(*_args):
        return AuthStepResult(status="error", error="Некоректний номер телефону.")

    monkeypatch.setattr(telethon_auth, "start_phone_auth", fake_start_phone_auth)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="123")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_phone)

    await connect_handlers.process_phone(message, state)

    assert await state.get_state() is None
    assert "Некоректний номер телефону." in message.answers[0]


async def test_process_code_success_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_submit_code(*_args):
        return AuthStepResult(status="connected")

    monkeypatch.setattr(telethon_auth, "submit_code", fake_submit_code)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="12345")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_code)

    await connect_handlers.process_code(message, state)

    assert await state.get_state() is None
    assert message.deleted is True
    assert "підключено" in message.answers[0].lower()


async def test_process_code_password_required_moves_to_waiting_for_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_submit_code(*_args):
        return AuthStepResult(status="password_required")

    monkeypatch.setattr(telethon_auth, "submit_code", fake_submit_code)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="12345")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_code)

    await connect_handlers.process_code(message, state)

    assert await state.get_state() == Connect.waiting_for_password


async def test_process_code_error_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_submit_code(*_args):
        return AuthStepResult(status="error", error="Код невірний або застарів. Почни знову.")

    monkeypatch.setattr(telethon_auth, "submit_code", fake_submit_code)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="00000")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_code)

    await connect_handlers.process_code(message, state)

    assert await state.get_state() is None
    assert "Код невірний або застарів." in message.answers[0]


async def test_process_password_success_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_submit_password(*_args):
        return AuthStepResult(status="connected")

    monkeypatch.setattr(telethon_auth, "submit_password", fake_submit_password)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="hunter2")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_password)

    await connect_handlers.process_password(message, state)

    assert await state.get_state() is None
    assert message.deleted is True
    assert "підключено" in message.answers[0].lower()
