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
export ALI_DELAY="${ALI_DELAY:-25}"              # base spacing between AliExpress lookups (jitter added)
export ALI_MAX_CANDIDATES="${ALI_MAX_CANDIDATES:-8}"  # single-IP safe cap (raise cautiously)
export ALI_SELLER="${ALI_SELLER:-1}"             # enrich matches with AliExpress store data (feedback/rating/age + >=6mo filter)
export ALI_SELLER_MAX="${ALI_SELLER_MAX:-3}"     # cap seller lookups (each is +1 request on the single-IP budget)

echo "[$(date -u +%FT%TZ)] daily finder run start"

# 1. eBay scrape (residential IP). Dropship-leaning niches so Tab 2 has AliExpress hits.
Q="massage gun||fascia gun||robot vacuum||dash cam||smart watch||portable blender||neck massager||car phone holder||led strip lights||security camera||electric shaver||hair clipper||mini projector||bluetooth earbuds"
"$PY" -m scrapy crawl products -a queries="$Q" -a max_pages=2 \
  -O out/pool.json -s CLOSESPIDER_ITEMCOUNT=120 -s CLOSESPIDER_TIMEOUT=1000 || echo "scrape had errors"

# 2. 5 filters -> final.json ; split winners (Tab1) / dropshippable candidates (Tab2)
"$PY" curate_finder.py || exit 1
"$PY" classify_candidates.py || exit 1

# 3. AliExpress match by self-scraping on THIS Mac's residential IP (nodriver).
#    Free: no API, no token, no paid tool. Forces en_US/USD, paces itself, and a
#    circuit-breaker aborts on throttle. (ali_apify.py is kept as a fallback.)
"$PY" ali_match_local.py || echo "aliexpress step had errors - Tab 2 keeps prior data"

# 4. write BOTH tabs straight to the Google Sheet (private, no formulas/gists)
"$PY" sheet_write.py

echo "[$(date -u +%FT%TZ)] daily finder run done"
