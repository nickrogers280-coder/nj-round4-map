"""Offline tests for the plan-finder agent loop (fake API client)."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import find_plans as fp  # noqa: E402


def _resp(content, stop):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=100, output_tokens=20, server_tool_use=NS(web_search_requests=2, web_fetch_requests=1)))


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


RECORD = {"result": "adopted_amendment_with_base", "latest_adopted_date": "2026-03-10",
          "documents": [
              {"title": "Amendment", "url": "https://example.gov/amend.pdf", "doc_type": "amendment",
               "role": "latest_adopted", "adopted_date": "2026-03-10", "source_kind": "municipal_site", "evidence": "Adopted March 10, 2026"},
              {"title": "2025 plan", "url": "https://example.gov/2025.pdf", "doc_type": "full_plan",
               "role": "base_plan", "adopted_date": "2025-06-23", "source_kind": "municipal_site", "evidence": "adopted June 23, 2025"}],
          "notes": ""}
TOWN = {"county": "Bergen", "municipality": "Waldwick", "tracker_status": "Need base plan",
        "latest_adoption": "", "in_folder": "", "next_step": ""}


def test_pause_turn_then_record():
    c = FakeClient([
        _resp([NS(type="text", text="searching")], "pause_turn"),
        _resp([NS(type="tool_use", name="record_plan_sources", input=RECORD)], "tool_use"),
    ])
    rec, usage = fp.run_town(c, "m", TOWN, basic_tools=False)
    assert rec["result"] == "adopted_amendment_with_base"
    assert usage["web_search_requests"] == 4
    assert c.calls[1]["messages"][-1]["role"] == "assistant"  # paused turn sent back


def test_nudge_forces_record():
    c = FakeClient([
        _resp([NS(type="text", text="I think...")], "end_turn"),
        _resp([NS(type="tool_use", name="record_plan_sources", input=RECORD)], "tool_use"),
    ])
    rec, _ = fp.run_town(c, "m", TOWN, basic_tools=True)
    assert rec is not None
    assert c.calls[1]["tool_choice"] == {"type": "tool", "name": "record_plan_sources"}
    assert c.calls[0]["tools"][0]["type"] == "web_search_20250305"


def test_rows_and_merge(tmp_path):
    src, status = fp.rows_for(TOWN, RECORD, "2026-11-19")
    assert status["latest_adopted_url"].endswith("amend.pdf")
    assert status["base_plan_url"].endswith("2025.pdf")
    p = tmp_path / "s.csv"
    fp.merge_write(p, fp.STATUS_COLS, [status], {("Bergen", "Waldwick")})
    fp.merge_write(p, fp.STATUS_COLS, [status], {("Bergen", "Waldwick")})  # re-run replaces, no dupes
    assert len(fp.read_csv(p)) == 1


def test_pick_towns_defaults_skip_ready():
    munis = fp.read_csv(fp.MUNIS_CSV)
    picked = fp.pick_towns(munis, ["Bergen"], None, None, None, None)
    assert all(m["tracker_status"] not in fp.DONE_STATUSES for m in picked)
    assert fp.pick_towns(munis, None, ["fort lee"], None, None, None)[0]["municipality"] == "Fort Lee"
