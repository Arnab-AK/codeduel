"""
The auth dependency used on every protected HTTP route (submissions,
queue join/leave/status). Deliberately returns just the user's UUID, not
the full User row -- validating a session is a single Redis GET with no
database round trip at all, which matters here because this dependency
sits on the hot path of every submission (see phase 1's design decisions
on grading being synchronous and fast). Routes that actually need the
user's email (just /auth/me) fetch the row themselves.
"""
import uuid

from fastapi import Depends, HTTPException

from app.auth.sessions import extract_bearer_token, resolve_session


async def get_current_user_id(token: str = Depends(extract_bearer_token)) -> uuid.UUID:
    user_id = await resolve_session(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user_id
