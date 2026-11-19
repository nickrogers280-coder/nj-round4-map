#!/usr/bin/env python3
"""Turn data/sites.csv into map-ready parcel GeoJSON.

For each site, every block/lot listed in `blocks_lots` is looked up in New
Jersey's statewide parcel + MOD-IV composite layer (NJOGIS, ArcGIS Online).
Matched parcels are written to docs/data/sites.geojson with the site's plan
fields and the parcel's tax fields; anything that doesn't match goes to
docs/data/unmatched.csv so it can be fixed by hand.

blocks_lots format:   "107:1,3,5;109.02:6.04"
  - blocks separated by ";"
  - "block:lot,lot,lot"
  - a qualifier can follow a lot with "/", e.g. "12:4/C0002"

Run:  python pipeline/geocode_sites.py
Needs internet access to services2.arcgis.com (GitHub Actions has it).
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITES_CSV = ROOT / "data" / "sites.csv"
MUNIS_CSV = ROOT / "data" / "municipalities.csv"
OUT_DIR = ROOT / "docs" / "data"
CACHE_DIR = ROOT / "pipeline" / "cache"

SERVICE = (
    "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/"
    "Parcels_Composite_NJ_WM/FeatureServer/0/query"
)
PARCEL_FIELDS = [
    "PAMS_PIN", "PCL_MUN", "MUN_NAME", "COUNTY", "PCLBLOCK", "PCLLOT", "PCLQCODE",
    "PROP_LOC", "PROP_CLASS", "CALC_ACRE", "LAND_VAL", "IMPRVT_VAL", "NET_VALUE",
    "OWNER_NAME", "ST_ADDRESS", "CITY_STATE", "BLDG_DESC", "LAND_DESC",
    "SALE_PRICE", "DEED_DATE", "YR_CONSTR",
]

# Words that differ between how plans name a town and how MOD-IV does.
_TYPE_WORDS = {
    "BORO", "BOROUGH", "TWP", "TOWNSHIP", "TOWN", "CITY", "VILLAGE", "OF", "THE",
    "CITY OF",
}


# --------------------------------------------------------------------------- #
# Parsing and normalisation (pure functions; covered by tests)
# --------------------------------------------------------------------------- #
def parse_blocks_lots(text: str) -> list[tuple[str, str, str]]:
    """'107:1,3;109.02:6.04/C01' -> [('107','1',''),('107','3',''),('109.02','6.04','C01')]"""
    out: list[tuple[str, str, str]] = []
    if not text or not text.strip():
        return out
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Missing ':' between block and lots in {chunk!r}")
        block, lots = chunk.split(":", 1)
        block = block.strip()
        for lot in lots.split(","):
            lot = lot.strip()
            if not lot:
                continue
            qual = ""
            if "/" in lot:
                lot, qual = (p.strip() for p in lot.split("/", 1))
            out.append((block, lot, qual))
    return out


def norm_num(value: str | None) -> str:
    """Normalise a block or lot so '0107', '107.00' and '107' compare equal.
    Sub-lots keep their digits as written ('6.01' stays '6.01'), because
    MOD-IV treats 6.01 and 6.1 as different lots."""
    if value is None:
        return ""
    v = str(value).strip().upper()
    if not v:
        return ""
    m = re.fullmatch(r"0*(\d+)(?:\.(\d+))?([A-Z]*)", v)
    if not m:
        return v
    whole, frac, suffix = m.groups()
    whole = whole or "0"
    if frac is not None and set(frac) <= {"0"}:
        frac = None
    return whole + (f".{frac}" if frac else "") + suffix


def norm_town(name: str) -> str:
    """'Washington Twp (Bergen)' / 'WASHINGTON TOWNSHIP' -> 'WASHINGTON'."""
    s = name.upper()
    s = re.sub(r"\(.*?\)", " ", s)          # drop "(Town)", "(Morris)" etc.
    s = s.replace("-", " ").replace(".", " ").replace("'", "")
    words = [w for w in re.split(r"\s+", s) if w and w not in _TYPE_WORDS]
    return " ".join(words)


def town_kind(name: str) -> str:
    """Rough municipal type, used to break ties (e.g. Freehold Boro vs Twp)."""
    s = name.upper()
    if re.search(r"\bTWP\b|\bTOWNSHIP\b", s):
        return "TWP"
    if re.search(r"\bBORO\b|\bBOROUGH\b", s):
        return "BORO"
    if re.search(r"\bCITY\b", s):
        return "CITY"
    if re.search(r"\bTOWN\b", s):
        return "TOWN"
    if re.search(r"\bVILLAGE\b", s):
        return "VILLAGE"
    return ""


def match_town(plan_name: str, county: str, candidates: list[dict]) -> dict | None:
    """Pick the MOD-IV municipality record for a town named in a plan.
    candidates: [{'MUN_NAME':..., 'PCL_MUN':..., 'COUNTY':...}, ...]"""
    county_u = county.upper()
    pool = [c for c in candidates if county_u in (c.get("COUNTY") or "").upper()]
    key = norm_town(plan_name)
    hits = [c for c in pool if norm_town(c["MUN_NAME"]) == key]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        kind = town_kind(plan_name)
        typed = [c for c in hits if town_kind(c["MUN_NAME"]) == kind]
        if len(typed) == 1:
            return typed[0]
        return None  # ambiguous: e.g. "Freehold" with no Borough/Twp given
    # Fallback: prefix match (e.g. "Avon-by-the-Sea" vs "AVON BY THE SEA BORO")
    hits = [c for c in pool if norm_town(c["MUN_NAME"]).startswith(key) or key.startswith(norm_town(c["MUN_NAME"]))]
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------- #
# Service access
# --------------------------------------------------------------------------- #
class ParcelService:
    def __init__(self, cache_dir: Path = CACHE_DIR, pause: float = 0.25, fetch=None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pause = pause
        self._fetch = fetch or self._http_get

    @staticmethod
    def _http_get(url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "nj-round4-map/0.1"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # network hiccup or 5xx
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                print(f"  retrying after {exc}", file=sys.stderr)
        raise RuntimeError("unreachable")

    def query(self, params: dict, cache_key: str | None = None) -> dict:
        if cache_key:
            path = self.cache_dir / f"{cache_key}.json"
            if path.exists():
                return json.loads(path.read_text())
        url = SERVICE + "?" + urllib.parse.urlencode(params)
        data = self._fetch(url)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Parcel service error: {data['error']}")
        if cache_key:
            (self.cache_dir / f"{cache_key}.json").write_text(json.dumps(data))
        time.sleep(self.pause)
        return data

    def municipalities(self, county: str) -> list[dict]:
        data = self.query(
            {
                "where": f"UPPER(COUNTY) LIKE '%{county.upper()}%'",
                "outFields": "MUN_NAME,PCL_MUN,COUNTY",
                "returnDistinctValues": "true",
                "returnGeometry": "false",
                "f": "json",
            },
            cache_key=f"munis_{county.lower()}",
        )
        return [f["attributes"] for f in data.get("features", [])]

    def block(self, mun_code: str, block: str) -> list[dict]:
        """All parcels on one block, as GeoJSON features in WGS84."""
        safe_block = block.replace("'", "")
        variants = sorted({safe_block, norm_num(safe_block)})
        where = f"PCL_MUN='{mun_code}' AND (" + " OR ".join(
            f"PCLBLOCK='{b}'" for b in variants) + ")"
        data = self.query(
            {
                "where": where,
                "outFields": ",".join(PARCEL_FIELDS),
                "outSR": "4326",
                "returnGeometry": "true",
                "geometryPrecision": "6",
                "resultRecordCount": "2000",
                "f": "geojson",
            },
            cache_key=f"block_{mun_code}_{norm_num(block).replace('.', '_')}",
        )
        feats = data.get("features", [])
        if not feats:
            # Some towns store blocks with leading zeros or suffixes; widen once.
            data = self.query(
                {
                    "where": f"PCL_MUN='{mun_code}' AND PCLBLOCK LIKE '%{norm_num(safe_block)}%'",
                    "outFields": ",".join(PARCEL_FIELDS),
                    "outSR": "4326",
                    "returnGeometry": "true",
                    "geometryPrecision": "6",
                    "resultRecordCount": "2000",
                    "f": "geojson",
                },
                cache_key=f"blocklike_{mun_code}_{norm_num(block).replace('.', '_')}",
            )
            feats = [f for f in data.get("features", [])
                     if norm_num(f["properties"].get("PCLBLOCK")) == norm_num(block)]
        return feats


# --------------------------------------------------------------------------- #
# Main build
# --------------------------------------------------------------------------- #
def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def to_number(v: str):
    if v is None or str(v).strip() == "":
        return None
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def build(service: ParcelService, sites: list[dict], munis: list[dict]) -> tuple[dict, list[dict], dict]:
    features: list[dict] = []
    unmatched: list[dict] = []
    town_codes: dict[tuple[str, str], str | None] = {}
    candidates_by_county: dict[str, list[dict]] = {}

    for site in sites:
        county, town = site["county"].strip(), site["municipality"].strip()
        key = (county, town)
        if key not in town_codes:
            if county not in candidates_by_county:
                candidates_by_county[county] = service.municipalities(county)
            rec = match_town(town, county, candidates_by_county[county])
            town_codes[key] = rec["PCL_MUN"] if rec else None
            print(f"{town}, {county}: {rec['MUN_NAME'] + ' / ' + rec['PCL_MUN'] if rec else 'NO MATCH'}")
        mun_code = town_codes[key]

        try:
            wanted = parse_blocks_lots(site.get("blocks_lots", ""))
        except ValueError as exc:
            unmatched.append({**_ref(site), "block": "", "lot": "", "reason": str(exc)})
            continue
        if not wanted:
            unmatched.append({**_ref(site), "block": "", "lot": "", "reason": "No block/lot listed"})
            continue
        if not mun_code:
            for b, l, q in wanted:
                unmatched.append({**_ref(site), "block": b, "lot": l, "reason": "Town not found in parcel layer"})
            continue

        props_site = {
            "site_id": site["site_id"],
            "county": county,
            "municipality": town,
            "site_name": site.get("site_name", ""),
            "address": site.get("address", ""),
            "site_acres": to_number(site.get("acres")),
            "mechanism": site.get("mechanism", "") or "Other",
            "total_units": to_number(site.get("total_units")),
            "affordable_units": to_number(site.get("affordable_units")),
            "set_aside": site.get("set_aside", ""),
            "max_density_du_ac": to_number(site.get("max_density_du_ac")),
            "zoning": site.get("zoning", ""),
            "site_status": site.get("site_status", ""),
            "plan_doc": site.get("plan_doc", ""),
            "plan_page": site.get("plan_page", ""),
            "notes": site.get("notes", ""),
        }

        blocks: dict[str, list[dict]] = {}
        for b, l, q in wanted:
            if b not in blocks:
                blocks[b] = service.block(mun_code, b)
            pool = blocks[b]
            hits = [f for f in pool if norm_num(f["properties"].get("PCLLOT")) == norm_num(l)]
            if q:
                hq = [f for f in hits if (f["properties"].get("PCLQCODE") or "").strip().upper() == q.upper()]
                hits = hq or hits
            if not hits:
                unmatched.append({**_ref(site), "block": b, "lot": l, "reason": "Lot not found on block"})
                continue
            for f in hits:
                p = f["properties"]
                features.append({
                    "type": "Feature",
                    "geometry": f["geometry"],
                    "properties": {
                        **props_site,
                        "block": b,
                        "lot": l,
                        "pams_pin": p.get("PAMS_PIN"),
                        "mod4_town": p.get("MUN_NAME"),
                        "prop_loc": p.get("PROP_LOC"),
                        "prop_class": p.get("PROP_CLASS"),
                        "parcel_acres": round(p["CALC_ACRE"], 3) if p.get("CALC_ACRE") else None,
                        "owner": p.get("OWNER_NAME"),
                        "owner_address": " ".join(x for x in [p.get("ST_ADDRESS"), p.get("CITY_STATE")] if x),
                        "land_value": p.get("LAND_VAL"),
                        "improvement_value": p.get("IMPRVT_VAL"),
                        "net_value": p.get("NET_VALUE"),
                        "building": p.get("BLDG_DESC"),
                        "last_sale_price": p.get("SALE_PRICE"),
                        "last_sale_date": p.get("DEED_DATE"),
                        "year_built": p.get("YR_CONSTR"),
                    },
                })

    towns_out = {
        "generated": time.strftime("%Y-%m-%d"),
        "municipalities": [
            {k: m.get(k, "") for k in ("county", "municipality", "tracker_status", "latest_adoption")}
            for m in munis
        ],
    }
    geo = {"type": "FeatureCollection", "generated": time.strftime("%Y-%m-%d"), "features": features}
    return geo, unmatched, towns_out


def _ref(site: dict) -> dict:
    return {"site_id": site.get("site_id", ""), "county": site.get("county", ""),
            "municipality": site.get("municipality", ""), "site_name": site.get("site_name", "")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-cache", action="store_true", help="Ignore cached service responses")
    args = ap.parse_args(argv)

    if args.no_cache and CACHE_DIR.exists():
        for p in CACHE_DIR.glob("*.json"):
            p.unlink()

    sites = read_csv(SITES_CSV)
    munis = read_csv(MUNIS_CSV)
    geo, unmatched, towns = build(ParcelService(), sites, munis)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sites.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    (OUT_DIR / "municipalities.json").write_text(json.dumps(towns, indent=1))
    with (OUT_DIR / "unmatched.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["site_id", "county", "municipality", "site_name", "block", "lot", "reason"])
        w.writeheader()
        w.writerows(unmatched)

    n_sites = len({f["properties"]["site_id"] for f in geo["features"]})
    print(f"\n{len(geo['features'])} parcels across {n_sites} sites mapped; {len(unmatched)} block/lots unmatched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
