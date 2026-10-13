"""Smoke-test Constanza's international (non-US, country-level-only) geo resolver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.constanza_geo import resolve_geo

print("=" * 80)
print("Constanza international geo resolver")
print("=" * 80)

r = resolve_geo("Mexico")
assert r.method == "country" and r.geo_confidence == "low_confidence_international"
assert len(r.areas) == 1 and r.areas[0].dma is None
assert 100_000_000 < r.areas[0].area_population < 200_000_000
print(f"'Mexico' -> country, pop {r.areas[0].area_population:,.0f}, flagged low-confidence: OK")

r2 = resolve_geo("France")
assert r2.method == "country"
assert 50_000_000 < r2.areas[0].area_population < 80_000_000
print(f"'France' -> country, pop {r2.areas[0].area_population:,.0f}: OK")

r3 = resolve_geo("UK")  # common alias, not UN WPP's formal "United Kingdom" name
assert r3.method == "country"
print(f"'UK' alias -> {r3.areas[0].label}: OK")

# A real US town also happens to be named "Mexico" (Mexico, ME / Mexico, NY)
# — the country must win for a bare country-shaped query rather than
# colliding with the weak city-name fallback.
r4 = resolve_geo("Mexico", geo_type_hint=None)
assert r4.areas[0].dma is None  # country, not a US town
print("'Mexico' does not collide with the US towns of the same name: OK")

r5 = resolve_geo("Nowhereistan")
assert r5.method == "unresolved"
print("'Nowhereistan' -> unresolved: OK")

print()
print("All Constanza international geo checks passed.")
