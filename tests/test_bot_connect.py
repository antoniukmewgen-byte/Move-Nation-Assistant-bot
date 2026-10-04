"""Tests for `/connect` (app/bot/handlers/connect.py).

The QR/password state-machine logic itself is already exercised against a
fake Telethon client in `tests/test_telethon_auth.py`; these tests only
cover the handler layer's own job — FSM state transitions and the messages
sent back to the user for each `AuthStepResult`, so
`app.services.telethon_auth`'s functions are monkeypatched directly.
"""

import pytest

from app.bot.handlers import connect as connect_handlers
from app.bot.states import Connect
from app.services import telethon_auth
from app.services.telethon_auth import AuthStepResult
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser, make_fsm_context

pytestmark = pytest.mark.asyncio


async def test_cmd_connect_sends_qr_and_sets_waiting_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_qr_auth(*_args):
        return AuthStepResult(status="qr_pending", qr_url="tg://login?token=abc")

    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="waiting")

    monkeypatch.setattr(telethon_auth, "start_qr_auth", fake_start_qr_auth)
    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    try:
        await connect_handlers.cmd_connect(message, state)

        assert await state.get_state() == Connect.waiting_for_qr_scan
        assert len(message.answers) == 1
        assert 1 in connect_handlers._poll_tasks
    finally:
        connect_handlers._cancel_poll_task(1)


async def test_cmd_connect_error_reports_and_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_start_qr_auth(*_args):
        return AuthStepResult(status="error", error="Не вдалося згенерувати QR-код.")

    monkeypatch.setattr(telethon_auth, "start_qr_auth", fake_start_qr_auth)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await connect_handlers.cmd_connect(message, state)

    assert await state.get_state() is None
    assert "Не вдалося згенерувати QR-код." in message.answers[0]
    assert 1 not in connect_handlers._poll_tasks


async def test_poll_loop_reports_waiting_status_without_leaving_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [AuthStepResult(status="waiting"), AuthStepResult(status="connected")]

    async def fake_poll_qr_auth(_user_id):
        return results.pop(0)

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    sent = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_qr_scan)

    await connect_handlers._poll_loop(1, state, sent)

    assert not results
    assert await state.get_state() is None


async def test_poll_loop_qr_renewed_updates_the_photo_and_keeps_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        AuthStepResult(status="qr_renewed", qr_url="tg://login?token=new"),
        AuthStepResult(status="connected"),
    ]

    async def fake_poll_qr_auth(_user_id):
        return results.pop(0)

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    sent = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_qr_scan)

    await connect_handlers._poll_loop(1, state, sent)

    assert not results
    assert sent.edits  # the QR photo was replaced with the renewed one


async def test_poll_loop_password_required_moves_to_waiting_for_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="password_required")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    sent = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_qr_scan)

    await connect_handlers._poll_loop(1, state, sent)

    assert await state.get_state() == Connect.waiting_for_password
    assert "пароль" in sent.answers[-1].lower()


async def test_poll_loop_connected_edits_caption_and_clears_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="connected")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    sent = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_qr_scan)

    await connect_handlers._poll_loop(1, state, sent)

    assert await state.get_state() is None
    assert sent.edits[-1] == "Акаунт підключено!"
    assert "newgroup" in sent.answers[-1].lower() or "міні-застосунок" in sent.answers[-1].lower()


async def test_poll_loop_error_clears_state_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_poll_qr_auth(_user_id):
        return AuthStepResult(status="error", error="Не вдалося увійти.")

    monkeypatch.setattr(telethon_auth, "poll_qr_auth", fake_poll_qr_auth)

    sent = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_qr_scan)

    await connect_handlers._poll_loop(1, state, sent)

    assert await state.get_state() is None
    assert "Не вдалося увійти." in sent.answers[-1]


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


async def test_process_password_error_clears_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_submit_password(*_args):
        return AuthStepResult(status="error", error="Пароль невірний.")

    monkeypatch.setattr(telethon_auth, "submit_password", fake_submit_password)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="wrong")
    state = make_fsm_context()
    await state.set_state(Connect.waiting_for_password)

    await connect_handlers.process_password(message, state)

    assert await state.get_state() is None
    assert "Пароль невірний." in message.answers[0]
