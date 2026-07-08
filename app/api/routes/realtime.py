"""WebSocket endpoint for real-time Mini App invalidation pushes.

Auth handshake deliberately does *not* use a query string or header:
`initData` is sensitive HMAC-signed data (see
app/services/telegram_auth.py::validate_init_data) that shouldn't end up in
URLs — proxies/load balancers commonly log full request URLs including query
strings, and browser history/referrer headers can leak them too. It also
can't be a header, since the browser `WebSocket` constructor has no API for
setting arbitrary headers. So instead: the client opens the socket, then
sends `initData` as the very first *message* — this endpoint validates it
with the exact same `validate_init_data` every REST endpoint already uses
(see app/api/deps.py::get_verified_webapp_user) before registering the
connection in app/services/realtime.py, and closes the socket if it's
invalid.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.services import realtime
from app.services.telegram_auth import InitDataValidationError, validate_init_data

logger = logging.getLogger(__name__)

router = APIRouter()

# Arbitrary WebSocket close code in the private-use range (4000-4999) —
# there's no standard code for "auth rejected", so this mirrors HTTP 401 the
# same way the REST dependency does.
_UNAUTHORIZED_CLOSE_CODE = 4401


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        init_data = await websocket.receive_text()
    except WebSocketDisconnect:
        return

    try:
        user = validate_init_data(
            init_data, settings.bot_token, max_age_seconds=settings.init_data_max_age_seconds
        )
    except InitDataValidationError as exc:
        logger.warning("Відхилено WS-підключення з невалідним initData: %s", exc)
        await websocket.close(code=_UNAUTHORIZED_CLOSE_CODE)
        return

    realtime.register(user.id, websocket)
    try:
        while True:
            # The client never sends anything meaningful after the handshake
            # message above — this loop exists only to block until the
            # socket actually closes, so the `finally` below runs and the
            # registry doesn't accumulate dead entries.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        realtime.unregister(user.id, websocket)
