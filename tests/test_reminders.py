"""Tests for the reminder-gating logic in app/services/reminders.py.

Reminders are triggered by an APScheduler interval job anchored to process
startup (see app/main.py), not to the client's message time. Without gating,
`send_reminders` would fire for every awaiting-response group on every tick,
regardless of how long the group has actually been waiting — this is the bug
the tests below guard against.
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import crud
from app.db.models import CLIENT_TAG, Base, Role

pytestmark = pytest.mark.asyncio


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
async def _patch_reminders(monkeypatch: pytest.MonkeyPatch):
    from app.services import reminders as reminders_service

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(reminders_service, "async_session", sessionmaker)

    fake_bot = FakeBot()
    monkeypatch.setattr(reminders_service, "bot", fake_bot)

    yield sessionmaker, fake_bot
    await engine.dispose()


async def _seed_group_awaiting(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    last_message_at: datetime,
    last_reminder_at: datetime | None = None,
) -> None:
    async with sessionmaker() as session:
        await crud.create_group_record(session, 100, "Group A", created_by_userbot=True)
        await crud.get_or_create_user(session, 1, "manager", "Manager")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.get_or_create_user(session, 2, "client", "Client")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)

        group = await crud.get_group(session, 100)
        assert group is not None
        group.awaiting_response = True
        group.last_message_at = last_message_at
        group.last_reminder_at = last_reminder_at

        await session.commit()


async def test_no_reminder_before_interval_elapses_since_client_message(_patch_reminders) -> None:
    from app.services.reminders import send_reminders

    sessionmaker, fake_bot = _patch_reminders
    await _seed_group_awaiting(sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=1))

    await send_reminders()

    assert fake_bot.sent == []
    async with sessionmaker() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.last_reminder_at is None


async def test_first_reminder_sent_once_interval_elapsed_since_client_message(_patch_reminders) -> None:
    from app.services.reminders import send_reminders

    sessionmaker, fake_bot = _patch_reminders
    await _seed_group_awaiting(sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=6))

    await send_reminders()

    assert len(fake_bot.sent) == 1
    assert fake_bot.sent[0][0] == 1
    async with sessionmaker() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.last_reminder_at is not None


async def test_no_repeat_reminder_before_interval_elapses_since_last_reminder(_patch_reminders) -> None:
    from app.services.reminders import send_reminders

    sessionmaker, fake_bot = _patch_reminders
    await _seed_group_awaiting(
        sessionmaker,
        last_message_at=datetime.utcnow() - timedelta(minutes=20),
        last_reminder_at=datetime.utcnow() - timedelta(minutes=1),
    )

    await send_reminders()

    assert fake_bot.sent == []


async def test_repeat_reminder_sent_once_interval_elapsed_since_last_reminder(_patch_reminders) -> None:
    from app.services.reminders import send_reminders

    sessionmaker, fake_bot = _patch_reminders
    old_reminder_at = datetime.utcnow() - timedelta(minutes=6)
    await _seed_group_awaiting(
        sessionmaker,
        last_message_at=datetime.utcnow() - timedelta(minutes=20),
        last_reminder_at=old_reminder_at,
    )

    await send_reminders()

    assert len(fake_bot.sent) == 1
    async with sessionmaker() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.last_reminder_at is not None
        assert group.last_reminder_at > old_reminder_at


async def test_no_reminder_when_group_is_not_awaiting_response(_patch_reminders) -> None:
    from app.services.reminders import send_reminders

    sessionmaker, fake_bot = _patch_reminders
    async with sessionmaker() as session:
        await crud.create_group_record(session, 100, "Group A", created_by_userbot=True)
        await crud.get_or_create_user(session, 1, "manager", "Manager")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    await send_reminders()

    assert fake_bot.sent == []
