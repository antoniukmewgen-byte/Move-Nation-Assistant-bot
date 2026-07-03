from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.guards import require_user
from app.db import crud
from app.db.session import async_session

router = Router()


@router.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: Message) -> None:
    sender = require_user(message)
    async with async_session() as session:
        await crud.get_or_create_user(session, sender.id, sender.username, sender.full_name)
        await session.commit()

    await message.answer(
        "Вітаю в MoveNation Assistant!\n\n"
        "Реєстрація, вибір посади, підключення особистого акаунта та керування групами — "
        "усе тепер у міні-застосунку. Відкрий його кнопкою біля поля вводу (Menu/Open)."
    )


@router.message(Command("role"), F.chat.type == "private")
async def cmd_role(message: Message) -> None:
    await message.answer("Посаду можна змінити в міні-застосунку — розділ «Профіль».")
