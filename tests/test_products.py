import uuid

from conftest import make_product


def test_crud_and_soft_delete(client, seller):
    p = make_product(client, seller, price=4999.90)
    assert p["status"] == "ACTIVE" and p["seller_id"] == seller["id"] and p["price"] == 4999.90
    assert p["created_at"] and p["updated_at"]

    r = client.get(f"/products/{p['id']}", headers=seller["headers"])
    assert r.status_code == 200 and r.json()["id"] == p["id"]

    upd = {"name": "Keyboard v2", "description": None, "price": 5000, "stock": 3, "category": "electronics", "status": "INACTIVE"}
    r = client.put(f"/products/{p['id']}", json=upd, headers=seller["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Keyboard v2" and body["status"] == "INACTIVE" and body["description"] is None
    assert body["updated_at"] > body["created_at"]

    r = client.delete(f"/products/{p['id']}", headers=seller["headers"])
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    # still readable: soft delete
    r = client.get(f"/products/{p['id']}", headers=seller["headers"])
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"


def test_not_found(client, seller):
    missing = uuid.uuid4()
    for method in ("get", "delete"):
        r = getattr(client, method)(f"/products/{missing}", headers=seller["headers"])
        assert r.status_code == 404 and r.json()["error_code"] == "PRODUCT_NOT_FOUND"
    r = client.get("/products/not-a-uuid", headers=seller["headers"])
    assert r.status_code == 400 and r.json()["error_code"] == "VALIDATION_ERROR"


def test_validation(client, seller):
    r = client.post(
        "/products",
        json={"name": "", "price": 0, "stock": -1, "category": "x" * 101, "description": "d" * 4001},
        headers=seller["headers"],
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    fields = {e["field"] for e in body["details"]["errors"]}
    assert fields == {"body.name", "body.price", "body.stock", "body.category", "body.description"}

    r = client.post("/products", json={"name": "x"}, headers=seller["headers"])
    assert r.status_code == 400
    r = client.post("/products", content="{not json", headers={**seller["headers"], "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error_code"] == "VALIDATION_ERROR"

    # ARCHIVED cannot be set via POST/PUT — only via DELETE
    r = client.post("/products", json={"name": "x", "price": 1, "stock": 1, "category": "c", "status": "ARCHIVED"}, headers=seller["headers"])
    assert r.status_code == 400


def test_pagination_and_filters(client, seller):
    category = "cat-" + uuid.uuid4().hex[:8]
    ids = [make_product(client, seller, category=category, name=f"p{i}")["id"] for i in range(5)]
    client.delete(f"/products/{ids[0]}", headers=seller["headers"])

    r = client.get("/products", params={"category": category, "size": 2}, headers=seller["headers"])
    assert r.status_code == 200
    page = r.json()
    assert page["totalElements"] == 5 and page["page"] == 0 and page["size"] == 2 and len(page["items"]) == 2

    r = client.get("/products", params={"category": category, "size": 2, "page": 2}, headers=seller["headers"])
    assert len(r.json()["items"]) == 1

    r = client.get("/products", params={"category": category, "status": "ARCHIVED"}, headers=seller["headers"])
    assert r.json()["totalElements"] == 1 and r.json()["items"][0]["id"] == ids[0]

    r = client.get("/products", params={"category": category, "status": "ACTIVE"}, headers=seller["headers"])
    assert r.json()["totalElements"] == 4

    r = client.get("/products", params={"status": "DELETED"}, headers=seller["headers"])
    assert r.status_code == 400 and r.json()["error_code"] == "VALIDATION_ERROR"
    r = client.get("/products", params={"page": -1}, headers=seller["headers"])
    assert r.status_code == 400
    r = client.get("/products", params={"size": 0}, headers=seller["headers"])
    assert r.status_code == 400


def test_roles(client, user, seller, admin):
    p = make_product(client, seller)
    body = {"name": "n", "price": 1, "stock": 1, "category": "c"}

    # USER cannot manage products
    for r in (
        client.post("/products", json=body, headers=user["headers"]),
        client.put(f"/products/{p['id']}", json=body, headers=user["headers"]),
        client.delete(f"/products/{p['id']}", headers=user["headers"]),
        client.post("/promo-codes", json={}, headers=user["headers"]),
    ):
        assert r.status_code == 403 and r.json()["error_code"] == "ACCESS_DENIED", r.text

    # but can read
    assert client.get(f"/products/{p['id']}", headers=user["headers"]).status_code == 200
    assert client.get("/products", headers=user["headers"]).status_code == 200

    # another SELLER cannot touch this product
    other = __import__("conftest").register_and_login(client, "SELLER")
    r = client.put(f"/products/{p['id']}", json=body, headers=other["headers"])
    assert r.status_code == 403 and r.json()["error_code"] == "ACCESS_DENIED"
    r = client.delete(f"/products/{p['id']}", headers=other["headers"])
    assert r.status_code == 403

    # ADMIN can
    r = client.put(f"/products/{p['id']}", json=body, headers=admin["headers"])
    assert r.status_code == 200 and r.json()["name"] == "n"
    r = client.delete(f"/products/{p['id']}", headers=admin["headers"])
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
