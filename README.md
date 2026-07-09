# eBay Product Finder -> AliExpress Matcher

A free, self-hosted daily automation that finds **low-competition, fast-selling
eBay products** and matches the dropshippable ones to the **same product on
AliExpress** - then writes the results straight into a Google Sheet.

No paid APIs, no scraper services, no proxies. Just Scrapy, a stealth browser, and
a Google service account.

---

## What it finds

Every eBay product it surfaces passes **all five** of these hard filters:

| # | Filter | Why it matters |
|---|--------|----------------|
| 1 | Price >= **$40** (true USD) | Skips low-ticket noise |
| 2 | Seller has **<= 500** feedback | Small sellers you can compete with |
| 3 | **>= 5 sales / month** | Proven demand (counted from eBay's dated Sold listings) |
| 4 | **< 5 sellers** of the exact same product | Low competition |
| 5 | Seller **not in China** | Ships-from, including masked Chinese cities |

For the subset that are **dropshippable** (generic / China-origin goods, not Western
retail brands), it then finds the matching AliExpress listing and records its URL,
price, and order count.

## The output

Two tabs in a Google Sheet, rewritten on every run:

- **eBay winners** - every product passing the five filters.
- **AliExpress** - the dropshippable subset, each paired with its AliExpress match
  (URL, price, orders, confidence).

## Architecture

```
                 CLOUD (optional backup)            LOCAL Mac (primary, daily)
                 GitHub Actions, datacenter IP       launchd @ 09:00, residential IP
                 +-------------------------+          +--------------------------------+
   eBay  ------> | scrape + 5 filters      |   ...    | scrape eBay + 5 filters        |
                 | upload final.json       |          | classify dropshippable         |
                 +-------------------------+          | match AliExpress (nodriver)    |
                                                      | write BOTH tabs -> Google Sheet |
                                                      +--------------------------------+
```

**Why the split?** eBay serves a datacenter IP fine, so its scrape can run free in
the cloud. **AliExpress blocks datacenter IPs outright** (its anti-bot only clears a
residential IP + real browser), so that step - and the sheet write - runs on the Mac.
This is a proven constraint, not a preference: curl, `curl_cffi`, and a cloud
headless browser were all served AliExpress's "punish" CAPTCHA from a datacenter IP;
only a residential browser (nodriver) gets through.

## How it works

`run_local.sh` (the daily job) runs this pipeline:

1. **`scrapy crawl products`** - eBay search + item pages + the Sold/Completed view
   (for velocity) + same-model search (for the seller count). Cookie-primed with
   browser headers to clear eBay's bot gate. See `ebay_scraper/`.
2. **`curate_finder.py`** - applies the five filters -> `final.json`.
3. **`classify_candidates.py`** - splits winners (Tab 1) from the dropshippable
   subset worth checking on AliExpress (Tab 2 seed).
4. **`ali_match_local.py`** - drives a stealth browser (nodriver) on the residential
   IP to find each candidate's AliExpress match. Paced to avoid throttling.
5. **`sheet_write.py`** - writes both tabs to the sheet via a Google service account.
   Guards against wiping the sheet if a run produces no data.

## Setup

Requires Python 3.11+, Google Chrome, and a Google account.

```bash
# 1. install
python3.11 -m venv ~/.finder-venv
~/.finder-venv/bin/pip install -r requirements.txt

# 2. Google service account (one time)
#    - create a service account in the Google Cloud console, enable the Sheets API
#    - download its JSON key to ~/.config/gws/sa.json  (chmod 600)
#    - share your target sheet with the service account's email as Editor
export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gws/sa.json
export SHEET_ID=<your-google-sheet-id>

# 3. run it once
./run_local.sh

# 4. schedule it daily (macOS)
cp com.mickey.ebay-ali-finder.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mickey.ebay-ali-finder.plist
# run on demand:  launchctl start com.mickey.ebay-ali-finder
```

To run even while the Mac is closed (plugged in), add a scheduled wake:
`sudo pmset repeat wakeorpoweron MTWRFSU 08:55:00`.

## Honest limitations

- **Most eBay winners are not on AliExpress.** Branded goods (Dyson, Dualit,
  KitchenAid, ...) simply are not sold there. Tab 2 only fills for generic /
  dropshippable products - that is the real ceiling of eBay<->AliExpress arbitrage,
  not a bug.
- **AliExpress throttles under load.** A once-daily paced run is fine; hammering the
  same IP (e.g. heavy testing) triggers its CAPTCHA for a few hours.
- **eBay / AliExpress scraping** is against their Terms of Service; this is for
  personal research. Rate-limit politely and use at your own risk.
- **Scrapy is pinned to 2.13.4** - 2.14+ dropped `start_requests()` and the spider
  makes zero requests without it.

## Files

| Path | Role |
|------|------|
| `ebay_scraper/` | Scrapy project: spider, settings, the eBay parsing/filters |
| `curate_finder.py` | Applies the five hard filters |
| `classify_candidates.py` | Winners vs dropshippable split |
| `ali_match_local.py` | AliExpress matching via nodriver (residential) |
| `sheet_write.py` | Direct Google Sheets writer (service account) |
| `run_local.sh` | The daily pipeline |
| `com.mickey.ebay-ali-finder.plist` | launchd daily schedule |
| `.github/workflows/finder.yml` | Optional cloud eBay backup (artifact only) |

## Stack

Scrapy - [nodriver](https://github.com/ultrafunkamsterdam/nodriver) - Google Sheets
API (service account) - launchd. 100% free and open-source.
