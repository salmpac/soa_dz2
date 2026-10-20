import jwt
import pytest

from conftest import register_and_login


def test_register_login_refresh(client):
    u = register_and_login(client, "USER")
    assert u["tokens"]["token_type"] == "Bearer"
    claims = jwt.decode(u["tokens"]["access_token"], options={"verify_signature": False})
    assert claims["role"] == "USER" and claims["type"] == "access" and claims["sub"] == u["id"]

    r = client.post("/auth/refresh", json={"refresh_token": u["tokens"]["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_register_duplicate_email(client, user):
    r = client.post("/auth/register", json={"email": user["email"], "password": "secret123"})
    assert r.status_code == 409 and r.json()["error_code"] == "USER_ALREADY_EXISTS"


def test_login_wrong_password(client, user):
    r = client.post("/auth/login", json={"email": user["email"], "password": "wrong-pass"})
    assert r.status_code == 401 and r.json()["error_code"] == "INVALID_CREDENTIALS"


def test_register_validation(client):
    r = client.post("/auth/register", json={"email": "not-an-email", "password": "123"})
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    fields = {e["field"] for e in body["details"]["errors"]}
    assert {"body.email", "body.password"} <= fields


def test_missing_token(client):
    r = client.get("/products")
    assert r.status_code == 401 and r.json()["error_code"] == "TOKEN_INVALID"


def test_garbage_token(client):
    r = client.get("/products", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401 and r.json()["error_code"] == "TOKEN_INVALID"


def test_refresh_token_used_as_access(client, user):
    r = client.get("/products", headers={"Authorization": f"Bearer {user['tokens']['refresh_token']}"})
    assert r.status_code == 401 and r.json()["error_code"] == "TOKEN_INVALID"


def test_invalid_refresh_token(client, user):
    r = client.post("/auth/refresh", json={"refresh_token": user["tokens"]["access_token"]})
    assert r.status_code == 401 and r.json()["error_code"] == "REFRESH_TOKEN_INVALID"
    r = client.post("/auth/refresh", json={"refresh_token": "garbage"})
    assert r.status_code == 401 and r.json()["error_code"] == "REFRESH_TOKEN_INVALID"


def test_expired_access_token(client, user):
    """An expired token signed with the right secret must give TOKEN_EXPIRED (not TOKEN_INVALID)."""
    import os, time
    secret = os.getenv("JWT_SECRET", "dev-secret-change-me")
    token = jwt.encode(
        {"sub": user["id"], "role": "USER", "type": "access", "iat": int(time.time()) - 100, "exp": int(time.time()) - 10},
        secret,
        algorithm="HS256",
    )
    r = client.get("/products", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["error_code"] == "TOKEN_EXPIRED"


def test_request_id_header(client):
    r = client.get("/health")
    assert len(r.headers["x-request-id"]) == 36
