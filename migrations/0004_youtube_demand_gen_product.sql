-- Introduce a separate Demand Gen product without touching any existing
-- YouTube catalog row or administrator edit. Operational terms below mirror
-- the existing YouTube products and require Sales Planning review before merge.
DO $$
DECLARE
    anchor_order INTEGER;
BEGIN
    -- A fresh database has not had the full code catalog seeded yet; its
    -- normal first-read seed includes Demand Gen from app/catalog.py.
    IF NOT EXISTS (SELECT 1 FROM products) THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM products WHERE name = 'YouTube Ads - Demand Gen') THEN
        RETURN;
    END IF;

    SELECT sort_order INTO anchor_order
    FROM products WHERE name = 'YouTube Ads - Non-Skippable';
    IF anchor_order IS NULL THEN
        RAISE EXCEPTION 'YouTube Ads - Non-Skippable is missing from products';
    END IF;

    UPDATE products SET sort_order = sort_order + 1
    WHERE sort_order > anchor_order;

    INSERT INTO products (
        sort_order, name, family, short_label, proposal_description, sizes,
        buying_model, base_rate, estimated_impressions, discloses_impressions,
        minimum_spend, minimum_flight_days_min, minimum_flight_days_max,
        sla_data_days, sla_creative_days, sla_activate_days, sla_total_days,
        media_allocation_pct, margin_upper, margin_lower, tech_platform,
        wide_orbit_code, billing_interval, billing_calendar, billing_source,
        national_supported, notes, estimated_cpm_for_imps,
        hispanic_targeting_forced, cannabis_policy, political_policy, is_addon
    ) VALUES (
        anchor_order + 1, 'YouTube Ads - Demand Gen', 'Online Video', 'Demand Gen',
        'Demand Gen video advertising on YouTube, including eligible in-stream, in-feed and Shorts placements. Select YouTube channels in Google Ads when the proposal is for YouTube-only delivery; Demand Gen can otherwise serve on other Google surfaces. Targeting and placement eligibility are confirmed by Sales Planning.',
        'Video assets per selected YouTube placement',
        'Fixed', NULL, TRUE, FALSE,
        500.00, 30, 90, 1, 3, 7, 9,
        0, 0.4, 0.3, '',
        'YouTube Ads - Demand Gen', 'Monthly', 'Standard/prorated', '1st Party Actual',
        TRUE,
        '*Team planning estimate: $20 retail CPM. Restrict Demand Gen channels to YouTube when quoting YouTube-only delivery. Confirm campaign setup and operational eligibility with Sales Planning.',
        20.00, NULL, 'not_allowed', 'not_allowed', FALSE
    );
END $$;
