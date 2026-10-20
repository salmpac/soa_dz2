"""End-to-end tests. They talk HTTP to a running instance (BASE_URL) — no mocks.

Run the stack first (`make up` or `make db && make run`), then `make test`.
Set ORDER_RATE_LIMIT_SECONDS to the value the server uses (default 60) — the rate-limit
test waits that long once; everything else uses fresh users to stay under the limit.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
RATE_LIMIT_SECONDS = int(os.getenv("ORDER_RATE_LIMIT_SECONDS", "60"))


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        yield c


def register_and_login(client: httpx.Client, role: str) -> dict:
    email = f"{role.lower()}-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "secret123", "role": role})
    assert r.status_code == 201, r.text
    user = r.json()
    r = client.post("/auth/login", json={"email": email, "password": "secret123"})
    assert r.status_code == 200, r.text
    tokens = r.json()
    return {
        "id": user["id"],
        "email": email,
        "role": role,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        "tokens": tokens,
    }


@pytest.fixture
def user(client):
    return register_and_login(client, "USER")


@pytest.fixture
def seller(client):
    return register_and_login(client, "SELLER")


@pytest.fixture
def admin(client):
    return register_and_login(client, "ADMIN")


def make_product(client, seller, **overrides) -> dict:
    body = {"name": "Keyboard", "description": "87 keys", "price": 100.00, "stock": 10, "category": "electronics"}
    body.update(overrides)
    r = client.post("/products", json=body, headers=seller["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def make_promo(client, seller, **overrides) -> dict:
    now = datetime.now(timezone.utc)
    body = {
        "code": "SALE" + uuid.uuid4().hex[:8].upper(),
        "discount_type": "PERCENTAGE",
        "discount_value": 10,
        "min_order_amount": 0,
        "max_uses": 5,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_until": (now + timedelta(days=1)).isoformat(),
    }
    body.update(overrides)
    r = client.post("/promo-codes", json=body, headers=seller["headers"])
    assert r.status_code == 201, r.text
    return r.json()
