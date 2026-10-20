"""Implementation of the `auth` operations (register / login / refresh)."""
from psycopg.errors import UniqueViolation

from app.auth import create_token_pair, decode_refresh_token, hash_password, verify_password
from app.db import pool
from app.errors import ApiError
from app.generated.models import (
    ErrorCode,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    TokenPair,
    UserResponse,
)


async def register(*, body: RegisterRequest) -> UserResponse:
    role = body.role or Role.USER
    async with pool.connection() as conn:
        try:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO users (email, password_hash, role)
                    VALUES (%s, %s, %s::user_role)
                    RETURNING id, email, role, created_at
                    """,
                    (str(body.email).lower(), hash_password(body.password), role.value),
                )
            ).fetchone()
        except UniqueViolation:
            raise ApiError(409, ErrorCode.USER_ALREADY_EXISTS, "User with this email already exists")
    return UserResponse(**row)


async def login(*, body: LoginRequest) -> TokenPair:
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT id, password_hash, role FROM users WHERE email = %s", (str(body.email).lower(),)
            )
        ).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise ApiError(401, ErrorCode.INVALID_CREDENTIALS, "Invalid email or password")
    return create_token_pair(row["id"], Role(row["role"]))


async def refresh(*, body: RefreshRequest) -> TokenPair:
    user_id = decode_refresh_token(body.refresh_token)
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))).fetchone()
    if row is None:
        raise ApiError(401, ErrorCode.REFRESH_TOKEN_INVALID, "User of the refresh token no longer exists")
    # The role is re-read from the DB so that a role change is reflected in the new access token.
    return create_token_pair(row["id"], Role(row["role"]))
