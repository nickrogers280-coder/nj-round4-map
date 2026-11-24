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
ZONES_CSV = ROOT / "data" / "zones.csv"
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
    "BORO", "BOR", "BOROUGH", "TWP", "TOWNSHIP", "TOWN", "CITY", "VILLAGE", "OF", "THE",
}
# MOD-IV abbreviates names ("E RUTHERFORD", "HASBROUCK HGHTS", "PARSIPPANY TR HLS").
_ABBREV = {
    "E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH", "SO": "SOUTH", "NO": "NORTH",
    "MT": "MOUNT", "HGHTS": "HEIGHTS", "HTS": "HEIGHTS", "HGTS": "HEIGHTS", "PK": "PARK",
    "HLS": "HILLS", "TR": "TROY", "BCH": "BEACH", "LK": "LAKE", "LKS": "LAKES", "SPGS": "SPRINGS",
    "CTR": "CENTER", "VLY": "VALLEY", "BR": "BROOK", "ST": "SAINT", "TWSP": "TWP", "UPR": "UPPER", "LWR": "LOWER",
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


def norm_town(name: str, keep_inner: bool = False) -> str:
    """Comparable key for a town name, ignoring type words, abbreviations,
    punctuation and spacing: 'Ho-Ho-Kus' and 'HOHOKUS BORO' -> 'HOHOKUS';
    'Parsippany-Troy Hills' and 'PARSIPPANY TR HLS TWP' -> 'PARSIPPANYTROYHILLS'."""
    s = name.upper()
    s = re.sub(r"\(.*?\)", " ", s)          # drop "(Town)", "(Morris)" etc.
    s = re.sub(r"[-.'/,]", " ", s)
    words = [_ABBREV.get(w, w) for w in s.split()]
    if keep_inner:  # drop only the trailing type word: 'NEPTUNE CITY BORO' -> 'NEPTUNECITY'
        if len(words) > 1 and words[-1] in _TYPE_WORDS:
            words = words[:-1]
        return "".join(words)
    words = [w for w in words if w not in _TYPE_WORDS]
    return "".join(words)


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
    # Most specific first: the plan's name as written ('Neptune City'), then
    # without its trailing type word ('Neptune Twp' -> 'Neptune').
    as_written = re.sub(r"[^A-Z]", "", " ".join(_ABBREV.get(w, w) for w in
                        re.sub(r"[-.'/,]", " ", re.sub(r"\(.*?\)", " ", plan_name.upper())).split()))
    for key0 in (as_written, norm_town(plan_name, True)):
        exact = [c for c in pool if norm_town(c["MUN_NAME"], True) == key0]
        if len(exact) == 1:
            return exact[0]
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

    def address(self, mun_code: str, hint: str) -> list[dict]:
        """Lots in a town whose MOD-IV property location contains `hint`
        (e.g. 'CLOSTER DOCK'), for sites the plan names by address only."""
        safe = re.sub(r"[^A-Z0-9 &.-]", "", hint.upper()).strip()
        if not safe:
            return []
        data = self.query(
            {
                "where": f"PCL_MUN='{mun_code}' AND UPPER(PROP_LOC) LIKE '%{safe}%'",
                "outFields": ",".join(PARCEL_FIELDS),
                "outSR": "4326",
                "returnGeometry": "true",
                "geometryPrecision": "6",
                "resultRecordCount": "50",
                "f": "geojson",
            },
            cache_key=f"addr_{mun_code}_{re.sub(r'[^A-Z0-9]+', '_', safe)}",
        )
        return data.get("features", [])


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


SITE_FIELDS = ["site_id", "county", "municipality", "round", "site_name", "mechanism", "zone_id", "blocks_lots",
               "address", "acres", "total_units", "affordable_units", "set_aside", "max_density_du_ac", "zoning",
               "site_status", "existing_conditions", "plan_doc", "plan_url", "plan_page", "notes"]
ZONE_FIELDS = ["zone_id", "county", "municipality", "zone_code", "zone_name", "round", "mechanism", "applies_to",
               "permitted_uses", "max_density_du_ac", "max_units", "set_aside", "min_lot_area", "min_frontage",
               "max_height", "max_building_coverage", "max_impervious_coverage", "setbacks", "parking",
               "other_standards", "ordinance_status", "plan_doc", "plan_page"]
ADDR_MAX_LOTS = 3   # an address hint that matches more lots than this is too vague to map


def norm_round(v: str) -> str:
    """'4', 'Round 4', 'fourth' -> 'Round 4'; '3', 'third' -> 'Round 3'; 1/2/prior -> 'Prior (Rounds 1-2)'."""
    s = str(v or "").strip().lower()
    if not s or s in {"4", "round 4", "fourth", "4th", "fourth round", "4th round"}:
        return "Round 4"
    if s in {"3", "round 3", "third", "3rd", "third round", "3rd round"}:
        return "Round 3"
    return "Prior (Rounds 1-2)"


def zone_record(z: dict) -> dict:
    out = {k: (z.get(k) or "").strip() for k in ZONE_FIELDS}
    out["max_density_du_ac"] = to_number(z.get("max_density_du_ac"))
    out["max_units"] = to_number(z.get("max_units"))
    out["round"] = norm_round(z.get("round"))
    return out


def site_record(site: dict, zone: dict | None) -> dict:
    return {
        "site_id": site["site_id"].strip(),
        "county": site["county"].strip(),
        "municipality": site["municipality"].strip(),
        "round": norm_round(site.get("round")),
        "site_name": site.get("site_name", ""),
        "address": site.get("address", ""),
        "site_acres": to_number(site.get("acres")),
        "mechanism": site.get("mechanism", "") or (zone or {}).get("mechanism", "") or "Other",
        "total_units": to_number(site.get("total_units")),
        "affordable_units": to_number(site.get("affordable_units")),
        "set_aside": site.get("set_aside", "") or (zone or {}).get("set_aside", ""),
        "max_density_du_ac": to_number(site.get("max_density_du_ac")) if site.get("max_density_du_ac") else (zone or {}).get("max_density_du_ac"),
        "zoning": site.get("zoning", ""),
        "site_status": site.get("site_status", ""),
        "existing_conditions": site.get("existing_conditions", ""),
        "plan_doc": site.get("plan_doc", ""),
        "plan_url": site.get("plan_url", ""),
        "plan_page": site.get("plan_page", ""),
        "notes": site.get("notes", ""),
        "zone": zone,
    }


OWNERS_CSV = ROOT / "data" / "owners.csv"
_OWNERS: dict | None = None


def owner_lookup(pams_pin: str | None, block, lot) -> dict:
    """Owner of record from the county tax boards (data/owners.csv). The statewide
    parcel layer blanks OWNER_NAME (Daniel's Law), so names come from there."""
    global _OWNERS
    if _OWNERS is None:
        _OWNERS = {}
        if OWNERS_CSV.exists():
            with open(OWNERS_CSV, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    _OWNERS[(r["mun_code"].zfill(4), norm_num(r["block"]), norm_num(r["lot"]))] = r
    mun = (pams_pin or "").split("_")[0].zfill(4)
    return _OWNERS.get((mun, norm_num(str(block or "")), norm_num(str(lot or "")))) or {}


def parcel_props(f: dict, block: str, lot: str, how: str) -> dict:
    p = f["properties"]
    own = owner_lookup(p.get("PAMS_PIN"), block or p.get("PCLBLOCK"), lot or p.get("PCLLOT"))
    return {
        "block": block or p.get("PCLBLOCK"),
        "lot": lot or p.get("PCLLOT"),
        "located_by": how,
        "pams_pin": p.get("PAMS_PIN"),
        "mod4_town": p.get("MUN_NAME"),
        "prop_loc": p.get("PROP_LOC"),
        "prop_class": p.get("PROP_CLASS"),
        "parcel_acres": round(p["CALC_ACRE"], 3) if p.get("CALC_ACRE") else None,
        "owner": own.get("owner_name") or p.get("OWNER_NAME"),
        "owner_source": (f'{own["source"]}, {own["retrieved"]}' if own else ""),
        "owner_address": " ".join(x for x in [p.get("ST_ADDRESS"), p.get("CITY_STATE")] if x),
        "land_value": p.get("LAND_VAL"),
        "improvement_value": p.get("IMPRVT_VAL"),
        "net_value": p.get("NET_VALUE"),
        "building": p.get("BLDG_DESC"),
        "last_sale_price": p.get("SALE_PRICE"),
        "last_sale_date": p.get("DEED_DATE"),
        "year_built": p.get("YR_CONSTR"),
    }


def build(service: ParcelService, sites: list[dict], munis: list[dict], zones: list[dict] | None = None):
    """Returns (geojson, unmatched rows, towns json, unmapped list).

    unmapped: sites with no lot on the map yet, plus zones that no site row
    points to (e.g. an overlay covering a whole zoning district), so the map
    can still list them."""
    features: list[dict] = []
    unmatched: list[dict] = []
    unmapped: list[dict] = []
    town_codes: dict[tuple[str, str], str | None] = {}
    candidates_by_county: dict[str, list[dict]] = {}
    zone_by_id = {z["zone_id"].strip(): zone_record(z) for z in (zones or []) if z.get("zone_id", "").strip()}
    used_zones: set[str] = set()

    for site in sites:
        if not site.get("site_id", "").strip():
            continue
        county, town = site["county"].strip(), site["municipality"].strip()
        zid = (site.get("zone_id") or "").strip()
        zone = zone_by_id.get(zid) if zid else None
        if zid:
            used_zones.add(zid)
        rec_site = site_record(site, zone)

        key = (county, town)
        if key not in town_codes:
            if county not in candidates_by_county:
                candidates_by_county[county] = service.municipalities(county)
            rec = match_town(town, county, candidates_by_county[county])
            town_codes[key] = rec["PCL_MUN"] if rec else None
            print(f"{town}, {county}: {rec['MUN_NAME'] + ' / ' + rec['PCL_MUN'] if rec else 'NO MATCH'}")
        mun_code = town_codes[key]

        def miss(reason, b="", l=""):
            unmatched.append({**_ref(site), "block": b, "lot": l, "reason": reason})

        raw = (site.get("blocks_lots") or "").strip()
        hits_for_site: list[dict] = []
        if raw.lower().startswith("addr:"):
            hint = raw[5:].strip()
            if not mun_code:
                miss("Town not found in parcel layer")
            else:
                try:
                    found = service.address(mun_code, hint)
                except RuntimeError as exc:  # parcel service rejected this one query; don't fail the whole build
                    print(f"  address lookup failed for {site.get('site_id')} ({hint!r}): {exc}", file=sys.stderr)
                    found = None
                    miss(f"Address lookup failed at '{hint}'")
                if found is None:
                    pass
                elif not found:
                    miss(f"No lot found at '{hint}'")
                elif len(found) > ADDR_MAX_LOTS:
                    miss(f"'{hint}' matches {len(found)} lots; add block/lot")
                else:
                    hits_for_site = [{**f, "properties": {**rec_site, **parcel_props(f, "", "", "address")}} for f in found]
        else:
            try:
                wanted = parse_blocks_lots(raw)
            except ValueError as exc:
                wanted = None
                miss(str(exc))
            if wanted == []:
                miss("No block/lot listed")
            elif wanted and not mun_code:
                for b, l, q in wanted:
                    miss("Town not found in parcel layer", b, l)
            elif wanted:
                blocks: dict[str, list[dict]] = {}
                for b, l, q in wanted:
                    if b not in blocks:
                        blocks[b] = service.block(mun_code, b)
                    hits = [f for f in blocks[b] if norm_num(f["properties"].get("PCLLOT")) == norm_num(l)]
                    if q:
                        hq = [f for f in hits if (f["properties"].get("PCLQCODE") or "").strip().upper() == q.upper()]
                        hits = hq or hits
                    if not hits:
                        miss("Lot not found on block", b, l)
                        continue
                    for f in hits:
                        hits_for_site.append({**f, "properties": {**rec_site, **parcel_props(f, b, l, "block/lot")}})
        if hits_for_site:
            features.extend({"type": "Feature", "geometry": f["geometry"], "properties": f["properties"]} for f in hits_for_site)
        else:
            reasons = [u["reason"] for u in unmatched if u["site_id"] == site.get("site_id")]
            unmapped.append({**rec_site, "blocks_lots": raw, "unmapped_reason": "; ".join(dict.fromkeys(reasons)) or "Not located"})

    for zid, z in zone_by_id.items():
        if zid in used_zones:
            continue
        unmapped.append({
            "site_id": "zone-" + zid, "county": z["county"], "municipality": z["municipality"], "round": z["round"],
            "site_name": f"{z['zone_code']} {z['zone_name']}".strip() or zid, "address": "", "site_acres": None,
            "mechanism": z["mechanism"] or "Overlay zone", "total_units": z["max_units"], "affordable_units": None,
            "set_aside": z["set_aside"], "max_density_du_ac": z["max_density_du_ac"], "zoning": z["zone_code"],
            "site_status": z["ordinance_status"], "existing_conditions": z["applies_to"], "plan_doc": z["plan_doc"],
            "plan_url": "", "plan_page": z["plan_page"], "notes": "", "zone": z, "blocks_lots": "",
            "unmapped_reason": "Zone covers an area described in the plan, not listed lots",
        })

    towns_out = {
        "generated": time.strftime("%Y-%m-%d"),
        "municipalities": [
            {k: m.get(k, "") for k in ("county", "municipality", "tracker_status", "latest_adoption")}
            for m in munis
        ],
    }
    geo = {"type": "FeatureCollection", "generated": time.strftime("%Y-%m-%d"), "features": features}
    return geo, unmatched, towns_out, unmapped


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
    zones = read_csv(ZONES_CSV) if ZONES_CSV.exists() else []
    geo, unmatched, towns, unmapped = build(ParcelService(), sites, munis, zones)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sites.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    (OUT_DIR / "municipalities.json").write_text(json.dumps(towns, indent=1))
    (OUT_DIR / "unmapped.json").write_text(json.dumps({"generated": geo["generated"], "items": unmapped}, indent=1))
    with (OUT_DIR / "unmatched.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["site_id", "county", "municipality", "site_name", "block", "lot", "reason"])
        w.writeheader()
        w.writerows(unmatched)

    n_sites = len({f["properties"]["site_id"] for f in geo["features"]})
    print(f"\n{len(geo['features'])} parcels across {n_sites} sites mapped; "
          f"{len(unmapped)} sites/zones not on the map; {len(unmatched)} block/lot problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
