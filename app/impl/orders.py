"""Implementation of the `orders` operations: create / get / update / cancel / status change.

Every mutating operation runs in ONE database transaction (`conn.transaction()`): stock
reservation, price snapshots, promo accounting and the operation record either all commit
or all roll back. Rows that are read-then-modified are locked with SELECT ... FOR UPDATE.
"""
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from psycopg import AsyncConnection

from app.auth import CurrentUser
from app.config import settings
from app.db import pool
from app.errors import ApiError
from app.generated.models import (
    ErrorCode,
    OrderCreate,
    OrderItemCreate,
    OrderItemResponse,
    OrderResponse,
    OrderStatus,
    OrderStatusChange,
    OrderUpdate,
)
from app.impl.promo_codes import compute_discount, lock_promo_by_code, lock_promo_by_id, promo_is_valid

ACTIVE_ORDER_STATUSES = (OrderStatus.CREATED.value, OrderStatus.PAYMENT_PENDING.value)
CANCELABLE_STATUSES = (OrderStatus.CREATED.value, OrderStatus.PAYMENT_PENDING.value)

# The mandatory state machine: CREATED -> PAYMENT_PENDING -> PAID -> SHIPPED -> COMPLETED
NEXT_STATUS = {
    OrderStatus.CREATED.value: OrderStatus.PAYMENT_PENDING.value,
    OrderStatus.PAYMENT_PENDING.value: OrderStatus.PAID.value,
    OrderStatus.PAID.value: OrderStatus.SHIPPED.value,
    OrderStatus.SHIPPED.value: OrderStatus.COMPLETED.value,
}

ORDER_COLUMNS = "id, user_id, status, promo_code_id, total_amount, discount_amount, created_at, updated_at"


# ----------------------------------------------------------------- helpers
async def _lock_user(conn: AsyncConnection, user_id: UUID) -> None:
    """Serializes order operations of one user (rate limit + active-order check are read-then-write)."""
    await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))


