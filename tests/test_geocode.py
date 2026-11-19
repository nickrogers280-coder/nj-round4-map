"""Offline tests for the parcel pipeline (no network)."""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import geocode_sites as g  # noqa: E402


def test_parse_blocks_lots():
    assert g.parse_blocks_lots("107:1,3,5") == [("107", "1", ""), ("107", "3", ""), ("107", "5", "")]
    assert g.parse_blocks_lots("101:6.01;109.02:6.04") == [("101", "6.01", ""), ("109.02", "6.04", "")]
    assert g.parse_blocks_lots("12:4/C0002") == [("12", "4", "C0002")]
    assert g.parse_blocks_lots("") == []


def test_parse_blocks_lots_rejects_bad_format():
    try:
        g.parse_blocks_lots("107 lot 1")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_norm_num():
    assert g.norm_num("0107") == "107"
    assert g.norm_num("107.00") == "107"
    assert g.norm_num("6.01") == "6.01"
    assert g.norm_num("6.1") == "6.1"
    assert g.norm_num("12a") == "12A"


def test_match_town():
    cands = [
        {"MUN_NAME": "WALDWICK BORO", "PCL_MUN": "0263", "COUNTY": "BERGEN"},
        {"MUN_NAME": "WASHINGTON TWP", "PCL_MUN": "0266", "COUNTY": "BERGEN"},
        {"MUN_NAME": "FREEHOLD BORO", "PCL_MUN": "1315", "COUNTY": "MONMOUTH"},
        {"MUN_NAME": "FREEHOLD TWP", "PCL_MUN": "1316", "COUNTY": "MONMOUTH"},
        {"MUN_NAME": "AVON BY THE SEA BORO", "PCL_MUN": "1305", "COUNTY": "MONMOUTH"},
        {"MUN_NAME": "BOONTON TOWN", "PCL_MUN": "1401", "COUNTY": "MORRIS"},
        {"MUN_NAME": "BOONTON TWP", "PCL_MUN": "1402", "COUNTY": "MORRIS"},
        {"MUN_NAME": "NEPTUNE CITY BORO", "PCL_MUN": "1334", "COUNTY": "MONMOUTH"},
        {"MUN_NAME": "NEPTUNE TWP", "PCL_MUN": "1335", "COUNTY": "MONMOUTH"},
        {"MUN_NAME": "HASBROUCK HGHTS BORO", "PCL_MUN": "0230", "COUNTY": "BERGEN"},
        {"MUN_NAME": "HOHOKUS BORO", "PCL_MUN": "0233", "COUNTY": "BERGEN"},
        {"MUN_NAME": "JERSEY CITY CITY", "PCL_MUN": "0906", "COUNTY": "HUDSON"},
        {"MUN_NAME": "PARSIPPANY TR HLS TWP", "PCL_MUN": "1429", "COUNTY": "MORRIS"},
    ]
    assert g.match_town("Waldwick", "Bergen", cands)["PCL_MUN"] == "0263"
    assert g.match_town("Washington Twp", "Bergen", cands)["PCL_MUN"] == "0266"
    assert g.match_town("Freehold Borough", "Monmouth", cands)["PCL_MUN"] == "1315"
    assert g.match_town("Freehold Twp", "Monmouth", cands)["PCL_MUN"] == "1316"
    assert g.match_town("Freehold", "Monmouth", cands) is None  # ambiguous on purpose
    assert g.match_town("Avon-by-the-Sea", "Monmouth", cands)["PCL_MUN"] == "1305"
    assert g.match_town("Boonton (Town)", "Morris", cands)["PCL_MUN"] == "1401"
    assert g.match_town("Waldwick", "Morris", cands) is None  # wrong county
    assert g.match_town("Neptune City", "Monmouth", cands)["PCL_MUN"] == "1334"
    assert g.match_town("Neptune Twp", "Monmouth", cands)["PCL_MUN"] == "1335"
    assert g.match_town("Hasbrouck Heights", "Bergen", cands)["PCL_MUN"] == "0230"
    assert g.match_town("Ho-Ho-Kus", "Bergen", cands)["PCL_MUN"] == "0233"
    assert g.match_town("Jersey City", "Hudson", cands)["PCL_MUN"] == "0906"
    assert g.match_town("Parsippany-Troy Hills", "Morris", cands)["PCL_MUN"] == "1429"


