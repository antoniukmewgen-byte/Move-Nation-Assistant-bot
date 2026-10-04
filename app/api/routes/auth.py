"""Mini App endpoints for the Telethon QR/password login flow.

This is the Mini App's counterpart to the bot's `/connect` command — both
drive the same shared state in `app.services.telethon_auth`, so either
surface can be used to finish a login that was started on the other.
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_verified_user_id, get_verified_webapp_user
from app.api.schemas import AuthStatusOut, PasswordRequest
from app.services import telethon_auth
from app.services.qr_image import render_qr_data_url
from app.services.telegram_auth import TelegramWebAppUser

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_status_out(result: telethon_auth.AuthStepResult) -> AuthStatusOut:
    # qr_url is still sent to the client (it's the user's own login token,
    # needed for the "Open in Telegram" same-device deep link) — but the
    # *image* is rendered here rather than by a client-side QR library, so
    # the token never has to leave our backend for a third-party service.
    qr_image = render_qr_data_url(result.qr_url) if result.qr_url else None
    return AuthStatusOut(status=result.status, qr_url=result.qr_url, qr_image=qr_image, error=result.error)


@router.post("/qr", response_model=AuthStatusOut)
async def start_qr(user: TelegramWebAppUser = Depends(get_verified_webapp_user)) -> AuthStatusOut:
    result = await telethon_auth.start_qr_auth(user.id, user.username, user.full_name)
    return _to_status_out(result)


@router.post("/qr/poll", response_model=AuthStatusOut)
async def poll_qr(user_id: int = Depends(get_verified_user_id)) -> AuthStatusOut:
    result = await telethon_auth.poll_qr_auth(user_id)
    return _to_status_out(result)


@router.post("/password", response_model=AuthStatusOut)
async def submit_password(
    payload: PasswordRequest, user_id: int = Depends(get_verified_user_id)
) -> AuthStatusOut:
    result = await telethon_auth.submit_password(user_id, payload.password)
    return AuthStatusOut(status=result.status, error=result.error)


@router.post("/cancel")
async def cancel(user_id: int = Depends(get_verified_user_id)) -> dict[str, bool]:
    await telethon_auth.cancel_auth(user_id)
    return {"ok": True}
