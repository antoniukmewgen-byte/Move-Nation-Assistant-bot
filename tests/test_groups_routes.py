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
from app.services import group_service

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(groups_routes, "async_session", sessionmaker)
    # create_group (below) delegates to group_service.create_group, which
    # owns its own async_session reference — must be patched too, or it
    # would fall through to the real (file-based) database.
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
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
    # created_by_userbot=True (created through the app itself) never needs
    # the Mini App's silent-sync button.
    assert result[0].needs_sync is False


async def test_list_groups_flags_pre_existing_unsynced_group_as_needs_sync(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        g1 = await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, g1.id, 1, "Менеджер")
        await session.commit()

    result = await groups_routes.list_groups(user_id=1)

    assert result[0].needs_sync is True


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

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: "decrypted-" + s)

    # sync_tag_to_telegram talks to the real Bot API through the module-level
    # `bot` singleton — stub it so this test never hits the network (same
    # reasoning as create_group_with_team below).
    async def fake_sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    async def fake_create_group_with_team(session_string, title, staff):
        assert session_string == "decrypted-encrypted-session-string"
        assert title == "New Group"
        assert staff == [(1, "alice", Role.MANAGER)]
        return 555

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

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

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise FloodWaitError(request=None, capture=30)

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

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

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    with caplog.at_level("ERROR"), pytest.raises(HTTPException) as exc_info:
        await groups_routes.create_group(GroupCreateRequest(title="New Group"), requested_by=1)

    assert exc_info.value.status_code == 502
    assert "Не вдалося створити групу" in caplog.text


async def test_sync_group_route_requires_membership(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.sync_group(100, requested_by=1)

    assert exc_info.value.status_code == 403


async def test_sync_group_route_returns_404_for_unknown_group_as_403(_patch_db) -> None:
    # Same access-denied response as an unowned group — never leaks whether
    # a group_id exists at all to a caller who isn't a member of it.
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.sync_group(999, requested_by=1)

    assert exc_info.value.status_code == 403


async def test_sync_group_route_calls_service_and_returns_counts(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    async def fake_sync_group(actor_user_id, group_id, title):
        assert (actor_user_id, group_id, title) == (1, 100, "Group One")
        return 3, 1

    monkeypatch.setattr(group_service, "sync_group", fake_sync_group)

    result = await groups_routes.sync_group(100, requested_by=1)

    assert result.updated == 3
    assert result.removed == 1


async def test_sync_group_route_translates_not_connected_to_400(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.sync_group(100, requested_by=1)

    assert exc_info.value.status_code == 400


async def test_sync_group_route_translates_flood_wait_to_429(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    async def fake_sync_group(*_args):
        raise FloodWaitError(request=None, capture=30)

    monkeypatch.setattr(group_service, "sync_group", fake_sync_group)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.sync_group(100, requested_by=1)

    assert exc_info.value.status_code == 429


async def test_sync_group_route_translates_sync_failure_to_502(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    async def fake_sync_group(*_args):
        raise group_service.GroupSyncFailedError()

    monkeypatch.setattr(group_service, "sync_group", fake_sync_group)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.sync_group(100, requested_by=1)

    assert exc_info.value.status_code == 502


async def test_remove_group_requires_membership(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.remove_group(100, requested_by=1)

    assert exc_info.value.status_code == 403


async def test_remove_group_deletes_record_and_reports_telegram_result(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: "decrypted-" + s)

    async def fake_delete_group_in_telegram(session_string, group_id):
        assert session_string == "decrypted-encrypted-session-string"
        assert group_id == 100
        return True

    monkeypatch.setattr(groups_routes, "delete_group_in_telegram", fake_delete_group_in_telegram)

    result = await groups_routes.remove_group(100, requested_by=1)

    assert result == {"ok": True, "deleted_in_telegram": True}
    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_remove_group_still_removes_record_when_not_connected(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    result = await groups_routes.remove_group(100, requested_by=1)

    assert result == {"ok": True, "deleted_in_telegram": False}
    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_remove_group_still_removes_record_when_telegram_action_fails(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: s)

    async def fake_delete_group_in_telegram(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(groups_routes, "delete_group_in_telegram", fake_delete_group_in_telegram)

    with caplog.at_level("ERROR"):
        result = await groups_routes.remove_group(100, requested_by=1)

    assert result == {"ok": True, "deleted_in_telegram": False}
    assert "Не вдалося видалити групу" in caplog.text
    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_remove_group_translates_flood_wait_to_429(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        group = await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(groups_routes, "decrypt_session", lambda s: s)

    async def fake_delete_group_in_telegram(*_args):
        raise FloodWaitError(request=None, capture=30)

    monkeypatch.setattr(groups_routes, "delete_group_in_telegram", fake_delete_group_in_telegram)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.remove_group(100, requested_by=1)

    assert exc_info.value.status_code == 429


async def test_remove_group_returns_404_when_record_already_gone(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    # Simulates the record disappearing between the membership check and
    # the actual delete (e.g. a concurrent removal by another request) —
    # the membership check itself is patched to succeed so this exercises
    # the route's own "not found" branch rather than the 403 one.
    async def fake_user_is_group_member(*_args):
        return True

    async def fake_delete_group(*_args):
        return False

    monkeypatch.setattr(crud, "user_is_group_member", fake_user_is_group_member)
    monkeypatch.setattr(crud, "delete_group", fake_delete_group)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.remove_group(999, requested_by=1)

    assert exc_info.value.status_code == 404
