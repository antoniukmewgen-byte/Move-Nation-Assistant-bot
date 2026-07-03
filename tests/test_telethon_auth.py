"""Tests for the shared phone/code/password login flow.

Exercises `app.services.telethon_auth` against a fake Telethon client so we
can assert the status-transition logic (code_sent -> password_required /
connected, invalid code/password -> error, cancel) without touching the
real Telegram network — this is the same flow both `/connect` in the bot
and the Mini App's connect screen drive.
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import PhoneCodeInvalidError, PhoneNumberInvalidError, SessionPasswordNeededError

from app.db import crud
from app.db.models import Base
from app.services import telethon_auth

pytestmark = pytest.mark.asyncio


class FakeTelegramClient:
    """Stands in for `telethon.TelegramClient` in tests.

    Behaviour is driven by the phone number / code / password passed in, so
    each test can steer it into the branch it wants to exercise (invalid
    phone, invalid code, 2FA required, success) without any real network
    access.
    """

    instances: ClassVar[list["FakeTelegramClient"]] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.disconnected = False
        self.session = SimpleNamespace(save=lambda: "fake-session-string")
        FakeTelegramClient.instances.append(self)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_code_request(self, phone: str) -> SimpleNamespace:
        if phone == "+000invalid":
            raise PhoneNumberInvalidError(None)
        return SimpleNamespace(phone_code_hash="hash123")

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        phone_code_hash: str | None = None,
        password: str | None = None,
    ) -> None:
        if password is not None:
            if password == "wrong":
                raise RuntimeError("SRP verification failed")
            return
        if code == "000000":
            raise PhoneCodeInvalidError(None)
        if code == "222222":
            raise SessionPasswordNeededError(None)
        # any other code succeeds outright (no 2FA)


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telethon_auth, "TelegramClient", FakeTelegramClient)
    telethon_auth._pending_clients.clear()
    telethon_auth._pending_phone_data.clear()


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(telethon_auth, "async_session", sessionmaker)
    yield
    await engine.dispose()


async def test_invalid_phone_is_rejected_without_creating_pending_client() -> None:
    result = await telethon_auth.start_phone_auth(1, "alice", "Alice", "+000invalid")
    assert result.status == "error"
    assert result.error
    assert 1 not in telethon_auth._pending_clients


async def test_full_login_without_2fa() -> None:
    start = await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")
    assert start.status == "code_sent"

    result = await telethon_auth.submit_code(1, "123456")
    assert result.status == "connected"
    assert 1 not in telethon_auth._pending_clients

    async with telethon_auth.async_session() as session:
        assert await crud.get_user_session(session, 1) is not None


async def test_login_with_2fa_requires_password() -> None:
    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")

    code_result = await telethon_auth.submit_code(1, "222222")
    assert code_result.status == "password_required"
    # Client must stay pending — the user still needs to submit the password.
    assert 1 in telethon_auth._pending_clients

    wrong = await telethon_auth.submit_password(1, "wrong")
    assert wrong.status == "error"
    assert 1 not in telethon_auth._pending_clients  # cancelled after a hard failure

    # Re-attempt via /connect (or the Mini App) from scratch, this time with the right password.
    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")
    await telethon_auth.submit_code(1, "222222")
    ok = await telethon_auth.submit_password(1, "correct")
    assert ok.status == "connected"


async def test_invalid_code_clears_pending_state() -> None:
    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")
    result = await telethon_auth.submit_code(1, "000000")
    assert result.status == "error"
    assert 1 not in telethon_auth._pending_clients


async def test_submit_code_without_prior_phone_step_is_an_error() -> None:
    result = await telethon_auth.submit_code(42, "123456")
    assert result.status == "error"


async def test_starting_a_new_phone_auth_cancels_the_previous_pending_client() -> None:
    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")
    first_client = telethon_auth._pending_clients[1]

    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380111111")
    assert first_client.disconnected is True
    assert telethon_auth._pending_clients[1] is not first_client


async def test_cancel_auth_disconnects_and_clears_state() -> None:
    await telethon_auth.start_phone_auth(1, "alice", "Alice", "+380000000")
    client = telethon_auth._pending_clients[1]

    await telethon_auth.cancel_auth(1)
    assert client.disconnected is True
    assert 1 not in telethon_auth._pending_clients
    assert 1 not in telethon_auth._pending_phone_data
