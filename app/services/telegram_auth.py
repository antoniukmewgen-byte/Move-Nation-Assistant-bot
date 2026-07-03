"""Server-side validation of Telegram Mini App ``initData``.

The Mini App JS has access to ``window.Telegram.WebApp.initDataUnsafe``, but
that object is populated client-side and **must never be trusted** as a
source of identity — anyone can call the backend API directly with a forged
``user_id``. Telegram signs the real ``initData`` string with an HMAC derived
from the bot token; verifying that signature server-side is the only way to
know which Telegram user is actually making a request.

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataValidationError(Exception):
    """Raised when ``initData`` is missing, malformed, unsigned, or expired."""


@dataclass(frozen=True)
class TelegramWebAppUser:
    id: int
    username: str | None
    full_name: str | None


def _data_check_string(pairs: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86400) -> TelegramWebAppUser:
    """Validate a raw ``initData`` string and return the authenticated user.

    Raises :class:`InitDataValidationError` if the signature is missing,
    invalid, or the payload is older than ``max_age_seconds`` (default 24h,
    matching the lifetime Telegram itself uses for these tokens).
    """
    if not init_data:
        raise InitDataValidationError("Порожній initData")

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as exc:
        raise InitDataValidationError("Некоректний формат initData") from exc

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataValidationError("Відсутній hash в initData")

    secret_key = hmac.new(key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256).digest()
    computed_hash = hmac.new(
        key=secret_key, msg=_data_check_string(pairs).encode(), digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataValidationError("Підпис initData не збігається")

    auth_date = pairs.get("auth_date")
    if auth_date is None or not auth_date.isdigit():
        raise InitDataValidationError("Відсутня або некоректна auth_date")
    if time.time() - int(auth_date) > max_age_seconds:
        raise InitDataValidationError("initData застарів, відкрий Mini App заново")

    raw_user = pairs.get("user")
    if not raw_user:
        raise InitDataValidationError("Відсутні дані користувача в initData")

    try:
        user_payload = json.loads(raw_user)
        user_id = int(user_payload["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InitDataValidationError("Некоректні дані користувача в initData") from exc

    full_name = (
        " ".join(part for part in (user_payload.get("first_name"), user_payload.get("last_name")) if part)
        or None
    )

    return TelegramWebAppUser(id=user_id, username=user_payload.get("username"), full_name=full_name)
