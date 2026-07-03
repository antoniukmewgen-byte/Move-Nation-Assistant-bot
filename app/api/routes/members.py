import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import FloodWaitError

from app.api.deps import get_verified_user_id
from app.api.schemas import AddClientRequest, MemberOut, TagRequest
from app.db import crud
from app.db.models import CLIENT_TAG
from app.db.session import async_session
from app.services.crypto import decrypt_session
from app.userbot.actions import add_client_to_group

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/members", tags=["members"])


async def _require_membership(session: AsyncSession, group_id: int, user_id: int) -> None:
    if not await crud.user_is_group_member(session, group_id, user_id):
        raise HTTPException(status_code=403, detail="Немає доступу до цієї групи")


@router.get("", response_model=list[MemberOut])
async def list_members(group_id: int, user_id: int = Depends(get_verified_user_id)) -> list[MemberOut]:
    async with async_session() as session:
        await _require_membership(session, group_id, user_id)
        members = await crud.get_group_members(session, group_id)
        return [
            MemberOut(
                user_id=m.user_id, name=m.user.full_name or m.user.username or str(m.user_id), tag=m.tag
            )
            for m in members
        ]


@router.post("/tag")
async def tag_member(payload: TagRequest, user_id: int = Depends(get_verified_user_id)) -> dict:
    async with async_session() as session:
        await _require_membership(session, payload.group_id, user_id)
        await crud.add_member_tag(session, payload.group_id, payload.user_id, payload.tag)
        await session.commit()
    return {"ok": True}


@router.post("/add-client")
async def add_client(payload: AddClientRequest, requested_by: int = Depends(get_verified_user_id)) -> dict:
    async with async_session() as session:
        await _require_membership(session, payload.group_id, requested_by)
        encrypted_session = await crud.get_user_session(session, requested_by)

    if encrypted_session is None:
        raise HTTPException(status_code=400, detail="Спочатку підключи Telegram-акаунт через /connect")

    try:
        client_user_id, invite_link = await add_client_to_group(
            decrypt_session(encrypted_session), payload.group_id, payload.identifier
        )
    except FloodWaitError as exc:
        raise HTTPException(
            status_code=429, detail=f"Забагато запитів до Telegram, спробуй через {exc.seconds} с"
        ) from exc
    except Exception as exc:
        logger.exception(
            "Не вдалося додати клієнта «%s» у групу %s від імені user_id=%s",
            payload.identifier,
            payload.group_id,
            requested_by,
        )
        raise HTTPException(status_code=502, detail="Не вдалося додати клієнта. Спробуй пізніше.") from exc

    if client_user_id is None:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")

    async with async_session() as session:
        await crud.get_or_create_user(session, client_user_id, payload.identifier.lstrip("@"), None)
        await crud.add_member_tag(session, payload.group_id, client_user_id, CLIENT_TAG)
        await session.commit()

    return {"ok": True, "invite_link": invite_link}
