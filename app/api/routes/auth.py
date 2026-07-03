"""Mini App endpoints for the Telethon phone/code/password login flow.

This is the Mini App's counterpart to the bot's `/connect` command — both
drive the same shared state in `app.services.telethon_auth`, so either
surface can be used to finish a login that was started on the other.
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_verified_user_id, get_verified_webapp_user
from app.api.schemas import AuthStatusOut, CodeRequest, PasswordRequest, PhoneRequest
from app.services import telethon_auth
from app.services.telegram_auth import TelegramWebAppUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/phone", response_model=AuthStatusOut)
async def submit_phone(
    payload: PhoneRequest, user: TelegramWebAppUser = Depends(get_verified_webapp_user)
) -> AuthStatusOut:
    result = await telethon_auth.start_phone_auth(user.id, user.username, user.full_name, payload.phone)
    return AuthStatusOut(status=result.status, error=result.error)


@router.post("/code", response_model=AuthStatusOut)
async def submit_code(payload: CodeRequest, user_id: int = Depends(get_verified_user_id)) -> AuthStatusOut:
    result = await telethon_auth.submit_code(user_id, payload.code)
    return AuthStatusOut(status=result.status, error=result.error)


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
