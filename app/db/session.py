from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Schema is managed exclusively through Alembic migrations (see alembic/) —
# there is intentionally no `create_all()`-based auto-create path here.
# Having two independent ways to create the same tables (raw metadata vs.
# tracked migrations) is how you end up with a database Alembic doesn't
# recognize as being at any revision. Run `alembic upgrade head` before
# starting the app (the Dockerfile's CMD already does this).
engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
