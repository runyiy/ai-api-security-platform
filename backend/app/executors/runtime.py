from app.executors.rate_limit import InMemoryRateLimiter


platform_rate_limiter = InMemoryRateLimiter(
    requests_per_second=2.0,
)
