"""Validation tests for the Pydantic request schemas in app/api/schemas.py.

These schemas are the only gate between whatever a client sends and the rest
of the API (route handlers assume `title`/`identifier`/`phone`/etc. are
already within the declared bounds) — so every `Field(min_length=...,
max_length=...)` constraint gets both a positive (accepted at the boundary)
and a negative (rejected just past the boundary) case here, plus the type
coercion pitfalls Pydantic is prone to (e.g. bools/None where a str is
expected).
"""

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    AddClientRequest,
    CodeRequest,
    GroupCreateRequest,
    PasswordRequest,
    PhoneRequest,
    RoleRequest,
    TagRequest,
)

# --- GroupCreateRequest.title: min_length=1, max_length=128 -----------------


def test_group_title_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        GroupCreateRequest(title="")


def test_group_title_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        GroupCreateRequest(title="a" * 129)


def test_group_title_accepts_boundary_lengths() -> None:
    assert GroupCreateRequest(title="a").title == "a"
    assert GroupCreateRequest(title="a" * 128).title == "a" * 128


def test_group_title_rejects_missing_field() -> None:
    with pytest.raises(ValidationError):
        GroupCreateRequest()  # type: ignore[call-arg]


def test_group_title_rejects_non_string() -> None:
    with pytest.raises(ValidationError):
        GroupCreateRequest(title=None)  # type: ignore[arg-type]


# --- AddClientRequest.identifier: min_length=1, max_length=64 ---------------


def test_add_client_identifier_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        AddClientRequest(group_id=1, identifier="")


def test_add_client_identifier_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        AddClientRequest(group_id=1, identifier="a" * 65)


def test_add_client_identifier_accepts_boundary_lengths() -> None:
    assert AddClientRequest(group_id=1, identifier="@a").identifier == "@a"
    assert AddClientRequest(group_id=1, identifier="a" * 64).identifier == "a" * 64


def test_add_client_group_id_rejects_non_integer_string() -> None:
    with pytest.raises(ValidationError):
        AddClientRequest(group_id="not-an-id", identifier="@client")  # type: ignore[arg-type]


# --- TagRequest.tag: min_length=1, max_length=64 ----------------------------


def test_tag_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        TagRequest(group_id=1, user_id=1, tag="")


def test_tag_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        TagRequest(group_id=1, user_id=1, tag="a" * 65)


def test_tag_accepts_boundary_lengths() -> None:
    assert TagRequest(group_id=1, user_id=1, tag="a").tag == "a"
    assert TagRequest(group_id=1, user_id=1, tag="a" * 64).tag == "a" * 64


# --- RoleRequest.role: min_length=1, max_length=32 --------------------------
# Note: the schema only constrains length/type — whether the *value* names a
# real Role enum member is checked separately in the route handler
# (app/api/routes/users.py's `Role[payload.role]` lookup), not here.


def test_role_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        RoleRequest(role="")


def test_role_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        RoleRequest(role="a" * 33)


def test_role_accepts_boundary_lengths() -> None:
    assert RoleRequest(role="a").role == "a"
    assert RoleRequest(role="a" * 32).role == "a" * 32


# --- PhoneRequest.phone: min_length=5, max_length=20 ------------------------
# Note: actual phone-number *format* validation (leading "+", digits only,
# etc.) is Telethon/Telegram's job at the `send_code_request` call in
# telethon_auth.py, not this schema's — it only bounds the length so an
# empty or absurdly long string can't reach that call.


def test_phone_rejects_too_short() -> None:
    with pytest.raises(ValidationError):
        PhoneRequest(phone="123")


def test_phone_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        PhoneRequest(phone="1" * 21)


def test_phone_accepts_boundary_lengths() -> None:
    assert PhoneRequest(phone="12345").phone == "12345"
    assert PhoneRequest(phone="1" * 20).phone == "1" * 20


def test_phone_rejects_missing_field() -> None:
    with pytest.raises(ValidationError):
        PhoneRequest()  # type: ignore[call-arg]


# --- CodeRequest.code: min_length=3, max_length=10 --------------------------


def test_code_rejects_too_short() -> None:
    with pytest.raises(ValidationError):
        CodeRequest(code="12")


def test_code_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        CodeRequest(code="1" * 11)


def test_code_accepts_boundary_lengths() -> None:
    assert CodeRequest(code="123").code == "123"
    assert CodeRequest(code="1" * 10).code == "1" * 10


# --- PasswordRequest.password: min_length=1, max_length=256 -----------------


def test_password_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        PasswordRequest(password="")


def test_password_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        PasswordRequest(password="a" * 257)


def test_password_accepts_boundary_lengths() -> None:
    assert PasswordRequest(password="a").password == "a"
    assert PasswordRequest(password="a" * 256).password == "a" * 256
