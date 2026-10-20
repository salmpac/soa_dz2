"""Implementation of the `products` operations (CRUD with soft delete)."""
from typing import Optional
from uuid import UUID

from psycopg import AsyncConnection

from app.auth import CurrentUser
from app.db import pool
from app.errors import ApiError
from app.generated.models import (
    ErrorCode,
    ProductCreate,
    ProductEditableStatus,
    ProductPage,
    ProductResponse,
    ProductStatus,
    ProductUpdate,
)

PRODUCT_COLUMNS = "id, name, description, price, stock, category, status, seller_id, created_at, updated_at"


async def _load_for_update(conn: AsyncConnection, product_id: UUID) -> dict:
    row = await (
        await conn.execute(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = %s FOR UPDATE", (product_id,))
    ).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.PRODUCT_NOT_FOUND, "Product not found", {"product_id": str(product_id)})
    return row


def _check_ownership(product: dict, user: CurrentUser) -> None:
    """SELLER may manage only own products; ADMIN may manage any."""
    if not user.is_admin and product["seller_id"] != user.id:
        raise ApiError(
            403,
            ErrorCode.ACCESS_DENIED,
            "You can manage only your own products",
            {"product_id": str(product["id"])},
        )


async def create_product(*, body: ProductCreate, current_user: CurrentUser) -> ProductResponse:
    status = body.status or ProductEditableStatus.ACTIVE
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                f"""
                INSERT INTO products (name, description, price, stock, category, status, seller_id)
                VALUES (%s, %s, %s, %s, %s, %s::product_status, %s)
                RETURNING {PRODUCT_COLUMNS}
                """,
                (body.name, body.description, body.price, body.stock, body.category, status.value, current_user.id),
            )
        ).fetchone()
    return ProductResponse(**row)


async def get_product(*, id: UUID, current_user: CurrentUser) -> ProductResponse:
    async with pool.connection() as conn:
        row = await (await conn.execute(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = %s", (id,))).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.PRODUCT_NOT_FOUND, "Product not found", {"product_id": str(id)})
    return ProductResponse(**row)


async def list_products(
    *,
    page: int,
    size: int,
    status: Optional[ProductStatus],
    category: Optional[str],
    current_user: CurrentUser,
) -> ProductPage:
    conditions, params = [], []
    if status is not None:
        conditions.append("status = %s::product_status")
        params.append(status.value)
    if category is not None:
        conditions.append("category = %s")
        params.append(category)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with pool.connection() as conn:
        total = (await (await conn.execute(f"SELECT count(*) AS n FROM products {where}", params)).fetchone())["n"]
        rows = await (
            await conn.execute(
                f"SELECT {PRODUCT_COLUMNS} FROM products {where} ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                [*params, size, page * size],
            )
        ).fetchall()
    return ProductPage(items=[ProductResponse(**r) for r in rows], totalElements=total, page=page, size=size)


async def update_product(*, id: UUID, body: ProductUpdate, current_user: CurrentUser) -> ProductResponse:
    async with pool.connection() as conn, conn.transaction():
        product = await _load_for_update(conn, id)
        _check_ownership(product, current_user)
        new_status = body.status.value if body.status is not None else product["status"]
        row = await (
            await conn.execute(
                f"""
                UPDATE products
                SET name = %s, description = %s, price = %s, stock = %s, category = %s, status = %s::product_status
                WHERE id = %s
                RETURNING {PRODUCT_COLUMNS}
                """,
                (body.name, body.description, body.price, body.stock, body.category, new_status, id),
            )
        ).fetchone()
    return ProductResponse(**row)


async def delete_product(*, id: UUID, current_user: CurrentUser) -> ProductResponse:
    """Soft delete: the row stays in the table, status becomes ARCHIVED."""
    async with pool.connection() as conn, conn.transaction():
        product = await _load_for_update(conn, id)
        _check_ownership(product, current_user)
        row = await (
            await conn.execute(
                f"UPDATE products SET status = 'ARCHIVED' WHERE id = %s RETURNING {PRODUCT_COLUMNS}", (id,)
            )
        ).fetchone()
    return ProductResponse(**row)
