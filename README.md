# NJ Round 4 sites map

An interactive parcel map of the sites listed in New Jersey municipalities' Fourth Round (2025–2035) Housing Element & Fair Share Plans, plus an agent that finds each town's latest adopted plan.

## How it fits together

```
plans (PDFs)  ──►  data/sites.csv  ──►  Map sites workflow  ──►  docs/data/sites.geojson  ──►  docs/index.html
                   one row per site       matches each block/lot      parcel shapes + plan        the map
                                          to NJ's statewide parcels   fields + tax fields

data/municipalities.csv  ──►  Find adopted plans workflow  ──►  data/plan_status.csv / data/plan_sources.csv
every tracked town             Claude agent with web search      what was found, with links and dates
```

| Path | What it is |
|---|---|
| `data/municipalities.csv` | Every tracked town and its status (mirrors the Round 4 HEFSP Tracker sheet) |
| `data/sites.csv` | The sites pulled from the plans. This is the file to add to. |
| `pipeline/geocode_sites.py` | Looks up each block/lot in the NJOGIS *Parcels and MOD-IV Composite* layer |
| `docs/index.html` | The map: filters by county, town, mechanism, status, units and acreage; CSV export |
| `docs/data/` | Generated map data, plus `unmatched.csv` for block/lots that need fixing |
| `agent/find_plans.py` | The plan-finder agent |
| `data/plan_status.csv`, `data/plan_sources.csv` | Agent output (created on first run) |

## Adding sites

Add one row per site to `data/sites.csv` and push. The **Map sites** workflow runs on its own and commits the updated map data.

| Column | Notes |
|---|---|
| `site_id` | Unique, lowercase, e.g. `waldwick-harrison-ave` |
| `county`, `municipality` | As in `municipalities.csv`. Give the type where two towns share a name (Freehold Borough / Freehold Twp). |
| `blocks_lots` | `block:lot,lot;block:lot`, e.g. `107:1,3,5;109.02:6.04`. A qualifier goes after a slash: `12:4/C0002`. |
| `mechanism` | One of: Overlay zone, Inclusionary zoning, Redevelopment, 100% affordable, Municipally sponsored, Supportive housing, Existing / credit, Other |
| `total_units`, `affordable_units`, `max_density_du_ac`, `acres` | Numbers only |
| `set_aside`, `zoning`, `site_status`, `notes` | Free text |
| `plan_doc`, `plan_page` | Which document and page the site came from, so every pin can be checked |

Overlay zones defined by a whole zoning district rather than specific lots (e.g. Waldwick's AHO-1, which covers the entire VC-3 zone) can't be matched by block/lot. Leave them out of `sites.csv` for now; they need a zoning layer.

Anything that doesn't match lands in `docs/data/unmatched.csv` with the reason (town not found, lot not on block, bad format).

## Running the plan finder

1. Add a repository secret named `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions).
2. Go to Actions → **Find adopted plans** → Run workflow. Pick a county, a batch size, and which tracker statuses to include.
3. Results are committed to `data/plan_status.csv` (one row per town) and `data/plan_sources.csv` (one row per document), with full detail in `agent/runs/<date>/`.

The agent follows the sourcing order that works best in practice: the town's own website first (Affordable Housing, Planning/Land Use Board, Master Plan pages), then board agendas and resolutions to confirm adoption dates, and the NJ Courts library only as a cross-check, since it's often out of date. It reports both the 2025 base plan and the adopted amendment when the newest document only amends the original, and it never marks a draft or a copy with a blank adoption line as adopted.

Local run: `pip install -r requirements.txt`, set `ANTHROPIC_API_KEY`, then for example `python agent/find_plans.py --county Bergen --status Missing --limit 5`. Add `--dry-run` to see which towns would be checked without calling the API.

Cost control: the default model is `claude-sonnet-5-5`, each town is capped at 15 searches and 10 page fetches, and `--skip-checked-within 14` avoids re-checking towns found recently.

## Viewing the map

The repository is private. To view the map, either:

- open `docs/index.html` from a local clone with a simple server (`python -m http.server -d docs`) so it can load `docs/data/`, or
- ask Claude to publish it as a private claude.ai artifact with the current data embedded.

GitHub Pages also works if the repository is made public or the account has a plan that supports Pages on private repositories.

Note: the statewide public parcel layer returns blank owner names, so the map's owner field stays empty. Owner data has to come from county or municipal tax records if you want it on the map.

## Tests

`python -m pytest -q tests` (runs offline; the parcel service and Claude API are faked).
