-- Marketplace schema (Flyway migration V1)

CREATE TYPE user_role       AS ENUM ('USER', 'SELLER', 'ADMIN');
CREATE TYPE product_status  AS ENUM ('ACTIVE', 'INACTIVE', 'ARCHIVED');
CREATE TYPE order_status    AS ENUM ('CREATED', 'PAYMENT_PENDING', 'PAID', 'SHIPPED', 'COMPLETED', 'CANCELED');
CREATE TYPE discount_type   AS ENUM ('PERCENTAGE', 'FIXED_AMOUNT');
CREATE TYPE operation_type  AS ENUM ('CREATE_ORDER', 'UPDATE_ORDER');

-- updated_at is maintained by a trigger so that the application cannot forget it
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE users (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255)  NOT NULL UNIQUE,
    password_hash TEXT          NOT NULL,
    role          user_role     NOT NULL DEFAULT 'USER',
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255)    NOT NULL,
    description VARCHAR(4000),
    price       NUMERIC(12, 2)  NOT NULL CHECK (price > 0),
    stock       INTEGER         NOT NULL CHECK (stock >= 0),
    category    VARCHAR(100)    NOT NULL,
    status      product_status  NOT NULL DEFAULT 'ACTIVE',
    seller_id   UUID            NOT NULL REFERENCES users (id),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX idx_products_status   ON products (status);
CREATE INDEX idx_products_category ON products (category);
CREATE INDEX idx_products_seller   ON products (seller_id);
CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE promo_codes (
    id               UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    code             VARCHAR(20)     NOT NULL UNIQUE,
    discount_type    discount_type   NOT NULL,
    discount_value   NUMERIC(12, 2)  NOT NULL CHECK (discount_value > 0),
    min_order_amount NUMERIC(12, 2)  NOT NULL DEFAULT 0 CHECK (min_order_amount >= 0),
    max_uses         INTEGER         NOT NULL CHECK (max_uses > 0),
    current_uses     INTEGER         NOT NULL DEFAULT 0 CHECK (current_uses >= 0),
    valid_from       TIMESTAMPTZ     NOT NULL,
    valid_until      TIMESTAMPTZ     NOT NULL,
    active           BOOLEAN         NOT NULL DEFAULT TRUE,
    CHECK (valid_until > valid_from)
);

CREATE TABLE orders (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID            NOT NULL REFERENCES users (id),
    status          order_status    NOT NULL DEFAULT 'CREATED',
    promo_code_id   UUID            REFERENCES promo_codes (id),
    total_amount    NUMERIC(12, 2)  NOT NULL CHECK (total_amount >= 0),
    discount_amount NUMERIC(12, 2)  NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_user_status ON orders (user_id, status);
CREATE TRIGGER trg_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE order_items (
    id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id       UUID            NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id     UUID            NOT NULL REFERENCES products (id),
    quantity       INTEGER         NOT NULL CHECK (quantity > 0),
    price_at_order NUMERIC(12, 2)  NOT NULL CHECK (price_at_order > 0)
);
CREATE INDEX idx_order_items_order ON order_items (order_id);

CREATE TABLE user_operations (
    id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID            NOT NULL REFERENCES users (id),
    operation_type operation_type  NOT NULL,
    created_at     TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_operations_lookup ON user_operations (user_id, operation_type, created_at DESC);
