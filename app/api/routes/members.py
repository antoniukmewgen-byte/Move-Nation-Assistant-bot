import logging

from aiogram.exceptions import TelegramBadRequest
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import FloodWaitError

from app.api.deps import get_verified_user_id
from app.api.schemas import AddClientRequest, MemberOut, RemoveMemberRequest, TagRequest
from app.bot.bot_instance import bot
from app.db import crud
from app.db.session import async_session
from app.services import group_service
from app.services.crypto import decrypt_session
from app.userbot.actions import remove_member_from_group as remove_member_in_telegram

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
                user_id=m.user_id,
                name=m.user.full_name or m.user.username or str(m.user_id),
                tag=m.tag,
                pending=m.pending,
            )
            for m in members
        ]


@router.post("/tag")
async def tag_member(payload: TagRequest, user_id: int = Depends(get_verified_user_id)) -> dict:
    async with async_session() as session:
        await _require_membership(session, payload.group_id, user_id)
        await crud.add_member_tag(session, payload.group_id, payload.user_id, payload.tag)
        await session.commit()
    await group_service.sync_tag_to_telegram(payload.group_id, payload.user_id, payload.tag)
    return {"ok": True}


@router.post("/add-client")
async def add_client(payload: AddClientRequest, requested_by: int = Depends(get_verified_user_id)) -> dict:
    try:
        _client_user_id, invite_link = await group_service.add_client(
            requested_by, payload.group_id, payload.identifier
        )
    except group_service.GroupAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail="Немає доступу до цієї групи") from exc
    except group_service.NotConnectedError as exc:
        raise HTTPException(
            status_code=400, detail="Спочатку підключи Telegram-акаунт через /connect"
        ) from exc
    except FloodWaitError as exc:
        raise HTTPException(
            status_code=429, detail=f"Забагато запитів до Telegram, спробуй через {exc.seconds} с"
        ) from exc
    except group_service.AddClientFailedError as exc:
        raise HTTPException(status_code=502, detail="Не вдалося додати клієнта. Спробуй пізніше.") from exc
    except group_service.ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Користувача не знайдено") from exc

    return {"ok": True, "invite_link": invite_link}


async def _kick_via_assistant_bot(chat_id: int, user_id: int) -> bool:
    """Кікає учасника через нашого бота-асистента (Bot API), а не через
    особистий Telethon-акаунт того, хто ініціював видалення.

    Це головний шлях: наш бот отримує право ban_users одразу при створенні
    групи через застосунок (див. app/userbot/actions.py::_promote_assistant_bot
    / ASSISTANT_ADMIN_RIGHTS), тож на відміну від пересічного співробітника
    (лише творець групи в Telegram справді має права адміна) працює для
    будь-кого з команди, хто натисне «видалити» в Mini App — саме цього й не
    вистачало раніше, коли кік намагався виконати особистий акаунт того, хто
    ініціював дію, і мовчки не мав на це прав.

    ban_chat_member одразу прибирає людину з чату; наступний unban знімає
    заборону (замість залишати її забаненою назавжди), щоб вона могла
    приєднатися знову за новим запрошенням.
    """
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        return True
    except TelegramBadRequest:
        logger.warning(
            "Бот не зміг видалити user_id=%s з групи %s через Bot API "
            "(не адмін у цьому чаті чи учасника вже нема)",
            user_id,
            chat_id,
            exc_info=True,
        )
        return False


@router.post("/remove")
async def remove_member(payload: RemoveMemberRequest, requested_by: int = Depends(get_verified_user_id)) -> dict:
    async with async_session() as session:
        await _require_membership(session, payload.group_id, requested_by)
        encrypted_session = await crud.get_user_session(session, requested_by)

    removed_in_telegram = await _kick_via_assistant_bot(payload.group_id, payload.user_id)

    # Fallback для груп, де в бота з якоїсь причини немає прав адміна
    # (наприклад, зареєстрованих через /register, а не створених через
    # застосунок, — там _promote_assistant_bot ніколи не викликався): якщо
    # особистий акаунт того, хто ініціює видалення, підключений через
    # /connect, пробуємо кікнути ще й через нього. Обидва шляхи best-effort —
    # тег з нашої БД прибираємо нижче незалежно від їхнього результату.
    if not removed_in_telegram and encrypted_session is not None:
        try:
            removed_in_telegram = await remove_member_in_telegram(
                decrypt_session(encrypted_session), payload.group_id, payload.user_id
            )
        except FloodWaitError as exc:
            raise HTTPException(
                status_code=429, detail=f"Забагато запитів до Telegram, спробуй через {exc.seconds} с"
            ) from exc
        except Exception:
            logger.exception(
                "Не вдалося видалити user_id=%s з групи %s в Telegram від імені user_id=%s",
                payload.user_id,
                payload.group_id,
                requested_by,
            )

    async with async_session() as session:
        removed = await crud.remove_member(session, payload.group_id, payload.user_id)
        await session.commit()

    if not removed:
        raise HTTPException(status_code=404, detail="Учасника не знайдено")

    return {"ok": True, "removed_in_telegram": removed_in_telegram}
