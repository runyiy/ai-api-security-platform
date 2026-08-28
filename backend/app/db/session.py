from collections.abc import Generator

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
network_coordination_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"connect_timeout": 1},
)


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
