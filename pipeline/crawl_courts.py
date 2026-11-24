"""Index every municipality's affordable housing filings on NJ Courts.

Reads the 21 county pages at https://www.njcourts.gov/courts/civil/affordable-housing/<county>
(each town is an <h2>, followed by its documents: type label, posted date, PDF link) and writes

  data/court_filings.csv  one row per document
  data/court_status.csv   one row per town: newest Housing Element & Fair Share Plan,
                          compliance order, and the operative-plan call (Nick's rules):
                            - newest HEFSP is 2026 / amended  -> "2026 / amended plan"
                            - 2025 HEFSP + compliance order   -> "2025 plan, compliant"
                            - 2025 HEFSP, no order yet        -> "2025 plan, no compliance order yet"
                            - nothing posted                  -> "No plan on NJ Courts"

Runs in GitHub Actions (.github/workflows/court-filings.yml); the court site is reachable there.
usage: python pipeline/crawl_courts.py [--html-dir DIR]   (DIR: saved <county>.html pages, for tests)
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geocode_sites as g  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILINGS = ROOT / "data" / "court_filings.csv"
STATUS = ROOT / "data" / "court_status.csv"
BASE = "https://www.njcourts.gov/courts/civil/affordable-housing/"
COUNTIES = ["Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland", "Essex", "Gloucester",
            "Hudson", "Hunterdon", "Mercer", "Middlesex", "Monmouth", "Morris", "Ocean", "Passaic", "Salem",
            "Somerset", "Sussex", "Union", "Warren"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 nj-round4-map"

FILING_FIELDS = ["county", "court_name", "municipality", "mun_code", "doc_type", "posted", "file_name", "url"]
STATUS_FIELDS = ["county", "municipality", "mun_code", "court_name", "court_status", "plan_posted", "plan_url",
                 "plan_kind", "base_plan_posted", "base_plan_url", "compliance_posted", "compliance_url",
                 "hefsp_count", "doc_count", "page_url"]

# Court names our matcher can't resolve: a typo on the court page, and Fanwood, whose code is
# mislabeled in the parcel layer's name list.
COURT_ALIASES = {("Union", "Fanwood Boro"): "2005", ("Warren", "Indenpendence Twp"): "2112"}

H2 = re.compile(r'<h2[^>]*>(.*?)</h2>', re.S)
DOC = re.compile(r'<a[^>]+href="(https://library\.njcourts\.gov/files/civil/affordable-housing/[^"]+)"[^>]*>(.*?)</a>\s*(?:<em>(.*?)</em>)?', re.S)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).replace("\xa0", " ").strip()


def parse_date(s: str) -> str:
    s = clean(s).replace("Sept.", "Sep.").replace(".", "")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def parse_county(page: str, county: str) -> list[dict]:
    rows = []
    heads = [(m.start(), clean(m.group(1))) for m in H2.finditer(page)]
    for i, (pos, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(page)
        for m in DOC.finditer(page, pos, end):
            url = html.unescape(m.group(1))
            rows.append({"county": county, "court_name": name, "doc_type": clean(m.group(2)),
                         "posted": parse_date(m.group(3) or ""), "url": url,
                         "file_name": url.split("?")[0].rsplit("/", 1)[-1]})
    return rows


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def is_hefsp(r: dict) -> bool:
    t = (r["doc_type"] + " " + r["file_name"]).lower()
    return ("housing element" in t or "fair share" in t or "hefsp" in t or "housing plan" in t) and "resolution" not in r["doc_type"].lower()


def is_compliance(r: dict) -> bool:
    t = (r["doc_type"] + " " + r["file_name"]).lower()
    return "compliance" in t and ("order" in t or "certif" in t)


def is_2026(r: dict) -> bool:
    t = (r["doc_type"] + " " + r["file_name"]).lower()
    return (r["posted"] >= "2025-12-01") or "amend" in t or "2026" in t


def town_status(docs: list[dict]) -> dict:
    hefsp = sorted([d for d in docs if is_hefsp(d)], key=lambda d: d["posted"] or "0")
    comp = sorted([d for d in docs if is_compliance(d)], key=lambda d: d["posted"] or "0")
    out = {"hefsp_count": len(hefsp), "doc_count": len(docs), "plan_posted": "", "plan_url": "", "plan_kind": "",
           "base_plan_posted": "", "base_plan_url": "", "compliance_posted": "", "compliance_url": ""}
    if comp:
        out.update(compliance_posted=comp[-1]["posted"], compliance_url=comp[-1]["url"])
    if not hefsp:
        out["court_status"] = "No plan on NJ Courts"
        return out
    latest = hefsp[-1]
    out.update(plan_posted=latest["posted"], plan_url=latest["url"], plan_kind=latest["doc_type"])
    if is_2026(latest):
        base = [d for d in hefsp if not is_2026(d)]
        if base:
            out.update(base_plan_posted=base[-1]["posted"], base_plan_url=base[-1]["url"])
        out["court_status"] = "2026 / amended plan"
    elif comp:
        out["court_status"] = "2025 plan, compliant"
    else:
        out["court_status"] = "2025 plan, no compliance order yet"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-dir")
    a = ap.parse_args()
    cands = [{"MUN_NAME": r["mod4_name"], "PCL_MUN": r["mun_code"], "COUNTY": r["county"].upper()}
             for r in g.read_csv(ROOT / "data" / "nj_municipalities_all.csv")]
    munis = {r["mun_code"]: r for r in g.read_csv(g.MUNIS_CSV)}
    filings, failed = [], []
    for county in COUNTIES:
        slug = county.lower().replace(" ", "-")
        try:
            page = (Path(a.html_dir) / f"{slug}.html").read_text() if a.html_dir else fetch(BASE + slug)
        except Exception as e:  # keep going; report at the end
            failed.append(f"{county}: {e}")
            continue
        rows = parse_county(page, county)
        names = {}
        for r in rows:
            if r["court_name"] not in names:
                alias = COURT_ALIASES.get((county, r["court_name"]))
                m = None if alias else g.match_town(r["court_name"], county, cands)
                names[r["court_name"]] = alias or (m["PCL_MUN"] if m else "")
            r["mun_code"] = names[r["court_name"]]
            r["municipality"] = munis.get(r["mun_code"], {}).get("municipality", "")
        filings += rows
        print(f"{county}: {len(names)} towns, {len(rows)} documents"
              + (f"; unmatched names: {[n for n, c in names.items() if not c]}" if any(not c for c in names.values()) else ""))
        if not a.html_dir:
            time.sleep(1)
    if failed and not filings:
        sys.exit("no county page could be read: " + "; ".join(failed))
    by_town: dict[tuple, list] = {}
    for r in filings:
        by_town.setdefault((r["county"], r["court_name"]), []).append(r)
    status = []
    seen = set()
    for (county, name), docs in sorted(by_town.items()):
        st = town_status(docs)
        code = docs[0]["mun_code"]
        seen.add(code)
        status.append({"county": county, "municipality": docs[0]["municipality"] or name, "mun_code": code,
                       "court_name": name, "page_url": BASE + county.lower().replace(" ", "-"), **st})
    for code, m in munis.items():  # towns with no section at all
        if code not in seen and (not a.html_dir or m["county"] in {c for c, _ in by_town}):
            status.append({"county": m["county"], "municipality": m["municipality"], "mun_code": code, "court_name": "",
                           "court_status": "No plan on NJ Courts", "hefsp_count": 0, "doc_count": 0,
                           "page_url": BASE + m["county"].lower().replace(" ", "-")})
    status.sort(key=lambda r: (r["county"], r["municipality"]))
    for path, fields, rows in [(FILINGS, FILING_FIELDS, filings), (STATUS, STATUS_FIELDS, status)]:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    from collections import Counter
    print(f"{len(filings)} documents, {len(status)} towns:", dict(Counter(r["court_status"] for r in status)))
    if failed:
        print("FAILED:", failed)


if __name__ == "__main__":
    main()
