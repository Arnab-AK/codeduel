import uuid

from pydantic import BaseModel, EmailStr, Field

from app.auth.security import MAX_PASSWORD_BYTES


class RegisterRequest(BaseModel):
    email: EmailStr
    # A floor, not a full password-strength policy (breach lists, character
    # class rules, etc.) -- "simple auth" was the explicit scope for this
    # phase. max_length matches bcrypt's own input limit (security.py) so
    # a too-long password is rejected with a clear 422 instead of being
    # silently truncated.
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)


class AuthResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    token: str


class UserPublic(BaseModel):
    id: uuid.UUID
    email: str
