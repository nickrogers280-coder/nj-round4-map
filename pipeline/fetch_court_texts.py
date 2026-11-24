"""Download each town's operative plan (and base plan and compliance order) from NJ Courts
and save the text, so the site extractor can read plans without paging PDFs.

Input:  data/court_status.csv (from crawl_courts.py)
Output: <out>/texts/<county>/<mun_code>-<town>/<file>.txt and <out>/index.csv, where <out> is a
        checkout of the `court-texts` branch. Scanned PDFs are OCR'd (ocrmypdf) while time allows.

Runs in GitHub Actions (court-filings.yml). Needs poppler-utils, ocrmypdf.
usage: python pipeline/fetch_court_texts.py --out DIR [--minutes 45] [--ocr-max 12]
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "data" / "court_status.csv"
LOG = ROOT / "data" / "extraction_log.csv"
INDEX_FIELDS = ["mun_code", "county", "municipality", "role", "posted", "file_name", "url", "text_path",
                "pages", "chars", "method", "fetched"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 nj-round4-map"
ROLES = [("plan", "plan_url", "plan_posted"), ("base", "base_plan_url", "base_plan_posted"),
         ("compliance", "compliance_url", "compliance_posted")]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def pages_of(pdf: Path) -> int:
    try:
        m = re.search(r"Pages:\s+(\d+)", run(["pdfinfo", str(pdf)], 60).stdout)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def to_text(pdf: Path) -> str:
    try:
        return run(["pdftotext", "-layout", str(pdf), "-"], 300).stdout
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--ocr-max", type=int, default=12)
    a = ap.parse_args()
    out = Path(a.out)
    idx_path = out / "index.csv"
    index = {}
    if idx_path.exists():
        for r in csv.DictReader(open(idx_path, newline="", encoding="utf-8")):
            index[(r["mun_code"], r["role"])] = r
    extracted = set()
    if LOG.exists():
        extracted = {(r["county"], r["municipality"]) for r in csv.DictReader(open(LOG, newline="", encoding="utf-8"))}

    todo = []
    for t in csv.DictReader(open(STATUS, newline="", encoding="utf-8")):
        for role, ukey, pkey in ROLES:
            url = t.get(ukey) or ""
            if not url:
                continue
            old = index.get((t["mun_code"], role))
            needs_ocr = old and old["method"] == "needs_ocr"
            if old and old["url"] == url and not needs_ocr and old["method"] != "error":
                continue
            # towns not yet on the map first, then plans before compliance orders
            todo.append(((t["county"], t["municipality"]) in extracted, role != "plan", bool(needs_ocr),
                         t, role, url, t.get(pkey, "")))
    todo.sort(key=lambda x: x[:3])
    print(f"{len(todo)} documents to fetch")

    deadline = time.time() + a.minutes * 60
    ocr_left = a.ocr_max
    done = 0
    for *_, t, role, url, posted in todo:
        if time.time() > deadline:
            break
        fname = url.split("?")[0].rsplit("/", 1)[-1]
        rel = Path("texts") / slug(t["county"]) / f'{t["mun_code"]}-{slug(t["municipality"])}' / f"{role}-{Path(fname).stem}.txt"
        row = {"mun_code": t["mun_code"], "county": t["county"], "municipality": t["municipality"], "role": role,
               "posted": posted, "file_name": fname, "url": url, "text_path": rel.as_posix(),
               "fetched": time.strftime("%Y-%m-%d")}
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "doc.pdf"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as r:
                    pdf.write_bytes(r.read())
            except Exception as e:
                row.update(method="error", pages=0, chars=0)
                index[(t["mun_code"], role)] = row
                print("error", fname, e)
                continue
            pages = pages_of(pdf)
            text = to_text(pdf)
            method = "text"
            if len(text.strip()) < max(500, 150 * max(pages, 1)):  # scanned / image PDF
                if ocr_left > 0 and time.time() + 60 < deadline:
                    ocr_left -= 1
                    ocrd = Path(td) / "ocr.pdf"
                    try:
                        run(["ocrmypdf", "--skip-text", "--jobs", "4", "-l", "eng", "--output-type", "pdf",
                             "--optimize", "0", str(pdf), str(ocrd)], int(max(120, deadline - time.time())))
                        text = to_text(ocrd) if ocrd.exists() else text
                        method = "ocr" if len(text.strip()) > 500 else "needs_ocr"
                    except Exception as e:
                        print("ocr failed", fname, e)
                        method = "needs_ocr"
                else:
                    method = "needs_ocr"
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            row.update(method=method, pages=pages, chars=len(text))
            index[(t["mun_code"], role)] = row
            done += 1
            print(f"{method:9} {pages:4}p {len(text):8}c  {t['municipality']} / {role} / {fname}")
    rows = sorted(index.values(), key=lambda r: (r["county"], r["municipality"], r["role"]))
    with open(idx_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    left = len(todo) - done
    print(f"fetched {done}; {left} left for the next run")


if __name__ == "__main__":
    main()
