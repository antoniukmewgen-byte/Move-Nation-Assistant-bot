"""In-process WebSocket registry for pushing "something changed" signals to the Mini App.

Not designed for multi-process/Redis deployment — same single-instance
assumption as `app/services/telethon_auth.py` and
`app/services/group_creation_registry.py` (see their docstrings, and
README.md "⚠️ Лише один інстанс"): the whole app is one asyncio process, so a
plain in-memory `dict[int, set[WebSocket]]` is enough.

Deliberately an *invalidation* channel, not a data channel: every event below
is a small `{"type": ..., ...}` signal telling the Mini App "go re-fetch X",
never the actual serialized data. The client already has REST endpoints
(`/groups`, `/members`, `/users/me`) that own serialization *and*
authorization-scoped queries — duplicating that here would mean two places
that must independently stay in sync with the DB schema and, worse, two
places that must independently stay correct about who's allowed to see what.
A stale/extra GET after a false-positive push is a harmless no-op; a
broadcast path that ever drifts from the REST path's auth checks is a data
leak. See the module docstring precedent in group_service.py for the same
reasoning applied to a different duplication risk.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

from app.db import crud
from app.db.session import async_session

logger = logging.getLogger(__name__)

_connections: dict[int, set[WebSocket]] = {}


def register(user_id: int, websocket: WebSocket) -> None:
    """Adds `websocket` to the set of live connections for `user_id`.

    One user can have more than one open socket at once (e.g. the Mini App
    open in two tabs, or a reconnect racing the old socket's close) — all of
    them receive every event for that user.
    """
    _connections.setdefault(user_id, set()).add(websocket)


def unregister(user_id: int, websocket: WebSocket) -> None:
    """Removes `websocket`, and drops the now-empty set entry if it was the last one."""
    sockets = _connections.get(user_id)
    if not sockets:
        return
    sockets.discard(websocket)
    if not sockets:
        _connections.pop(user_id, None)


async def notify_user(user_id: int, event: dict[str, Any]) -> None:
    """Pushes `event` to every currently-open socket for `user_id`, if any.

    Best-effort and silent: the caller's own DB write already committed
    before this is ever called (see every call site in group_service.py /
    the API routes / the bot handlers) — a user with no open socket, or a
    socket that dies mid-send, is an entirely normal, harmless case, not a
    failure of the action that triggered the notification.
    """
    for websocket in list(_connections.get(user_id, ())):
        try:
            await websocket.send_json(event)
        except Exception:
            # The socket is already broken (client navigated away, network
            # dropped, etc.) — the endpoint's own receive loop will notice
            # the disconnect and call unregister() too, but doing it here as
            # well means a *second* event arriving in the same tick doesn't
            # keep retrying a socket we already know is dead.
            unregister(user_id, websocket)


async def notify_users(user_ids: Iterable[int], event: dict[str, Any]) -> None:
    for user_id in user_ids:
        await notify_user(user_id, event)


async def notify_group(group_id: int, event: dict[str, Any]) -> None:
    """Pushes `event` to every user currently tagged as a member of `group_id`.

    Resolves the member list itself (rather than making every call site do
    it) so callers can't accidentally notify a stale or partial member list.
    Must be called *before* the group itself is deleted (see
    app/api/routes/groups.py::remove_group and
    app/bot/handlers/messages.py::on_bot_removed_from_group, which fetch the
    member ids first and call `notify_users` directly instead of this
    helper, precisely because by the time they'd want to notify, the group
    row is already gone and `crud.get_group_members` would return nothing).
    """
    async with async_session() as session:
        members = await crud.get_group_members(session, group_id)
    await notify_users((m.user_id for m in members), event)
