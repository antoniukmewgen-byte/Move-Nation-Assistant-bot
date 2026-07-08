from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import crud
from app.db.session import async_session

router = Router()


@router.message(Command("team"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_team(message: Message) -> None:
    async with async_session() as session:
        group = await crud.get_group(session, message.chat.id)
        if group is None:
            await message.answer("Ця група ще не зареєстрована в системі. Використай /sync.")
            return
        members = await crud.get_group_members(session, message.chat.id)

    if not members:
        await message.answer("У цій групі ще немає тегованих учасників.")
        return

    lines = []
    for m in members:
        name = m.user.full_name or m.user.username or str(m.user_id)
        suffix = " (очікує приєднання)" if m.pending else ""
        lines.append(f"• {name} — {m.tag}{suffix}")

    await message.answer("\n".join(lines))
