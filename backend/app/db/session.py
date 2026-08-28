from collections.abc import Generator
import math

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

# Network admission holds a PostgreSQL session for the lifetime of a request.
# Do not pool these sessions: PostgreSQL remains the sole concurrency authority,
# and a lock-bearing physical session can never be reused after close.
def create_network_coordination_engine(
    *,
    database_url: str = settings.database_url,
    coordination_timeout_seconds: float = 1.0,
):
    if (
        isinstance(coordination_timeout_seconds, bool)
        or not isinstance(coordination_timeout_seconds, (int, float))
        or not math.isfinite(coordination_timeout_seconds)
        or coordination_timeout_seconds <= 0
    ):
        raise ValueError("coordination_timeout_seconds must be finite and positive")
    statement_timeout_ms = max(
        1, math.ceil(coordination_timeout_seconds * 1000)
    )
    coordination_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": max(1, math.ceil(coordination_timeout_seconds)),
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
    )
    coordination_engine._network_coordination_statement_timeout_ms = (
        statement_timeout_ms
    )
    return coordination_engine


network_coordination_engine = create_network_coordination_engine()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
