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
    GroupCreateRequest,
    PasswordRequest,
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
