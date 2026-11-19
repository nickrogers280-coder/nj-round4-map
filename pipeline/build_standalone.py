#!/usr/bin/env python3
"""Build a single self-contained map page (dist/round4-map.html) with the
current site data inlined, for publishing as a private claude.ai artifact.

The artifact frame only allows scripts from a few CDNs and blocks all other
network requests, so: Leaflet's CSS is inlined, the data is embedded, and the
page runs in "standalone" mode (no tile basemap, copy-instead-of-download).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "dist" / "round4-map.html"


def main():
    html = (DOCS / "index.html").read_text()
    sites = (DOCS / "data" / "sites.geojson").read_text()
    towns = (DOCS / "data" / "municipalities.json").read_text()
    css = (DOCS / "vendor" / "leaflet-1.9.4.css").read_text()

    # The artifact host supplies <!doctype>/<html>/<head>/<body>; keep only their contents.
    head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
    head = re.sub(r'<meta charset[^>]*>\s*|<meta name="viewport"[^>]*>\s*', "", head)
    head = re.sub(r'<link rel="stylesheet" href="https://cdnjs[^"]*leaflet[^"]*">',
                  "<style>\n" + css + "\n</style>", head)

    body = body.replace('<script id="embedded-sites" type="application/json"></script>',
                        f'<script id="embedded-sites" type="application/json">{sites}</script>')
    body = body.replace('<script id="embedded-towns" type="application/json"></script>',
                        f'<script id="embedded-towns" type="application/json">{towns}</script>')
    marker = '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>'
    assert marker in body, "leaflet script tag not found"
    body = body.replace(marker, "<script>window.ROUND4_STANDALONE = true;</script>\n" + marker, 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(head.strip() + "\n" + body.strip() + "\n")
    n = len(json.loads(sites)["features"])
    print(f"Wrote {OUT.relative_to(ROOT)} with {n} parcels ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
