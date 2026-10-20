#!/usr/bin/env sh
# E2E walk-through for the defense. Requires: curl, python3, a running stack (make up).
#   sh scripts/demo.sh            # BASE_URL defaults to http://localhost:8000
set -eu
BASE_URL="${BASE_URL:-http://localhost:8000}"
SUFFIX=$(date +%s)

step() { printf '\n\033[1;34m== %s\033[0m\n' "$1"; }
call() { # call METHOD PATH [TOKEN] [JSON]
  method=$1; path=$2; token=${3:-}; data=${4:-}
  printf '\033[0;33m%s %s\033[0m' "$method" "$path"; [ -n "$data" ] && printf '  %s' "$data"; echo
  if [ -n "$data" ]; then
    curl -s -X "$method" "$BASE_URL$path" -H "Content-Type: application/json" ${token:+-H "Authorization: Bearer $token"} -d "$data" -w '\n(http %{http_code})\n'
  else
    curl -s -X "$method" "$BASE_URL$path" ${token:+-H "Authorization: Bearer $token"} -w '\n(http %{http_code})\n'
  fi
}
field() { python3 -c 'import sys,json; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
pretty() { python3 -m json.tool; }
json() { curl -s -X "$1" "$BASE_URL$2" -H "Content-Type: application/json" ${3:+-H "Authorization: Bearer $3"} ${4:+-d "$4"}; }

step "1. Register SELLER, USER, ADMIN and log in"
call POST /auth/register "" "{\"email\":\"seller-$SUFFIX@example.com\",\"password\":\"secret123\",\"role\":\"SELLER\"}"
call POST /auth/register "" "{\"email\":\"user-$SUFFIX@example.com\",\"password\":\"secret123\",\"role\":\"USER\"}"
call POST /auth/register "" "{\"email\":\"admin-$SUFFIX@example.com\",\"password\":\"secret123\",\"role\":\"ADMIN\"}"
SELLER=$(json POST /auth/login "" "{\"email\":\"seller-$SUFFIX@example.com\",\"password\":\"secret123\"}" | field access_token)
USER=$(json POST /auth/login "" "{\"email\":\"user-$SUFFIX@example.com\",\"password\":\"secret123\"}" | field access_token)
ADMIN=$(json POST /auth/login "" "{\"email\":\"admin-$SUFFIX@example.com\",\"password\":\"secret123\"}" | field access_token)
echo "tokens received (access, 15 min; refresh, 7 days)"

step "2. Authorization errors"
call GET /products
call GET /products "not.a.jwt"
call POST /products "$USER" '{"name":"x","price":1,"stock":1,"category":"c"}'

step "3. SELLER creates products; validation error example"
P1=$(json POST /products "$SELLER" '{"name":"Keyboard","description":"87 keys","price":4999.90,"stock":10,"category":"electronics"}')
echo "$P1" | pretty
P1_ID=$(echo "$P1" | field id)
P2_ID=$(json POST /products "$SELLER" '{"name":"Mouse","price":1500,"stock":3,"category":"electronics"}' | field id)
P3_ID=$(json POST /products "$SELLER" '{"name":"Old monitor","price":9000,"stock":1,"category":"electronics","status":"INACTIVE"}' | field id)
call POST /products "$SELLER" '{"name":"","price":0,"stock":-1,"category":""}'

step "4. List with pagination and filters"
call GET "/products?category=electronics&status=ACTIVE&page=0&size=20" "$USER"

step "5. Promo code (SELLER): 10%, min order 5000, 1 use"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ); LATER=$(date -u -d '+1 year' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+1y +%Y-%m-%dT%H:%M:%SZ)
call POST /promo-codes "$SELLER" "{\"code\":\"SALE10\",\"discount_type\":\"PERCENTAGE\",\"discount_value\":10,\"min_order_amount\":5000,\"max_uses\":1,\"valid_from\":\"2000-01-01T00:00:00Z\",\"valid_until\":\"$LATER\"}"

step "6. Orders: business errors"
call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P3_ID\",\"quantity\":1}]}"
call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P2_ID\",\"quantity\":5}]}"
call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P2_ID\",\"quantity\":1}],\"promo_code\":\"SALE10\"}"
call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P2_ID\",\"quantity\":1}],\"promo_code\":\"NOPE\"}"
call POST /orders "$SELLER" "{\"items\":[{\"product_id\":\"$P2_ID\",\"quantity\":1}]}"

step "7. USER creates an order with the promo code (stock reserved, price snapshot, discount)"
ORDER=$(json POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P1_ID\",\"quantity\":1},{\"product_id\":\"$P2_ID\",\"quantity\":2}],\"promo_code\":\"SALE10\"}")
echo "$ORDER" | pretty
ORDER_ID=$(echo "$ORDER" | field id)
call GET "/products/$P2_ID" "$USER"

step "8. Rate limit (N = ORDER_RATE_LIMIT_SECONDS) and active-order check"
call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P1_ID\",\"quantity\":1}]}"
LIMIT="${ORDER_RATE_LIMIT_SECONDS:-60}"
if [ "$LIMIT" -le 10 ]; then
  echo "waiting ${LIMIT}s for the rate limit window..."; sleep "$LIMIT"
  call POST /orders "$USER" "{\"items\":[{\"product_id\":\"$P1_ID\",\"quantity\":1}]}"
else
  echo "(set ORDER_RATE_LIMIT_SECONDS<=10 on the server and in this shell to also see ORDER_HAS_ACTIVE)"
fi

step "9. State machine"
call POST "/orders/$ORDER_ID/status" "$USER" '{"status":"PAID"}'
call POST "/orders/$ORDER_ID/status" "$USER" '{"status":"PAYMENT_PENDING"}'
call PUT  "/orders/$ORDER_ID" "$USER" "{\"items\":[{\"product_id\":\"$P1_ID\",\"quantity\":1}]}"

step "10. Ownership: ADMIN may read any order, another user may not"
call GET "/orders/$ORDER_ID" "$ADMIN"
call GET "/orders/$ORDER_ID" "$SELLER"

step "11. Cancel: stock and promo use are returned"
call POST "/orders/$ORDER_ID/cancel" "$USER"
call GET "/products/$P2_ID" "$USER"

step "12. Soft delete"
call DELETE "/products/$P3_ID" "$SELLER"

cat <<SQL

== Check the database (make psql):
SELECT id, name, price, stock, status, seller_id FROM products ORDER BY created_at DESC LIMIT 5;
SELECT id, user_id, status, promo_code_id, total_amount, discount_amount FROM orders ORDER BY created_at DESC LIMIT 5;
SELECT * FROM order_items WHERE order_id = '$ORDER_ID';
SELECT code, current_uses, max_uses FROM promo_codes;
SELECT user_id, operation_type, created_at FROM user_operations ORDER BY created_at DESC LIMIT 5;
SQL
