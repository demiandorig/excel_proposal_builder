-- 0005_forecast_map_win_rate.sql
--
-- Why: Constanza multiplies the avails of programmatic (MadHive / The Trade Desk) products by a win rate, because those avails
-- are bid opportunities, not delivered impressions. Which products that applies to is a built-in rule (every platform series
-- except Roku's; Prime Video, bought through the Amazon DSP, has a rate of its own), but it rests on how each product is bought, which the data files do not say (Netflix's
-- export is labelled "Direct", the Live Sports sheets are Entravision's own weekly estimates). Admin -> Forecast can therefore
-- set it per product: this column holds that choice.
--
-- NULL = use the built-in rule (every existing row), TRUE = always apply the win rate, FALSE = never. Safe on a database
-- with data: it only adds a nullable column, and re-running it is a no-op. schema.sql carries the same statement for a new
-- database and for startup (schema_bootstrap), so whichever runs first, the other finds the column already there.
DO $$
BEGIN
    IF to_regclass('public.forecast_product_map') IS NOT NULL THEN
        ALTER TABLE forecast_product_map ADD COLUMN IF NOT EXISTS win_rate BOOLEAN;
    END IF;
END $$;
