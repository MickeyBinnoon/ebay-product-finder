# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A free, self-hosted daily automation that scrapes eBay for low-competition,
fast-selling products, matches the dropshippable ones to the same product on
AliExpress, and writes both sets into a Google Sheet. No paid scraping APIs and no
proxies: the AliExpress step self-scrapes on the Mac's residential IP. See `README.md`
for the product rationale and the five hard filters.

## Commands

```bash
# Environment (Python 3.11 required; Scrapy is pinned and needs 3.11)
python3.11 -m venv ~/.finder-venv
~/.finder-venv/bin/pip install -r requirements.txt

# Run the full daily pipeline (scrape -> filter -> classify -> AliExpress -> Sheet)
./run_local.sh

# Run just the eBay scrape (queries are `||`-separated; writes out/pool.json)
scrapy crawl products -a queries="massage gun||dash cam" -a max_pages=2 \
  -O out/pool.json -s CLOSESPIDER_ITEMCOUNT=120 -s CLOSESPIDER_TIMEOUT=1000

# Run individual pipeline stages (each reads/writes JSON in the repo root)
python curate_finder.py        # out/*.json -> final.json
python classify_candidates.py  # final.json -> winners.json + candidates.json
python ali_match_local.py      # candidates.json -> ali_enriched.json (self-scrape; residential IP)
python sheet_write.py          # winners.json + ali_enriched.json -> Google Sheet
```

There is no test suite, linter, or build step — this is a small script pipeline.
Verify changes by running the relevant stage and inspecting its JSON output.

## Required environment / credentials

- `GOOGLE_APPLICATION_CREDENTIALS` — path to a Google service-account JSON key (default
  `~/.config/gws/sa.json`). The target sheet must be shared with the SA's
  `client_email` as Editor. Sheets API must be enabled.
- `SHEET_ID` — target Google Sheet (a default is hardcoded in `run_local.sh` and
  `sheet_write.py`).

## Architecture

The pipeline is a chain of standalone scripts communicating through **JSON files in the
repo root** (all gitignored). Each stage reads the previous stage's output file:

```
scrapy crawl products  ->  out/*.json      (raw scraped listings, per-listing hard filters applied in-spider)
curate_finder.py       ->  final.json      (the 5 filters; best row per distinct product)
classify_candidates.py ->  winners.json    (Tab 1: all passers)
                           candidates.json (Tab 2 seed: dropshippable subset only)
ali_match_local.py     ->  ali_enriched.json (candidates + their AliExpress match)
sheet_write.py         ->  Google Sheet (two tabs, rewritten each run)
```

### The residential-IP constraint (why the pipeline is split)

eBay serves datacenter IPs fine, so the scrape can run in the cloud
(`.github/workflows/finder.yml`, an optional artifact-only backup). **AliExpress
blocks datacenter IPs outright** — only a residential IP clears its anti-bot, which is
why the match step (`ali_match_local.py`) runs on the Mac.

`ali_match_local.py` self-scrapes AliExpress by driving a real browser via `nodriver`
on the Mac's residential IP — **free, no API, no token, no proxy** (this is the only
AliExpress matcher; there is no paid-service path). Key behaviour to preserve:

- Forces en_US/USD via the site's own currency cookie (`aep_usuc_f`, injected before
  the single search navigation with **no warm-up load** — an extra page load was
  observed to help trip the bot gate), so titles come back in English (they token-match
  the English eBay titles) and prices are real USD.
- Throttle handling: paces itself (`ALI_DELAY` + jitter), caps lookups
  (`ALI_MAX_CANDIDATES`, default 8), detects captcha/"punish" pages (0 cards + a block
  marker in the `<title>`; raw HTML is unreliable — a good page contains
  "verify"/"slider" in its scripts), backs off (0/30/60s), and a **circuit-breaker
  aborts the run** (writing partial results) after repeated blocks. `ALI_HEADLESS=0`
  drives a visible window; `ALI_FORCE_USD=0` skips the currency cookie.
