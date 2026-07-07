from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from telethon.errors import FloodWaitError

from app.bot.guards import require_text, require_user
from app.bot.states import GroupCreation
from app.db import crud
from app.db.session import async_session
from app.services import group_service

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

    await message.answer(f"Створюю групу «{title}»…")

    try:
        await group_service.create_group(sender.id, title)
    except group_service.NotConnectedError:
        await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
        return
    except FloodWaitError as exc:
        await message.answer(f"Забагато запитів до Telegram, спробуй через {exc.seconds} с.")
        return
    except group_service.GroupCreationFailedError:
        await message.answer("Не вдалося створити групу. Спробуй пізніше.")
        return

    await message.answer(f"Групу «{title}» створено та основний состав додано.")
