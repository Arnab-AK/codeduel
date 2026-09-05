"""
Single shared async Redis client for the process. `decode_responses=True`
means every value comes back as `str` rather than `bytes` -- the matchmaking
queue only ever stores small text/UUID values, so there's no reason to make
every call site decode manually.

This module holds process-wide state (a connection pool) deliberately, the
same way app/db/session.py holds one engine for the process -- both are
"create once at import time, reuse everywhere" by design, not something
each request should construct fresh.
"""
import redis.asyncio as redis

from app.config import settings

redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)
