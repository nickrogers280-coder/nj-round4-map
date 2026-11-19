#!/usr/bin/env python3
"""Check data/sites.csv and data/zones.csv before they are committed.
Exit code 1 (with a list of problems) if anything is wrong.

  python pipeline/validate_data.py
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_sites import (MUNIS_CSV, SITE_FIELDS, SITES_CSV, ZONE_FIELDS, ZONES_CSV,  # noqa: E402
                           parse_blocks_lots, to_number)

MECHANISMS = {"Overlay zone", "Inclusionary zoning", "Redevelopment", "100% affordable", "Municipally sponsored",
              "Supportive housing", "Existing / credit", "Other"}
ROUNDS = {"", "4", "3", "prior", "Round 4", "Round 3", "Prior (Rounds 1-2)"}
NUMERIC = ["acres", "total_units", "affordable_units", "max_density_du_ac"]


def main() -> int:
    errs = []
    towns = {(r["county"], r["municipality"]) for r in csv.DictReader(MUNIS_CSV.open(encoding="utf-8"))}
    with ZONES_CSV.open(newline="", encoding="utf-8") as fh:
        zr = csv.DictReader(fh)
        if zr.fieldnames != ZONE_FIELDS:
            errs.append(f"zones.csv columns must be exactly: {','.join(ZONE_FIELDS)}")
        zones = list(zr)
    with SITES_CSV.open(newline="", encoding="utf-8") as fh:
        sr = csv.DictReader(fh)
        if sr.fieldnames != SITE_FIELDS:
            errs.append(f"sites.csv columns must be exactly: {','.join(SITE_FIELDS)}")
        sites = list(sr)

    zone_ids = set()
    for i, z in enumerate(zones, 2):
        zid = z.get("zone_id", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9.]+)*", zid or ""):
            errs.append(f"zones.csv line {i}: zone_id {zid!r} must be lowercase-with-dashes")
        if zid in zone_ids:
            errs.append(f"zones.csv line {i}: duplicate zone_id {zid}")
        zone_ids.add(zid)
        if (z["county"], z["municipality"]) not in towns:
            errs.append(f"zones.csv line {i}: town {z['municipality']}, {z['county']} is not in municipalities.csv")
        if z.get("mechanism") and z["mechanism"] not in MECHANISMS:
            errs.append(f"zones.csv line {i}: mechanism {z['mechanism']!r} not one of {sorted(MECHANISMS)}")
        if z.get("round", "") not in ROUNDS:
            errs.append(f"zones.csv line {i}: round {z['round']!r} not one of 4, 3, prior")
        for k in ("max_density_du_ac", "max_units"):
            if z.get(k) and to_number(z[k]) is None:
                errs.append(f"zones.csv line {i}: {k} {z[k]!r} must be a number")

    site_ids = set()
    for i, s in enumerate(sites, 2):
        sid = s.get("site_id", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9.]+)*", sid or ""):
            errs.append(f"sites.csv line {i}: site_id {sid!r} must be lowercase-with-dashes")
        if sid in site_ids:
            errs.append(f"sites.csv line {i}: duplicate site_id {sid}")
        site_ids.add(sid)
        if (s["county"], s["municipality"]) not in towns:
            errs.append(f"sites.csv line {i}: town {s['municipality']}, {s['county']} is not in municipalities.csv")
        if s.get("mechanism") not in MECHANISMS:
            errs.append(f"sites.csv line {i}: mechanism {s.get('mechanism')!r} not one of {sorted(MECHANISMS)}")
        if s.get("round", "") not in ROUNDS:
            errs.append(f"sites.csv line {i}: round {s['round']!r} not one of 4, 3, prior")
        if s.get("zone_id") and s["zone_id"] not in zone_ids:
            errs.append(f"sites.csv line {i}: zone_id {s['zone_id']} not found in zones.csv")
        bl = (s.get("blocks_lots") or "").strip()
        if bl and not bl.lower().startswith("addr:"):
            try:
                parse_blocks_lots(bl)
            except ValueError as e:
                errs.append(f"sites.csv line {i}: blocks_lots {bl!r}: {e}")
        for k in NUMERIC:
            if s.get(k) and to_number(s[k]) is None:
                errs.append(f"sites.csv line {i}: {k} {s[k]!r} must be a number")

    if errs:
        print("\n".join(errs))
        return 1
    print(f"OK: {len(sites)} sites, {len(zones)} zones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
