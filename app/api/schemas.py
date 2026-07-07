from pydantic import BaseModel, ConfigDict, Field


class GroupCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    awaiting_response: bool


class MemberOut(BaseModel):
    user_id: int
    name: str
    tag: str


class AddClientRequest(BaseModel):
    group_id: int
    identifier: str = Field(min_length=1, max_length=64)


class TagRequest(BaseModel):
    group_id: int
    user_id: int
    tag: str = Field(min_length=1, max_length=64)


class RemoveMemberRequest(BaseModel):
    group_id: int
    user_id: int


class RoleOut(BaseModel):
    name: str
    value: str


class RoleRequest(BaseModel):
    role: str = Field(min_length=1, max_length=32)


class UserMeOut(BaseModel):
    id: int
    username: str | None
    full_name: str | None
    role: str | None
    is_connected: bool


class PhoneRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=20)


class CodeRequest(BaseModel):
    code: str = Field(min_length=3, max_length=10)


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AuthStatusOut(BaseModel):
    status: str
    error: str | None = None
