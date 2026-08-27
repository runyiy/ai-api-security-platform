from app.db.session import engine
from app.executors.rate_limit import PostgresRateLimiter


platform_rate_limiter = PostgresRateLimiter(
    requests_per_second=2.0,
    bind=engine,
)
