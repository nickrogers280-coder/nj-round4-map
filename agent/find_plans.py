#!/usr/bin/env python3
"""Plan finder: locate each town's latest ADOPTED Round 4 Housing Element &
Fair Share Plan (and the 2025 base plan when the adopted document is only an
amendment), using Claude with web search and web fetch.

For every town picked from data/municipalities.csv it writes:
  data/plan_sources.csv   one row per document found (merged across runs)
  data/plan_status.csv    one row per town: what was found and what's missing
  agent/runs/<date>/<county>_<town>.json   the full structured result

Examples
  python agent/find_plans.py --county Bergen --limit 5
  python agent/find_plans.py --town "Fort Lee" --town Hackensack
  python agent/find_plans.py --status Missing --status "Need base plan" --limit 20
  python agent/find_plans.py --town Waldwick --dry-run      # print the prompt only

Needs ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUNIS_CSV = ROOT / "data" / "municipalities.csv"
SOURCES_CSV = ROOT / "data" / "plan_sources.csv"
STATUS_CSV = ROOT / "data" / "plan_status.csv"
RUNS_DIR = ROOT / "agent" / "runs"

DEFAULT_MODEL = "claude-sonnet-5-5"
DONE_STATUSES = {"Ready", "Ready (2 docs)"}

SYSTEM_PROMPT = """\
You are a research agent for a New Jersey real estate broker who maps every
site listed in municipal Fourth Round (2025-2035) affordable housing plans.
For ONE municipality at a time, you find the documents that make up its
operative plan, then report them by calling the record_plan_sources tool.

Background
- Every NJ municipality adopted a Fourth Round Housing Element & Fair Share
  Plan (HEFSP) around June 2025 and filed it with the Affordable Housing
  Dispute Resolution Program. Most were challenged (often by Fair Share
  Housing Center), mediated, and then AMENDED and re-adopted, mostly between
  December 2025 and mid 2026. Some towns amended again later, after court
  review or a fairness hearing.
- The operative plan is the newest ADOPTED version. A 2026 document is often
  only an amendment that changes parts of the 2025 plan. In that case BOTH
  documents are needed: the 2025 base plan and the adopted amendment(s).
- A draft, or a copy whose adoption line is blank, is not adopted. Report it,
  but keep looking for the adopted version and never mark a draft as the
  latest adopted document.

Where to look, in this order
1. The municipality's own official website. Plans usually sit under pages
   such as Affordable Housing, Fourth Round, Planning Board, Land Use Board,
   Zoning Board, Master Plan, Documents or the Agenda Center. Search for the
   town's name with phrases like "housing element and fair share plan",
   "4th round", "fourth round", "amended housing element", "affordable
   housing plan amendment".
2. Planning Board and governing body agendas, minutes and resolutions, which
   confirm adoption dates (e.g. "resolution adopting amended housing element",
   "resolution endorsing amended HEFSP").
3. The NJ Courts housing document library (njcourts.gov). It is often out of
   date, so use it to cross-check or as a fallback, not as proof that nothing
   newer exists.
4. County planning sites or the town planner's site only if the town links to them.

How to judge a document
- Read the cover page or the adopting resolution for: document type (full
  plan, amendment, resolution, court order, draft), the adoption date and the
  adopting body (Planning Board / Land Use Board), and any endorsement date.
- Only report dates you actually saw. If a cover shows a blank adoption line,
  say so. Never guess or infer a date.
- Large PDFs may be too big to fetch whole; filter for the first pages, or
  rely on the listing page text and the file name, and say which you used.
- Quote at most a short phrase as evidence for each date.
- Prefer direct PDF links over pages that merely mention the plan.

