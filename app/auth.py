"""JWT authentication (access + refresh tokens) and role checks.

Access token claims:  sub (user id), role, type="access", iat, exp (15 min), jti
Refresh token claims: sub, type="refresh", iat, exp (7 days), jti
Both are HS256-signed with JWT_SECRET. Refresh tokens are stateless (no server-side store).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.errors import ApiError
from app.generated.models import ErrorCode, Role, TokenPair, TokenType

_bearer = HTTPBearer(auto_error=False)
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


# ----------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ----------------------------------------------------------------- tokens
def _encode(claims: dict, ttl_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {**claims, "iat": now, "exp": now + timedelta(seconds=ttl_seconds), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def create_token_pair(user_id: UUID, role: Role) -> TokenPair:
    access = _encode({"sub": str(user_id), "role": role.value, "type": "access"}, settings.access_token_ttl_seconds)
    refresh = _encode({"sub": str(user_id), "type": "refresh"}, settings.refresh_token_ttl_seconds)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        token_type=TokenType.Bearer,
        expires_in=settings.access_token_ttl_seconds,
    )


def decode_access_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ApiError(401, ErrorCode.TOKEN_EXPIRED, "Access token has expired")
    except jwt.InvalidTokenError:
        raise ApiError(401, ErrorCode.TOKEN_INVALID, "Access token is invalid")
    if payload.get("type") != "access":
        raise ApiError(401, ErrorCode.TOKEN_INVALID, "Not an access token")
    try:
        return CurrentUser(id=UUID(payload["sub"]), role=Role(payload["role"]))
    except (KeyError, ValueError):
        raise ApiError(401, ErrorCode.TOKEN_INVALID, "Access token has malformed claims")


def decode_refresh_token(token: str) -> UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:  # includes ExpiredSignatureError
        raise ApiError(401, ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired")
    if payload.get("type") != "refresh":
        raise ApiError(401, ErrorCode.REFRESH_TOKEN_INVALID, "Not a refresh token")
    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError):
        raise ApiError(401, ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token has malformed claims")


# ----------------------------------------------------------------- dependencies
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """Resolves the bearer token into a CurrentUser (401 TOKEN_INVALID / TOKEN_EXPIRED)."""
    if credentials is None:
        raise ApiError(401, ErrorCode.TOKEN_INVALID, "Missing or malformed Authorization header")
    user = decode_access_token(credentials.credentials)
    request.state.user_id = str(user.id)  # picked up by the request logger
    return user


def require_roles(roles: list[str]):
    """Route dependency generated from the `x-roles` extension of each operation."""
    allowed = {Role(r) for r in roles}

    async def _check(user: CurrentUser = Depends(get_current_user)) -> None:
        if user.role not in allowed:
            raise ApiError(
                403,
                ErrorCode.ACCESS_DENIED,
                "This operation is not allowed for your role",
                {"role": user.role.value, "allowed_roles": sorted(r.value for r in allowed)},
            )

    return _check
