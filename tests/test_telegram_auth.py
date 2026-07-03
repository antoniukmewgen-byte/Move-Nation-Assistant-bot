import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.services.telegram_auth import InitDataValidationError, validate_init_data

BOT_TOKEN = "123456:test-token"


def _sign(pairs: dict[str, str], bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256).digest()
    return hmac.new(key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()


def _build_init_data(
    *, user_id: int = 42, username: str = "staffer", auth_date: int | None = None, bot_token: str = BOT_TOKEN
) -> str:
    pairs = {
        "user": json.dumps({"id": user_id, "first_name": "Staff", "username": username}),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAA123",
    }
    pairs["hash"] = _sign(pairs, bot_token)
    return urlencode(pairs)


def test_valid_init_data_is_accepted() -> None:
    init_data = _build_init_data(user_id=777, username="ivan")
    user = validate_init_data(init_data, BOT_TOKEN)
    assert user.id == 777
    assert user.username == "ivan"


def test_tampered_payload_is_rejected() -> None:
    init_data = _build_init_data(user_id=777)
    # Flip the user id after signing — the hash no longer matches the payload.
    tampered = init_data.replace("777", "778")
    with pytest.raises(InitDataValidationError):
        validate_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_is_rejected() -> None:
    init_data = _build_init_data(bot_token=BOT_TOKEN)
    with pytest.raises(InitDataValidationError):
        validate_init_data(init_data, "999999:someone-elses-token")


def test_missing_hash_is_rejected() -> None:
    with pytest.raises(InitDataValidationError):
        validate_init_data("user=%7B%22id%22%3A1%7D&auth_date=123", BOT_TOKEN)


def test_empty_init_data_is_rejected() -> None:
    with pytest.raises(InitDataValidationError):
        validate_init_data("", BOT_TOKEN)


def test_expired_init_data_is_rejected() -> None:
    stale_auth_date = int(time.time()) - 999_999
    init_data = _build_init_data(auth_date=stale_auth_date)
    with pytest.raises(InitDataValidationError):
        validate_init_data(init_data, BOT_TOKEN, max_age_seconds=86400)
