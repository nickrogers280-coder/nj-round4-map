# Site extraction guide

How to turn one town's Round 4 Housing Element & Fair Share Plan (HEFSP) into rows in
`data/sites.csv` and `data/zones.csv`. The scheduled "site extractor" follows this file;
a person adding rows by hand should too.

## 1. Which documents

Use the town's operative plan:

- the newest **adopted** amended plan (a plan dated 2026, or titled "Amended", is usually it), and
- the **2025 base plan** as well when the amendment only changes parts of it ("the remaining sections
  of the 2025 plan remain in effect").

A final 2026 copy whose only gap is a blank signature or adoption line (or "anticipated adoption"
date) counts as the operative plan: the content does not change at signing (Nick, 2026-11-23).
Extract it fully, take the adoption date from the Plan finder or a resolution if one is known, and
do not mark the town `partial` for that reason alone. A document still marked DRAFT or clearly
pre-settlement is different: prefer a later version if one exists, and say in `notes` which you used.

Where to find them, in order:

1. Nick's newest Drive folder **"HEFSP 2026 Project"** (`1JtzRft5m9lTz2l_i4ibE1uGRpUO_CoL8`,
   county folders under its `hefsp` subfolder `18G6iDpVJMxssM-fO6rkp4KA-PSm0WNPz`). Check it first;
   when a town has a file here and in an older folder, prefer the newer/amended one per section 1.
   County folders: Bergen `1JqSEFIjpfBXbbpUXEjJ9tm_jOyPbSJVk`, Monmouth `1bEwAT1NRP9HC6Qq50SZ4LrT8SzN-9HYo`,
   Morris `14CpOJqsttaB3H5yJySdjFiuPAThKALjO`, Middlesex `1tnVRczaBd7n4O43AWp1OtELSwbFQHEJ3`
   (OPRA subfolder `106s3d-M13mmWct9JtwlP3uAOOhS4OsYt`), Essex `14dGI4IDBAzcogVz93wBIgoy2RvRJMKfp`,
   Somerset `1vJne5IgjBm_jTEQyW6ndj5FyyUe8Tna7`, Hunterdon `1cMjtBGYYXTVbXqdQeQJXvpkqiPXnGLPC`,
   Warren `1_qt_ZzaUXAt_64jWH4SQZjS-7FlAfF7A`, Ocean `1Ko3FD_rGKfV5WHRscM1zDQX_IFshVhF2`,
   Mercer `1CxN4zeqItYyesahykwg3XhBK7TAaX18Z`, Passaic `1GcxE2TFUOmik1fYMrLld8d7O_s3_tHUt`,
   Sussex `1eRxbGnVWsB50H4DbuBq8p14uDlxnjpPD`, Burlington `17nx1lUH-riROLblEKj7Klwf-125BVHBa`.
   Search all of it with `'1JtzRft5m9lTz2l_i4ibE1uGRpUO_CoL8' in ancestors`.
2. Nick's original Drive county folders (parent folder id `1x_xQ-FWCRvreZa9dVEE-pff3A4OXxeNs`;
   Bergen `19gV3828eIb1U5FikZPbNdUig-Wfn6VMu`, Monmouth `1BF-4-ADWLJPIxm6-T7mIZBZqCdhI8_qM`,
   Morris `1YgqGGp2efgeeNL3wQedIKmlO5SReiIk8`).
3. The links in the **Plan finder** tab of the Round 4 HEFSP Tracker sheet
   (https://claude.ai/artifact/2rNF11HemfXXkdcA5tUHtz): "Latest adopted plan" and "Base plan (2025)".

## 2. Reading a long plan

`read_file_content` on a Drive PDF returns the text; when it is too long it is saved to a file.
Load that file in Python (`json.loads(...)["fileContent"]` if it is JSON) and search it rather than
reading it front to back. Useful searches: `Block`, `Lot`, `overlay`, `inclusionary`, `redevelopment`,
`Fourth Round`, `Third Round`, `Prior Round`, `credit`, `group home`, `100%`, `units per acre`,
`du/ac`, `set-aside`, `height`, `setback`, `coverage`, `Appendix`, `Ordinance`.

The sections that matter are usually:

- the **Fair Share Plan** chapter (compliance mechanisms for the Fourth Round, remaining Third Round
  obligation, and the list of prior-round credits),
- tables of sites or credits (often "Table: Fourth Round Compliance Mechanisms", "Prior Round credits",
  "Third Round sites"),
- the **zoning ordinance appendices** (overlay zone text with permitted uses and bulk standards).

Drive's text can stop partway through a very long PDF, and scanned PDFs have no text at all.
Record either case in the extraction log (`text cut off` / `scanned, needs OCR`) and extract what you can.

**Missing zoning ordinances.** When a Round 4 zone's ordinance is not in the plan (not appended, cut off,
or scanned), look it up before leaving the bulk standards blank: search the town's eCode page
(`ecode360.com`, often `ecode360.com/<CODE>/laws` for recently adopted local laws), the town website's
ordinances / resolutions / agendas, and the court-library filing. Use the adopted (or introduced) ordinance
for the zone row's standards and blocks/lots, cite it in `ordinance_status` and `plan_doc`, and say in
the log which standards came from an ordinance rather than the plan. Developers care most about land
with new zoning that is not built yet, so these zones come first.

