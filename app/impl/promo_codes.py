"""Implementation of the `promo-codes` operations plus discount rules shared with orders."""
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from app.auth import CurrentUser
from app.db import pool
from app.errors import ApiError
from app.generated.models import DiscountType, ErrorCode, PromoCodeCreate, PromoCodeResponse

CENT = Decimal("0.01")
MAX_PERCENTAGE_SHARE = Decimal("0.70")  # a percentage discount may not exceed 70% of the order total

PROMO_COLUMNS = (
    "id, code, discount_type, discount_value, min_order_amount, max_uses, current_uses, "
    "valid_from, valid_until, active"
)


async def create_promo_code(*, body: PromoCodeCreate, current_user: CurrentUser) -> PromoCodeResponse:
    if body.valid_until <= body.valid_from:
        raise ApiError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            {"errors": [{"field": "body.valid_until", "message": "must be after valid_from", "constraint": "date_range"}]},
        )
    if body.discount_type == DiscountType.PERCENTAGE and body.discount_value > 100:
        raise ApiError(
            400,
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            {"errors": [{"field": "body.discount_value", "message": "percentage must be <= 100", "constraint": "less_than_equal"}]},
        )
    async with pool.connection() as conn:
        try:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO promo_codes
                        (code, discount_type, discount_value, min_order_amount, max_uses, valid_from, valid_until, active)
                    VALUES (%s, %s::discount_type, %s, %s, %s, %s, %s, %s)
                    RETURNING {PROMO_COLUMNS}
                    """,
                    (
                        body.code,
                        body.discount_type.value,
                        body.discount_value,
                        body.min_order_amount,
                        body.max_uses,
                        body.valid_from,
                        body.valid_until,
                        body.active,
                    ),
                )
            ).fetchone()
        except UniqueViolation:
            raise ApiError(409, ErrorCode.PROMO_CODE_ALREADY_EXISTS, "Promo code already exists", {"code": body.code})
    return PromoCodeResponse(**row)


# ----------------------------------------------------------------- rules shared with orders
async def lock_promo_by_code(conn: AsyncConnection, code: str) -> Optional[dict]:
    return await (
        await conn.execute(f"SELECT {PROMO_COLUMNS} FROM promo_codes WHERE code = %s FOR UPDATE", (code,))
    ).fetchone()


async def lock_promo_by_id(conn: AsyncConnection, promo_id) -> Optional[dict]:
    return await (
        await conn.execute(f"SELECT {PROMO_COLUMNS} FROM promo_codes WHERE id = %s FOR UPDATE", (promo_id,))
    ).fetchone()


def promo_is_valid(promo: Optional[dict], now: datetime, *, already_counted: bool = False) -> bool:
    """active = true, current_uses < max_uses, now within [valid_from, valid_until].

    already_counted=True when re-validating a promo that this very order already incremented.
    """
    if promo is None or not promo["active"]:
        return False
    uses = promo["current_uses"] - (1 if already_counted else 0)
    if uses >= promo["max_uses"]:
        return False
    return promo["valid_from"] <= now <= promo["valid_until"]


def compute_discount(promo: dict, subtotal: Decimal) -> Decimal:
    """Discount for a promo that already passed validity and min_order_amount checks.

    PERCENTAGE: subtotal * value / 100, but no more than 70% of the subtotal — if the percentage
                discount would exceed 70%, the order is charged at full price (discount 0).
    FIXED_AMOUNT: min(value, subtotal).
    """
    if promo["discount_type"] == DiscountType.PERCENTAGE.value:
        discount = (subtotal * promo["discount_value"] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        if discount > (subtotal * MAX_PERCENTAGE_SHARE).quantize(CENT, rounding=ROUND_HALF_UP):
            return Decimal("0.00")
        return discount
    return min(Decimal(promo["discount_value"]), subtotal).quantize(CENT, rounding=ROUND_HALF_UP)
