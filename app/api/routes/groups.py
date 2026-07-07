import logging

from fastapi import APIRouter, Depends, HTTPException
from telethon.errors import FloodWaitError

from app.api.deps import get_verified_user_id
from app.api.schemas import GroupCreateRequest, GroupOut
from app.db import crud
from app.db.session import async_session
from app.services.crypto import decrypt_session
from app.userbot.actions import create_group_with_team
from app.userbot.actions import delete_group as delete_group_in_telegram

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
async def list_groups(user_id: int = Depends(get_verified_user_id)) -> list[GroupOut]:
    async with async_session() as session:
        groups = await crud.get_groups_for_user(session, user_id)
        return [
            GroupOut(id=g.id, title=g.title, status=g.status.value, awaiting_response=g.awaiting_response)
            for g in groups
        ]


@router.post("", response_model=GroupOut)
async def create_group(
    payload: GroupCreateRequest, requested_by: int = Depends(get_verified_user_id)
) -> GroupOut:
    async with async_session() as session:
        encrypted_session = await crud.get_user_session(session, requested_by)
        if encrypted_session is None:
            raise HTTPException(status_code=400, detail="Спочатку підключи Telegram-акаунт через /connect")
        staff = await crud.get_staff_users(session)
        staff_ids = [(u.id, u.username, u.role) for u in staff]

    try:
        chat_id = await create_group_with_team(decrypt_session(encrypted_session), payload.title, staff_ids)
    except FloodWaitError as exc:
        raise HTTPException(
            status_code=429, detail=f"Забагато запитів до Telegram, спробуй через {exc.seconds} с"
        ) from exc
    except Exception as exc:
        logger.exception("Не вдалося створити групу «%s» від імені user_id=%s", payload.title, requested_by)
        raise HTTPException(status_code=502, detail="Не вдалося створити групу. Спробуй пізніше.") from exc

    async with async_session() as session:
        group = await crud.create_group_record(session, chat_id, payload.title, created_by_userbot=True)
        for user_id, _username, role in staff_ids:
            if role is not None:
                await crud.add_member_tag(session, chat_id, user_id, role.value)
        await session.commit()
        return GroupOut(id=group.id, title=group.title, status=group.status.value, awaiting_response=False)


@router.delete("/{group_id}")
async def remove_group(group_id: int, requested_by: int = Depends(get_verified_user_id)) -> dict:
    async with async_session() as session:
        if not await crud.user_is_group_member(session, group_id, requested_by):
            raise HTTPException(status_code=403, detail="Немає доступу до цієї групи")
        encrypted_session = await crud.get_user_session(session, requested_by)

    # Видалення самого чату в Telegram — це "best effort": якщо в акаунта
    # користувача, що ініціює видалення, немає прав творця/адміна (або він
    # ще не підключив Telegram-акаунт через /connect), просто пропускаємо
    # цей крок і однаково прибираємо групу з нашої БД нижче — інакше
    # "завислі" чи вже недоступні групи неможливо було б прибрати з застосунку.
    deleted_in_telegram = False
    if encrypted_session is not None:
        try:
            deleted_in_telegram = await delete_group_in_telegram(decrypt_session(encrypted_session), group_id)
        except FloodWaitError as exc:
            raise HTTPException(
                status_code=429, detail=f"Забагато запитів до Telegram, спробуй через {exc.seconds} с"
            ) from exc
        except Exception:
            logger.exception(
                "Не вдалося видалити групу %s в Telegram від імені user_id=%s", group_id, requested_by
            )

    async with async_session() as session:
        removed = await crud.delete_group(session, group_id)
        await session.commit()

    if not removed:
        raise HTTPException(status_code=404, detail="Групу не знайдено")

    return {"ok": True, "deleted_in_telegram": deleted_in_telegram}
