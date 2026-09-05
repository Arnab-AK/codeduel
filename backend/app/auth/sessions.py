"""
Session tokens, stored in Redis rather than as stateless JWTs. This is
already-wired infrastructure (Redis is used for matchmaking and pub/sub
elsewhere in this app), so it's zero new moving parts, and unlike a JWT a
Redis-backed session can actually be revoked on logout -- deleting the key
immediately invalidates it everywhere, rather than needing a separate
blocklist to fake revocation on top of a normally-stateless token scheme.

The token itself is an opaque, high-entropy random string (not the user_id
itself, not anything derived from it) -- `secrets.token_urlsafe` is
generated from `os.urandom`, so it's infeasible to guess or enumerate.
"""
import secrets
import uuid

from fastapi import Header, HTTPException

from app.core.redis_client import redis_client

SESSION_KEY = "session:{token}"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


async def create_session(user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    await redis_client.set(SESSION_KEY.format(token=token), str(user_id), ex=SESSION_TTL_SECONDS)
    return token


async def destroy_session(token: str) -> None:
    await redis_client.delete(SESSION_KEY.format(token=token))


async def resolve_session(token: str) -> uuid.UUID | None:
    value = await redis_client.get(SESSION_KEY.format(token=token))
    return uuid.UUID(value) if value else None


def extract_bearer_token(authorization: str | None = Header(default=None)) -> str:
    """Shared by the logout endpoint (needs the raw token, to delete it)
    and dependencies.get_current_user_id (needs it resolved to a user) --
    small enough that splitting header-parsing out on its own, rather than
    folding it into get_current_user_id, is worth it just to avoid the two
    call sites duplicating the same two lines."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    return authorization.removeprefix("Bearer ").strip()
