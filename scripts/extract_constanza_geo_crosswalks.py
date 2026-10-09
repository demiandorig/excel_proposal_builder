"""
Builds Project Constanza's US sub-DMA geo crosswalks: county, ZCTA
(ZIP-code proxy), and Census Place (city) to DMA, plus congressional
district to DMA (via the official Census CD-to-county relationship file).

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
importantly congressional districts, whose county intersections are
weighted by each county's real population disaggregated by area share
within just that one county, not by land area across different counties
— this script uses ACS county population once available (see
CENSUS_API_KEY below) rather than land area, per the Phase 2 plan's
correction to the first-draft design.

Run the shapefile downloads once first (see README at the top of main()),
then:
    python scripts/extract_constanza_geo_crosswalks.py --shp-dir /tmp/constanza_shp

Writes, under app/data/constanza/geo/:
    county_dma.json    — county FIPS -> [{dma, share}], area-weighted
    zcta_dma.json       — ZCTA5 -> [{dma, share}], area-weighted
    place_dma.json      — "STATEFP|PLACEFP" -> [{dma, share, name}], area-weighted
    cd_county.json       — "STATEFP|CD" -> [{county_fips, land_share}] (from the Census relationship file)
    cd_dma.json          — "STATEFP|CD" -> [{dma, share}], population-weighted via ACS county population when available
"""
from __future__ import annotations

import argparse
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
CD_COUNTY_REL_FILE_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld/tab20_cd11920_county20_natl.txt"

sys.path.insert(0, str(REPO_ROOT))
from app.data.constanza import load_dma_reference  # noqa: E402
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


def load_dma_polygons() -> gpd.GeoDataFrame:
    """Real Nielsen DMA boundaries, name-matched to our own dma_reference.json."""
    topo = json.loads(_fetch(NIELSEN_DMA_TOPOJSON_URL))
    features = _decode_topojson(topo)
    gdf = gpd.GeoDataFrame(
        {"dma_name": [f["properties"].get("dma1", "") for f in features]},
        geometry=[f["geometry"] for f in features],
        crs="EPSG:4326",
    )

    my_dma_list = list(load_dma_reference())
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
    gdf = gdf[gdf["dma"].notna()].to_crs("EPSG:5070")  # CONUS Albers Equal Area — for accurate area weighting
    return gdf[["dma", "geometry"]]


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
    return dict(result)


