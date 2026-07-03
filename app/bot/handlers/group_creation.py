import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from telethon.errors import FloodWaitError

from app.bot.guards import require_text, require_user
from app.bot.states import GroupCreation
from app.db import crud
from app.db.session import async_session
from app.services.crypto import decrypt_session
from app.userbot.actions import create_group_with_team

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("newgroup"), F.chat.type == "private")
async def cmd_newgroup(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    async with async_session() as session:
        user = await crud.get_or_create_user(session, sender.id, sender.username, sender.full_name)
        await session.commit()
        if user.role is None:
            await message.answer("Спочатку обери посаду через /start.")
            return
        if user.session_string is None:
            await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
            return

    await state.set_state(GroupCreation.waiting_for_title)
    await message.answer("Введи назву нової групи з клієнтом:")


@router.message(GroupCreation.waiting_for_title)
async def process_group_title(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    title = require_text(message).strip()
    await state.clear()

    async with async_session() as session:
        encrypted_session = await crud.get_user_session(session, sender.id)

    if encrypted_session is None:
        await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
        return

    await message.answer(f"Створюю групу «{title}»…")

    async with async_session() as session:
        staff = await crud.get_staff_users(session)
        staff_ids = [(u.id, u.username, u.role) for u in staff]

    try:
        chat_id = await create_group_with_team(decrypt_session(encrypted_session), title, staff_ids)
    except FloodWaitError as exc:
        await message.answer(f"Забагато запитів до Telegram, спробуй через {exc.seconds} с.")
        return
    except Exception:
        logger.exception("Не вдалося створити групу «%s» для user_id=%s", title, sender.id)
        await message.answer("Не вдалося створити групу. Спробуй пізніше.")
        return

    async with async_session() as session:
        await crud.create_group_record(session, chat_id, title, created_by_userbot=True)
        for user_id, _username, role in staff_ids:
            if role is not None:
                await crud.add_member_tag(session, chat_id, user_id, role.value)
        await session.commit()

    await message.answer(f"Групу «{title}» створено та основний состав додано.")
