"""
Builds Project Constanza's US sub-DMA geo crosswalks: county, ZCTA
(ZIP-code proxy), and Census Place (city) to DMA, plus congressional
district to DMA (via the official Census CD-to-ZCTA relationship file,
population-weighted with ACS ZCTA population).

Method: real Nielsen DMA polygon boundaries (community-maintained, see
NIELSEN_DMA_GEOJSON_URL) intersected against Census TIGER/Line
cartographic boundaries for each geography type — an AREA-weighted split
when a geography crosses a DMA boundary. This is the dasymetric-mapping
approach from the Phase 2 research: real boundaries, not an assumption of
even distribution. (The Harvard Dataverse county/ZIP/DMA crosswalk the
original research cited was deaccessioned by its maintainer in 2021 at
Nielsen's request — this script's spatial-join approach was built as the
replacement after verifying that dataset no longer exists.)

Where a downstream weight ideally wants POPULATION (not raw area) — most
importantly congressional districts — the CD -> DMA split is built from
the finer CD-to-ZCTA relationship file: each (CD, ZCTA) piece carries that
ZCTA's real ACS population x the fraction of the ZCTA's land inside the
CD, then that ZCTA's own DMA split. (The earlier CD -> county route
silently dropped Connecticut, whose 2023 county-equivalents — planning
regions — don't exist in the 2020 relationship file's county FIPS, and was
coarser besides.)

Alaska's three DMAs (Anchorage, Fairbanks, Juneau) have no polygon in the
community Nielsen file, and Nielsen's borough/census-area assignment is
not something this script should guess — Alaska sub-DMA geographies are
left unresolved (the resolver reports that explicitly) while Alaska DMA /
state queries keep working off the workbook. Honolulu's DMA is exactly
the state of Hawaii, so its polygon is synthesized from the Hawaii
counties.

Run the shapefile downloads once first (see README at the top of main()),
then:
    python scripts/extract_constanza_geo_crosswalks.py --shp-dir /tmp/constanza_shp

Writes (gzip-compressed — see read_json_maybe_gz), under app/data/constanza/geo/:
    county_dma.json.gz    — county FIPS -> [{dma, share}], area-weighted
    county_names.json.gz  — county FIPS -> {name, state_fips}
    zcta_dma.json.gz      — ZCTA5 -> [{dma, share}], area-weighted
    place_dma.json.gz     — {"shares": place GEOID -> [{dma, share}], "names": ...}
    cd_dma.json.gz        — {"_meta", "cd_dma": STATEFP+CD -> [{dma, share}]}, ACS-ZCTA-population-weighted
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "data" / "constanza" / "geo"

# The repo's own .geojson exports are broken (they dump the TopoJSON's raw
# quantized/delta-encoded arc integers without applying the topology's
# scale+translate transform or delta-decoding them — verified by hand:
# the "coordinates" in nielsen-mkt-map.json/_simplified.json don't
# correspond to their own stated lat/long properties at all). The
# .topojson is the actual source of truth; this script decodes it itself
# per the TopoJSON spec instead of trusting the repo's GeoJSON conversion.
NIELSEN_DMA_TOPOJSON_URL = "https://raw.githubusercontent.com/simzou/nielsen-dma/master/nielsentopo.json"
CD_ZCTA_REL_FILE_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld/tab20_cd11920_zcta520_natl.txt"

sys.path.insert(0, str(REPO_ROOT))
from app.data.constanza import load_dma_reference, read_json_maybe_gz, write_json_gz  # noqa: E402
from app.services.constanza_geo import _normalize, _dma_candidates  # noqa: E402

# geojson dma_name (before the trailing ", ST"/", ST-ST" suffix) -> exact
# dma_reference.json name, for the ~27 DMAs whose heavily-Nielsen-
# abbreviated workbook name ("Cedar Rapids-Wtrlo-IWC&Dub") doesn't
# normalize-match the geojson's fuller name ("Cedar Rapids-Waterloo-Iowa
# City & Dubuque") even after segment-splitting. Built by hand once,
# diffing the two 210-ish-item name lists — see the Phase 2 build notes.
DMA_NAME_ALIASES = {
    "idaho falls-pocatello": "Idaho Fals-Pocatllo(Jcksn)",
    "wilkes barre-scranton": "Wilkes Barre-Scranton-Hztn",
    "lincoln & hastings-kearney": "Lincoln & Hastings-Krny",
    "birmingham (anniston and tuscaloosa)": "Birmingham (Ann and Tusc)",
    "orlando-daytona beach-melbourne": "Orlando-Daytona Bch-Melbrn",
    "norfolk-portsmouth-newport news": "Norfolk-Portsmth-Newpt Nws",
    "grand rapids-kalamazoo-battle creek": "Grand Rapids-Kalmzoo-B.Crk",
    "tri-cities": "Tri-Cities, TN-VA",
    "harrisburg-lancaster-lebanon-york": "Harrisburg-Lncstr-Leb-York",
    "santa barbara-santa maria-san luis obispo": "Santa Barbra-Sntmra-Slobs",
    "yakima-pasco-richland-kennewick": "Yakima-Pasco-Rchlnd-Knnwck",
    "tampa-st. petersburg (sarasota)": "Tampa-St. Pete (Sarasota)",
    "cedar rapids-waterloo-iowa city & dubuque": "Cedar Rapids-Wtrlo-IWC&Dub",
    "wichita-hutchinson": "Wichita-Hutchinson Plus",
    "champaign & springfield-decatur": "Champaign&Sprngfld-Decatur",
    "huntsville-decatur (florence)": "Huntsville-Decatur (Flor)",
    "ft. smith-fayetteville-springdale-rogers": "Ft. Smith-Fay-Sprngdl-Rgrs",
    "columbus-tupelo-west point": "Columbus-Tupelo-W Pnt-Hstn",
    "minot-bismarck-dickinson(williston)": "Minot-Bsmrck-Dcknsn(Wlstn)",
    "johnstown-altoona": "Johnstown-Altoona-St Colge",
    "greensboro-high point-winston salem": "Greensboro-H.Point-W.Salem",
    "wichita falls": "Wichita Falls & Lawton",
    "greenville-spartanburg": "Greenville-Spart-Ashevll-And",
    "tyler-longview(lufkin & nacogdoches)": "Tyler-Longview(Lfkn&Ncgd)",
    "greenville-new bern-washington": "Greenville-N.Bern-Washngtn",
    "harlingen-weslaco-brownsville-mcallen": "Harlingen-Wslco-Brnsvl-McA",
}


# Keyed by the geojson's FULL normalized name, for the rare polygon whose
# first-comma base ("Rochester") collides with a different DMA's base AND
# whose workbook name is too abbreviated/misspelled to match by segment
# ("Rochestr-Mason City-Austin" — the workbook's own spelling). Without
# this the Minnesota polygon was silently labeled "Rochester, NY": two
# polygons shared one label, Rochester NY came out 31% over its workbook
# population, and the real Rochester MN DMA had zero coverage.
DMA_FULLNAME_ALIASES = {
    "rochester, mn-mason city, ia-austin, mn": "Rochestr-Mason City-Austin",
}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _decode_topojson(topo: dict) -> list[dict]:
    """Minimal TopoJSON -> [{"properties": ..., "geometry": shapely geometry}]
    decoder: delta-decodes each arc, applies the topology's quantization
    transform, and assembles Polygon/MultiPolygon geometries from arc
    index lists (per the TopoJSON spec: a negative arc index ~i means
    "arc i, reversed")."""
    scale = topo["transform"]["scale"]
    translate = topo["transform"]["translate"]

    def decode_arc(arc):
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((translate[0] + scale[0] * x, translate[1] + scale[1] * y))
        return pts

    arcs = [decode_arc(a) for a in topo["arcs"]]

    def arc_coords(i: int) -> list:
        return arcs[i] if i >= 0 else list(reversed(arcs[~i]))

    def ring(arc_indices: list[int]) -> list:
        coords = []
        for i in arc_indices:
            pts = arc_coords(i)
            coords.extend(pts[1:] if coords else pts)  # consecutive arcs share their join point
        return coords

    features = []
    for obj in topo["objects"].values():
        for geom in obj["geometries"]:
            if geom["type"] == "Polygon":
                rings = [ring(r) for r in geom["arcs"]]
                shape = Polygon(rings[0], rings[1:]) if rings else None
            elif geom["type"] == "MultiPolygon":
                polys = []
                for poly_arcs in geom["arcs"]:
                    rings = [ring(r) for r in poly_arcs]
                    if rings:
                        polys.append(Polygon(rings[0], rings[1:]))
                shape = MultiPolygon(polys) if polys else None
            else:
                continue
            if shape is None or shape.is_empty:
                continue
            if not shape.is_valid:
                # A handful of coastal/multi-part DMAs decode to a
                # self-intersecting MultiPolygon at this quantization
                # level (ring touches/overlaps at a shared vertex) —
                # make_valid() repairs this without materially changing
                # the shape, which is all that's needed for an area-
                # weighted overlay (not survey-grade cartography). It can
                # return a GeometryCollection mixing in degenerate
                # points/lines from the repair — keep only the polygonal
                # part, which is all an area-weighted overlay needs.
                shape = make_valid(shape)
                if shape.geom_type == "GeometryCollection":
                    polys = [g for g in shape.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
                    shape = MultiPolygon([p for g in polys for p in (g.geoms if g.geom_type == "MultiPolygon" else [g])]) if polys else None
            if shape is not None and shape.is_valid and not shape.is_empty:
                features.append({"properties": geom.get("properties", {}), "geometry": shape})
    return features


HAWAII_STATEFP = "15"


def _synthesize_honolulu(shp_dir: Path) -> gpd.GeoSeries:
    """Nielsen's Honolulu DMA is the entire state of Hawaii, which the
    community polygon file omits — dissolve the Hawaii counties instead.
    Returned in EPSG:4326 to match the decoded Nielsen polygons."""
    counties = gpd.read_file(shp_dir / "county" / "cb_2023_us_county_500k.shp")
    hawaii = counties[counties["STATEFP"].astype(str) == HAWAII_STATEFP].to_crs("EPSG:4326")
    return hawaii.geometry.union_all() if hasattr(hawaii.geometry, "union_all") else hawaii.geometry.unary_union


def load_dma_polygons(shp_dir: Path) -> gpd.GeoDataFrame:
    """Real Nielsen DMA boundaries, name-matched to our own dma_reference.json."""
    topo = json.loads(_fetch(NIELSEN_DMA_TOPOJSON_URL))
    features = _decode_topojson(topo)
    gdf = gpd.GeoDataFrame(
        {"dma_name": [f["properties"].get("dma1", "") for f in features]},
        geometry=[f["geometry"] for f in features],
        crs="EPSG:4326",
    )

    my_dma_list = list(load_dma_reference())
    my_dmas = {d.dma for d in my_dma_list}
    by_norm_full: dict[str, list] = defaultdict(list)
    by_norm_segment: dict[str, list] = defaultdict(list)
    for d in my_dma_list:
        by_norm_full[_normalize(d.dma)].append(d)
        for seg in _dma_candidates(d.dma):
            by_norm_segment[_normalize(seg)].append(d)

    aliases_normalized = {_normalize(k): v for k, v in DMA_NAME_ALIASES.items()}
    STATE_RE = re.compile(r"\b([A-Z]{2})\b")

    def match(dma_name: str) -> str | None:
        # A short common first-word ("Columbus", "Greenville", ...) can
        # legitimately be a segment of SEVERAL of our 210 DMA names
        # ("Columbus, OH" / "Columbus, GA (Opelika, AL)" / "Columbus-
        # Tupelo-W Pnt-Hstn") — discarding everything after the first
        # comma (as an earlier version of this script did) throws away
        # exactly the state code needed to disambiguate them, and picking
        # "whichever candidate happens to match first" silently produced
        # a wrong match (verified: an Alabama county was mis-tagged as
        # partly in "Columbus, OH"). Every state code actually present in
        # the geojson's own name is required to match one of the
        # candidate DMA's `state` field before accepting it.
        full_key = dma_name.strip().lower()
        if full_key in DMA_FULLNAME_ALIASES:
            return DMA_FULLNAME_ALIASES[full_key]
        base = re.split(r",", dma_name)[0].strip()
        norm = _normalize(base)
        states_in_name = set(STATE_RE.findall(dma_name))

        candidates = None
        if norm in aliases_normalized:
            return aliases_normalized[norm]
        if norm in by_norm_full:
            candidates = by_norm_full[norm]
        elif norm in by_norm_segment:
            candidates = by_norm_segment[norm]
        if not candidates:
            return None
        if len(candidates) == 1:
            # A lone candidate still has to agree with the state codes in
            # the polygon's own name — "Rochester, MN-..." must not be
            # accepted as "Rochester, NY" just because it was the only
            # name-segment hit.
            if states_in_name and candidates[0].state not in states_in_name:
                return None
            return candidates[0].dma
        state_matched = [d for d in candidates if d.state in states_in_name]
        if len(state_matched) == 1:
            return state_matched[0].dma
        # Still ambiguous even after state filtering (or the geojson name
        # carried no state code at all) — refuse rather than guess; the
        # caller reports this as an unmatched polygon.
        return None

    gdf["dma"] = gdf["dma_name"].apply(match)
    unmatched = gdf[gdf["dma"].isna()]["dma_name"].tolist()
    if unmatched:
        print(f"  (unmatched Nielsen DMA polygons, skipped: {unmatched})")
    gdf = gdf[gdf["dma"].notna()]
    dupes = gdf["dma"][gdf["dma"].duplicated()].unique().tolist()
    if dupes:
        raise RuntimeError(f"Two Nielsen polygons were given the same DMA label {dupes} — a name-matching bug "
                           "(this is exactly how Rochester MN got swallowed by Rochester NY); fix the aliases above.")
    if "Honolulu" in my_dmas and "Honolulu" not in set(gdf["dma"]):
        hi = gpd.GeoDataFrame({"dma": ["Honolulu"]}, geometry=[_synthesize_honolulu(shp_dir)], crs="EPSG:4326")
        gdf = pd.concat([gdf, hi], ignore_index=True)
        print("  (Honolulu DMA polygon synthesized from the Hawaii counties)")
    no_polygon = sorted(my_dmas - set(gdf["dma"]))
    if no_polygon:
        print(f"  (DMAs with NO polygon in the Nielsen file — must be synthesized or left unsupported: {no_polygon})")
    gdf = gdf.to_crs("EPSG:5070")  # CONUS Albers Equal Area — for accurate area weighting
    return gdf[["dma", "geometry"]]


# The community Nielsen polygons are simplified/quantized, so a county or
# ZIP that sits cleanly inside one DMA still "overlaps" its neighbor along
# the boundary by a few percent (measured: 1,992 counties overlap >1 DMA,
# yet 1,977 of those are >90% inside one — boundary noise, not real
# splits; Nielsen itself assigns whole counties to a single DMA). Without
# this collapse a planner sees a spurious second DMA on most border-area
# ZIPs/counties/cities. A geography >= SNAP_SHARE inside one DMA is assigned
# wholly to it; below that, shares under MIN_SPLIT_SHARE are dropped as
# noise and the rest renormalized.
SNAP_SHARE = 0.90
MIN_SPLIT_SHARE = 0.02


def _denoise_shares(shares: list[dict]) -> list[dict]:
    top = max(shares, key=lambda s: s["share"])
    if top["share"] >= SNAP_SHARE:
        return [{"dma": top["dma"], "share": 1.0}]
    kept = [s for s in shares if s["share"] >= MIN_SPLIT_SHARE]
    total = sum(s["share"] for s in kept)
    return [{"dma": s["dma"], "share": round(s["share"] / total, 6)} for s in sorted(kept, key=lambda s: -s["share"])]


def _area_weighted_dma_shares(features: gpd.GeoDataFrame, id_col: str, dma_polys: gpd.GeoDataFrame) -> dict[str, list[dict]]:
    """For each feature, its area-weighted share across every DMA polygon
    it intersects. A feature fully inside one DMA gets a single
    {dma, share=1.0} entry; a feature straddling N DMAs gets N entries
    summing to ~1.0 (rounding aside)."""
    features = features.to_crs("EPSG:5070")
    features["_area"] = features.geometry.area
    overlay = gpd.overlay(features[[id_col, "_area", "geometry"]], dma_polys, how="intersection", keep_geom_type=False)
    overlay["_part_area"] = overlay.geometry.area

    result: dict[str, list[dict]] = defaultdict(list)
    grouped = overlay.groupby([id_col, "dma"])["_part_area"].sum().reset_index()
    totals = grouped.groupby(id_col)["_part_area"].transform("sum")
    grouped["share"] = grouped["_part_area"] / totals
    for _, row in grouped.iterrows():
        if row["share"] <= 0:
            continue
        result[str(row[id_col])].append({"dma": row["dma"], "share": round(float(row["share"]), 6)})
    return {key: _denoise_shares(shares) for key, shares in result.items()}


def build_county_dma(shp_dir: Path, dma_polys: gpd.GeoDataFrame) -> tuple[dict, dict]:
    print("Building county -> DMA (area-weighted)...")
    counties = gpd.read_file(shp_dir / "county" / "cb_2023_us_county_500k.shp")
    counties["GEOID"] = counties["GEOID"].astype(str)
    result = _area_weighted_dma_shares(counties.rename(columns={"GEOID": "county_fips"}), "county_fips", dma_polys)
    print(f"  {len(result)} counties resolved")
    # NAME + STATEFP so the text resolver can go "Sacramento County, CA" -> FIPS,
    # not just FIPS -> DMA share (which is all the overlay above produces).
    # full_name = NAMELSAD ("Sacramento County", "Orleans Parish", "Richmond
    # city") — NAME alone can't tell Virginia's independent "Richmond city"
    # from "Richmond County", and can't label Louisiana's parishes correctly.
    names = {
        row["GEOID"]: {"name": row["NAME"], "full_name": row["NAMELSAD"], "state_fips": row["STATEFP"]}
        for _, row in counties.iterrows()
    }
    return result, names


def build_zcta_dma(shp_dir: Path, dma_polys: gpd.GeoDataFrame) -> dict:
    print("Building ZCTA -> DMA (area-weighted) — this is the biggest layer, may take a few minutes...")
    zcta = gpd.read_file(shp_dir / "zcta" / "cb_2020_us_zcta520_500k.shp")
    zcta_col = "ZCTA5CE20" if "ZCTA5CE20" in zcta.columns else "ZCTA5CE10"
    zcta[zcta_col] = zcta[zcta_col].astype(str)
    result = _area_weighted_dma_shares(zcta.rename(columns={zcta_col: "zcta"}), "zcta", dma_polys)
    print(f"  {len(result)} ZCTAs resolved")
    return result


def build_place_dma(shp_dir: Path, dma_polys: gpd.GeoDataFrame) -> tuple[dict, dict]:
    print("Building Census Place (city) -> DMA (area-weighted)...")
    place_dir = shp_dir / "places"
    frames = []
    for sub in sorted(place_dir.iterdir()):
        shp_files = list(sub.glob("*.shp"))
        if shp_files:
            frames.append(gpd.read_file(shp_files[0]))
    places = pd.concat(frames, ignore_index=True)
    places = gpd.GeoDataFrame(places, crs=frames[0].crs)
    places["GEOID"] = places["GEOID"].astype(str)  # STATEFP + PLACEFP
    result = _area_weighted_dma_shares(places.rename(columns={"GEOID": "place_geoid"}), "place_geoid", dma_polys)
    # Name + state FIPS lookup so the resolver can match free-text city
    # names (and disambiguate the many repeated city names across states).
    names = {
        row["GEOID"]: {"name": row["NAME"], "state_fips": row["STATEFP"]}
        for _, row in places.iterrows()
    }
    print(f"  {len(result)} places resolved")
    return result, names


def build_cd_dma(zcta_dma: dict, zcta_population: dict[str, float]) -> tuple[dict, dict]:
    """CD -> DMA shares from the Census CD-to-ZCTA relationship file.

    Each (CD, ZCTA) row contributes   zcta_pop x (land of the ZCTA inside
    the CD / the ZCTA's total land)   people, split across DMAs by that
    ZCTA's own area-weighted DMA shares. Summed per CD and normalized.
    A CD whose ZCTAs mostly have no DMA assignment (Alaska) is skipped
    rather than reported off a sliver of its population."""
    print("Fetching Census 119th Congress CD-to-ZCTA relationship file...")
    text = _fetch(CD_ZCTA_REL_FILE_URL).decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines(), delimiter="|"))

    dma_weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    covered: dict[str, float] = defaultdict(float)
    total: dict[str, float] = defaultdict(float)
    for row in rows:
        zcta = row.get("GEOID_ZCTA5_20") or ""
        if not zcta:
            continue
        cd_geoid = row["GEOID_CD119_20"]
        zcta_land = float(row["AREALAND_ZCTA5_20"] or 0)
        part_land = float(row["AREALAND_PART"] or 0)
        if zcta_land <= 0:
            continue
        pop_in_cd = zcta_population.get(zcta, 0.0) * min(part_land / zcta_land, 1.0)
        if pop_in_cd <= 0:
            continue
        total[cd_geoid] += pop_in_cd
        shares = zcta_dma.get(zcta)
        if not shares:
            continue
        covered[cd_geoid] += pop_in_cd
        for s_ in shares:
            dma_weight[cd_geoid][s_["dma"]] += pop_in_cd * s_["share"]

    result: dict[str, list[dict]] = {}
    skipped = []
    for cd_geoid, weights in dma_weight.items():
        if covered[cd_geoid] < 0.5 * total[cd_geoid]:
            skipped.append(cd_geoid)
            continue
        norm = sum(weights.values())
        result[cd_geoid] = [{"dma": dma, "share": round(w / norm, 6)} for dma, w in sorted(weights.items(), key=lambda kv: -kv[1])]
    for cd_geoid in set(total) - set(dma_weight):
        skipped.append(cd_geoid)
    print(f"  {len(result)} congressional districts resolved; skipped (no DMA coverage): {sorted(skipped)}")
    return result, {"skipped_no_dma_coverage": sorted(skipped)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shp-dir", required=True, help="Directory containing county/, zcta/, places/ shapefile subfolders (see module docstring for the download URLs)")
    args = parser.parse_args()
    shp_dir = Path(args.shp_dir)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading Nielsen DMA boundary polygons...")
    dma_polys = load_dma_polygons(shp_dir)
    print(f"  {len(dma_polys)} DMA polygons matched to dma_reference.json")

    county_dma, county_names = build_county_dma(shp_dir, dma_polys)
    write_json_gz(OUT_DIR / "county_dma.json", county_dma)
    write_json_gz(OUT_DIR / "county_names.json", county_names)

    zcta_dma = build_zcta_dma(shp_dir, dma_polys)
    write_json_gz(OUT_DIR / "zcta_dma.json", zcta_dma)

    place_dma, place_names = build_place_dma(shp_dir, dma_polys)
    write_json_gz(OUT_DIR / "place_dma.json", {"shares": place_dma, "names": place_names})

    acs_zcta = read_json_maybe_gz(OUT_DIR.parent / "acs_age_sex_zcta.json")
    zcta_population = {z: v["total_population"] for z, v in acs_zcta.items() if not z.startswith("_")}
    cd_dma, cd_info = build_cd_dma(zcta_dma, zcta_population)
    write_json_gz(OUT_DIR / "cd_dma.json", {
        "_meta": {
            "weighting": "population",
            "vintage": "119th Congress CD (2020 tabulation geography) x ACS 2023 5-year",
            "refresh_cadence_days": 180,  # re-pull after any redistricting (2025-26 mid-decade maps are in flux)
            "source": "Census 119th Congress CD-to-ZCTA relationship file (2020 tabulation geography) x ACS 2023 ZCTA population x area-weighted ZCTA-to-DMA shares",
            **cd_info,
        },
        "cd_dma": cd_dma,
    })
    stale = OUT_DIR / "cd_county.json.gz"
    if stale.exists():
        stale.unlink()  # superseded by the CD-to-ZCTA route; nothing reads it anymore

    print("Done.")


if __name__ == "__main__":
    main()
