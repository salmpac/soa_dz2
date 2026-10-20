import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import RATE_LIMIT_SECONDS, make_product, make_promo, register_and_login


def order_body(*items, promo=None):
    body = {"items": [{"product_id": pid, "quantity": q} for pid, q in items]}
    if promo:
        body["promo_code"] = promo
    return body


def test_create_order_happy_path(client, user, seller):
    p1 = make_product(client, seller, price=100, stock=10)
    p2 = make_product(client, seller, price=49.99, stock=5)

    r = client.post("/orders", json=order_body((p1["id"], 2), (p2["id"], 1)), headers=user["headers"])
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "CREATED" and o["user_id"] == user["id"]
    assert o["total_amount"] == 249.99 and o["discount_amount"] == 0 and o["promo_code_id"] is None
    assert {i["product_id"]: (i["quantity"], i["price_at_order"]) for i in o["items"]} == {
        p1["id"]: (2, 100.0),
        p2["id"]: (1, 49.99),
    }

    # stock reserved
    assert client.get(f"/products/{p1['id']}", headers=user["headers"]).json()["stock"] == 8
    assert client.get(f"/products/{p2['id']}", headers=user["headers"]).json()["stock"] == 4

    # price snapshot: changing the product price does not affect the order
    client.put(f"/products/{p1['id']}", json={"name": "x", "price": 999, "stock": 8, "category": "c"}, headers=seller["headers"])
    r = client.get(f"/orders/{o['id']}", headers=user["headers"])
    assert r.status_code == 200 and r.json()["total_amount"] == 249.99


def test_order_validation(client, user):
    pid = str(uuid.uuid4())
    for body in (
        {"items": []},
        {"items": [{"product_id": pid, "quantity": 0}]},
        {"items": [{"product_id": pid, "quantity": 1000}]},
        {"items": [{"product_id": pid, "quantity": 1}] * 51},
        {"items": [{"product_id": pid, "quantity": 1}], "promo_code": "bad code"},
        {"items": [{"product_id": "nope", "quantity": 1}]},
        {},
    ):
        r = client.post("/orders", json=body, headers=user["headers"])
        assert r.status_code == 400 and r.json()["error_code"] == "VALIDATION_ERROR", (body, r.text)


