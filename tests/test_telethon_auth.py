"""Tests for the shared QR/password login flow.

Exercises `app.services.telethon_auth` against a fake Telethon client so we
can assert the status-transition logic (qr_pending -> qr_renewed /
password_required / connected, errors, cancel) without touching the real
Telegram network — this is the same flow both `/connect` in the bot and the
Mini App's connect screen drive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError, SessionPasswordNeededError

from app.db import crud
from app.db.models import Base, User
from app.services import telethon_auth

pytestmark = pytest.mark.asyncio


class FakeQrLogin:
    """Stands in for `telethon.tl.custom.qrlogin.QRLogin` in tests.

    `wait_results` is a queue tests push onto to steer the next `wait()`
    call: an exception instance to raise, or any other value to return as
    the "logged in" TL user. An empty queue means "not scanned yet" (mirrors
    Telethon's own `asyncio.TimeoutError` when nothing happened within the
    poll window).
    """

    def __init__(self, url: str = "tg://login?token=initial") -> None:
        self.url = url
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.wait_results: list[Any] = []
        self.recreated = False

    async def wait(self, timeout: float | None = None) -> Any:
        if not self.wait_results:
            raise asyncio.TimeoutError()
        result = self.wait_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def recreate(self) -> None:
        self.recreated = True
        self.url = "tg://login?token=renewed"
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)


class FakeTelegramClient:
    """Stands in for `telethon.TelegramClient` in tests.

    `qr_login_error`, when set, makes the *next* `qr_login()` call raise it
    (e.g. a `FloodWaitError`) — lets a test steer the "can't even generate a
    QR code" branch without any real network access.
    """

    instances: ClassVar[list["FakeTelegramClient"]] = []
    qr_login_error: ClassVar[Exception | None] = None

    def __init__(self, *_args, **_kwargs) -> None:
        self.disconnected = False
        self.session = SimpleNamespace(save=lambda: "fake-session-string")
        self.qr = FakeQrLogin()
        FakeTelegramClient.instances.append(self)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self.disconnected = True

    async def qr_login(self) -> FakeQrLogin:
        if FakeTelegramClient.qr_login_error is not None:
            raise FakeTelegramClient.qr_login_error
        return self.qr

    async def sign_in(self, password: str | None = None) -> None:
        if password == "wrong":
            raise RuntimeError("SRP verification failed")


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTelegramClient.instances.clear()
    FakeTelegramClient.qr_login_error = None
    monkeypatch.setattr(telethon_auth, "TelegramClient", FakeTelegramClient)
    telethon_auth._pending_clients.clear()
    telethon_auth._pending_qr.clear()


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(telethon_auth, "async_session", sessionmaker)
    yield
    await engine.dispose()


async def test_qr_login_error_is_reported_without_creating_pending_client() -> None:
    FakeTelegramClient.qr_login_error = FloodWaitError(request=None, capture=60)

    result = await telethon_auth.start_qr_auth(1, "alice", "Alice")

    assert result.status == "error"
    assert result.error
    assert 1 not in telethon_auth._pending_clients


async def test_full_login_without_2fa() -> None:
    start = await telethon_auth.start_qr_auth(1, "alice", "Alice")
    assert start.status == "qr_pending"
    assert start.qr_url == "tg://login?token=initial"

    telethon_auth._pending_qr[1].wait_results.append(SimpleNamespace(phone="+380000000"))
    result = await telethon_auth.poll_qr_auth(1)

    assert result.status == "connected"
    assert 1 not in telethon_auth._pending_clients

    async with telethon_auth.async_session() as session:
        assert await crud.get_user_session(session, 1) is not None


async def test_successful_login_persists_the_scanned_phone_number() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    telethon_auth._pending_qr[1].wait_results.append(SimpleNamespace(phone="+380000000"))
    await telethon_auth.poll_qr_auth(1)

    async with telethon_auth.async_session() as session:
        user = await session.get(User, 1)
        assert user is not None
        assert user.phone == "+380000000"


async def test_poll_without_prior_start_is_an_error() -> None:
    result = await telethon_auth.poll_qr_auth(42)
    assert result.status == "error"


async def test_poll_reports_waiting_when_not_yet_scanned() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")

    result = await telethon_auth.poll_qr_auth(1)

    assert result.status == "waiting"
    assert 1 in telethon_auth._pending_clients


async def test_poll_renews_an_expired_qr_code() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    qr_login = telethon_auth._pending_qr[1]
    qr_login.expires = datetime.now(timezone.utc) - timedelta(seconds=1)

    result = await telethon_auth.poll_qr_auth(1)

    assert result.status == "qr_renewed"
    assert result.qr_url == "tg://login?token=renewed"
    assert qr_login.recreated is True


async def test_login_with_2fa_requires_password() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    telethon_auth._pending_qr[1].wait_results.append(SessionPasswordNeededError(None))

    scan_result = await telethon_auth.poll_qr_auth(1)
    assert scan_result.status == "password_required"
    # Client must stay pending — the user still needs to submit the password.
    assert 1 in telethon_auth._pending_clients

    wrong = await telethon_auth.submit_password(1, "wrong")
    assert wrong.status == "error"
    assert 1 not in telethon_auth._pending_clients  # cancelled after a hard failure

    # Re-attempt via /connect (or the Mini App) from scratch, this time with the right password.
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    telethon_auth._pending_qr[1].wait_results.append(SessionPasswordNeededError(None))
    await telethon_auth.poll_qr_auth(1)
    ok = await telethon_auth.submit_password(1, "correct")
    assert ok.status == "connected"


async def test_poll_error_cancels_pending_state() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    telethon_auth._pending_qr[1].wait_results.append(RuntimeError("boom"))

    result = await telethon_auth.poll_qr_auth(1)

    assert result.status == "error"
    assert 1 not in telethon_auth._pending_clients


async def test_starting_a_new_qr_auth_cancels_the_previous_pending_client() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    first_client = telethon_auth._pending_clients[1]

    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    assert first_client.disconnected is True
    assert telethon_auth._pending_clients[1] is not first_client


async def test_cancel_auth_disconnects_and_clears_state() -> None:
    await telethon_auth.start_qr_auth(1, "alice", "Alice")
    client = telethon_auth._pending_clients[1]

    await telethon_auth.cancel_auth(1)
    assert client.disconnected is True
    assert 1 not in telethon_auth._pending_clients
    assert 1 not in telethon_auth._pending_qr
