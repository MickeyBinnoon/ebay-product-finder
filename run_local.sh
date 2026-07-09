#!/usr/bin/env bash
# Self-contained daily job (runs on the Mac, residential IP). Scrapes eBay, applies
# the 5 filters, matches dropshippable candidates on AliExpress (nodriver), and
# writes BOTH tabs directly to the Google Sheet via the service account.
#
# 100% free / open-source (Scrapy + nodriver + Google Sheets API). Scheduled by launchd.
set -uo pipefail
cd "$(dirname "$0")"

PY="${FINDER_PY:-$HOME/.finder-venv/bin/python}"          # venv311: scrapy+nodriver+sheets
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gws/sa.json}"
export SHEET_ID="${SHEET_ID:-1zm5_swG9rt9R82x3wLBXgSZlGw9uuKDhsFWcMkcke08}"

echo "[$(date -u +%FT%TZ)] daily finder run start"

# 1. eBay scrape (residential IP). Dropship-leaning niches so Tab 2 has AliExpress hits.
Q="massage gun||fascia gun||robot vacuum||dash cam||smart watch||portable blender||neck massager||car phone holder||led strip lights||security camera||electric shaver||hair clipper||mini projector||bluetooth earbuds"
"$PY" -m scrapy crawl products -a queries="$Q" -a max_pages=2 \
  -O out/pool.json -s CLOSESPIDER_ITEMCOUNT=120 -s CLOSESPIDER_TIMEOUT=1000 || echo "scrape had errors"

# 2. 5 filters -> final.json ; split winners (Tab1) / dropshippable candidates (Tab2)
"$PY" curate_finder.py || exit 1
"$PY" classify_candidates.py || exit 1

# 3. AliExpress match (residential, paced). Best-effort: throttle keeps prior Tab 2.
"$PY" ali_match_local.py || echo "ali match had errors (throttle?) - Tab 2 may be sparse"

# 4. write BOTH tabs straight to the Google Sheet (private, no formulas/gists)
"$PY" sheet_write.py

echo "[$(date -u +%FT%TZ)] daily finder run done"
