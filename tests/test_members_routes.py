"""Tests for `/members/*` (app/api/routes/members.py).

Same conventions as `tests/test_groups_routes.py`: in-memory SQLite via a
patched `async_session`, and the real Telethon-touching call
(`add_client_to_group`) monkeypatched on the route module.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.api.routes import members as members_routes
from app.api.schemas import AddClientRequest, TagRequest
from app.db import crud
from app.db.models import CLIENT_TAG, Base

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(members_routes, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def _seed_group_with_member(sessionmaker, group_id: int = 100, user_id: int = 1) -> None:
    async with sessionmaker() as session:
        await crud.get_or_create_user(session, user_id, "alice", "Alice A.")
        await crud.create_group_record(session, group_id, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group_id, user_id, "Менеджер")
        await session.commit()


async def test_list_members_rejects_non_members(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.list_members(group_id=100, user_id=999)

    assert exc_info.value.status_code == 403


async def test_list_members_returns_members_for_a_group_member(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    result = await members_routes.list_members(group_id=100, user_id=1)

    assert len(result) == 1
    assert result[0].user_id == 1
    assert result[0].name == "Alice A."
    assert result[0].tag == "Менеджер"


async def test_tag_member_rejects_non_members(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.tag_member(TagRequest(group_id=100, user_id=1, tag="Тімлід"), user_id=999)

    assert exc_info.value.status_code == 403


async def test_tag_member_adds_a_new_tag_row(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    result = await members_routes.tag_member(TagRequest(group_id=100, user_id=1, tag="Тімлід"), user_id=1)

    assert result == {"ok": True}
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert {m.tag for m in members} == {"Менеджер", "Тімлід"}


async def test_add_client_rejects_non_members(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(
            AddClientRequest(group_id=100, identifier="@client"), requested_by=999
        )

    assert exc_info.value.status_code == 403


async def test_add_client_requires_a_connected_session(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(AddClientRequest(group_id=100, identifier="@client"), requested_by=1)

    assert exc_info.value.status_code == 400


async def test_add_client_success_persists_client_and_tag(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: "decrypted-" + s)

    async def fake_add_client_to_group(session_string, group_id, identifier):
        assert session_string == "decrypted-encrypted-session-string"
        assert group_id == 100
        assert identifier == "@newclient"
        return 42, "https://t.me/+invitelink"

    monkeypatch.setattr(members_routes, "add_client_to_group", fake_add_client_to_group)

    result = await members_routes.add_client(
        AddClientRequest(group_id=100, identifier="@newclient"), requested_by=1
    )

    assert result == {"ok": True, "invite_link": "https://t.me/+invitelink"}
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        client_rows = [m for m in members if m.tag == CLIENT_TAG]
        assert len(client_rows) == 1
        assert client_rows[0].user_id == 42


async def test_add_client_user_not_found_returns_404(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        return None, None

    monkeypatch.setattr(members_routes, "add_client_to_group", fake_add_client_to_group)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(AddClientRequest(group_id=100, identifier="@ghost"), requested_by=1)

    assert exc_info.value.status_code == 404


async def test_add_client_translates_flood_wait_to_429(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        raise FloodWaitError(request=None, capture=15)

    monkeypatch.setattr(members_routes, "add_client_to_group", fake_add_client_to_group)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(AddClientRequest(group_id=100, identifier="@client"), requested_by=1)

    assert exc_info.value.status_code == 429
