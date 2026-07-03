from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str

    api_id: int
    api_hash: str
    session_encryption_key: str
    bot_username: str = "MoveNation_Assistant_bot"

    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'movenation.db'}"
    default_logo_path: Path = BASE_DIR / "assets" / "default_logo.png"

    reminder_interval_minutes: int = 5

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    webapp_url: str = "http://127.0.0.1:8000/miniapp/index.html"

    # Comma-separated list of extra origins allowed to call the API cross-origin.
    # Leave empty (default) if the Mini App is always served from the same
    # FastAPI app, which is the case out of the box — same-origin requests
    # don't need CORS headers at all.
    cors_allowed_origins: list[str] = []

    # How long a Telegram Mini App initData payload stays valid, in seconds.
    init_data_max_age_seconds: int = 86400

    log_level: str = "INFO"

    @field_validator("session_encryption_key")
    @classmethod
    def _validate_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "SESSION_ENCRYPTION_KEY має бути валідним Fernet-ключем. Згенеруй: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


# Required fields are populated from the environment/`.env` by pydantic-settings
# at runtime; mypy can't see that, hence the call-arg ignore (standard pattern
# for pydantic-settings — see https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
settings = Settings()  # type: ignore[call-arg]

# `data/` is gitignored (it holds the local SQLite file + Telethon session
# artifacts), so it doesn't exist on a fresh clone. Both `python -m app.main`
# and `alembic` (via alembic/env.py, which imports `settings`) need the parent
# directory to already exist before SQLite can create the file — ensure it
# does, here, once, regardless of which entrypoint runs first.
if settings.database_url.startswith("sqlite"):
    _db_path = settings.database_url.split("///", 1)[-1]
    if _db_path not in ("", ":memory:"):
        Path(_db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
