-- Align the persisted seed value with the planning team's confirmed $12
-- retail Awareness CPM. Compare-and-set preserves any admin-edited rate.
UPDATE products
SET estimated_cpm_for_imps = 12.00
WHERE name = 'Facebook & Instagram Ads | Awareness'
  AND estimated_cpm_for_imps = 13.00;