def _square(x, y):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + .001, y], [x + .001, y + .001], [x, y + .001], [x, y]]]}


def fake_fetch(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    where = q["where"][0]
    if "returnDistinctValues" in q:
        return {"features": [{"attributes": {"MUN_NAME": "WALDWICK BORO", "PCL_MUN": "0263", "COUNTY": "BERGEN"}}]}
    if "PCLBLOCK='107'" in where:
        feats = []
        for lot in ["1", "3", "5", "7", "8", "9", "10", "11", "12"]:
            feats.append({"type": "Feature", "geometry": _square(-74.1, 41.0),
                          "properties": {"PCLBLOCK": "107", "PCLLOT": lot, "PAMS_PIN": f"0263_107_{lot}",
                                         "CALC_ACRE": 0.15, "OWNER_NAME": "TEST OWNER", "MUN_NAME": "WALDWICK BORO"}})
        return {"type": "FeatureCollection", "features": feats}
    if "PCLBLOCK='101'" in where:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": _square(-74.12, 41.01),
             "properties": {"PCLBLOCK": "101", "PCLLOT": "6.01", "PAMS_PIN": "0263_101_6.01", "MUN_NAME": "WALDWICK BORO"}}]}
    return {"type": "FeatureCollection", "features": []}


def fake_fetch_addr(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    where = q["where"][0]
    if "CLOSTER DOCK" in where:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": _square(-73.95, 40.96),
             "properties": {"PCLBLOCK": str(70 + i), "PCLLOT": "1", "PAMS_PIN": f"0201_{70+i}_1", "PROP_LOC": "CLOSTER DOCK RD"}}
            for i in range(5)]}
    if "returnDistinctValues" in q:
        return {"features": [{"attributes": {"MUN_NAME": "WALDWICK BORO", "PCL_MUN": "0263", "COUNTY": "BERGEN"}},
                             {"attributes": {"MUN_NAME": "ALPINE BORO", "PCL_MUN": "0201", "COUNTY": "BERGEN"}}]}
    if "PCLBLOCK='11'" in where:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": _square(-73.93, 40.95),
             "properties": {"PCLBLOCK": "11", "PCLLOT": "2", "PAMS_PIN": "0201_11_2", "MUN_NAME": "ALPINE BORO"}}]}
    return fake_fetch(url)


def test_build_end_to_end(tmp_path):
    svc = g.ParcelService(cache_dir=tmp_path, pause=0, fetch=fake_fetch_addr)
    sites = g.read_csv(g.SITES_CSV)
    munis = g.read_csv(g.MUNIS_CSV)
    zones = g.read_csv(g.ZONES_CSV)
    geo, unmatched, towns, unmapped = g.build(svc, sites, munis, zones)

    harrison = [f for f in geo["features"] if f["properties"]["site_id"] == "waldwick-harrison-ave"]
    assert len(harrison) == 8  # lot 12 is on the block but not in the plan
    hp = harrison[0]["properties"]
    assert hp["affordable_units"] == 6 and hp["mechanism"] == "Redevelopment" and hp["round"] == "Round 4"
    assert hp["zone"]["zone_code"] == "Harrison Ave RDP" and hp["zone"]["max_density_du_ac"] == 33

    aho2 = [f for f in geo["features"] if f["properties"]["site_id"] == "waldwick-aho2"]
    assert len(aho2) == 1 and aho2[0]["properties"]["zone"]["min_lot_area"] == "30,000 sf"

    montammy = [f for f in geo["features"] if f["properties"]["site_id"] == "alpine-ah1-montammy"]
    assert len(montammy) == 1 and montammy[0]["properties"]["zone"]["max_units"] == 150

    ids = {u["site_id"]: u for u in unmapped}
    assert "alpine-r3-sylco" in ids and "matches 5 lots" in ids["alpine-r3-sylco"]["unmapped_reason"]
    assert ids["alpine-bcuw"]["round"] == "Prior (Rounds 1-2)"
    assert "zone-waldwick-aho1" in ids  # overlay on a whole district, no lots listed
    assert ids["alpine-r3-tower"]["round"] == "Round 3"  # block 80 isn't in the fake layer
    assert len(towns["municipalities"]) == 564


def test_norm_round():
    assert g.norm_round("") == "Round 4"
    assert g.norm_round("4") == "Round 4"
    assert g.norm_round("Third") == "Round 3"
    assert g.norm_round("prior") == "Prior (Rounds 1-2)"
