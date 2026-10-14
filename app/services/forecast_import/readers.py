"""
Readers: one per source format, each turning a file into a `SeriesPayload` with CANONICAL keys (one spelling per DMA,
state and district) and a list of findings for everything that was wrong or odd. Readers never write to the database
and never guess: a label they cannot map becomes an error finding, not a silently dropped or invented row.

  madhive_zip           the six-CSV export (by Metros / States / Zip Codes / Congressional District / Devices / Publishers)
  netflix_baseline_csv  Market, Targeting, Rate, Length, Max. Monthly Avails - a SAMPLE of markets, extended to all DMAs
  live_sports_xlsx      one tab = one product: DMA weekly estimates plus a period note in the header row
  bundled_platform_json the original CTV model workbook snapshot (app/data/constanza/platform_dma.json)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import statistics
import zipfile
from pathlib import Path
from typing import Optional

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import.model import Figures, PeriodInfo, SeriesPayload, SeriesSpec, SourceError

MAX_MEMBER_BYTES = 80 * 1024 * 1024          # a single CSV inside a zip (the largest real one is ~1 MB)
MAX_ZIP_MEMBERS = 40

MADHIVE_KINDS = {"Metros": "dma", "States": "state", "Zip Codes": "zip", "Congressional District": "cd",
                 "Devices": "device", "Publishers": "publisher"}
MADHIVE_COLUMNS = ["Total Avails", "Daily Avails", "Weekly Avails", "Monthly Avails", "Reach Universe", "Est. Reach", "Est. Reach %"]
_MEMBER_RE = re.compile(r"^(?P<prefix>.+) by (?P<kind>Metros|States|Zip Codes|Congressional District|Devices|Publishers)\.csv$")
_DEVICES = {"connected tv": "connected_tv", "ctv": "connected_tv", "mobile": "mobile", "desktop": "desktop", "tablet": "tablet"}
_ZIP = re.compile(r"^\d{5}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_number(text: str) -> Optional[float]:
    """'1,234' / '1234.5' / '$54.00' -> float; None for blank or non-numeric. Percent strings are not numbers here."""
    t = (text or "").strip().replace(",", "").replace("$", "")
    if t == "" or t.endswith("%"):
        return None
    try:
        value = float(t)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# ======================================================================================================== madhive

def _zip_members(zf: zipfile.ZipFile, payload: SeriesPayload) -> dict[str, tuple[str, zipfile.ZipInfo]]:
    """kind -> (file-name prefix, member). macOS resource forks and folders are ignored; a duplicate or missing kind is
    an error."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise SourceError(f"{len(infos)} entries in the archive (limit {MAX_ZIP_MEMBERS}) - not an avails export")
    found: dict[str, tuple[str, zipfile.ZipInfo]] = {}
    for info in infos:
        name = info.filename
        base = name.rsplit("/", 1)[-1]
        if name.startswith("__MACOSX/") or base.startswith("._") or base == ".DS_Store":
            continue
        if ".." in name.split("/") or name.startswith("/"):
            payload.add("S001", "error", f"archive entry {name!r} has an unsafe path - ignored", "zip")
            continue
        m = _MEMBER_RE.match(base)
        if not m:
            payload.add("S001", "warning", f"unexpected file {name!r} in the export - ignored", "zip")
            continue
        kind = MADHIVE_KINDS[m.group("kind")]
        if kind in found:
            payload.add("S001", "error", f"two files for the {m.group('kind')} breakdown ({found[kind][1].filename!r} and {name!r})", "zip")
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            raise SourceError(f"{name!r} is {info.file_size:,} bytes uncompressed (limit {MAX_MEMBER_BYTES:,})")
        found[kind] = (m.group("prefix"), info)
    for kind in MADHIVE_KINDS.values():
        if kind not in found:
            payload.add("S001", "error", f"the export has no 'by {next(k for k, v in MADHIVE_KINDS.items() if v == kind)}' file", "zip")
    return found


