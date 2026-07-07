"""Shared Telethon phone/code/password login flow.

Both the bot's `/connect` command and the Mini App's registration screen
drive this *same* in-process pending-client store, keyed by Telegram user
id. If they each kept their own state, a user who requests a code from one
surface and tries to enter it from the other would find their session
"lost" — this module is the single owner of that state so either surface
can carry the flow through to completion.

Not designed for multi-process/Redis deployment — the whole app already
runs as a single asyncio process (see `app/main.py`), so an in-memory dict
is fine, same as the aiogram FSM `MemoryStorage` it lives alongside.

This is a hard requirement, not a scaling knob: running more than one
instance of the bot at once (e.g. `docker compose --scale bot=2`, or a
Kubernetes/Swarm deployment with `replicas > 1`) will silently split this
state across processes — a code requested on one instance and submitted
on another will report "session lost" with no other symptom. It also
breaks aiogram's `getUpdates` long-polling, which is a separate reason the
whole app must stay single-instance regardless of this module. See
README.md, "⚠️ Лише один інстанс", for the full explanation and what
would need to change (shared storage + webhooks) to lift this.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app.config import settings
from app.db import crud
from app.db.session import async_session
from app.services.crypto import encrypt_session

logger = logging.getLogger(__name__)

_pending_clients: dict[int, TelegramClient] = {}
_pending_phone_data: dict[int, dict[str, str]] = {}

# Guards the two dicts above against concurrent calls for the *same*
# user_id — e.g. a double-tap on "Отримати код" in the Mini App, or the bot
# and Mini App being used at once, could otherwise interleave two
# start_phone_auth calls and leak one of the two TelegramClient connections
# (the second call's `_pending_clients[user_id] = client` assignment would
# silently overwrite the first, which never gets `.disconnect()`-ed).
# `defaultdict` is safe here despite never being explicitly cleaned up per
# user: it only ever holds cheap `asyncio.Lock` objects, one per user_id
# that has ever attempted /connect, for the lifetime of this single-process
# app (see module docstring above).
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@dataclass(frozen=True)
class AuthStepResult:
    status: str  # "code_sent" | "password_required" | "connected" | "error"
    error: str | None = None


async def start_phone_auth(
    user_id: int, username: str | None, full_name: str | None, phone: str
) -> AuthStepResult:
    """Kick off a login attempt: send the Telegram code to the user's own account."""
    async with _locks[user_id]:
        # Uses the lock-free helper, not the public cancel_auth() — asyncio.Lock
        # isn't reentrant, and we're already holding _locks[user_id] here.
        await _cancel_auth_locked(user_id)
        return await _start_phone_auth_locked(user_id, username, full_name, phone)


async def _start_phone_auth_locked(
    user_id: int, username: str | None, full_name: str | None, phone: str
) -> AuthStepResult:
    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)

    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        await client.disconnect()
        return AuthStepResult(status="error", error="Некоректний номер телефону.")
    except FloodWaitError as exc:
        logger.warning("FloodWait при send_code_request для user_id=%s: чекати %s с", user_id, exc.seconds)
        await client.disconnect()
        minutes = max(1, exc.seconds // 60)
        return AuthStepResult(
            status="error",
            error=f"Забагато спроб. Telegram тимчасово заблокував запити — спробуй через ~{minutes} хв.",
        )
    except Exception:
        logger.exception("Не вдалося надіслати код для user_id=%s", user_id)
        with contextlib.suppress(Exception):
            await client.disconnect()
        return AuthStepResult(status="error", error="Не вдалося відправити код. Спробуй ще раз за хвилину.")

    # Telegram can report "sent" while actually delivering via very different
    # channels (existing-session push vs SMS vs voice call) — or silently
    # throttling delivery after repeated requests for the same number without
    # raising FloodWaitError. Logging exactly what Telegram claims here is the
    # only way to tell those apart from the outside.
    sent_type = getattr(sent, "type", None)
    sent_next_type = getattr(sent, "next_type", None)
    logger.info(
        "Код надіслано для user_id=%s: type=%s next_type=%s timeout=%s",
        user_id,
        type(sent_type).__name__ if sent_type else None,
        type(sent_next_type).__name__ if sent_next_type else None,
        getattr(sent, "timeout", None),
    )

    # Ensure the user row exists before anything else touches it — mirrors the
    # bot's /start behaviour so this also works if a user reaches the Mini
    # App's connect screen before ever talking to the bot.
    async with async_session() as session:
        await crud.get_or_create_user(session, user_id, username, full_name)
        await session.commit()

    _pending_clients[user_id] = client
    _pending_phone_data[user_id] = {"phone": phone, "phone_code_hash": sent.phone_code_hash}
    return AuthStepResult(status="code_sent")


async def submit_code(user_id: int, code: str) -> AuthStepResult:
    async with _locks[user_id]:
        client = _pending_clients.get(user_id)
        data = _pending_phone_data.get(user_id)
        if client is None or data is None:
            return AuthStepResult(status="error", error="Сесія авторизації втрачена. Почни знову.")

        try:
            await client.sign_in(phone=data["phone"], code=code, phone_code_hash=data["phone_code_hash"])
        except SessionPasswordNeededError:
            return AuthStepResult(status="password_required")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await _cancel_auth_locked(user_id)
            return AuthStepResult(status="error", error="Код невірний або застарів. Почни знову.")
        except Exception as exc:
            logger.exception("Не вдалося підтвердити код для user_id=%s", user_id)
            await _cancel_auth_locked(user_id)
            return AuthStepResult(status="error", error=f"Не вдалося увійти: {exc}")

        return await _finish(user_id, client)


async def submit_password(user_id: int, password: str) -> AuthStepResult:
    async with _locks[user_id]:
        client = _pending_clients.get(user_id)
        if client is None:
            return AuthStepResult(status="error", error="Сесія авторизації втрачена. Почни знову.")

        try:
            await client.sign_in(password=password)
        except Exception as exc:
            logger.warning("Не вдалося завершити 2FA для user_id=%s: %s", user_id, exc)
            await _cancel_auth_locked(user_id)
            return AuthStepResult(status="error", error=f"Не вдалося увійти: {exc}")

        return await _finish(user_id, client)


async def cancel_auth(user_id: int) -> None:
    """Drop any live client waiting on a code/password for this user, if any."""
    async with _locks[user_id]:
        await _cancel_auth_locked(user_id)


async def _cancel_auth_locked(user_id: int) -> None:
    client = _pending_clients.pop(user_id, None)
    _pending_phone_data.pop(user_id, None)
    if client is not None:
        await client.disconnect()


async def _finish(user_id: int, client: TelegramClient) -> AuthStepResult:
    session_string = client.session.save()
    await client.disconnect()
    _pending_clients.pop(user_id, None)
    _pending_phone_data.pop(user_id, None)

    encrypted = encrypt_session(session_string)
    async with async_session() as session:
        await crud.set_user_session(session, user_id, encrypted)
        await session.commit()

    return AuthStepResult(status="connected")
