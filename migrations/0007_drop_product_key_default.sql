-- 0007_drop_product_key_default.sql
--
-- The stable-key trigger assigns readable, de-duplicated slugs. A UUID column
-- default is evaluated before the BEFORE INSERT trigger and makes the trigger
-- treat the key as explicitly supplied, bypassing that assignment.
ALTER TABLE products ALTER COLUMN product_key DROP DEFAULT;
