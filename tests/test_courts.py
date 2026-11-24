"""Offline tests for the NJ Courts filings crawler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import crawl_courts as c  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "fixtures" / "court_page.html").read_text()


def test_parse_county():
    rows = c.parse_county(PAGE, "Monmouth")
    towns = {r["court_name"] for r in rows}
    assert {"Allenhurst Boro", "Allentown Boro", "Asbury Park City", "Colts Neck Twp"} <= towns
    allen = [r for r in rows if r["court_name"] == "Allenhurst Boro"]
    assert [r["doc_type"] for r in allen] == ["Municipal Resolution", "HOUSING ELEMENT AND FAIR SHARE PLAN", "Order/Certificate Of Compliance"]
    assert allen[2]["posted"] == "2026-04-15" and allen[0]["posted"] == "2025-01-15"
    assert allen[1]["file_name"].endswith(".pdf") and "VersionId" in allen[1]["url"]


def test_town_status_rules():
    rows = c.parse_county(PAGE, "Monmouth")
    st = lambda n: c.town_status([r for r in rows if r["court_name"] == n])
    assert st("Allenhurst Boro")["court_status"] == "2025 plan, compliant"
    assert st("Allenhurst Boro")["compliance_posted"] == "2026-04-15"
    assert st("Colts Neck Twp")["court_status"] == "2025 plan, no compliance order yet"
    amended = [{"doc_type": "HOUSING ELEMENT AND FAIR SHARE PLAN", "posted": "2025-06-20", "file_name": "a.pdf", "url": "u1"},
               {"doc_type": "HOUSING ELEMENT AND FAIR SHARE PLAN", "posted": "2026-03-13", "file_name": "b.pdf", "url": "u2"}]
    s = c.town_status(amended)
    assert s["court_status"] == "2026 / amended plan" and s["plan_url"] == "u2" and s["base_plan_url"] == "u1"
    assert c.town_status([])["court_status"] == "No plan on NJ Courts"
    assert c.parse_date(" Sept. 30, 2026") == "2026-09-30"
