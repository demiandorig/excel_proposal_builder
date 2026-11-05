-- 0006_forecast_map_method_open.sql
--
-- Why: Admin -> Forecast chooses, per catalog product, what Constanza's "Auto-calculate avails" computes. The method column listed
-- the allowed names ('platform', 'generic', 'tiktok'), so every new source of an estimate (Meta is the second live API) would have
-- needed a migration of its own, and a published deployment only gets a CHECK change through a migration plus Publish's schema diff.
-- Which methods exist is now the registry in app/services/constanza_methods.py (a save is validated by constanza_forecast_map.clean_config,
-- a row is validated when it is read, and a name no method owns is ignored with a warning), so the database only checks that the value
-- looks like a method key: a lower-case word of 2-32 letters, digits and underscores. From now on adding a method needs no SQL.
--
-- Safe on a database with data: every existing value ('platform', 'generic', 'tiktok') matches the new format, nothing is dropped or
-- rewritten, and re-running it is a no-op (it drops the constraint if it exists and adds it again). schema.sql, the baseline for a new
-- database, carries the same check, so on a fresh install this finds the constraint already as wanted and recreates it unchanged.
-- Do not re-run 0004 after this: it narrows the check back to three names. This file is the latest word on the method check.
DO $$
BEGIN
    IF to_regclass('public.forecast_product_map') IS NOT NULL THEN
        ALTER TABLE forecast_product_map DROP CONSTRAINT IF EXISTS forecast_product_map_method_check;
        ALTER TABLE forecast_product_map ADD CONSTRAINT forecast_product_map_method_check
            CHECK (method ~ '^[a-z][a-z0-9_]{1,31}$');
    END IF;
END $$;
