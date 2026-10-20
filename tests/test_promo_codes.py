from datetime import datetime, timedelta, timezone

from conftest import make_promo


def test_create_and_duplicate(client, seller):
    promo = make_promo(client, seller)
    assert promo["current_uses"] == 0 and promo["active"] is True
    r = client.post("/promo-codes", json={**promo, "id": None}, headers=seller["headers"])
    assert r.status_code == 409 and r.json()["error_code"] == "PROMO_CODE_ALREADY_EXISTS"


def test_validation(client, seller, admin):
    now = datetime.now(timezone.utc)
    base = {
        "code": "OK_CODE",
        "discount_type": "PERCENTAGE",
        "discount_value": 10,
        "min_order_amount": 0,
        "max_uses": 5,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "valid_until": (now + timedelta(days=1)).isoformat(),
    }
    for patch in (
        {"code": "lower"},
        {"code": "ABC"},
        {"code": "A" * 21},
        {"discount_type": "COUPON"},
        {"discount_value": 0},
        {"max_uses": 0},
        {"valid_until": base["valid_from"]},
        {"discount_value": 150},
    ):
        r = client.post("/promo-codes", json={**base, **patch}, headers=admin["headers"])
        assert r.status_code == 400 and r.json()["error_code"] == "VALIDATION_ERROR", (patch, r.text)
