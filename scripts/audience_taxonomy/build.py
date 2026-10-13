"""
Build app/data/constanza/audience_taxonomy.json from the VERIFIED research records and the editorial layer.

    python scripts/audience_taxonomy/build.py --research DIR [--out PATH]

DIR holds one JSON array per research batch: the records an independent fact-checker re-verified, each with the researched
rate, its source and evidence, and a verdict (confirmed / adjusted / rejected). Rules applied here, never relaxed:

  * a record the fact-checker REJECTED, or one that is missing, becomes 'no_public_rate' (kept in the taxonomy and
    searchable, with no number) - a number nobody could verify is not shipped;
  * a rate must be a share of US adults between 0 and 1;
  * a sourced or derived rate must name its source (title and url).

The names, groups, keywords and descriptions come from editorial.py. Re-running with new research output refreshes the file;
the result is validated by app/services/audience_taxonomy.parse before it is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services import audience_taxonomy  # noqa: E402
import editorial  # noqa: E402

DEFAULT_OUT = ROOT / "app" / "data" / "constanza" / "audience_taxonomy.json"


def load_records(directory: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("segments", [])
        for rec in items:
            records[rec["id"]] = rec          # later files (retries) replace earlier ones
    return records


def incidence_for(rec: dict | None) -> tuple[dict, str | None]:
    """(incidence block, problem). The problem is a human-readable reason the segment fell back to no_public_rate."""
    blank = {"status": "no_public_rate", "all": None, "male": None, "female": None, "definition": "", "denominator": "", "time_window": "",
             "confidence": "none", "caveats": "", "supporting_sources": [], "verification": {}}
    if rec is None:
        blank["caveats"] = "Research for this segment did not complete; no rate is applied."
        return blank, "no research record"
    verdict = rec.get("verdict", "confirmed")
    issues = (rec.get("issues") or "").strip()
    if rec.get("status") == "no_public_rate" or rec.get("rate_all") is None:
        blank.update(definition=rec.get("definition_used") or "", caveats=rec.get("caveats") or "",
                     verification={"verdict": verdict, "notes": issues})
        return blank, None
    if verdict == "rejected":
        blank.update(definition=rec.get("definition_used") or "", verification={"verdict": "rejected", "notes": issues},
                     caveats="A figure was researched but the independent check rejected it, so no rate is applied. " + issues)
        return blank, f"rejected: {issues[:120]}"
    rate = float(rec["rate_all"])
    if not (0 < rate <= 1) or not rec.get("source_url") or not rec.get("source_title"):
        blank.update(caveats=f"The researched figure could not be used (rate {rate!r} or missing source). " + issues,
                     verification={"verdict": "rejected", "notes": "invalid record"})
        return blank, "invalid rate or missing source"
    block = {
        "status": rec["status"], "all": rate, "male": rec.get("rate_male"), "female": rec.get("rate_female"),
        "definition": rec.get("definition_used") or "", "denominator": rec.get("denominator_used") or "", "time_window": rec.get("time_window") or "",
        "source": {"title": rec["source_title"], "publisher": rec.get("source_publisher", ""), "url": rec["source_url"], "date": rec.get("source_date", "")},
        "evidence": rec.get("evidence_quote"), "derivation": rec.get("derivation"), "confidence": rec.get("confidence", "low"),
        "caveats": rec.get("caveats") or "", "supporting_sources": rec.get("supporting_sources") or [],
        "verification": {"verdict": verdict, "notes": issues},
    }
    return block, None


def build(research_dir: Path, out: Path) -> dict:
    records = load_records(research_dir)
    groups = [{"key": k, "label": label, "sort": sort} for k, label, sort in editorial.GROUPS]
    segments, problems = [], []
    for order, (sid, (group, label, company, keywords, description)) in enumerate(editorial.SEGMENTS.items()):
        block, problem = incidence_for(records.get(sid))
        if problem:
            problems.append((sid, problem))
        segments.append({
            "id": editorial.LEGACY_IDS.get(sid, sid), "group": group, "label": label, "company_names": company, "keywords": keywords,
            "description": description, "sort": (order + 1) * 10, "incidence": block,
        })
    unused = sorted(set(records) - set(editorial.SEGMENTS))
    data = {
        "_meta": {
            "description": "Audience segments with sourced incidence rates for Constanza's interest targeting.",
            "rate_definition": "The share of US adults (18+) who match the segment in a typical 12-month window, 0-1. Male / female only where the same source gives them.",
            "policy": "No rate ships without a source and evidence, and every rate was re-checked against its source by an independent verifier. A segment with no defensible public number is listed with status 'no_public_rate' and no rate - the engine never guesses one.",
            "generated_on": date.today().isoformat(),
            "vintage": date.today().isoformat(),          # scripts/check_constanza_data_freshness.py reads these two
            "refresh_cadence_days": 365,
            "built_by": "scripts/audience_taxonomy/build.py",
        },
        "groups": groups, "segments": segments,
    }
    audience_taxonomy.parse(data)                                 # refuse to write anything the database would refuse
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"segments": len(segments), "problems": problems, "unused_records": unused,
            "status": {s: sum(1 for x in segments if x["incidence"]["status"] == s) for s in audience_taxonomy.STATUSES},
            "confidence": {c: sum(1 for x in segments if x["incidence"]["confidence"] == c) for c in audience_taxonomy.CONFIDENCES}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--research", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    report = build(args.research, args.out)
    print(f"wrote {args.out} - {report['segments']} segments; status {report['status']}; confidence {report['confidence']}")
    for sid, problem in report["problems"]:
        print(f"  no rate for {sid}: {problem}")
    if report["unused_records"]:
        print("  research records with no editorial entry:", ", ".join(report["unused_records"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
