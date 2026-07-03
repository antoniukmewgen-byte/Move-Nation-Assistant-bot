from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_verified_webapp_user
from app.api.schemas import RoleOut, RoleRequest, UserMeOut
from app.db import crud
from app.db.models import Role, User
from app.db.session import async_session
from app.services.telegram_auth import TelegramWebAppUser

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/roles", response_model=list[RoleOut])
async def list_roles() -> list[RoleOut]:
    return [RoleOut(name=role.name, value=role.value) for role in Role]


@router.get("/me", response_model=UserMeOut)
async def get_me(user: TelegramWebAppUser = Depends(get_verified_webapp_user)) -> UserMeOut:
    """Ensure the calling Telegram user has a row, and report onboarding progress.

    The Mini App calls this first on every load and uses the result to decide
    which screen to show — role picker, phone/code/password connect flow, or
    the full group-management UI — instead of relying on chat commands.
    """
    async with async_session() as session:
        db_user = await crud.get_or_create_user(session, user.id, user.username, user.full_name)
        await session.commit()
        return _to_user_out(db_user)


@router.post("/role", response_model=UserMeOut)
async def set_role(
    payload: RoleRequest, user: TelegramWebAppUser = Depends(get_verified_webapp_user)
) -> UserMeOut:
    try:
        role = Role[payload.role]
    except KeyError:
        raise HTTPException(status_code=400, detail="Невідома посада") from None

    async with async_session() as session:
        db_user = await crud.get_or_create_user(session, user.id, user.username, user.full_name)
        await crud.set_user_role(session, user.id, role)
        await session.commit()
        await session.refresh(db_user)
        return _to_user_out(db_user)


def _to_user_out(user: User) -> UserMeOut:
    return UserMeOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role.value if user.role else None,
        is_connected=user.session_string is not None,
    )