async def _check_rate_limit(conn: AsyncConnection, user_id: UUID, operation_type: str) -> None:
    row = await (
        await conn.execute(
            """
            SELECT EXTRACT(EPOCH FROM (now() - created_at)) AS elapsed
            FROM user_operations
            WHERE user_id = %s AND operation_type = %s::operation_type
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, operation_type),
        )
    ).fetchone()
    if row is not None and row["elapsed"] < settings.order_rate_limit_seconds:
        raise ApiError(
            429,
            ErrorCode.ORDER_LIMIT_EXCEEDED,
            f"Too many {operation_type} operations, try again later",
            {
                "operation_type": operation_type,
                "limit_seconds": settings.order_rate_limit_seconds,
                "retry_after_seconds": math.ceil(settings.order_rate_limit_seconds - float(row["elapsed"])),
            },
        )


async def _record_operation(conn: AsyncConnection, user_id: UUID, operation_type: str) -> None:
    await conn.execute(
        "INSERT INTO user_operations (user_id, operation_type) VALUES (%s, %s::operation_type)",
        (user_id, operation_type),
    )


def _merge_items(items: list[OrderItemCreate]) -> dict[UUID, int]:
    """Same product listed twice is one line with the summed quantity."""
    wanted: dict[UUID, int] = {}
    for item in items:
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity
    return wanted


async def _reserve_items(conn: AsyncConnection, wanted: dict[UUID, int]) -> list[dict]:
    """Catalog check -> stock check -> stock reservation -> price snapshot (steps 3-6 of the spec)."""
    rows = await (
        await conn.execute(
            "SELECT id, status, stock, price FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (list(wanted),),
        )
    ).fetchall()
    products = {row["id"]: row for row in rows}

    missing = [str(pid) for pid in wanted if pid not in products]
    if missing:
        raise ApiError(404, ErrorCode.PRODUCT_NOT_FOUND, "Some products do not exist", {"product_ids": missing})

    inactive = [
        {"product_id": str(pid), "status": products[pid]["status"]}
        for pid in wanted
        if products[pid]["status"] != "ACTIVE"
    ]
    if inactive:
        raise ApiError(409, ErrorCode.PRODUCT_INACTIVE, "Some products are not active", {"products": inactive})

    insufficient = [
        {"product_id": str(pid), "requested": qty, "available": products[pid]["stock"]}
        for pid, qty in wanted.items()
        if products[pid]["stock"] < qty
    ]
    if insufficient:
        raise ApiError(
            409, ErrorCode.INSUFFICIENT_STOCK, "Insufficient stock for some products", {"products": insufficient}
        )

    reserved = []
    for pid, qty in wanted.items():
        await conn.execute("UPDATE products SET stock = stock - %s WHERE id = %s", (qty, pid))
        reserved.append({"product_id": pid, "quantity": qty, "price_at_order": products[pid]["price"]})
    return reserved


async def _release_items(conn: AsyncConnection, order_id: UUID) -> None:
    """Returns the stock of all current items of the order to the products."""
    await conn.execute(
        """
        UPDATE products p
        SET stock = p.stock + oi.quantity
        FROM order_items oi
        WHERE oi.order_id = %s AND oi.product_id = p.id
        """,
        (order_id,),
    )


async def _insert_items(conn: AsyncConnection, order_id: UUID, reserved: list[dict]) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO order_items (order_id, product_id, quantity, price_at_order) VALUES (%s, %s, %s, %s)",
            [(order_id, r["product_id"], r["quantity"], r["price_at_order"]) for r in reserved],
        )


def _subtotal(reserved: list[dict]) -> Decimal:
    return sum((r["price_at_order"] * r["quantity"] for r in reserved), Decimal("0.00"))


async def _load_order(conn: AsyncConnection, order_id: UUID, *, for_update: bool) -> dict:
    row = await (
        await conn.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = %s {'FOR UPDATE' if for_update else ''}",
            (order_id,),
        )
    ).fetchone()
    if row is None:
        raise ApiError(404, ErrorCode.ORDER_NOT_FOUND, "Order not found", {"order_id": str(order_id)})
    return row


def _check_owner(order: dict, user: CurrentUser) -> None:
    if not user.is_admin and order["user_id"] != user.id:
        raise ApiError(
            403,
            ErrorCode.ORDER_OWNERSHIP_VIOLATION,
            "Order belongs to another user",
            {"order_id": str(order["id"])},
        )


async def _build_response(conn: AsyncConnection, order_id: UUID) -> OrderResponse:
    order = await _load_order(conn, order_id, for_update=False)
    items = await (
        await conn.execute(
            "SELECT id, product_id, quantity, price_at_order FROM order_items WHERE order_id = %s ORDER BY id",
            (order_id,),
        )
    ).fetchall()
    return OrderResponse(**order, items=[OrderItemResponse(**i) for i in items])


# ----------------------------------------------------------------- operations
async def create_order(*, body: OrderCreate, current_user: CurrentUser) -> OrderResponse:
    wanted = _merge_items(body.items)
    now = datetime.now(timezone.utc)

    async with pool.connection() as conn, conn.transaction():
        await _lock_user(conn, current_user.id)

        # 1. rate limit
        await _check_rate_limit(conn, current_user.id, "CREATE_ORDER")

        # 2. no other active order
        active = await (
            await conn.execute(
                "SELECT id FROM orders WHERE user_id = %s AND status = ANY(%s::order_status[]) LIMIT 1",
                (current_user.id, list(ACTIVE_ORDER_STATUSES)),
            )
        ).fetchone()
        if active is not None:
            raise ApiError(
                409, ErrorCode.ORDER_HAS_ACTIVE, "User already has an active order", {"order_id": str(active["id"])}
            )

        # 3-6. catalog + stock checks, reservation, price snapshot
        reserved = await _reserve_items(conn, wanted)

        # 7. totals and promo code
        subtotal = _subtotal(reserved)
        discount = Decimal("0.00")
        promo_id: Optional[UUID] = None
        if body.promo_code:
            promo = await lock_promo_by_code(conn, body.promo_code)
            if not promo_is_valid(promo, now):
                raise ApiError(
                    422, ErrorCode.PROMO_CODE_INVALID, "Promo code is invalid", {"promo_code": body.promo_code}
                )
            if subtotal < promo["min_order_amount"]:
                raise ApiError(
                    422,
                    ErrorCode.PROMO_CODE_MIN_AMOUNT,
                    "Order amount is below the promo code minimum",
                    {
                        "promo_code": body.promo_code,
                        "min_order_amount": promo["min_order_amount"],
                        "order_amount": subtotal,
                    },
                )
            discount = compute_discount(promo, subtotal)
            promo_id = promo["id"]
            await conn.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = %s", (promo_id,))

        order = await (
            await conn.execute(
                """
                INSERT INTO orders (user_id, status, promo_code_id, total_amount, discount_amount)
                VALUES (%s, 'CREATED', %s, %s, %s)
                RETURNING id
                """,
                (current_user.id, promo_id, subtotal - discount, discount),
            )
        ).fetchone()
        await _insert_items(conn, order["id"], reserved)

        # 8. operation record
        await _record_operation(conn, current_user.id, "CREATE_ORDER")

        return await _build_response(conn, order["id"])


async def get_order(*, id: UUID, current_user: CurrentUser) -> OrderResponse:
    async with pool.connection() as conn:
        order = await _load_order(conn, id, for_update=False)
        _check_owner(order, current_user)
        return await _build_response(conn, id)


async def update_order(*, id: UUID, body: OrderUpdate, current_user: CurrentUser) -> OrderResponse:
    wanted = _merge_items(body.items)
    now = datetime.now(timezone.utc)

    async with pool.connection() as conn, conn.transaction():
        order = await _load_order(conn, id, for_update=True)

        # 1. owner
        _check_owner(order, current_user)

        # 2. only CREATED orders can be edited
        if order["status"] != OrderStatus.CREATED.value:
            raise ApiError(
                409,
                ErrorCode.INVALID_STATE_TRANSITION,
                "Order can be updated only in CREATED status",
                {"current_status": order["status"], "allowed_statuses": [OrderStatus.CREATED.value]},
            )

        # 3. rate limit
        await _check_rate_limit(conn, current_user.id, "UPDATE_ORDER")

        # 4. give back the previously reserved stock
        await _release_items(conn, id)
        await conn.execute("DELETE FROM order_items WHERE order_id = %s", (id,))

        # 5. check and reserve the new items
        reserved = await _reserve_items(conn, wanted)
        await _insert_items(conn, id, reserved)

        # 6. recalculate totals; re-validate the promo code if one was applied
        subtotal = _subtotal(reserved)
        discount = Decimal("0.00")
        promo_id: Optional[UUID] = order["promo_code_id"]
        if promo_id is not None:
            promo = await lock_promo_by_id(conn, promo_id)
            if promo_is_valid(promo, now, already_counted=True) and subtotal >= promo["min_order_amount"]:
                discount = compute_discount(promo, subtotal)
            else:
                # promo no longer applicable: drop the discount and give the use back
                await conn.execute(
                    "UPDATE promo_codes SET current_uses = GREATEST(current_uses - 1, 0) WHERE id = %s", (promo_id,)
                )
                promo_id = None
        await conn.execute(
            "UPDATE orders SET promo_code_id = %s, total_amount = %s, discount_amount = %s WHERE id = %s",
            (promo_id, subtotal - discount, discount, id),
        )

        # 7. operation record
        await _record_operation(conn, current_user.id, "UPDATE_ORDER")

        return await _build_response(conn, id)


async def cancel_order(*, id: UUID, current_user: CurrentUser) -> OrderResponse:
    async with pool.connection() as conn, conn.transaction():
        order = await _load_order(conn, id, for_update=True)
        _check_owner(order, current_user)
        if order["status"] not in CANCELABLE_STATUSES:
            raise ApiError(
                409,
                ErrorCode.INVALID_STATE_TRANSITION,
                "Order cannot be canceled in its current status",
                {"current_status": order["status"], "allowed_statuses": list(CANCELABLE_STATUSES)},
            )
        await _release_items(conn, id)
        if order["promo_code_id"] is not None:
            await conn.execute(
                "UPDATE promo_codes SET current_uses = GREATEST(current_uses - 1, 0) WHERE id = %s",
                (order["promo_code_id"],),
            )
        await conn.execute("UPDATE orders SET status = 'CANCELED' WHERE id = %s", (id,))
        return await _build_response(conn, id)


async def change_order_status(*, id: UUID, body: OrderStatusChange, current_user: CurrentUser) -> OrderResponse:
    async with pool.connection() as conn, conn.transaction():
        order = await _load_order(conn, id, for_update=True)
        _check_owner(order, current_user)
        expected = NEXT_STATUS.get(order["status"])
        if expected != body.status.value:
            raise ApiError(
                409,
                ErrorCode.INVALID_STATE_TRANSITION,
                f"Cannot move order from {order['status']} to {body.status.value}",
                {
                    "current_status": order["status"],
                    "requested_status": body.status.value,
                    "allowed_transitions": [expected] if expected else [],
                },
            )
        await conn.execute("UPDATE orders SET status = %s::order_status WHERE id = %s", (body.status.value, id))
        return await _build_response(conn, id)
