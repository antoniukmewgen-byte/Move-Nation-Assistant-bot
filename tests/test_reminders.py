"""Tests for the per-group reminder scheduling in app/services/reminders.py.

Reminders used to be driven by a single APScheduler interval job anchored to
process startup (see app/main.py's old version), which meant `send_reminders`
fired for every awaiting-response group on every tick regardless of how long
the group had actually been waiting — gated manually inside the function.

Now each group gets its own one-shot APScheduler job, scheduled exactly at
"client message time + interval" (see app/bot/handlers/messages.py::
track_group_message), replaced/moved on every new client message, cancelled
on a staff reply, and re-created from DB state on process restart. The tests
below cover that scheduling/cancellation/recovery behavior directly, plus the
gate check `send_group_reminder` still performs as a safety net.
"""

from datetime import datetime, timedelta
from typing import Any

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import crud
from app.db.models import CLIENT_TAG, Base, Role

pytestmark = pytest.mark.asyncio


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.sent_kwargs: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append((chat_id, text))
        self.sent_kwargs.append(kwargs)

    async def send_sticker(self, chat_id: int, sticker: str, **_kwargs: Any) -> None:
        pass


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

    # A fresh scheduler per test. Must actually be *started* — add_job() on a
    # not-yet-started scheduler only appends to an internal pending-jobs list
    # without deduplicating by id (replace_existing is only honored once a
    # job is materialized into the jobstore), which would make the
    # replace-existing-job test see two jobs instead of one. All test run_dates
    # are safely in the future, so starting it won't actually fire anything
    # during the test.
    test_scheduler = AsyncIOScheduler(timezone="UTC")
    monkeypatch.setattr(reminders_service, "scheduler", test_scheduler)
    test_scheduler.start()

    yield sessionmaker, fake_bot, test_scheduler
    test_scheduler.shutdown(wait=False)
    await engine.dispose()


async def _seed_group_awaiting(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    last_message_at: datetime,
    last_reminder_at: datetime | None = None,
    group_id: int = 100,
) -> None:
    async with sessionmaker() as session:
        await crud.create_group_record(session, group_id, "Group A", created_by_userbot=True)
        await crud.get_or_create_user(session, 1, "manager", "Manager")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.add_member_tag(session, group_id, 1, Role.MANAGER.value)
        await crud.get_or_create_user(session, 2, "client", "Client")
        await crud.add_member_tag(session, group_id, 2, CLIENT_TAG)

        group = await crud.get_group(session, group_id)
        assert group is not None
        group.awaiting_response = True
        group.last_message_at = last_message_at
        group.last_reminder_at = last_reminder_at

        await session.commit()


async def test_send_group_reminder_skips_when_gate_not_elapsed(_patch_reminders) -> None:
    # Safety net: even if a job somehow fires early (race, stale recovered
    # job), send_group_reminder re-checks the gate instead of blindly sending.
    from app.services.reminders import send_group_reminder

    sessionmaker, fake_bot, _scheduler = _patch_reminders
    await _seed_group_awaiting(sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=1))

    await send_group_reminder(100)

    assert fake_bot.sent == []
    async with sessionmaker() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.last_reminder_at is None


async def test_send_group_reminder_sends_once_interval_elapsed_and_reschedules(_patch_reminders) -> None:
    from app.services.reminders import _job_id, send_group_reminder

    sessionmaker, fake_bot, scheduler = _patch_reminders
    await _seed_group_awaiting(sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=6))

    await send_group_reminder(100)

    assert len(fake_bot.sent) == 1
    assert fake_bot.sent[0][0] == 1
    async with sessionmaker() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.last_reminder_at is not None

    # Still awaiting response after sending — the next reminder must already
    # be scheduled, not left to some external re-trigger.
    job = scheduler.get_job(_job_id(100))
    assert job is not None


async def test_send_group_reminder_skips_when_group_no_longer_awaiting(_patch_reminders) -> None:
    from app.services.reminders import send_group_reminder

    sessionmaker, fake_bot, _scheduler = _patch_reminders
    async with sessionmaker() as session:
        await crud.create_group_record(session, 100, "Group A", created_by_userbot=True)
        await crud.get_or_create_user(session, 1, "manager", "Manager")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    await send_group_reminder(100)

    assert fake_bot.sent == []


async def test_send_group_reminder_attaches_group_deep_link_button_for_supergroups(
    _patch_reminders,
) -> None:
    from app.services.reminders import send_group_reminder

    sessionmaker, fake_bot, _scheduler = _patch_reminders
    supergroup_id = -1001234567890
    await _seed_group_awaiting(
        sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=6), group_id=supergroup_id
    )

    await send_group_reminder(supergroup_id)

    assert len(fake_bot.sent_kwargs) == 1
    reply_markup = fake_bot.sent_kwargs[0]["reply_markup"]
    assert reply_markup is not None
    button = reply_markup.inline_keyboard[0][0]
    assert button.url == "https://t.me/c/1234567890"


