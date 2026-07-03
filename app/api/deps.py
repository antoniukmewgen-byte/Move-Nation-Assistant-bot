"""Shared FastAPI dependencies for the Mini App backend."""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException

from app.config import settings
from app.services.telegram_auth import InitDataValidationError, TelegramWebAppUser, validate_init_data

logger = logging.getLogger(__name__)


async def get_verified_webapp_user(
    x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
) -> TelegramWebAppUser:
    """Verify the Mini App's ``initData`` and return the authenticated Telegram user.

    Every mutating (and any group/member-scoped) endpoint depends on this
    instead of trusting a client-supplied ``user_id``/``requested_by`` field,
    which would otherwise let anyone impersonate any staff member.
    """
    try:
        return validate_init_data(
            x_telegram_init_data, settings.bot_token, max_age_seconds=settings.init_data_max_age_seconds
        )
    except InitDataValidationError as exc:
        logger.warning("Відхилено запит з невалідним initData: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def get_verified_user_id(user: TelegramWebAppUser = Depends(get_verified_webapp_user)) -> int:
    """Convenience dependency for handlers that only need the numeric user id."""
    return user.id