def _parse_breakdown(payload: SeriesPayload, raw: bytes, first_column: str, where: str):
    """Yield (label, total, daily, weekly, monthly, reach_universe, est_reach) for each well-formed row; report the rest."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        payload.add("S002", "error", "the file is empty", where)
        return
    expected = [first_column] + MADHIVE_COLUMNS
    if [h.strip() for h in header] != expected:
        payload.add("S002", "error", f"columns are {header!r}, expected {expected!r}", where)
        return
    seen_bad = 0
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(header):
            payload.add("S003", "error", f"line {line_no} has {len(row)} columns, expected {len(header)}", where)
            continue
        label = row[0].strip()
        if not label:
            payload.add("S003", "error", f"line {line_no} has no {first_column} value", where)
            continue
        nums = [parse_number(c) for c in row[1:7]]
        if any(n is None for n in nums) or any(n < 0 for n in nums if n is not None):
            seen_bad += 1
            if seen_bad <= 5:
                payload.add("S004", "error", f"line {line_no} ({label!r}): a numeric column is blank, negative or not a number: {row[1:7]!r}", where)
            continue
        yield (label, *nums)
    if seen_bad > 5:
        payload.add("S004", "error", f"... and {seen_bad - 5} more rows with bad numbers", where)


def read_madhive_zip(spec: SeriesSpec, path: Path) -> SeriesPayload:
    payload = SeriesPayload(spec=spec, source_name=path.name, source_sha256=sha256_file(path))
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SourceError(f"{path.name}: not a readable zip archive ({exc})") from exc
    with zf:
        members = _zip_members(zf, payload)
        prefixes = {p for p, _ in members.values()}
        if len(prefixes) > 1:
            payload.add("S001", "error", f"the files in the export disagree about the product name: {sorted(prefixes)!r}", "zip")
        payload.stats["export_name"] = sorted(prefixes)[0] if prefixes else ""
        derived: dict[str, dict] = {}
        for kind, (prefix, info) in members.items():
            first = next(k for k, v in MADHIVE_KINDS.items() if v == kind)
            where = info.filename.rsplit("/", 1)[-1]
            stats = derived.setdefault(kind, {"rows": 0, "total_ne_monthly": 0, "daily_sum": 0.0, "weekly_sum": 0.0,
                                              "monthly_sum": 0.0, "reach_gt_universe": 0})
            targets: dict[str, Figures] = {}
            for label, total, daily, weekly, monthly, universe, est in _parse_breakdown(payload, zf.read(info), first, where):
                stats["rows"] += 1
                stats["daily_sum"] += daily
                stats["weekly_sum"] += weekly
                stats["monthly_sum"] += monthly
                if abs(total - monthly) > max(200.0, 1e-4 * monthly):
                    stats["total_ne_monthly"] += 1
                if est > universe * 1.01 + 10:
                    stats["reach_gt_universe"] += 1
                fig = Figures(avails=monthly, reach_universe=universe, est_reach=est)
                _place(payload, kind, label, fig, targets, where)
        payload.stats["derived"] = derived
    return payload


def _place(payload: SeriesPayload, kind: str, label: str, fig: Figures, targets: dict, where: str) -> None:
    """Map one source label to its canonical key (or report why not) and store the figures."""
    if kind in ("dma", "state", "cd") and label.strip().lower() == "unknown":
        payload.unknown[kind] = fig
        return
    if kind == "dma":
        key = geo.dma_key_for_label(label)
        if key is None:
            hints = "; ".join(f"{n!r} ({s})" for n, s in geo.suggest_dma(label))
            payload.add("G001", "error", f"DMA label {label!r} is not in dma_aliases.csv. Closest: {hints}", where)
            return
        _store(payload, "dma", key, fig, label, where)
    elif kind == "state":
        code = geo.state_code(label)
        if code is None:
            payload.add("G002", "error", f"state label {label!r} is not a US state or DC", where)
            return
        _store(payload, "state", code, fig, label, where)
    elif kind == "cd":
        key = geo.parse_cd_label(label)
        if key is None or key not in set(geo.all_cd_keys()):
            payload.add("G002", "error", f"congressional district label {label!r} is not a known district", where)
            return
        _store(payload, "cd", key, fig, label, where)
    elif kind == "zip":
        if not _ZIP.match(label):
            payload.add("G002", "error", f"ZIP {label!r} is not five digits (leading zeros must be kept)", where)
            return
        _store(payload, "zip", label, fig, label, where)
    elif kind == "device":
        dev = _DEVICES.get(label.strip().lower())
        if dev is None:
            payload.add("G002", "error", f"device {label!r} is not one of Connected TV, Mobile, Desktop, Tablet", where)
            return
        _store_flat(payload.devices, dev, fig, payload, label, where)
    elif kind == "publisher":
        name = re.sub(r"\s+", " ", label).strip()
        norm = geo.publisher_key(name)
        existing = next((n for n in payload.publishers if geo.publisher_key(n) == norm), None)
        if existing is not None:
            old = payload.publishers[existing]
            payload.add("S003", "warning", f"publisher {name!r} appears twice - figures added together", where)
            old.avails += fig.avails
            old.reach_universe = (old.reach_universe or 0) + (fig.reach_universe or 0)
            old.est_reach = (old.est_reach or 0) + (fig.est_reach or 0)
        else:
            payload.publishers[name] = fig


def _store(payload: SeriesPayload, level: str, key: str, fig: Figures, label: str, where: str) -> None:
    if key in payload.levels[level]:
        payload.add("S003", "error", f"{label!r} and an earlier row both mean {key!r} - a geography must appear once", where)
        return
    payload.levels[level][key] = fig


def _store_flat(target: dict, key: str, fig: Figures, payload: SeriesPayload, label: str, where: str) -> None:
    if key in target:
        payload.add("S003", "error", f"{label!r} duplicates an earlier row for {key!r}", where)
        return
    target[key] = fig


# ======================================================================================================== netflix

NETFLIX_COLUMNS = ["Market", "Language", "Targeting", "Rate", "Length", "Max. Monthly Avails"]


def read_netflix_csv(spec: SeriesSpec, path: Path, reference: Optional[dict[str, float]] = None) -> SeriesPayload:
    """The Netflix baseline: rows for a SAMPLE of markets and two creative lengths. The requested `length` becomes the
    reported rows; every other DMA is extended with a documented model (see `extend_to_all_dmas`)."""
    length = int(spec.params["length"])
    payload = SeriesPayload(spec=spec, source_name=path.name, source_sha256=sha256_file(path))
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise SourceError(f"{path.name} is empty")
        if [h.strip() for h in header] != NETFLIX_COLUMNS:
            payload.add("S002", "error", f"columns are {header!r}, expected {NETFLIX_COLUMNS!r}", path.name)
            return payload
        by_len: dict[int, dict[str, float]] = {}
        rates: dict[int, set] = {}
        targeting: set = set()
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != len(header):
                payload.add("S003", "error", f"line {line_no} has {len(row)} columns", path.name)
                continue
            market, _product, tgt, rate, ln, avails = (c.strip() for c in row)
            n, a = parse_number(ln), parse_number(avails)
            if n is None or a is None or a < 0:
                payload.add("S004", "error", f"line {line_no} ({market!r}): length or avails is not a number", path.name)
                continue
            key = geo.dma_key_for_label(market)
            if key is None:
                hints = "; ".join(f"{nm!r} ({s})" for nm, s in geo.suggest_dma(market))
                payload.add("G001", "error", f"market {market!r} is not in dma_aliases.csv. Closest: {hints}", path.name)
                continue
            lengths = by_len.setdefault(int(n), {})
            if key in lengths:
                payload.add("S003", "error", f"{market!r} appears twice for :{int(n)}", path.name)
                continue
            lengths[key] = a
            targeting.add(tgt)
            r = parse_number(rate)
            if r is not None:
                rates.setdefault(int(n), set()).add(r)
    if length not in by_len:
        payload.add("S001", "error", f"the file has no rows for the :{length} length", path.name)
        return payload
    payload.stats["sample_markets"] = len(by_len[length])
    payload.stats["targeting"] = sorted(targeting)
    payload.stats["rate_cpm"] = sorted(rates.get(length, []))
    other = 45 - length if length in (15, 30) else None
    payload.stats["sample_other_length"] = by_len.get(30 if length == 15 else 15, {})
    for key, value in by_len[length].items():
        payload.levels["dma"][key] = Figures(avails=value, basis="reported")
    if reference:
        extend_to_all_dmas(payload, reference)
    return payload


def extend_to_all_dmas(payload: SeriesPayload, reference: dict[str, float]) -> None:
    """Fill every DMA the sample does not cover.

    Model: avails(dma) = k x reference(dma), where `reference` is the real, reported market distribution of another
    CTV series (Entravision+ Connect - Default) and k is the MEDIAN of sample / reference over the reported markets
    (median, so one odd market cannot move it). Rows produced this way are marked basis='modeled' and the series
    data_basis is 'extrapolated'. The fit on the reported markets themselves is recorded as a finding so a reviewer
    sees how good the extension is; when it is poor the import fails rather than inventing a bad series."""
    payload.reference_hash = hashlib.sha256(
        json.dumps(sorted((k, round(v, 2)) for k, v in reference.items())).encode("utf-8")).hexdigest()[:16]
    reported = {k: f.avails for k, f in payload.levels["dma"].items() if f.basis == "reported"}
    pairs = [(reported[k], reference[k]) for k in reported if reference.get(k)]
    if len(pairs) < 8:
        payload.add("X001", "error", f"only {len(pairs)} reported markets overlap the reference series; at least 8 are needed to extend the sample", payload.source_name)
        return
    ratios = [a / r for a, r in pairs]
    k = statistics.median(ratios)
    errors = [abs(k * r - a) / a for a, r in pairs if a]
    mape = statistics.mean(errors)
    logs = [(math.log(r), math.log(a)) for a, r in pairs if a > 0 and r > 0]
    mx = statistics.mean(x for x, _ in logs)
    my = statistics.mean(y for _, y in logs)
    sxx = sum((x - mx) ** 2 for x, _ in logs)
    slope = sum((x - mx) * (y - my) for x, y in logs) / sxx if sxx else 0.0
    ss_tot = sum((y - my) ** 2 for _, y in logs)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in logs)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    payload.stats["extension"] = {
        "model": "reported_market_median_ratio_to_reference_series", "reference": payload.spec.params.get("extrapolate_from"),
        "k": k, "reported_markets": len(pairs), "mape_on_reported": mape, "log_log_r2": r2, "log_log_slope": slope,
    }
    if mape > 0.35:
        payload.add("X004", "error", f"the extension model misses the reported markets by {mape:.0%} on average (limit 35%) - not publishable", payload.source_name)
        return
    added = 0
    for dma in geo.dma_table():
        if dma.key in payload.levels["dma"]:
            continue
        ref = reference.get(dma.key)
        if ref is None:
            continue
        payload.levels["dma"][dma.key] = Figures(avails=k * ref, basis="modeled")
        added += 1
    payload.add("X002", "info",
                f"{len(pairs)} reported markets; {added} more extended as {k:.4f} x the reference market distribution "
                f"(mean error on the reported markets {mape:.1%}, log-log R2 {r2:.3f})", payload.source_name)
    # a :30 that equals the :15 (New York in the sample) while every other market is ~92% is a source oddity worth a look
    other = payload.stats.get("sample_other_length") or {}
    own = {k_: f.avails for k_, f in payload.levels["dma"].items() if f.basis == "reported"}
    ratio = [other[d] / own[d] for d in own if d in other and own[d]]
    if len(ratio) >= 5:
        med = statistics.median(ratio)
        for d in own:
            if d in other and own[d] and abs(other[d] / own[d] - med) > 0.03:
                payload.add("X003", "warning", f"{d}: the other creative length is {other[d] / own[d]:.3f}x this length; the median market is {med:.3f}x - check the source row", payload.source_name)


# ======================================================================================================== live sports

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def read_live_sports_xlsx(spec: SeriesSpec, path: Path) -> SeriesPayload:
    import openpyxl
    sheet = spec.params["sheet"]
    payload = SeriesPayload(spec=spec, source_name=f"{path.name}#{sheet}", source_sha256=sha256_file(path))
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"{path.name}: not a readable workbook ({exc})") from exc
    if sheet not in wb.sheetnames:
        raise SourceError(f"{path.name} has no tab named {sheet!r} (tabs: {wb.sheetnames})")
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise SourceError(f"tab {sheet!r} is empty")
    header = rows[0]
    payload.stats["header"] = [str(c) for c in header if c not in (None, "")]
    measure = str(header[1] or "").strip() if len(header) > 1 else ""
    payload.stats["measure_header"] = measure
    note_cells = [str(c).strip() for c in header[2:] if c not in (None, "") and str(c).strip()]
    payload.stats["header_note"] = note_cells[0] if note_cells else ""
    if not measure:
        payload.add("L001", "error", "column B has no header - cannot tell what the numbers measure", payload.source_name)
    elif "week" not in measure.lower():
        payload.add("L005", "warning", f"column B is headed {measure!r} (not 'Weekly'): treated as weekly estimates - confirm with the sheet's owner", payload.source_name)
    stray: list[str] = []
    for r_no, row in enumerate(rows[1:], start=2):
        label, value = (row[0] if len(row) > 0 else None), (row[1] if len(row) > 1 else None)
        extra = [c for c in row[2:] if c not in (None, "")]
        if label in (None, "") and value in (None, ""):
            if extra:
                stray.append(f"row {r_no}: {extra!r}")
            continue
        if extra:
            stray.append(f"row {r_no}: {extra!r}")
        if not isinstance(label, str) or not label.strip():
            payload.add("S003", "error", f"row {r_no}: a number with no market name", payload.source_name)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not math.isfinite(float(value)):
            payload.add("S004", "error", f"row {r_no} ({label.strip()!r}): {value!r} is not a non-negative number", payload.source_name)
            continue
        key = geo.dma_key_for_label(label)
        if key is None:
            hints = "; ".join(f"{n!r} ({s})" for n, s in geo.suggest_dma(label))
            payload.add("G001", "error", f"market {label.strip()!r} is not in dma_aliases.csv. Closest: {hints}", payload.source_name)
            continue
        _store(payload, "dma", key, Figures(avails=float(value), basis="modeled"), label, payload.source_name)
    if stray:
        payload.add("L002", "warning", f"{len(stray)} cell(s) outside the market/number columns were not loaded: " + "; ".join(stray[:4]), payload.source_name)
    return payload


# ======================================================================================================== original model workbook

def read_bundled_platform_json(spec: SeriesSpec, path: Path) -> SeriesPayload:
    """app/data/constanza/platform_dma.json: {platform: {lower-case DMA name: figures}} from the CTV model workbook."""
    platform = spec.params["platform"]
    payload = SeriesPayload(spec=spec, source_name=f"{path.name}#{platform}", source_sha256=sha256_file(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if platform not in data:
        raise SourceError(f"{path.name} has no platform {platform!r}")
    for name, figs in data[platform].items():
        key = geo.dma_key_for_label(name)
        if key is None:
            payload.add("G001", "error", f"DMA {name!r} is not in dma_aliases.csv", path.name)
            continue
        mid = figs.get("imps_mid")
        if mid is None or mid < 0:
            payload.add("S004", "error", f"{name!r}: imps_mid is missing or negative", path.name)
            continue
        _store(payload, "dma", key, Figures(
            avails=float(mid), avails_low=figs.get("imps_low"), avails_high=figs.get("imps_high"),
            est_reach=figs.get("unique_viewers"), unique_hh=figs.get("unique_hh"), reach_pct=figs.get("reach_pct"),
            basis="modeled"), name, path.name)
    return payload


# ======================================================================================================== dispatch

def read_series(spec: SeriesSpec, source_dir: Path, reference: Optional[dict[str, float]] = None) -> SeriesPayload:
    """Read the source of one series. `source_dir` holds the files the manifest names."""
    if spec.reader == "bundled_platform_json":
        path = Path(spec.source) if spec.source and Path(spec.source).is_absolute() else Path(__file__).resolve().parents[2] / "data" / "constanza" / "platform_dma.json"
        payload = read_bundled_platform_json(spec, path)
    else:
        if not spec.source:
            raise SourceError(f"series {spec.series_key!r} names no source file")
        path = source_dir / spec.source
        if not path.is_file():
            raise SourceError(f"source file {path} not found")
        if spec.reader == "madhive_zip":
            payload = read_madhive_zip(spec, path)
        elif spec.reader == "netflix_baseline_csv":
            payload = read_netflix_csv(spec, path, reference)
        elif spec.reader == "live_sports_xlsx":
            payload = read_live_sports_xlsx(spec, path)
        else:
            raise SourceError(f"unknown reader {spec.reader!r}")
    return payload
