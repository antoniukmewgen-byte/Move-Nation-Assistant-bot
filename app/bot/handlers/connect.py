import asyncio
import contextlib
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

from app.bot.guards import require_text, require_user
from app.bot.states import Connect
from app.services import telethon_auth
from app.services.qr_image import render_qr_png

logger = logging.getLogger(__name__)

router = Router()

# Caps how long a single /connect's background poll loop keeps a Telethon
# client connected waiting for a scan that never comes (user closed the chat,
# got distracted, etc.) — without this, an abandoned QR code would hold the
# connection open indefinitely, same class of leak the module docstring in
# telethon_auth.py warns about for concurrent start_qr_auth calls.
_MAX_POLL_SECONDS = 10 * 60

# One background poller per user_id — keyed here (not inside telethon_auth,
# which only tracks the Telethon-level session) so a second /connect can
# cancel the previous loop before it races the new one over editing the QR
# message or claiming a scan that belongs to the new code.
_poll_tasks: dict[int, asyncio.Task] = {}


def _cancel_poll_task(user_id: int) -> None:
    task = _poll_tasks.pop(user_id, None)
    if task is not None and not task.done():
        task.cancel()


def _open_in_telegram_markup(url: str) -> InlineKeyboardMarkup:
    # tg:// deep links are explicitly supported by Telegram's inline button
    # `url` field — lets a user on the *same* device confirm the login with a
    # tap instead of scanning the QR with a second one.
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Відкрити в Telegram", url=url)]])


@router.message(Command("connect"), F.chat.type == "private")
async def cmd_connect(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    _cancel_poll_task(sender.id)

    result = await telethon_auth.start_qr_auth(sender.id, sender.username, sender.full_name)
    if result.status == "error" or not result.qr_url:
        await message.answer(f"{result.error} Спробуй ще раз: /connect")
        await state.clear()
        return

    sent = await message.answer_photo(
        BufferedInputFile(render_qr_png(result.qr_url), filename="qr.png"),
        caption=(
            "Щоб бот міг створювати групи та додавати клієнтів від твого імені, відскануй цей QR-код "
            "в іншому пристрої з Telegram (Налаштування → Пристрої → Підключити пристрій) "
            "або натисни кнопку нижче, якщо відкриваєш з того ж телефону.\n\n"
            "⚠️ Сесія зберігається у зашифрованому вигляді і використовується лише для дій, "
            "які ти сам ініціюєш через бота.\n\n"
            "(Це саме можна зробити прямо в міні-застосунку, без /connect.)"
        ),
        reply_markup=_open_in_telegram_markup(result.qr_url),
    )

    await state.set_state(Connect.waiting_for_qr_scan)
    _poll_tasks[sender.id] = asyncio.create_task(_poll_loop(sender.id, state, sent))


@router.message(Connect.waiting_for_qr_scan)
async def process_qr_wait(message: Message, state: FSMContext) -> None:
    await message.answer("Скануй QR-код вище (або натисни кнопку \"Відкрити в Telegram\"), щоб завершити підключення.")


@router.message(Connect.waiting_for_password)
async def process_password(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    password = require_text(message).strip()

    with contextlib.suppress(Exception):
        await message.delete()

    result = await telethon_auth.submit_password(sender.id, password)
    if result.status == "connected":
        await state.clear()
        await message.answer("Акаунт підключено! Тепер можеш створювати групи через /newgroup або міні-застосунок.")
    else:
        await state.clear()
        await message.answer(f"{result.error} Почни знову: /connect")


async def _poll_loop(user_id: int, state: FSMContext, sent: Message) -> None:
    deadline = time.monotonic() + _MAX_POLL_SECONDS
    try:
        while time.monotonic() < deadline:
            result = await telethon_auth.poll_qr_auth(user_id)

            if result.status == "waiting":
                continue

            if result.status == "qr_renewed" and result.qr_url:
                with contextlib.suppress(Exception):
                    await sent.edit_media(
                        InputMediaPhoto(media=BufferedInputFile(render_qr_png(result.qr_url), filename="qr.png"))
                    )
                continue

            if result.status == "password_required":
                await state.set_state(Connect.waiting_for_password)
                await sent.answer("На акаунті ввімкнена двофакторна автентифікація. Введи пароль:")
                return

            if result.status == "connected":
                await state.clear()
                with contextlib.suppress(Exception):
                    await sent.edit_caption(caption="Акаунт підключено!")
                await sent.answer(
                    "Тепер можеш створювати групи через /newgroup або міні-застосунок."
                )
                return

            # status == "error"
            await state.clear()
            await sent.answer(f"{result.error} Почни знову: /connect")
            return

        # Timed out without a scan.
        await telethon_auth.cancel_auth(user_id)
        await state.clear()
        await sent.answer("Час очікування сканування QR-коду вийшов. Почни знову: /connect")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Помилка в циклі очікування QR для user_id=%s", user_id)
    finally:
        _poll_tasks.pop(user_id, None)