def test_product_not_found_and_inactive(client, user, seller):
    r = client.post("/orders", json=order_body((str(uuid.uuid4()), 1)), headers=user["headers"])
    assert r.status_code == 404 and r.json()["error_code"] == "PRODUCT_NOT_FOUND"

    p = make_product(client, seller, status="INACTIVE")
    r = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "PRODUCT_INACTIVE"

    archived = make_product(client, seller)
    client.delete(f"/products/{archived['id']}", headers=seller["headers"])
    r = client.post("/orders", json=order_body((archived["id"], 1)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "PRODUCT_INACTIVE"


def test_insufficient_stock_is_atomic(client, user, seller):
    ok = make_product(client, seller, stock=10)
    low = make_product(client, seller, stock=2)
    r = client.post("/orders", json=order_body((ok["id"], 3), (low["id"], 5)), headers=user["headers"])
    assert r.status_code == 409
    body = r.json()
    assert body["error_code"] == "INSUFFICIENT_STOCK"
    assert body["details"]["products"] == [{"product_id": low["id"], "requested": 5, "available": 2}]
    # nothing was reserved for the other product (single transaction)
    assert client.get(f"/products/{ok['id']}", headers=user["headers"]).json()["stock"] == 10


def test_duplicate_product_lines_are_merged(client, user, seller):
    p = make_product(client, seller, stock=3)
    r = client.post("/orders", json=order_body((p["id"], 2), (p["id"], 2)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INSUFFICIENT_STOCK"


def test_active_order_blocks_second(client, user, seller):
    p = make_product(client, seller, stock=10)
    r = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 201
    time.sleep(RATE_LIMIT_SECONDS + 0.5)
    r = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "ORDER_HAS_ACTIVE"


def test_rate_limit(client, user, seller):
    p = make_product(client, seller, stock=10)
    r = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 201
    client.post(f"/orders/{r.json()['id']}/cancel", headers=user["headers"])
    r = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 429
    body = r.json()
    assert body["error_code"] == "ORDER_LIMIT_EXCEEDED" and body["details"]["retry_after_seconds"] >= 1


def test_promo_percentage(client, user, seller):
    p = make_product(client, seller, price=200, stock=10)
    promo = make_promo(client, seller, discount_type="PERCENTAGE", discount_value=25, min_order_amount=300)
    r = client.post("/orders", json=order_body((p["id"], 2), promo=promo["code"]), headers=user["headers"])
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["discount_amount"] == 100 and o["total_amount"] == 300 and o["promo_code_id"] == promo["id"]


def test_promo_percentage_over_70_percent_is_not_applied(client, user, seller):
    p = make_product(client, seller, price=100, stock=10)
    promo = make_promo(client, seller, discount_type="PERCENTAGE", discount_value=90)
    r = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=user["headers"])
    assert r.status_code == 201, r.text
    assert r.json()["discount_amount"] == 0 and r.json()["total_amount"] == 100


def test_promo_fixed_amount_capped_by_total(client, user, seller):
    p = make_product(client, seller, price=30, stock=10)
    promo = make_promo(client, seller, discount_type="FIXED_AMOUNT", discount_value=50)
    r = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=user["headers"])
    assert r.status_code == 201, r.text
    assert r.json()["discount_amount"] == 30 and r.json()["total_amount"] == 0


def test_promo_min_amount(client, user, seller):
    p = make_product(client, seller, price=100, stock=10)
    promo = make_promo(client, seller, min_order_amount=500)
    r = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=user["headers"])
    assert r.status_code == 422
    assert r.json()["error_code"] == "PROMO_CODE_MIN_AMOUNT"
    # the whole transaction rolled back: stock untouched
    assert client.get(f"/products/{p['id']}", headers=user["headers"]).json()["stock"] == 10


def test_promo_invalid_variants(client, user, seller):
    p = make_product(client, seller, price=100, stock=10)
    now = datetime.now(timezone.utc)
    cases = {
        "unknown": "NOPE_CODE",
        "inactive": make_promo(client, seller, active=False)["code"],
        "expired": make_promo(
            client, seller, valid_from=(now - timedelta(days=2)).isoformat(), valid_until=(now - timedelta(days=1)).isoformat()
        )["code"],
        "not_yet": make_promo(
            client, seller, valid_from=(now + timedelta(days=1)).isoformat(), valid_until=(now + timedelta(days=2)).isoformat()
        )["code"],
    }
    for name, code in cases.items():
        r = client.post("/orders", json=order_body((p["id"], 1), promo=code), headers=user["headers"])
        assert r.status_code == 422 and r.json()["error_code"] == "PROMO_CODE_INVALID", (name, r.text)


def test_promo_exhausted_and_uses_counter(client, seller):
    p = make_product(client, seller, price=100, stock=100)
    promo = make_promo(client, seller, max_uses=1)
    u1 = register_and_login(client, "USER")
    u2 = register_and_login(client, "USER")
    r = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=u1["headers"])
    assert r.status_code == 201
    r2 = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=u2["headers"])
    assert r2.status_code == 422 and r2.json()["error_code"] == "PROMO_CODE_INVALID"
    # cancel returns the use
    assert client.post(f"/orders/{r.json()['id']}/cancel", headers=u1["headers"]).status_code == 200
    r3 = client.post("/orders", json=order_body((p["id"], 1), promo=promo["code"]), headers=u2["headers"])
    assert r3.status_code == 201, r3.text


