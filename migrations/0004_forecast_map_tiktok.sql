-- 0004_forecast_map_tiktok.sql
--
-- Why: Admin -> Forecast chooses, per catalog product, what Constanza's "Auto-calculate avails" computes. Until now that was
-- 'platform' (loaded avails data) or 'generic' (a population-share estimate). The TikTok ads product is now forecast from
-- TikTok's own audience-size API, which is a third thing, so the method column accepts 'tiktok' too.
--
-- Safe on a database with data: it only widens a CHECK (every existing row still satisfies it). schema.sql, the baseline for a
-- new database, carries the same wider check, so on a fresh install this finds the constraint already as wanted and
-- recreates it unchanged. Nothing is dropped or rewritten.
DO $$
BEGIN
    IF to_regclass('public.forecast_product_map') IS NOT NULL THEN
        ALTER TABLE forecast_product_map DROP CONSTRAINT IF EXISTS forecast_product_map_method_check;
        ALTER TABLE forecast_product_map ADD CONSTRAINT forecast_product_map_method_check
            CHECK (method IN ('platform', 'generic', 'tiktok'));
    END IF;
END $$;
