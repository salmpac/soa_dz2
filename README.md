# Marketplace API — OpenAPI + CRUD + заказы

Контрактный сервис маркетплейса: Python 3.12, FastAPI, PostgreSQL 16, Flyway, JWT.
Контракт (`openapi/openapi.yaml`) — единственный источник правды: из него генерируются
модели и роутеры, по нему же выполняется валидация, авторизация и проверка ролей.

## Быстрый старт

```sh
docker compose up --build          # postgres -> flyway (миграции) -> app (кодогенерация в сборке)
curl localhost:8000/health
open http://localhost:8000/docs    # Swagger UI поверх openapi.yaml
```

Переменные окружения (`docker-compose.yml`): `ORDER_RATE_LIMIT_SECONDS` (N для лимита частоты,
по умолчанию 60), `ACCESS_TOKEN_TTL_SECONDS` (900), `REFRESH_TOKEN_TTL_SECONDS` (604800), `JWT_SECRET`.
Для защиты удобно: `ORDER_RATE_LIMIT_SECONDS=5 docker compose up --build`.

Локальная разработка (Nix):

```sh
nix develop            # python + uv, создаёт .venv и ставит зависимости
make db                # только postgres + flyway в docker
make run               # генерация кода + uvicorn на :8000
make test              # e2e-тесты против запущенного сервиса (ORDER_RATE_LIMIT_SECONDS=2 ускоряет)
make demo              # сквозной сценарий curl-ами (ORDER_RATE_LIMIT_SECONDS=5 make demo — с показом ORDER_HAS_ACTIVE)
make psql              # psql внутри контейнера
```

## Структура

```
openapi/openapi.yaml      контракт: эндпоинты, схемы, ограничения, ошибки, security, x-roles
codegen/templates/        Jinja-шаблоны роутеров для fastapi-code-generator
codegen/visitors.py       визитор: тянет x-roles и коды успешных ответов из спеки в шаблон
scripts/generate.sh       одна команда генерации -> app/generated (в .gitignore)
migrations/V1__init.sql   Flyway-миграция: таблицы, enum-типы, индексы, триггер updated_at
app/main.py               сборка FastAPI: пул БД, обработчики ошибок, логирование, роутеры
app/auth.py               JWT (access/refresh), bcrypt, get_current_user, require_roles
app/errors.py             ApiError и единый формат {error_code, message, details}
app/logging_mw.py         ASGI-middleware: JSON-лог каждого запроса, X-Request-Id
app/db.py                 psycopg 3 async pool
app/impl/{auth,products,orders,promo_codes}.py   бизнес-логика, SQL
tests/                    e2e-тесты (pytest + httpx) по всем сценариям
```

## Как это работает

**Кодогенерация.** `scripts/generate.sh` запускает `fastapi-codegen` с нашими шаблонами.
Результат — `app/generated/models.py` (Pydantic-модели со всеми ограничениями из спеки:
`minLength`, `minimum`, `pattern`, `enum`, `nullable`) и `app/generated/routers/*.py`.
Каждый сгенерированный хэндлер:
1. валидирует путь/query/body по сгенерированным моделям (невалидный запрос не доходит до бизнес-логики);
2. требует bearer-токен (если у операции есть `security`);
3. проверяет роль по `x-roles` операции (`Depends(require_roles([...]))`);
4. вызывает `app.impl.<tag>.<operationId>(...)` — ручную реализацию.

Ручных DTO нет: бизнес-код принимает и возвращает только сгенерированные модели.
`/openapi.json` и `/docs` отдают сам `openapi.yaml`, а не выведенную FastAPI схему.

**Ошибки.** Любая ошибка — `ApiError(status, error_code, message, details)`; обработчик в
`app/errors.py` превращает её в JSON по схеме `Error`. Ошибки валидации FastAPI
(`RequestValidationError`) переводятся в `400 VALIDATION_ERROR` со списком
`details.errors[{field, message, constraint}]`.

**Заказы** (`app/impl/orders.py`). Создание, обновление и отмена выполняются в одной
транзакции (`conn.transaction()`), строки товаров и промокода блокируются `SELECT ... FOR UPDATE`,
а строка пользователя — как мьютекс на его операции с заказами. Порядок проверок при создании
совпадает с заданием: лимит частоты -> активный заказ -> каталог -> остатки -> резерв ->
снапшот цен -> промокод -> запись операции.

Переход по состояниям — `POST /orders/{id}/status` (только следующий шаг цепочки
`CREATED -> PAYMENT_PENDING -> PAID -> SHIPPED -> COMPLETED`), отмена — `POST /orders/{id}/cancel`.

**Промокоды.** `PERCENTAGE`: `total * value / 100`, но если скидка превышает 70% суммы,
заказ оплачивается по полной цене (скидка 0) — так буквально сформулировано в задании.
`FIXED_AMOUNT`: `min(value, total)`. При обновлении заказа промокод проверяется заново;
если он больше не применим (в т.ч. сумма ниже `min_order_amount`) — скидка снимается,
`promo_code_id` обнуляется, `current_uses` уменьшается.

**Логи.** Одна JSON-строка на запрос: `request_id`, `method`, `endpoint`, `status_code`,
`duration_ms`, `user_id`, `timestamp`; для POST/PUT/DELETE — `request_body` с замаскированными
`password`/`token`/`secret`. `request_id` возвращается в заголовке `X-Request-Id`.

**Авторизация.** HS256-JWT. Access-токен (15 мин) несёт `sub`, `role`, `type=access`;
refresh-токен (7 дней) — `sub`, `type=refresh`. При refresh роль перечитывается из БД.
Роль задаётся при регистрации (учебное упрощение, чтобы показать все три роли на защите).

## Матрица ролей

| Операция                   | USER        | SELLER      | ADMIN |
|----------------------------|-------------|-------------|-------|
| `GET /products`, `GET /products/{id}` | да | да   | да    |
| `POST/PUT/DELETE /products` | 403        | только свои | любые |
| `POST /orders`             | да          | 403         | да    |
| `GET/PUT /orders/{id}`, `cancel`, `status` | только свои | 403 | любые |
| `POST /promo-codes`        | 403         | да          | да    |

Чужой товар для SELLER — `403 ACCESS_DENIED`, чужой заказ для USER — `403 ORDER_OWNERSHIP_VIOLATION`.

## Проверка в БД (для защиты)

```sql
SELECT id, name, price, stock, status, seller_id, created_at, updated_at FROM products;
SELECT id, user_id, status, promo_code_id, total_amount, discount_amount FROM orders;
SELECT * FROM order_items;
SELECT code, current_uses, max_uses, active FROM promo_codes;
SELECT * FROM user_operations ORDER BY created_at DESC;
SELECT indexname FROM pg_indexes WHERE tablename = 'products';   -- idx_products_status
SELECT * FROM flyway_schema_history;
```
