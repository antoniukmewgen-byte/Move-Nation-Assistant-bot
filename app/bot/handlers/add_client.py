import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon.errors import FloodWaitError

from app.bot.guards import require_editable_message, require_text, require_user
from app.bot.states import AddClient
from app.db import crud
from app.db.models import CLIENT_TAG
from app.db.session import async_session
from app.services.crypto import decrypt_session
from app.userbot.actions import add_client_to_group

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("add_client"), F.chat.type == "private")
async def cmd_add_client(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    async with async_session() as session:
        user = await crud.get_or_create_user(session, sender.id, sender.username, sender.full_name)
        await session.commit()
        if user.session_string is None:
            await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
            return
        groups = await crud.get_groups_for_user(session, sender.id)

    if not groups:
        await message.answer("Ти не прив'язаний до жодної групи.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.title, callback_data=f"addclient_group:{group.id}")
    builder.adjust(1)

    await state.set_state(AddClient.choosing_group)
    await message.answer("Обери групу:", reply_markup=builder.as_markup())


@router.callback_query(AddClient.choosing_group, F.data.startswith("addclient_group:"))
async def choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None  # guaranteed by the startswith filter above
    group_id = int(callback.data.split(":", 1)[1])
    await state.update_data(group_id=group_id)
    await state.set_state(AddClient.waiting_for_contact)
    message = require_editable_message(callback)
    await message.edit_text("Надішли username клієнта (наприклад, @client_username):")
    await callback.answer()


@router.message(AddClient.waiting_for_contact)
async def process_contact(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    data = await state.get_data()
    group_id = data["group_id"]
    identifier = require_text(message).strip()
    await state.clear()

    async with async_session() as session:
        encrypted_session = await crud.get_user_session(session, sender.id)

    if encrypted_session is None:
        await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
        return

    try:
        client_user_id, invite_link = await add_client_to_group(
            decrypt_session(encrypted_session), group_id, identifier
        )
    except FloodWaitError as exc:
        await message.answer(f"Забагато запитів до Telegram, спробуй через {exc.seconds} с.")
        return
    except Exception:
        logger.exception(
            "Не вдалося додати клієнта «%s» у групу %s для user_id=%s",
            identifier,
            group_id,
            sender.id,
        )
        await message.answer("Не вдалося додати клієнта. Спробуй пізніше.")
        return

    if client_user_id is None:
        await message.answer("Користувача не знайдено. Перевір username.")
        return

    async with async_session() as session:
        await crud.get_or_create_user(session, client_user_id, identifier.lstrip("@"), None)
        await crud.add_member_tag(session, group_id, client_user_id, CLIENT_TAG)
        await session.commit()

    if invite_link:
        await message.answer(f"Не вдалось додати напряму. Перешли клієнту посилання: {invite_link}")
    else:
        await message.answer("Клієнта додано в групу та позначено тегом «Клієнт».")
