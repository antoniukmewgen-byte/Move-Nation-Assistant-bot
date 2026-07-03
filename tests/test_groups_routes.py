"""Tests for `/groups` (app/api/routes/groups.py).

Same direct-coroutine-call convention as `tests/test_users_routes.py`:
`async_session` is swapped for an in-memory-SQLite-backed factory, and the
real Telethon-touching call (`create_group_with_team`) is monkeypatched on
the route module so these tests never hit the network.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.api.routes import groups as groups_routes
from app.api.schemas import GroupCreateRequest
from app.db import crud
from app.db.models import Base, Role

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(groups_routes, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_list_groups_returns_only_groups_the_user_belongs_to(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        g1 = await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.create_group_record(session, 200, "Group Two (not mine)", created_by_userbot=True)
        await crud.add_member_tag(session, g1.id, 1, "Менеджер")
        await session.commit()

    result = await groups_routes.list_groups(user_id=1)

    assert [g.title for g in result] == ["Group One"]
    assert result[0].awaiting_response is False


async def test_create_group_requires_a_connected_session(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.create_group(GroupCreateRequest(title="New Group"), requested_by=1)

    assert exc_info.value.status_code == 400


async def test_create_group_persists_group_and_tags_staff(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: "decrypted-" + s)

    async def fake_create_group_with_team(session_string, title, staff):
        assert session_string == "decrypted-encrypted-session-string"
        assert title == "New Group"
        assert staff == [(1, "alice", Role.MANAGER)]
        return 555

    monkeypatch.setattr(groups_routes, "create_group_with_team", fake_create_group_with_team)

    result = await groups_routes.create_group(GroupCreateRequest(title="New Group"), requested_by=1)

    assert result.id == 555
    assert result.title == "New Group"
    assert result.awaiting_response is False

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 555)
        assert [(m.user_id, m.tag) for m in members] == [(1, Role.MANAGER.value)]


async def test_create_group_translates_flood_wait_to_429(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise FloodWaitError(request=None, capture=30)

    monkeypatch.setattr(groups_routes, "create_group_with_team", fake_create_group_with_team)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.create_group(GroupCreateRequest(title="New Group"), requested_by=1)

    assert exc_info.value.status_code == 429


async def test_create_group_translates_unexpected_error_to_502(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(groups_routes, "create_group_with_team", fake_create_group_with_team)

    with caplog.at_level("ERROR"), pytest.raises(HTTPException) as exc_info:
        await groups_routes.create_group(GroupCreateRequest(title="New Group"), requested_by=1)

    assert exc_info.value.status_code == 502
    assert "Не вдалося створити групу" in caplog.text