def test_update_order(client, user, seller):
    p1 = make_product(client, seller, price=100, stock=10)
    p2 = make_product(client, seller, price=50, stock=10)
    promo = make_promo(client, seller, discount_type="PERCENTAGE", discount_value=10, min_order_amount=150)
    r = client.post("/orders", json=order_body((p1["id"], 2), promo=promo["code"]), headers=user["headers"])
    assert r.status_code == 201
    o = r.json()
    assert o["total_amount"] == 180

    # new items: old stock returned, new reserved, promo re-validated (still >= 150)
    r = client.put(f"/orders/{o['id']}", json=order_body((p1["id"], 1), (p2["id"], 2)), headers=user["headers"])
    assert r.status_code == 200, r.text
    o2 = r.json()
    assert o2["total_amount"] == 180 and o2["discount_amount"] == 20 and o2["promo_code_id"] == promo["id"]
    assert client.get(f"/products/{p1['id']}", headers=user["headers"]).json()["stock"] == 9
    assert client.get(f"/products/{p2['id']}", headers=user["headers"]).json()["stock"] == 8

    # update rate limit
    r = client.put(f"/orders/{o['id']}", json=order_body((p2["id"], 1)), headers=user["headers"])
    assert r.status_code == 429 and r.json()["error_code"] == "ORDER_LIMIT_EXCEEDED"
    time.sleep(RATE_LIMIT_SECONDS + 0.5)

    # amount falls below min_order_amount: discount removed, promo detached, use returned
    r = client.put(f"/orders/{o['id']}", json=order_body((p2["id"], 1)), headers=user["headers"])
    assert r.status_code == 200, r.text
    o3 = r.json()
    assert o3["total_amount"] == 50 and o3["discount_amount"] == 0 and o3["promo_code_id"] is None
    assert client.get(f"/products/{p1['id']}", headers=user["headers"]).json()["stock"] == 10
    assert client.get(f"/products/{p2['id']}", headers=user["headers"]).json()["stock"] == 9

    # insufficient stock on update rolls everything back
    time.sleep(RATE_LIMIT_SECONDS + 0.5)
    r = client.put(f"/orders/{o['id']}", json=order_body((p2["id"], 999)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INSUFFICIENT_STOCK"
    assert client.get(f"/orders/{o['id']}", headers=user["headers"]).json()["total_amount"] == 50
    assert client.get(f"/products/{p2['id']}", headers=user["headers"]).json()["stock"] == 9


def test_state_machine(client, user, seller):
    p = make_product(client, seller, stock=10)
    o = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"]).json()

    # skipping a state is forbidden
    r = client.post(f"/orders/{o['id']}/status", json={"status": "PAID"}, headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATE_TRANSITION"
    assert r.json()["details"]["allowed_transitions"] == ["PAYMENT_PENDING"]

    r = client.post(f"/orders/{o['id']}/status", json={"status": "CREATED"}, headers=user["headers"])
    assert r.status_code == 400  # not in the enum of the contract

    for status in ("PAYMENT_PENDING", "PAID", "SHIPPED", "COMPLETED"):
        r = client.post(f"/orders/{o['id']}/status", json={"status": status}, headers=user["headers"])
        assert r.status_code == 200 and r.json()["status"] == status, r.text

    # terminal state
    r = client.post(f"/orders/{o['id']}/status", json={"status": "PAID"}, headers=user["headers"])
    assert r.status_code == 409
    r = client.post(f"/orders/{o['id']}/cancel", headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATE_TRANSITION"
    r = client.put(f"/orders/{o['id']}", json=order_body((p["id"], 1)), headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATE_TRANSITION"


def test_cancel_returns_stock_and_promo(client, user, seller):
    p = make_product(client, seller, price=100, stock=10)
    promo = make_promo(client, seller, max_uses=1)
    o = client.post("/orders", json=order_body((p["id"], 3), promo=promo["code"]), headers=user["headers"]).json()
    assert client.get(f"/products/{p['id']}", headers=user["headers"]).json()["stock"] == 7

    r = client.post(f"/orders/{o['id']}/status", json={"status": "PAYMENT_PENDING"}, headers=user["headers"])
    assert r.status_code == 200
    r = client.post(f"/orders/{o['id']}/cancel", headers=user["headers"])
    assert r.status_code == 200 and r.json()["status"] == "CANCELED"
    assert client.get(f"/products/{p['id']}", headers=user["headers"]).json()["stock"] == 10

    r = client.post(f"/orders/{o['id']}/cancel", headers=user["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATE_TRANSITION"


def test_order_not_found(client, user):
    missing = uuid.uuid4()
    assert client.get(f"/orders/{missing}", headers=user["headers"]).json()["error_code"] == "ORDER_NOT_FOUND"
    assert client.post(f"/orders/{missing}/cancel", headers=user["headers"]).status_code == 404
    assert client.put(f"/orders/{missing}", json=order_body((str(uuid.uuid4()), 1)), headers=user["headers"]).status_code == 404


def test_order_roles_and_ownership(client, user, seller, admin):
    p = make_product(client, seller, stock=10)
    o = client.post("/orders", json=order_body((p["id"], 1)), headers=user["headers"]).json()

    # SELLER cannot do anything with orders
    for r in (
        client.post("/orders", json=order_body((p["id"], 1)), headers=seller["headers"]),
        client.get(f"/orders/{o['id']}", headers=seller["headers"]),
        client.put(f"/orders/{o['id']}", json=order_body((p["id"], 1)), headers=seller["headers"]),
        client.post(f"/orders/{o['id']}/cancel", headers=seller["headers"]),
    ):
        assert r.status_code == 403 and r.json()["error_code"] == "ACCESS_DENIED", r.text

    # another USER: ownership violation
    other = register_and_login(client, "USER")
    for r in (
        client.get(f"/orders/{o['id']}", headers=other["headers"]),
        client.put(f"/orders/{o['id']}", json=order_body((p["id"], 1)), headers=other["headers"]),
        client.post(f"/orders/{o['id']}/cancel", headers=other["headers"]),
        client.post(f"/orders/{o['id']}/status", json={"status": "PAYMENT_PENDING"}, headers=other["headers"]),
    ):
        assert r.status_code == 403 and r.json()["error_code"] == "ORDER_OWNERSHIP_VIOLATION", r.text

    # ADMIN sees and manages any order
    assert client.get(f"/orders/{o['id']}", headers=admin["headers"]).status_code == 200
    r = client.post(f"/orders/{o['id']}/cancel", headers=admin["headers"])
    assert r.status_code == 200 and r.json()["status"] == "CANCELED"