def build_county_dma(shp_dir: Path, dma_polys: gpd.GeoDataFrame) -> tuple[dict, dict]:
    print("Building county -> DMA (area-weighted)...")
    counties = gpd.read_file(shp_dir / "county" / "cb_2023_us_county_500k.shp")
    counties["GEOID"] = counties["GEOID"].astype(str)
    result = _area_weighted_dma_shares(counties.rename(columns={"GEOID": "county_fips"}), "county_fips", dma_polys)
    print(f"  {len(result)} counties resolved")
    # NAME + STATEFP so the text resolver can go "Sacramento County, CA" -> FIPS,
    # not just FIPS -> DMA share (which is all the overlay above produces).
    names = {
        row["GEOID"]: {"name": row["NAME"], "state_fips": row["STATEFP"]}
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


def build_cd_county() -> dict:
    print("Fetching Census 119th Congress CD-to-county relationship file...")
    text = _fetch(CD_COUNTY_REL_FILE_URL).decode("utf-8-sig")
    rows = list(csv := __import__("csv").DictReader(text.splitlines(), delimiter="|"))
    by_cd: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cd_geoid = row["GEOID_CD119_20"]  # STATEFP(2) + CD(2)
        county_fips = row.get("GEOID_COUNTY_20", "")
        if not county_fips:
            continue
        land_part = float(row["AREALAND_PART"] or 0)
        by_cd[cd_geoid].append({"county_fips": county_fips, "land_area_part": land_part})
    print(f"  {len(by_cd)} congressional districts")
    return dict(by_cd)


def build_cd_dma(cd_county: dict, county_dma: dict, county_population: dict[str, float] | None) -> dict:
    """CD -> DMA shares. If real county population is available (ACS,
    keyed by county FIPS), each intersecting county's contribution to the
    CD is that county's OWN population, disaggregated only by this one
    county's own land-area split across the CD boundary (never compared
    against a DIFFERENT county's land area) — see module docstring. Falls
    back to land-area-only weighting (with a clear caveat) when no
    population data is available yet."""
    result: dict[str, list[dict]] = {}
    for cd_geoid, counties in cd_county.items():
        total_county_land = defaultdict(float)
        for c in counties:
            total_county_land[c["county_fips"]] += c["land_area_part"]

        dma_weight: dict[str, float] = defaultdict(float)
        for county_fips, land_in_cd in total_county_land.items():
            pop = (county_population or {}).get(county_fips)
            # Weight = (this county's population, or 1.0 as a land-only
            # fallback) x this county's OWN share of itself that falls in
            # this CD — never a cross-county area comparison.
            base_weight = pop if pop is not None else 1.0
            for dma_share in county_dma.get(county_fips, []):
                dma_weight[dma_share["dma"]] += base_weight * dma_share["share"]

        total = sum(dma_weight.values())
        if not total:
            continue
        result[cd_geoid] = [
            {"dma": dma, "share": round(w / total, 6)} for dma, w in dma_weight.items()
        ]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shp-dir", required=True, help="Directory containing county/, zcta/, places/ shapefile subfolders (see module docstring for the download URLs)")
    args = parser.parse_args()
    shp_dir = Path(args.shp_dir)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading Nielsen DMA boundary polygons...")
    dma_polys = load_dma_polygons()
    print(f"  {len(dma_polys)} DMA polygons matched to dma_reference.json")

    county_dma, county_names = build_county_dma(shp_dir, dma_polys)
    (OUT_DIR / "county_dma.json").write_text(json.dumps(county_dma, indent=2))
    (OUT_DIR / "county_names.json").write_text(json.dumps(county_names, indent=2))

    zcta_dma = build_zcta_dma(shp_dir, dma_polys)
    (OUT_DIR / "zcta_dma.json").write_text(json.dumps(zcta_dma, indent=2))

    place_dma, place_names = build_place_dma(shp_dir, dma_polys)
    (OUT_DIR / "place_dma.json").write_text(json.dumps({"shares": place_dma, "names": place_names}, indent=2))

    cd_county = build_cd_county()
    (OUT_DIR / "cd_county.json").write_text(json.dumps(cd_county, indent=2))

    # County population for CD weighting: read from the ACS bundle if
    # extract_constanza_acs.py has already produced one, else fall back to
    # land-area-only CD weighting with an explicit caveat.
    acs_county_path = OUT_DIR.parent / "acs_age_sex_county.json"
    county_population = None
    if acs_county_path.exists():
        acs_county = json.loads(acs_county_path.read_text())
        county_population = {fips: v["total_population"] for fips, v in acs_county.items() if fips != "_meta"}
        print("Using real ACS county population for CD weighting.")
    else:
        print("WARNING: no ACS county population bundle found yet — CD-to-DMA weighting will use "
              "land-area-only fallback for now. Re-run this script after extract_constanza_acs.py "
              "has run to upgrade CD weighting to real population (see cd_dma.json's _meta flag).")

    cd_dma = build_cd_dma(cd_county, county_dma, county_population)
    (OUT_DIR / "cd_dma.json").write_text(json.dumps({
        "_meta": {
            "weighting": "population" if county_population else "land_area_fallback",
            "source": "Census 119th Congress CD-to-county relationship file (2020 tabulation geography)",
        },
        "cd_dma": cd_dma,
    }, indent=2))

    print("Done.")


if __name__ == "__main__":
    main()
