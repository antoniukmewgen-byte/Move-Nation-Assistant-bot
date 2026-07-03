"""Lightweight fakes for aiogram Message/CallbackQuery/User objects.

Real aiogram types are pydantic models with many required fields; building
genuine instances for every handler test would be noisy and fragile, and
handlers only ever touch a small, well-defined slice of each object's
surface (`chat.id`, `from_user`, `text`, `answer()`, ...). These fakes
implement exactly that slice — same spirit as `FakeTelegramClient` in
tests/test_telethon_auth.py — and are duck-type compatible with the
handlers under test (which only need `isinstance` checks to pass for
`app.bot.guards`' `InaccessibleMessage` check, handled separately below).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class FakeUser:
    def __init__(self, id: int, username: str | None = None, full_name: str = "Test User") -> None:
        self.id = id
        self.username = username
        self.full_name = full_name


class FakeChat:
    def __init__(self, id: int, type: str = "private", title: str | None = None) -> None:
        self.id = id
        self.type = type
        self.title = title


class FakeMessage:
    """Stands in for `aiogram.types.Message` in handler tests."""

    def __init__(
        self,
        *,
        chat: FakeChat,
        from_user: FakeUser | None = None,
        text: str | None = None,
        date: datetime | None = None,
        reply_to_message: FakeMessage | None = None,
        bot: Any = None,
    ) -> None:
        self.chat = chat
        self.from_user = from_user
        self.text = text
        self.date = date
        self.reply_to_message = reply_to_message
        self.bot = bot
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.deleted = False

    async def answer(self, text: str, **_kwargs: Any) -> FakeMessage:
        self.answers.append(text)
        return self

    async def edit_text(self, text: str, **_kwargs: Any) -> FakeMessage:
        self.edits.append(text)
        return self

    async def delete(self) -> None:
        self.deleted = True


class FakeCallbackQuery:
    """Stands in for `aiogram.types.CallbackQuery` in handler tests."""

    def __init__(self, *, data: str, message: FakeMessage, from_user: FakeUser) -> None:
        self.data = data
        self.message = message
        self.from_user = from_user
        self.answered = False

    async def answer(self, *_args: Any, **_kwargs: Any) -> None:
        self.answered = True


def make_fsm_context(bot_id: int = 1, chat_id: int = 1, user_id: int = 1) -> FSMContext:
    """A real aiogram FSMContext backed by in-memory storage.

    aiogram's FSMContext is a thin, easily-instantiated wrapper around a
    storage backend — no need to fake it, unlike Message/CallbackQuery.
    """
    storage = MemoryStorage()
    key = StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)
