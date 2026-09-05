import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user_id
from app.auth.security import hash_password, verify_password
from app.auth.sessions import create_session, destroy_session, extract_bearer_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserPublic

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Normalized so "Alice@Example.com" and "alice@example.com" can't
    # register two accounts that a human would consider the same address.
    email = payload.email.lower()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Auto-login on register: one round trip to get a usable session
    # instead of forcing register-then-login as two separate calls for no
    # real benefit -- "don't over-build" cuts the other way too.
    token = await create_session(user.id)
    return AuthResponse(user_id=user.id, email=user.email, token=token)


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = payload.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Same error, same status code, for "no such email" and "wrong
    # password" -- distinguishing them would tell an attacker which emails
    # are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = await create_session(user.id)
    return AuthResponse(user_id=user.id, email=user.email, token=token)


@router.post("/logout", status_code=204)
async def logout(token: str = Depends(extract_bearer_token)):
    await destroy_session(token)


@router.get("/me", response_model=UserPublic)
async def me(user_id: uuid.UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    return UserPublic(id=user.id, email=user.email)
