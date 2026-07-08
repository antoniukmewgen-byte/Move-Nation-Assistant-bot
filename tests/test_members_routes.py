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
from app.api.schemas import AddClientRequest, RemoveMemberRequest, TagRequest
from app.db import crud
from app.db.models import CLIENT_TAG, Base
from app.services import group_service

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(members_routes, "async_session", sessionmaker)
    # add_client (below) delegates to group_service.add_client, which owns
    # its own async_session reference — must be patched too, or it would
    # fall through to the real (file-based) database.
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def _seed_group_with_member(sessionmaker, group_id: int = 100, user_id: int = 1) -> None:
    async with sessionmaker() as session:
        await crud.get_or_create_user(session, user_id, "alice", "Alice A.")
        await crud.create_group_record(session, group_id, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, group_id, user_id, "Менеджер")
        await session.commit()


@pytest.fixture(autouse=True)
def _patch_bot_kick(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    # _kick_via_assistant_bot (app/api/routes/members.py) talks to the real
    # Telegram Bot API through the module-level `bot` singleton — route
    # tests must never hit the network. Defaults to reporting failure so the
    # existing Telethon-fallback tests below exercise that path exactly as
    # before; test_remove_member_uses_bot_kick_and_skips_telethon_fallback
    # overrides this to report success.
    calls: list[tuple[int, int]] = []

    async def fake_kick_via_assistant_bot(chat_id: int, user_id: int) -> bool:
        calls.append((chat_id, user_id))
        return False

    monkeypatch.setattr(members_routes, "_kick_via_assistant_bot", fake_kick_via_assistant_bot)
    return calls


@pytest.fixture(autouse=True)
def _patch_sync_tag(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, str]]:
    # sync_tag_to_telegram (app/services/group_service.py) also talks to the
    # real Bot API (setChatMemberTag) through the same `bot` singleton — same
    # reasoning as _patch_bot_kick above, must be stubbed for every test here.
    calls: list[tuple[int, int, str]] = []

    async def fake_sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
        calls.append((chat_id, user_id, tag))

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)
    return calls


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

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: "decrypted-" + s)

    async def fake_add_client_to_group(session_string, group_id, identifier):
        assert session_string == "decrypted-encrypted-session-string"
        assert group_id == 100
        assert identifier == "@newclient"
        return 42, "New Client", "https://t.me/+invitelink"

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

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

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        return None, None, None

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(AddClientRequest(group_id=100, identifier="@ghost"), requested_by=1)

    assert exc_info.value.status_code == 404


async def test_add_client_translates_flood_wait_to_429(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        raise FloodWaitError(request=None, capture=15)

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.add_client(AddClientRequest(group_id=100, identifier="@client"), requested_by=1)

    assert exc_info.value.status_code == 429


async def test_remove_member_rejects_non_members(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=1), requested_by=999)

    assert exc_info.value.status_code == 403


async def test_remove_member_deletes_record_and_reports_telegram_result(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: "decrypted-" + s)

    async def fake_remove_member_in_telegram(session_string, group_id, user_id):
        assert session_string == "decrypted-encrypted-session-string"
        assert group_id == 100
        assert user_id == 2
        return True

    monkeypatch.setattr(members_routes, "remove_member_in_telegram", fake_remove_member_in_telegram)

    result = await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert result == {"ok": True, "removed_in_telegram": True}
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [m.user_id for m in members] == [1]


async def test_remove_member_still_removes_record_when_not_connected(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    result = await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert result == {"ok": True, "removed_in_telegram": False}


async def test_remove_member_still_removes_record_when_telegram_action_fails(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: s)

    async def fake_remove_member_in_telegram(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(members_routes, "remove_member_in_telegram", fake_remove_member_in_telegram)

    with caplog.at_level("ERROR"):
        result = await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert result == {"ok": True, "removed_in_telegram": False}
    assert "Не вдалося видалити" in caplog.text
    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [m.user_id for m in members] == [1]


async def test_remove_member_translates_flood_wait_to_429(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(members_routes, "decrypt_session", lambda s: s)

    async def fake_remove_member_in_telegram(*_args):
        raise FloodWaitError(request=None, capture=15)

    monkeypatch.setattr(members_routes, "remove_member_in_telegram", fake_remove_member_in_telegram)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert exc_info.value.status_code == 429


async def test_remove_member_returns_404_when_not_a_member(_patch_db) -> None:
    await _seed_group_with_member(_patch_db)

    with pytest.raises(HTTPException) as exc_info:
        await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=999), requested_by=1)

    assert exc_info.value.status_code == 404


async def test_remove_member_uses_bot_kick_and_skips_telethon_fallback(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_bot_kick: list[tuple[int, int]]
) -> None:
    # The assistant bot is an admin in every group created through the app
    # (see _promote_assistant_bot), so its own Bot API kick is the primary,
    # reliable path — it must not fall back to the requester's personal
    # Telethon session (which usually isn't a Telegram admin) when it works.
    await _seed_group_with_member(_patch_db)
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    async def fake_kick_via_assistant_bot(chat_id: int, user_id: int) -> bool:
        assert chat_id == 100
        assert user_id == 2
        return True

    monkeypatch.setattr(members_routes, "_kick_via_assistant_bot", fake_kick_via_assistant_bot)

    async def fail_if_called(*_args):
        raise AssertionError("Telethon fallback should not run when the bot kick already succeeded")

    monkeypatch.setattr(members_routes, "remove_member_in_telegram", fail_if_called)

    result = await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert result == {"ok": True, "removed_in_telegram": True}
