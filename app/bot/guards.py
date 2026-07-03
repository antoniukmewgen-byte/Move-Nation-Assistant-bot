"""Narrow-type helpers for aiogram objects.

aiogram types several always-present-in-practice fields as ``Optional``
(``Message.from_user``, ``Message.text``, ``CallbackQuery.message``) because
they genuinely can be absent for some update types (e.g. channel posts have
no ``from_user``). Our handlers are registered with filters that guarantee
these fields are present at runtime (private chat text messages, replies to
callback buttons, etc.), but mypy has no way to know that from the filter
alone. Centralizing the narrowing here keeps handlers readable and avoids
scattering ``# type: ignore`` comments or blind, unexplained asserts.

Raising :class:`UnexpectedUpdateError` (rather than silently returning) is
intentional: if one of these ever fires, it means our assumption about the
update shape was wrong, and we want that surfaced in logs/error tracking
rather than swallowed.
"""

from __future__ import annotations

from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from aiogram.types import User as TgUser


class UnexpectedUpdateError(RuntimeError):
    """Raised when an aiogram update is missing a field our filters should guarantee."""


def require_user(event: Message | CallbackQuery) -> TgUser:
    """Return the event's sender, raising if aiogram somehow gave us none."""
    user = event.from_user
    if user is None:
        raise UnexpectedUpdateError("Очікувався from_user, але його немає в оновленні")
    return user


def require_text(message: Message) -> str:
    """Return the message's text, raising if it has none."""
    if message.text is None:
        raise UnexpectedUpdateError("Очікувалось текстове повідомлення")
    return message.text


def require_editable_message(callback: CallbackQuery) -> Message:
    """Return the callback's message if it's still editable (not too old / not inaccessible)."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        raise UnexpectedUpdateError("Повідомлення callback-запиту недоступне для редагування")
    return message
