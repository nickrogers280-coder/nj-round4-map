#!/usr/bin/env python3
"""Write data/nj_municipalities_all.csv: every NJ municipality with its county
and MOD-IV municipal code, taken from the statewide parcel layer.

Run: python pipeline/list_municipalities.py   (needs internet; runs in Actions)
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_sites import ParcelService, ROOT  # noqa: E402

COUNTIES = ["Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland", "Essex",
            "Gloucester", "Hudson", "Hunterdon", "Mercer", "Middlesex", "Monmouth", "Morris",
            "Ocean", "Passaic", "Salem", "Somerset", "Sussex", "Union", "Warren"]


def main():
    svc = ParcelService(pause=0.5)
    rows = {}
    for county in COUNTIES:
        recs = svc.query(
            {
                "where": f"UPPER(COUNTY) = '{county.upper()}'",
                "outFields": "MUN_NAME,PCL_MUN,COUNTY",
                "returnDistinctValues": "true",
                "returnGeometry": "false",
                "f": "json",
            },
            cache_key=f"munis_exact_{county.lower().replace(' ', '_')}",
        )
        found = [f["attributes"] for f in recs.get("features", [])]
        for a in found:
            code = (a.get("PCL_MUN") or "").strip()
            if code:
                rows[code] = {"county": county, "mod4_name": (a.get("MUN_NAME") or "").strip(), "mun_code": code}
        print(f"{county}: {len(found)}")
    out = ROOT / "data" / "nj_municipalities_all.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["mun_code", "county", "mod4_name"])
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r["mun_code"]))
    print(f"{len(rows)} municipalities -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
