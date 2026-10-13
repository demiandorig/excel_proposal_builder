"""Smoke-test Constanza's geo resolver against realistic free-text geo strings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.constanza_geo import resolve_geo

print("=" * 80)
print("Constanza geo resolver")
print("=" * 80)

r = resolve_geo("National")
assert r.method == "national" and len(r.dmas) == 210
print("National -> 210 DMAs: OK")

r = resolve_geo("")
assert r.method == "national" and len(r.dmas) == 210
print("blank -> National (210 DMAs): OK")

r = resolve_geo("Los Angeles DMA")
assert r.method == "exact_dma" and r.dmas == ["Los Angeles"]
print("'Los Angeles DMA' -> exact_dma ['Los Angeles']: OK")

r = resolve_geo("Dallas/Fort Worth")
assert r.method == "exact_dma" and r.dmas == ["Dallas-Ft. Worth"], r
print("'Dallas/Fort Worth' -> exact_dma ['Dallas-Ft. Worth']: OK")

r = resolve_geo("Los Angeles, San Diego, Fresno")
assert r.method == "exact_dma" and set(r.dmas) == {"Los Angeles", "San Diego", "Fresno-Visalia"}, r
assert not r.unmatched_tokens
print("'Los Angeles, San Diego, Fresno' -> 3 exact DMAs: OK")

r = resolve_geo("CA, NV, AZ")
assert r.method == "state_list"
assert "Los Angeles" in r.dmas and "Phoenix (Prescott)" in r.dmas
assert len(r.dmas) > 3  # multiple DMAs per state
print(f"'CA, NV, AZ' -> state_list, {len(r.dmas)} DMAs: OK")

r = resolve_geo("New York, TX")
assert r.method == "state_list"
assert "New York" in r.dmas and "Houston" in r.dmas
print("'New York, TX' (state name + abbr mixed) -> state_list: OK")

r = resolve_geo("Southern California")
assert r.method == "unresolved" and not r.dmas
print("'Southern California' (ambiguous, not a real DMA) -> unresolved: OK")

r = resolve_geo("Nowhereville")
assert r.method == "unresolved" and not r.dmas
print("'Nowhereville' -> unresolved: OK")

r = resolve_geo("Los Angeles, San Diego, Neverland")
assert r.method == "exact_dma"
assert r.unmatched_tokens == ["Neverland"]
assert set(r.dmas) == {"Los Angeles", "San Diego"}
print("partially-resolvable string surfaces unmatched tokens instead of guessing: OK")

print()
print("All Constanza geo resolver checks passed.")
