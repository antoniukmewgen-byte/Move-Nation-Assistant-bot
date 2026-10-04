"""Shared Telethon QR/password login flow.

Both the bot's `/connect` command and the Mini App's registration screen
drive this *same* in-process pending-client store, keyed by Telegram user
id. If they each kept their own state, a user who starts a QR login on one
surface and finishes 2FA on the other would find their session "lost" —
this module is the single owner of that state so either surface can carry
the flow through to completion.

Not designed for multi-process/Redis deployment — the whole app already
runs as a single asyncio process (see `app/main.py`), so an in-memory dict
is fine, same as the aiogram FSM `MemoryStorage` it lives alongside.

This is a hard requirement, not a scaling knob: running more than one
instance of the bot at once (e.g. `docker compose --scale bot=2`, or a
Kubernetes/Swarm deployment with `replicas > 1`) will silently split this
state across processes — a QR code generated on one instance and scanned
while polling another will report "session lost" with no other symptom. It
also breaks aiogram's `getUpdates` long-polling, which is a separate reason
the whole app must stay single-instance regardless of this module. See
README.md, "⚠️ Лише один інстанс", for the full explanation and what would
need to change (shared storage + webhooks) to lift this.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.custom.qrlogin import QRLogin

from app.config import settings
from app.db import crud
from app.db.session import async_session
from app.services import realtime
from app.services.crypto import encrypt_session

logger = logging.getLogger(__name__)

_pending_clients: dict[int, TelegramClient] = {}
_pending_qr: dict[int, QRLogin] = {}

# Guards the two dicts above against concurrent calls for the *same*
# user_id — e.g. a double-tap on "Показати QR" in the Mini App, or the bot
# and Mini App being used at once, could otherwise interleave two
# start_qr_auth calls and leak one of the two TelegramClient connections
# (the second call's `_pending_clients[user_id] = client` assignment would
# silently overwrite the first, which never gets `.disconnect()`-ed).
# `defaultdict` is safe here despite never being explicitly cleaned up per
# user: it only ever holds cheap `asyncio.Lock` objects, one per user_id
# that has ever attempted /connect, for the lifetime of this single-process
# app (see module docstring above).
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# How long each poll_qr_auth() call blocks waiting for a scan before
# returning "waiting" — short enough that a request/response-based poller
# (HTTP from the Mini App, or a bot background task) still feels responsive,
# long enough to not hammer Telegram with near-instant repeat calls.
_POLL_INTERVAL_SECONDS = 1.5


@dataclass(frozen=True)
class AuthStepResult:
    status: str  # "qr_pending" | "waiting" | "qr_renewed" | "password_required" | "connected" | "error"
    qr_url: str | None = None
    error: str | None = None


async def start_qr_auth(user_id: int, username: str | None, full_name: str | None) -> AuthStepResult:
    """Kick off a login attempt: generate a `tg://login` QR token for the user to scan."""
    async with _locks[user_id]:
        # Uses the lock-free helper, not the public cancel_auth() — asyncio.Lock
        # isn't reentrant, and we're already holding _locks[user_id] here.
        await _cancel_auth_locked(user_id)
        return await _start_qr_auth_locked(user_id, username, full_name)


async def _start_qr_auth_locked(user_id: int, username: str | None, full_name: str | None) -> AuthStepResult:
    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)

    try:
        await client.connect()
        qr_login = await client.qr_login()
    except FloodWaitError as exc:
        logger.warning("FloodWait при qr_login для user_id=%s: чекати %s с", user_id, exc.seconds)
        await client.disconnect()
        minutes = max(1, exc.seconds // 60)
        return AuthStepResult(
            status="error",
            error=f"Забагато спроб. Telegram тимчасово заблокував запити — спробуй через ~{minutes} хв.",
        )
    except Exception:
        logger.exception("Не вдалося згенерувати QR-код для user_id=%s", user_id)
        with contextlib.suppress(Exception):
            await client.disconnect()
        return AuthStepResult(status="error", error="Не вдалося згенерувати QR-код. Спробуй ще раз за хвилину.")

    logger.info("QR-код згенеровано для user_id=%s, діє до %s", user_id, qr_login.expires)

    # Ensure the user row exists before anything else touches it — mirrors the
    # bot's /start behaviour so this also works if a user reaches the Mini
    # App's connect screen before ever talking to the bot.
    async with async_session() as session:
        await crud.get_or_create_user(session, user_id, username, full_name)
        await session.commit()

    _pending_clients[user_id] = client
    _pending_qr[user_id] = qr_login
    return AuthStepResult(status="qr_pending", qr_url=qr_login.url)


async def poll_qr_auth(user_id: int) -> AuthStepResult:
    """Check whether the pending QR code has been scanned yet.

    Meant to be called repeatedly (short-polled from the Mini App, or looped
    in a bot background task) until it returns something other than
    "waiting". Each call blocks for up to `_POLL_INTERVAL_SECONDS` — that's
    the mechanism, not an accident: Telethon's own `QRLogin.wait()` is how we
    actually learn a scan happened, so this just wraps it with a short
    timeout instead of hanging the caller for the code's full ~30s lifetime.
    """
    async with _locks[user_id]:
        client = _pending_clients.get(user_id)
        qr_login = _pending_qr.get(user_id)
        if client is None or qr_login is None:
            return AuthStepResult(status="error", error="Сесія авторизації втрачена. Почни знову.")

        try:
            tl_user = await qr_login.wait(timeout=_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            if qr_login.expires <= datetime.now(timezone.utc):
                try:
                    await qr_login.recreate()
                except Exception:
                    logger.exception("Не вдалося оновити QR-код для user_id=%s", user_id)
                    await _cancel_auth_locked(user_id)
                    return AuthStepResult(status="error", error="Не вдалося оновити QR-код. Почни знову.")
                logger.info("QR-код оновлено для user_id=%s, діє до %s", user_id, qr_login.expires)
                return AuthStepResult(status="qr_renewed", qr_url=qr_login.url)
            return AuthStepResult(status="waiting")
        except SessionPasswordNeededError:
            return AuthStepResult(status="password_required")
        except Exception as exc:
            logger.exception("Помилка під час очікування скану QR для user_id=%s", user_id)
            await _cancel_auth_locked(user_id)
            return AuthStepResult(status="error", error=f"Не вдалося увійти: {exc}")

        return await _finish(user_id, client, getattr(tl_user, "phone", None))


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
    """Drop any live client waiting on a scan/password for this user, if any."""
    async with _locks[user_id]:
        await _cancel_auth_locked(user_id)


async def _cancel_auth_locked(user_id: int) -> None:
    client = _pending_clients.pop(user_id, None)
    _pending_qr.pop(user_id, None)
    if client is not None:
        await client.disconnect()


async def _finish(user_id: int, client: TelegramClient, phone: str | None = None) -> AuthStepResult:
    session_string = client.session.save()
    await client.disconnect()
    _pending_clients.pop(user_id, None)
    _pending_qr.pop(user_id, None)

    encrypted = encrypt_session(session_string)
    async with async_session() as session:
        await crud.set_user_session(session, user_id, encrypted)
        # Only persisted here, on a *successful* login — not when the QR code
        # is first generated — so an abandoned attempt never overwrites an
        # already-connected account's real number with something unverified.
        if phone:
            await crud.set_user_phone(session, user_id, phone)
        await session.commit()

    # is_connected just flipped to true — both the bot's /connect flow and
    # the Mini App's registration screen route through here (see module
    # docstring), so this one hook covers Profile's "Активно" pill either way.
    await realtime.notify_user(user_id, {"type": "profile_changed"})

    return AuthStepResult(status="connected")
