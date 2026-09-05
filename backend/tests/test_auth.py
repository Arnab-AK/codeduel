"""
Auth tests: registration, login, session validation, logout. Calls the
route functions directly rather than through TestClient -- see
test_match_completion.py's module docstring for why (process-wide async
DB/Redis singletons vs. TestClient's separate thread/loop).
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.api.routes_auth import login, logout, register
from app.auth.dependencies import get_current_user_id
from app.auth.sessions import resolve_session
from app.db.models import User
from app.db.session import async_session_factory
from app.schemas.auth import LoginRequest, RegisterRequest


@pytest.fixture
def unique_email():
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


async def _cleanup(email: str):
    async with async_session_factory() as db:
        await db.execute(delete(User).where(User.email == email))
        await db.commit()


async def test_register_creates_user_and_returns_a_working_token(unique_email):
    try:
        async with async_session_factory() as db:
            result = await register(RegisterRequest(email=unique_email, password="hunter22"), db)

        assert result.email == unique_email
        # The returned token should actually resolve back to this user --
        # proving create_session()/resolve_session() agree, not just that
        # a token-shaped string came back in the response.
        assert await resolve_session(result.token) == result.user_id
    finally:
        await _cleanup(unique_email)


async def test_duplicate_email_registration_is_rejected(unique_email):
    try:
        async with async_session_factory() as db:
            await register(RegisterRequest(email=unique_email, password="hunter22"), db)
        async with async_session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                await register(RegisterRequest(email=unique_email, password="different1"), db)
        assert exc_info.value.status_code == 409
    finally:
        await _cleanup(unique_email)


async def test_email_matching_is_case_insensitive(unique_email):
    shouted_email = unique_email.upper()
    try:
        async with async_session_factory() as db:
            await register(RegisterRequest(email=unique_email, password="hunter22"), db)
        async with async_session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                await register(RegisterRequest(email=shouted_email, password="different1"), db)
        assert exc_info.value.status_code == 409
    finally:
        await _cleanup(unique_email)


async def test_login_with_correct_password_succeeds(unique_email):
    try:
        async with async_session_factory() as db:
            registered = await register(
                RegisterRequest(email=unique_email, password="hunter22"), db
            )
        async with async_session_factory() as db:
            result = await login(LoginRequest(email=unique_email, password="hunter22"), db)

        assert result.user_id == registered.user_id
        assert result.token != registered.token  # a fresh session, not the same one reused
    finally:
        await _cleanup(unique_email)


async def test_login_with_wrong_password_is_rejected(unique_email):
    try:
        async with async_session_factory() as db:
            await register(RegisterRequest(email=unique_email, password="hunter22"), db)
        async with async_session_factory() as db:
            with pytest.raises(HTTPException) as exc_info:
                await login(LoginRequest(email=unique_email, password="wrong-password"), db)
        assert exc_info.value.status_code == 401
    finally:
        await _cleanup(unique_email)


async def test_login_with_unknown_email_is_rejected():
    async with async_session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await login(LoginRequest(email="nobody-here@example.com", password="whatever1"), db)
    assert exc_info.value.status_code == 401


async def test_invalid_token_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(token="not-a-real-token")
    assert exc_info.value.status_code == 401


async def test_logout_invalidates_the_token(unique_email):
    try:
        async with async_session_factory() as db:
            result = await register(RegisterRequest(email=unique_email, password="hunter22"), db)

        assert await resolve_session(result.token) == result.user_id
        await logout(token=result.token)
        assert await resolve_session(result.token) is None
    finally:
        await _cleanup(unique_email)
