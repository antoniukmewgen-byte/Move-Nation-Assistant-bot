"""Tests for `GET /health` (app/api/routes/health.py).

Same direct-coroutine-call convention as the other route tests: no ASGI
transport, `async_session` swapped for an in-memory-SQLite-backed factory
(or, for the failure case, something that raises).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import health as health_routes
from app.db.models import Base

pytestmark = pytest.mark.asyncio


async def test_health_check_reports_ok_when_db_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(health_routes, "async_session", sessionmaker)

    result = await health_routes.health_check()

    assert result == {"status": "ok", "database": True}
    await engine.dispose()


async def test_health_check_reports_degraded_when_db_probe_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _broken_session_factory() -> AsyncSession:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(health_routes, "async_session", _broken_session_factory)

    with caplog.at_level("ERROR"):
        result = await health_routes.health_check()

    assert result == {"status": "degraded", "database": False}
    # The DB failure must be logged, not swallowed silently (see the comment
    # in health.py explaining why the broad except is intentional).
    assert "Health check DB probe failed" in caplog.text