Be efficient: stop searching once you have the latest adopted document (and
the base plan if it is only an amendment), or once further searching is
clearly not turning anything up. Finish by calling record_plan_sources
exactly once, even if you found nothing.
"""

RECORD_TOOL = {
    "name": "record_plan_sources",
    "description": "Record what was found for this municipality. Call exactly once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "result": {
                "type": "string",
                "enum": [
                    "adopted_full_plan",           # latest adopted doc is a complete plan
                    "adopted_amendment_with_base",  # adopted amendment + 2025 base plan both found
                    "adopted_amendment_no_base",    # adopted amendment found, base plan not found
                    "only_2025_plan",               # nothing newer than the 2025 plan found
                    "draft_only",                   # newest doc found is a draft / unadopted copy
                    "not_found",
                ],
            },
            "latest_adopted_date": {"type": ["string", "null"], "description": "YYYY-MM-DD of the newest adopted document, or null"},
            "documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "doc_type": {"type": "string", "enum": ["full_plan", "amendment", "resolution", "court_order", "draft", "agenda_or_minutes", "other"]},
                        "role": {"type": "string", "enum": ["latest_adopted", "base_plan", "superseded", "draft", "supporting"]},
                        "adopted_date": {"type": ["string", "null"], "description": "YYYY-MM-DD if seen, else null"},
                        "adopting_body": {"type": ["string", "null"]},
                        "source_kind": {"type": "string", "enum": ["municipal_site", "njcourts", "county_or_planner", "other"]},
                        "evidence": {"type": "string", "description": "Where the date/type was seen, with a short quote"},
                    },
                    "required": ["title", "url", "doc_type", "role", "adopted_date", "source_kind", "evidence"],
                },
            },
            "plan_page_url": {"type": ["string", "null"], "description": "The town's affordable housing / fourth round page, if found"},
            "notes": {"type": "string", "description": "Anything the broker should know: blank adoption lines, pending hearings, conflicts between sources"},
        },
        "required": ["result", "latest_adopted_date", "documents", "notes"],
    },
}


def server_tools(basic: bool) -> list[dict]:
    if basic:  # no dynamic filtering; works on every model
        return [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 15},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10, "max_content_tokens": 40000},
        ]
    return [
        {"type": "web_search_20260318", "name": "web_search", "max_uses": 15},
        {"type": "web_fetch_20260318", "name": "web_fetch", "max_uses": 10, "max_content_tokens": 40000},
    ]


# --------------------------------------------------------------------------- #
# Town selection
# --------------------------------------------------------------------------- #
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pick_towns(munis: list[dict], counties, towns, statuses, skip_days: int | None, limit: int | None) -> list[dict]:
    picked = munis
    if counties:
        cs = {c.lower() for c in counties}
        picked = [m for m in picked if m["county"].lower() in cs]
    if towns:
        ts = {norm(t) for t in towns}
        picked = [m for m in picked if norm(m["municipality"]) in ts]
    elif statuses:
        ss = {s.lower() for s in statuses}
        picked = [m for m in picked if m["tracker_status"].lower() in ss]
    else:
        picked = [m for m in picked if m["tracker_status"] not in DONE_STATUSES]
    if skip_days is not None:
        cutoff = dt.date.today() - dt.timedelta(days=skip_days)
        recent = {(r["county"], r["municipality"]) for r in read_csv(STATUS_CSV)
                  if r.get("checked_on") and dt.date.fromisoformat(r["checked_on"]) >= cutoff}
        picked = [m for m in picked if (m["county"], m["municipality"]) not in recent]
    return picked[:limit] if limit else picked


def user_prompt(m: dict) -> str:
    known = []
    if m.get("tracker_status"):
        known.append(f"Tracker status: {m['tracker_status']}")
    if m.get("latest_adoption"):
        known.append(f"Latest adoption seen so far: {m['latest_adoption']}")
    if m.get("in_folder"):
        known.append(f"Document already on file: {m['in_folder']}")
    if m.get("next_step"):
        known.append(f"What is still needed: {m['next_step']}")
    return (
        f"Municipality: {m['municipality']}, {m['county']} County, New Jersey.\n"
        f"Today's date: {dt.date.today().isoformat()}.\n"
        + ("\n".join(known) + "\n" if known else "")
        + "\nFind the documents that make up this town's operative Fourth Round "
          "Housing Element & Fair Share Plan, then call record_plan_sources."
    )


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
def run_town(client, model: str, m: dict, basic_tools: bool, max_rounds: int = 12) -> tuple[dict | None, dict]:
    tools = server_tools(basic_tools) + [RECORD_TOOL]
    messages = [{"role": "user", "content": user_prompt(m)}]
    usage = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0, "web_fetch_requests": 0}
    nudged = False
    tool_choice = {"type": "auto"}

    for _ in range(max_rounds):
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        u = resp.usage
        usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            usage["web_search_requests"] += getattr(stu, "web_search_requests", 0) or 0
            usage["web_fetch_requests"] += getattr(stu, "web_fetch_requests", 0) or 0

        for block in resp.content:
            if block.type == "tool_use" and block.name == "record_plan_sources":
                return dict(block.input), usage

        if resp.stop_reason == "pause_turn":
            # Long server-side search turn: send it back unchanged to continue.
            messages.append({"role": "assistant", "content": resp.content})
            continue

        if not nudged:
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": "Stop searching and call record_plan_sources now with what you found."})
            tool_choice = {"type": "tool", "name": "record_plan_sources"}
            nudged = True
            continue
        break
    return None, usage


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
SOURCE_COLS = ["county", "municipality", "checked_on", "role", "doc_type", "adopted_date", "adopting_body",
               "title", "url", "source_kind", "evidence"]
STATUS_COLS = ["county", "municipality", "checked_on", "result", "latest_adopted_date", "latest_adopted_url",
               "base_plan_url", "plan_page_url", "notes"]


def merge_write(path: Path, cols: list[str], new_rows: list[dict], done_keys: set[tuple[str, str]]):
    old = [r for r in read_csv(path) if (r["county"], r["municipality"]) not in done_keys]
    rows = old + new_rows
    rows.sort(key=lambda r: (r["county"], r["municipality"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def rows_for(m: dict, rec: dict, today: str) -> tuple[list[dict], dict]:
    docs = rec.get("documents") or []
    src = [{"county": m["county"], "municipality": m["municipality"], "checked_on": today,
            **{k: (d.get(k) or "") for k in SOURCE_COLS[3:]}} for d in docs]
    latest = next((d for d in docs if d.get("role") == "latest_adopted"), None)
    base = next((d for d in docs if d.get("role") == "base_plan"), None)
    status = {
        "county": m["county"], "municipality": m["municipality"], "checked_on": today,
        "result": rec.get("result", ""), "latest_adopted_date": rec.get("latest_adopted_date") or "",
        "latest_adopted_url": latest["url"] if latest else "", "base_plan_url": base["url"] if base else "",
        "plan_page_url": rec.get("plan_page_url") or "", "notes": rec.get("notes", ""),
    }
    return src, status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--county", action="append", help="Limit to a county (repeatable)")
    ap.add_argument("--town", action="append", help="Specific town(s); overrides --status")
    ap.add_argument("--status", action="append", help="Tracker status(es) to include; default = every status except Ready")
    ap.add_argument("--limit", type=int, default=5, help="Max towns this run (default 5; 0 = no limit)")
    ap.add_argument("--skip-checked-within", type=int, default=None, metavar="DAYS",
                    help="Skip towns already checked in the last N days")
    ap.add_argument("--model", default=os.environ.get("PLAN_AGENT_MODEL", DEFAULT_MODEL))
    ap.add_argument("--basic-tools", action="store_true", help="Use web search/fetch without dynamic filtering")
    ap.add_argument("--dry-run", action="store_true", help="Print the selected towns and prompt; call nothing")
    args = ap.parse_args(argv)

    munis = read_csv(MUNIS_CSV)
    towns = pick_towns(munis, args.county, args.town, args.status, args.skip_checked_within, args.limit or None)
    print(f"{len(towns)} town(s) selected: " + ", ".join(f"{t['municipality']} ({t['county']})" for t in towns))
    if not towns:
        return 0
    if args.dry_run:
        print("\n--- system prompt ---\n" + SYSTEM_PROMPT)
        print("--- first user prompt ---\n" + user_prompt(towns[0]))
        print("--- tools ---\n" + json.dumps([t.get("type", t.get("name")) for t in server_tools(args.basic_tools)] + ["record_plan_sources"]))
        return 0

    import anthropic  # imported here so --dry-run works without the package
    client = anthropic.Anthropic()
    today = dt.date.today().isoformat()
    run_dir = RUNS_DIR / today
    run_dir.mkdir(parents=True, exist_ok=True)

    all_src, all_status, done = [], [], set()
    totals = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0, "web_fetch_requests": 0}
    for i, m in enumerate(towns, 1):
        label = f"{m['municipality']} ({m['county']})"
        print(f"\n[{i}/{len(towns)}] {label}")
        t0 = time.time()
        try:
            rec, usage = run_town(client, args.model, m, args.basic_tools)
        except anthropic.APIError as exc:
            print(f"  API error: {exc}")
            continue
        for k in totals:
            totals[k] += usage.get(k, 0)
        if rec is None:
            print("  no result recorded")
            rec = {"result": "not_found", "latest_adopted_date": None, "documents": [],
                   "notes": "Agent did not record a result."}
        src, status = rows_for(m, rec, today)
        all_src += src
        all_status.append(status)
        done.add((m["county"], m["municipality"]))
        slug = re.sub(r"[^a-z0-9]+", "-", f"{m['county']}_{m['municipality']}".lower()).strip("-")
        (run_dir / f"{slug}.json").write_text(json.dumps({"town": m, "result": rec, "usage": usage, "model": args.model}, indent=2))
        print(f"  {rec['result']}  latest adopted: {rec.get('latest_adopted_date') or 'n/a'}  "
              f"docs: {len(rec.get('documents') or [])}  ({time.time() - t0:.0f}s, "
              f"{usage['web_search_requests']} searches, {usage['web_fetch_requests']} fetches)")

    merge_write(SOURCES_CSV, SOURCE_COLS, all_src, done)
    merge_write(STATUS_CSV, STATUS_COLS, all_status, done)
    print(f"\nDone: {len(done)} town(s). Tokens in/out: {totals['input_tokens']:,}/{totals['output_tokens']:,}; "
          f"searches: {totals['web_search_requests']}; fetches: {totals['web_fetch_requests']}")
    print(f"Wrote {SOURCES_CSV.relative_to(ROOT)} and {STATUS_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
