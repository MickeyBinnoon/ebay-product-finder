"""Write both tabs to the Google Sheet using the service account (non-interactive).

Tab 'eBay winners'  <- winners.json  (all 5-filter passers)
Tab 'AliExpress'    <- ali_enriched.json (dropshippable + AliExpress match)

Auth: GOOGLE_APPLICATION_CREDENTIALS points at the SA key. The sheet must be shared
with the SA's client_email as Editor. Idempotent: ensures tabs exist, clears, writes.
"""
import datetime
import json
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build

SHEET_ID = os.environ.get("SHEET_ID", "1zm5_swG9rt9R82x3wLBXgSZlGw9uuKDhsFWcMkcke08")
KEY = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", os.path.expanduser("~/.config/gws/sa.json"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

WIN_HEADER = ["Found (UTC)", "Title", "Price USD", "Seller feedback", "Ships from",
              "Sold /30d", "# sellers", "Match", "eBay link"]
ALI_HEADER = ["Found (UTC)", "eBay title", "eBay $", "Sold /30d", "# sellers",
              "AliExpress match", "AliExpress $", "AliExpress orders", "Confidence",
              "eBay link", "AliExpress link"]


def load(path):
    return json.load(open(path)) if os.path.exists(path) else []


def win_rows():
    rows = [WIN_HEADER]
    for x in load("winners.json") or load("final.json"):
        rows.append([now, x.get("title", "")[:140], x.get("price_usd"), x.get("feedback_count"),
                     x.get("item_location"), x.get("sold_last_30d"), x.get("distinct_sellers"),
                     "model-code" if x.get("match_precise") else "title", x.get("url")])
    return rows


def ali_rows():
    rows = [ALI_HEADER]
    for x in load("ali_enriched.json"):
        m = x.get("ali_match") or {}
        rows.append([now, x.get("title", "")[:120], x.get("price_usd"), x.get("sold_last_30d"),
                     x.get("distinct_sellers"), (m.get("ali_title") or "")[:100], m.get("ali_price"),
                     m.get("ali_orders"), "match" if x.get("ali_confident") else "none",
                     x.get("url"), m.get("ali_url", "")])
    return rows


def main():
    creds = service_account.Credentials.from_service_account_file(KEY, scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    ss = svc.spreadsheets()

    meta = ss.get(spreadsheetId=SHEET_ID).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    first_id = meta["sheets"][0]["properties"]["sheetId"]

    reqs = []
    # rename the first tab -> "eBay winners"
    if titles[0] != "eBay winners":
        reqs.append({"updateSheetProperties": {
            "properties": {"sheetId": first_id, "title": "eBay winners"}, "fields": "title"}})
    # add "AliExpress" tab if missing
    if "AliExpress" not in titles:
        reqs.append({"addSheet": {"properties": {"title": "AliExpress"}}})
    if reqs:
        ss.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()

    for tab, rows in (("eBay winners", win_rows()), ("AliExpress", ali_rows())):
        # GUARD: never wipe a tab when the run produced no data rows (e.g. a failed
        # or blocked scrape). Keep the prior contents instead of clearing to empty.
        if len(rows) <= 1:
            print(f"no data for '{tab}' - keeping prior contents (not clearing)")
            continue
        ss.values().clear(spreadsheetId=SHEET_ID, range=f"'{tab}'").execute()
        ss.values().update(spreadsheetId=SHEET_ID, range=f"'{tab}'!A1",
                           valueInputOption="USER_ENTERED", body={"values": rows}).execute()
        print(f"wrote {len(rows) - 1} rows to '{tab}'")

    print(f"done ({now})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        raise