- Two match tiers written to `ali_enriched.json` / the sheet Confidence column:
  **`match`** (title overlap ≥ 0.5 in a 0.15–1.3× price band) and **`likely`**
  (0.42–0.5 + a shared product noun, for human review). An `ACCESSORY_RE` drops
  eartips/cases/"for AirPods" listings that share the words but aren't the product.
- Optional seller enrichment (`ALI_SELLER=1`): a second pass visits each match's item
  page and pulls AliExpress's own store block (name, positive-feedback %, rating,
  age → sheet columns), and **hard-filters** a match whose store is confirmed < 6
  months old. Each visit is +1 request on the single-IP budget, so it's capped
  (`ALI_SELLER_MAX`) and best-effort. Preserve the `ali_enriched.json` shape that
  `sheet_write.py` reads.

### The eBay spider (`ebay_scraper/spiders/products.py`)

A single spider with a 5-callback chain, one product identified per candidate:

`start_requests` (prime cookies on ebay.com) → `parse_search` (cheap per-listing
filters: feedback ≤ 500, not China, price ≥ 0.7× min) → `parse_item` (authoritative
USD price + item location + Brand/MPN/Model) → `parse_count` (distinct sellers of the
*same product*) → `parse_velocity` (sales in trailing 30 days from the Sold/Completed
view, which date-stamps each sale).

Key design points to preserve when editing:
- **Product identity** is Brand + a real model-code token (a Model/MPN token with
  ≥2 consecutive digits, excluding unit/spec tokens like `1080p`, `ip67`) → "precise"
  matching. Falls back to Brand + title-token overlap ("approximate") when no code
  appears in the listing's own title. See `_identity_tokens`, `_product_query`,
  `_match`.
- **China exclusion** is on *item location* (ships-from), including masked Chinese
  cities (`CHINA_MARKERS`), since seller registration country isn't in the HTML.
- **Price** comes from `convertedFromValue`/`priceCurrency` JSON embedded in the item
  page; `FX_TO_USD` rates convert non-USD. The two-stage price check exists because
  search-card prices are approximate and the item page is authoritative.

### Filter thresholds live in two places

The spider applies *cheap per-listing* filters during crawl (feedback, China, a loose
price floor). `curate_finder.py` applies the *authoritative* five filters
(price ≥ $40, feedback ≤ 500, not China, sold_last_30d ≥ 5, 1 ≤ distinct_sellers < 5).
When changing a threshold, check both files.

## Gotchas

- **Scrapy is pinned to `2.13.4`.** 2.14+ removed `start_requests()`, which the spider
  relies on — without it the spider makes zero requests. Do not bump it.
- **`ROBOTSTXT_OBEY = False`** is deliberate (eBay disallows `/sch/`). The spider stays
  polite via `DOWNLOAD_DELAY`, low concurrency, and autothrottle in
  `ebay_scraper/settings.py`. This is a low-volume personal-research tool.
- **`sheet_write.py` never wipes a tab on empty data** — if a run produces no rows
  (blocked/failed scrape), it keeps the sheet's prior contents. Preserve this guard.
- The scrape depends on a **browser-like header fingerprint** (`USER_AGENT` +
  `DEFAULT_REQUEST_HEADERS`) that returned HTTP 200 in manual testing. Changing it can
  re-trigger eBay's bot gate. `Accept-Encoding` deliberately omits brotli (Scrapy can't
  decode it without extra deps).
- Hammering AliExpress from one IP (e.g. heavy testing) triggers throttling for
  hours. The daily paced run is fine; iterate carefully.

## Scheduling

`run_local.sh` is scheduled by launchd via `com.mickey.ebay-ali-finder.plist`
(replace `RUN_DIR` with the repo path before installing to `~/Library/LaunchAgents/`).
Logs go to `finder.log` / `finder.err`.
