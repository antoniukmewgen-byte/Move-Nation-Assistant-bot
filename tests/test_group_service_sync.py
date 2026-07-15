"""Tests for `group_service.sync_group` — the /sync command's business logic.

Mocks `scan_group_members` (app/userbot/actions.py) the same way
test_bot_group_creation.py/test_bot_add_client.py mock the other
Telethon-touching functions — these tests never talk to real Telegram.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.db import crud
from app.db.models import CLIENT_TAG, Base, Role, User
from app.services import group_service

pytestmark = pytest.mark.asyncio


class FakeBot:
    def __init__(self) -> None:
        self.tagged: list[tuple[int, int, str]] = []

    async def set_chat_member_tag(self, chat_id: int, user_id: int, tag: str, **_kwargs: Any) -> None:
        self.tagged.append((chat_id, user_id, tag))


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_bot(monkeypatch: pytest.MonkeyPatch) -> FakeBot:
    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)
    return fake_bot


async def test_sync_group_requires_connected_account(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    with pytest.raises(group_service.NotConnectedError):
        await group_service.sync_group(1, 100, "Team Chat")


async def test_sync_group_reports_flood_wait(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        raise FloodWaitError(request=None, capture=15)

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    with pytest.raises(FloodWaitError):
        await group_service.sync_group(1, 100, "Team Chat")


async def test_sync_group_wraps_generic_scan_failure(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    with caplog.at_level("ERROR"), pytest.raises(group_service.GroupSyncFailedError):
        await group_service.sync_group(1, 100, "Team Chat")

    assert "Не вдалося просканувати" in caplog.text


async def test_sync_group_registers_an_unregistered_group(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    await group_service.sync_group(1, 100, "Team Chat")

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.title == "Team Chat"


async def test_sync_group_tags_staff_with_their_role_and_others_as_client(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_bot: FakeBot
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [
            (1, "alice", "Alice A.", False),
            (2, "bob", "Bob B.", False),
            (999, "somebot", "Some Bot", True),
        ]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    updated, removed = await group_service.sync_group(1, 100, "Team Chat")

    assert updated == 2
    assert removed == 0

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        tags = {m.user_id: m.tag for m in members}
        assert tags == {1: Role.MANAGER.value, 2: CLIENT_TAG}
        # Bots are never tagged/created as users.
        assert await session.get(User, 999) is None

    assert sorted(_patch_bot.tagged) == [(100, 1, Role.MANAGER.value), (100, 2, CLIENT_TAG)]


async def test_sync_group_is_idempotent_on_a_second_run_with_no_changes(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_bot: FakeBot
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    await group_service.sync_group(1, 100, "Team Chat")
    _patch_bot.tagged.clear()

    updated, removed = await group_service.sync_group(1, 100, "Team Chat")

    assert (updated, removed) == (0, 0)
    assert _patch_bot.tagged == []


async def test_sync_group_replaces_a_stale_tag_when_role_changed_since_last_sync(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_bot: FakeBot
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    # Role changed after the group tag was set, e.g. via Settings — /sync
    # must overwrite the stale group tag to match (design decision "1 А").
    async with _patch_db() as session:
        await crud.set_user_role(session, 1, Role.TEAMLEAD)
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    updated, removed = await group_service.sync_group(1, 100, "Team Chat")

    assert (updated, removed) == (1, 0)
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [m.tag for m in members] == [Role.TEAMLEAD.value]


async def test_sync_group_sets_synced_at_timestamp(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """`synced_at` is what makes the Mini App's silent-sync button hide for
    good (see Group.synced_at in app/db/models.py and GroupOut.needs_sync) —
    must be None before the first sync and set after any successful one,
    even a no-op run that changes nothing.
    """
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        group = await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()
        assert group.synced_at is None

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    await group_service.sync_group(1, 100, "Team Chat")

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.synced_at is not None


async def test_sync_group_removes_members_the_scan_no_longer_sees(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.get_or_create_user(session, 2, "gone", "Gone Guy")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    # Full reconciliation (design decision "2 А") — the scan no longer sees
    # user 2, who must be removed from group_members entirely.
    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    _updated, removed = await group_service.sync_group(1, 100, "Team Chat")

    assert removed == 1
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [m.user_id for m in members] == [1]
