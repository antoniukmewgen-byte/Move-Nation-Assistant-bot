import contextlib

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.guards import require_text, require_user
from app.bot.states import Connect
from app.services import telethon_auth

router = Router()


@router.message(Command("connect"), F.chat.type == "private")
async def cmd_connect(message: Message, state: FSMContext) -> None:
    await state.set_state(Connect.waiting_for_phone)
    await message.answer(
        "Щоб бот міг створювати групи та додавати клієнтів від твого імені, потрібно один раз "
        "авторизувати твій особистий Telegram-акаунт.\n\n"
        "⚠️ Сесія зберігається у зашифрованому вигляді і використовується лише для дій, "
        "які ти сам ініціюєш через бота.\n\n"
        "(Це саме можна зробити прямо в міні-застосунку, без /connect.)\n\n"
        "Введи номер телефону у форматі +380XXXXXXXXX:"
    )


@router.message(Connect.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    phone = require_text(message).strip()

    result = await telethon_auth.start_phone_auth(sender.id, sender.username, sender.full_name, phone)
    if result.status == "error":
        await message.answer(f"{result.error} Спробуй ще раз: /connect")
        await state.clear()
        return

    await state.set_state(Connect.waiting_for_code)
    await message.answer("Надійшов код підтвердження в Telegram. Введи його тут:")


@router.message(Connect.waiting_for_code)
async def process_code(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    code = require_text(message).strip()

    with contextlib.suppress(Exception):
        await message.delete()

    result = await telethon_auth.submit_code(sender.id, code)
    await _handle_result(message, state, result)


@router.message(Connect.waiting_for_password)
async def process_password(message: Message, state: FSMContext) -> None:
    sender = require_user(message)
    password = require_text(message).strip()

    with contextlib.suppress(Exception):
        await message.delete()

    result = await telethon_auth.submit_password(sender.id, password)
    await _handle_result(message, state, result)


async def _handle_result(message: Message, state: FSMContext, result: telethon_auth.AuthStepResult) -> None:
    if result.status == "connected":
        await state.clear()
        await message.answer(
            "Акаунт підключено! Тепер можеш створювати групи через /newgroup або міні-застосунок."
        )
    elif result.status == "password_required":
        await state.set_state(Connect.waiting_for_password)
        await message.answer("На акаунті ввімкнена двофакторна автентифікація. Введи пароль:")
    else:
        await state.clear()
        await message.answer(f"{result.error} Почни знову: /connect")