async def test_send_group_reminder_omits_button_for_non_supergroup_chat_ids(_patch_reminders) -> None:
    # Basic (non-super) groups have no `-100<internal_id>`-encoded chat_id,
    # so there's no reliable t.me/c/ deep link to build — the reminder must
    # still send fine, just without the button, rather than link to garbage.
    from app.services.reminders import send_group_reminder

    sessionmaker, fake_bot, _scheduler = _patch_reminders
    await _seed_group_awaiting(sessionmaker, last_message_at=datetime.utcnow() - timedelta(minutes=6))

    await send_group_reminder(100)

    assert len(fake_bot.sent_kwargs) == 1
    assert fake_bot.sent_kwargs[0]["reply_markup"] is None


async def test_schedule_group_reminder_replaces_existing_job_instead_of_adding_a_new_one(
    _patch_reminders,
) -> None:
    from app.services.reminders import _job_id, schedule_group_reminder

    _sessionmaker, _fake_bot, scheduler = _patch_reminders

    first_run_at = datetime.utcnow() + timedelta(minutes=5)
    schedule_group_reminder(100, first_run_at)
    job = scheduler.get_job(_job_id(100))
    assert job is not None
    assert job.trigger.run_date.replace(tzinfo=None) == first_run_at

    # A second client message before the first reminder fires must move the
    # existing job, not create a second one for the same group.
    second_run_at = first_run_at + timedelta(minutes=10)
    schedule_group_reminder(100, second_run_at)

    assert len([j for j in scheduler.get_jobs() if j.id == _job_id(100)]) == 1
    job = scheduler.get_job(_job_id(100))
    assert job is not None
    assert job.trigger.run_date.replace(tzinfo=None) == second_run_at


async def test_cancel_group_reminder_removes_the_job(_patch_reminders) -> None:
    from app.services.reminders import _job_id, cancel_group_reminder, schedule_group_reminder

    _sessionmaker, _fake_bot, scheduler = _patch_reminders

    schedule_group_reminder(100, datetime.utcnow() + timedelta(minutes=5))
    assert scheduler.get_job(_job_id(100)) is not None

    cancel_group_reminder(100)

    assert scheduler.get_job(_job_id(100)) is None


async def test_cancel_group_reminder_is_a_no_op_when_nothing_was_scheduled(_patch_reminders) -> None:
    # A staff reply in a group that was never awaiting a response (or whose
    # reminder already fired) must not raise.
    from app.services.reminders import cancel_group_reminder

    cancel_group_reminder(999)


async def test_recover_pending_reminders_reschedules_from_last_message_at(_patch_reminders) -> None:
    from app.services.reminders import _job_id, recover_pending_reminders

    sessionmaker, _fake_bot, scheduler = _patch_reminders
    last_message_at = datetime.utcnow() - timedelta(minutes=2)
    await _seed_group_awaiting(sessionmaker, last_message_at=last_message_at)

    await recover_pending_reminders()

    job = scheduler.get_job(_job_id(100))
    assert job is not None
    expected_run_at = last_message_at + timedelta(minutes=settings.reminder_interval_minutes)
    assert job.trigger.run_date.replace(tzinfo=None) == expected_run_at


async def test_recover_pending_reminders_prefers_last_reminder_at_over_last_message_at(
    _patch_reminders,
) -> None:
    from app.services.reminders import _job_id, recover_pending_reminders

    sessionmaker, _fake_bot, scheduler = _patch_reminders
    last_message_at = datetime.utcnow() - timedelta(minutes=20)
    last_reminder_at = datetime.utcnow() - timedelta(minutes=3)
    await _seed_group_awaiting(sessionmaker, last_message_at=last_message_at, last_reminder_at=last_reminder_at)

    await recover_pending_reminders()

    job = scheduler.get_job(_job_id(100))
    assert job is not None
    expected_run_at = last_reminder_at + timedelta(minutes=settings.reminder_interval_minutes)
    assert job.trigger.run_date.replace(tzinfo=None) == expected_run_at


async def test_recover_pending_reminders_ignores_groups_not_awaiting_response(_patch_reminders) -> None:
    from app.services.reminders import _job_id, recover_pending_reminders

    sessionmaker, _fake_bot, scheduler = _patch_reminders
    async with sessionmaker() as session:
        await crud.create_group_record(session, 100, "Group A", created_by_userbot=True)
        await crud.get_or_create_user(session, 1, "manager", "Manager")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    await recover_pending_reminders()

    assert scheduler.get_job(_job_id(100)) is None
