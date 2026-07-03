"""Liveness/readiness endpoint for uptime checks and container orchestration."""

import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import async_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    db_ok = True
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        # Deliberately broad: any failure here (connection refused, auth error,
        # pool exhaustion, ...) should degrade the health check rather than
        # 500 it, but swallowing it silently would leave an on-call engineer
        # staring at "degraded" with zero clue why — so it needs to be logged.
        logger.exception("Health check DB probe failed")
        db_ok = False

    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
