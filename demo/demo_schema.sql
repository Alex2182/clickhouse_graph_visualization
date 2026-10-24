-- Demo schema for manual testing of the graph app.
-- Apply: clickhouse-client --multiquery < demo/demo_schema.sql

CREATE DATABASE IF NOT EXISTS shop;

CREATE TABLE IF NOT EXISTS shop.users
(
    id UInt32,
    name String,
    country LowCardinality(String)
)
ENGINE = MergeTree ORDER BY id;

CREATE TABLE IF NOT EXISTS shop.products
(
    id UInt32,
    title String,
    price Decimal(10, 2)
)
ENGINE = MergeTree ORDER BY id;

CREATE TABLE IF NOT EXISTS shop.orders
(
    id UInt64,
    user_id UInt32,
    product_id UInt32,
    created_at DateTime,
    amount UInt32
)
ENGINE = MergeTree ORDER BY id;

CREATE TABLE IF NOT EXISTS shop.countries
(
    code String,
    name String
)
ENGINE = MergeTree ORDER BY code;

CREATE VIEW IF NOT EXISTS shop.v_user_totals AS
SELECT
    u.id AS user_id,
    u.name,
    u.country,
    count(o.id) AS orders_cnt
FROM shop.orders AS o
INNER JOIN shop.users AS u ON o.user_id = u.id
GROUP BY user_id, name, country;

CREATE TABLE IF NOT EXISTS shop.orders_daily
(
    day Date,
    product_id UInt32,
    total_amount UInt64
)
ENGINE = SummingMergeTree ORDER BY (day, product_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS shop.mv_orders_daily TO shop.orders_daily AS
SELECT
    toDate(created_at) AS day,
    product_id,
    sum(amount) AS total_amount
FROM shop.orders
GROUP BY day, product_id;

CREATE DICTIONARY IF NOT EXISTS shop.dict_countries
(
    code String,
    name String
)
PRIMARY KEY code
SOURCE(CLICKHOUSE(host 'localhost' port 9000 db 'shop' table 'countries'))
LIFETIME(MIN 300 MAX 360)
LAYOUT(FLAT());
