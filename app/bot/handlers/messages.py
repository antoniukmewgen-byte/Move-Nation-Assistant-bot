from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated, Message

from app.bot.guards import require_text, require_user
from app.db import crud
from app.db.models import GroupStatus
from app.db.session import async_session
from app.services import group_service

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_bot_added_to_group(event: ChatMemberUpdated) -> None:
    # JOIN_TRANSITION (not-a-member -> member) fires only when the bot
    # actually joins the chat. aiogram's IS_MEMBER, by contrast, matches
    # any update where the *new* status is member-like — including the
    # member -> administrator promotion our own userbot flow triggers
    # right after creating a group (see
    # app/userbot/actions.py::_promote_assistant_bot). Filtering on
    # IS_MEMBER made that promotion re-trigger this handler and send the
    # welcome message a second time.
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        group = await crud.get_group(session, event.chat.id)
        if group is None:
            bot = event.bot
            if bot is None:
                return
            try:
                await bot.send_message(
                    event.chat.id,
                    "Я підключився до цієї групи. Щоб я міг стежити за повідомленнями клієнтів, "
                    "признач мене адміністратором і познач учасників тегами через /register та /tag.",
                )
            except TelegramMigrateToChat:
                # This chat_id belonged to a basic group that got migrated to a
                # supergroup between Telegram queuing this "bot was added"
                # update and us processing it — happens when our own
                # group-creation flow invites the bot at chat-creation time
                # and then immediately migrates it (see
                # app/userbot/actions.py::create_group_with_team). The real
                # group record (under the post-migration chat_id) is already
                # created by that flow, so there is nothing to register for
                # this stale, now-defunct chat_id.
                return
            except TelegramBadRequest as exc:
                # Same root cause as the TelegramMigrateToChat case above —
                # a stale, now-defunct chat_id after migration — but
                # depending on timing Telegram's Bot API reports it as a
                # plain "PEER_ID_INVALID" bad request instead of the
                # structured migrate-to-chat error. Any other bad-request
                # reason is a real problem and should still surface.
                if "PEER_ID_INVALID" not in exc.message:
                    raise
                return
            await crud.create_group_record(
                session, event.chat.id, event.chat.title or "Без назви", created_by_userbot=False
            )
            await session.commit()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_bot_removed_from_group(event: ChatMemberUpdated) -> None:
    """Прибирає групу з нашої БД, коли бот перестає бути її учасником.

    LEAVE_TRANSITION (member-like -> left/kicked) покриває і явний кік
    бота з групи, і повне видалення самої групи в Telegram — в обох
    випадках Telegram надсилає боту цю саму транзицію для chat_id групи.
    Без бота в чаті ми однаково більше не можемо стежити за повідомленнями
    чи нагадуваннями, тож застарілий запис сенсу тримати немає (а якщо
    Telegram колись перевикористає цей chat_id для нової групи — краще,
    щоб старого запису вже не було).
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        await crud.delete_group(session, event.chat.id)
        await session.commit()


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_joined_group(event: ChatMemberUpdated) -> None:
    """Знімає прапорець `pending` із клієнта, коли він дійсно приєднався за лінком.

    `group_service.add_client` (app/services/group_service.py) ставить
    `pending=True`, коли прямий додаток клієнта не вдався через приватність
    і йому лишилось надіслати лінк-запрошення — до цього моменту він ще не
    в чаті, тож Mini App/команда /register не повинні показувати його як
    повноцінного учасника. `router.chat_member` (на відміну від
    `router.my_chat_member` вище) якраз і стежить за змінами статусу *інших*
    учасників чату, а не самого бота, і спрацьовує, коли Telegram підтверджує
    фактичне приєднання.
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        tag = await crud.clear_pending(session, event.chat.id, event.new_chat_member.user.id)
        if tag is not None:
            await session.commit()

    if tag is not None:
        await group_service.sync_tag_to_telegram(event.chat.id, event.new_chat_member.user.id, tag)


@router.message(Command("register"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_register(message: Message) -> None:
    async with async_session() as session:
        group = await crud.get_group(session, message.chat.id)
        if group is None:
            await crud.create_group_record(
                session, message.chat.id, message.chat.title or "Без назви", created_by_userbot=False
            )
        else:
            group.status = GroupStatus.ACTIVE
        await session.commit()

    await message.answer(
        "Групу зареєстровано. Познач учасників тегами командою /tag "
        "у відповідь на повідомлення учасника, наприклад: /tag Менеджер"
    )


@router.message(Command("tag"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_tag(message: Message) -> None:
    if not message.reply_to_message:
        await message.answer(
            "Використовуй цю команду у відповідь на повідомлення учасника, якого хочеш позначити."
        )
        return

    text = require_text(message)
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Вкажи тег, наприклад: /tag Менеджер")
        return
    tag_value = parts[1].strip()

    target = require_user(message.reply_to_message)
    async with async_session() as session:
        await crud.get_or_create_user(session, target.id, target.username, target.full_name)
        await crud.add_member_tag(session, message.chat.id, target.id, tag_value)
        await session.commit()

    await group_service.sync_tag_to_telegram(message.chat.id, target.id, tag_value)
    await message.answer(f"{target.full_name} позначено тегом «{tag_value}».")


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text, ~F.text.startswith("/"))
async def track_group_message(message: Message) -> None:
    async with async_session() as session:
        group = await crud.get_group(session, message.chat.id)
        if group is None:
            return

        sender = require_user(message)
        sender_is_client = await crud.is_client(session, message.chat.id, sender.id)

        if sender_is_client:
            await crud.mark_awaiting_response(
                session, message.chat.id, sender.id, message.date or datetime.utcnow()
            )
        else:
            await crud.clear_awaiting_response(session, message.chat.id)

        await session.commit()