## 3. What to record

### zones.csv: one row per zone the plan creates or relies on

Overlay zones, inclusionary zones, redevelopment plans, and prior-round zones still in force.

| Column | What to put |
|---|---|
| `zone_id` | `<town-slug>-<zone-slug>`, lowercase with dashes, e.g. `waldwick-aho2`, `alpine-r3-tower` |
| `zone_code`, `zone_name` | As the plan writes them, e.g. `AHO-2`, `Affordable Housing Overlay Zone-2` |
| `round` | `4`, `3`, or `prior` (Rounds 1-2) |
| `mechanism` | One of: Overlay zone, Inclusionary zoning, Redevelopment, 100% affordable, Municipally sponsored, Supportive housing, Existing / credit, Other |
| `applies_to` | Where it applies, in the plan's words (districts, blocks, a described area) |
| `permitted_uses` | Uses allowed, briefly |
| `max_density_du_ac`, `max_units` | Numbers only. If an ordinance shows a change (old struck, new underlined), use the new value and note the change in `other_standards` |
| `set_aside` | e.g. `20% for-sale / 15% rental` |
| `min_lot_area`, `min_frontage`, `max_height`, `max_building_coverage`, `max_impervious_coverage`, `setbacks`, `parking` | As written, with units |
| `other_standards` | Anything else that matters to a developer (affordable bonus, phasing, age-restriction, unit caps per lot) |
| `ordinance_status` | e.g. `Adopted Ord. 2026-04`, `Draft ordinance in Appendix B` |
| `plan_doc`, `plan_page` | Which document and page the standards came from |

### sites.csv: one row per site

| Column | What to put |
|---|---|
| `site_id` | `<town-slug>-<site-slug>`; prior-round sites start `<town>-r3-` or `<town>-prior-` |
| `round` | `4` for Fourth Round mechanisms; `3` or `prior` for earlier rounds |
| `site_name` | The plan's name for the site, e.g. "Harrison Avenue Redevelopment" |
| `mechanism` | Same list as above |
| `zone_id` | The zone row this site sits in, if any |
| `blocks_lots` | `block:lot,lot;block:lot`, e.g. `107:1,3,5;109.02:6.04`. Qualifier after a slash: `12:4/C0002`. If the plan gives only an address, `addr:` plus a distinctive street fragment, e.g. `addr:CLOSTER DOCK` |
| `address`, `acres` | As the plan gives them |
| `total_units`, `affordable_units`, `max_density_du_ac` | Numbers only; for prior-round credits, `affordable_units` = credits or units. Always fill `total_units` (total homes allowed, market + affordable) when the plan or ordinance gives it, since developers look at it first. If only density and acreage are given, use density x acres rounded down and say "estimate" in `notes`. Credit tables often say "(2 of 15 units)": that means 2 of the project's 15 affordable units are credited to this round, **not** that the building has 15 units. Put the 15 in `affordable_units`, the credit split in `notes`, and leave `total_units` blank unless the plan states the building's total |
| `set_aside`, `zoning`, `site_status` | e.g. `Built 2019`, `Approved, not built`, `Overlay adopted`, `Proposed` |
| `existing_conditions` | **Required for prior-round sites**: what is there now, from the plan's description, e.g. "48-unit 100% affordable senior building, completed 2012", "Vacant wooded lot", "Group home with 4 bedrooms". For Round 4 sites, the current use if the plan says (e.g. "Driving range of the Montammy Golf Club") |
| `plan_doc`, `plan_url`, `plan_page` | Document name with adoption date, a link if there is one, and the page |
| `notes` | Short; caveats like "overlay covers only part of the lot" |

Rules:

- Use only what the documents say. Never invent block/lot numbers, unit counts or standards.
- One row per distinct site. A site made of several lots is one row with all its lots in `blocks_lots`.
- Rehabilitation programs, accessory apartment programs, market-to-affordable programs and
  development-fee ordinances are not sites. Skip them unless the plan ties them to specific lots.
- A town-wide mandatory set-aside ordinance is not a site either; it can be a zone row with
  `applies_to` = "Town-wide" if the plan gives standards.

## 4. Replacing a town

When a town is (re-)extracted, **delete every existing row for that town** in both CSVs and write the
new set. If a newer amended plan drops a site, it must disappear from the map. Mention what changed
from the previous extraction in the log's `notes`.

## 5. Log and checks

Add or replace the town's row in `data/extraction_log.csv`:
`county,municipality,extracted_on,plan_docs,plan_adopted,sites,zones,status,notes`
(`status`: `done`, `partial` (text cut off or some sites without lots), `needs OCR`, `no plan`).

Before committing, run:

```
python pipeline/validate_data.py
python -m pytest -q tests
```

Both must pass. Pushing to `main` starts the **Map sites** workflow, which matches lots and
updates the live map within a few minutes.
