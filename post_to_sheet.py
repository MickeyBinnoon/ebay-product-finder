"""POST the qualifying products to the Google Sheet via its Apps Script webhook.

The webhook (doPost) clears the sheet and rewrites the header + rows, so the sheet
REFRESHES to the current run's finds. SHEET_WEBHOOK_URL is a GitHub Actions secret.
"""
import datetime
import json
import os
import urllib.request

url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
final = json.load(open("final.json"))
if not url:
    print(f"SHEET_WEBHOOK_URL not set - skipping sheet write ({len(final)} products found)")
    raise SystemExit(0)
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

header = ["Found (UTC)", "Title", "Price USD", "Seller feedback", "Ships from",
          "Sold /30d", "# sellers", "Match", "eBay link"]
rows = [
    [now, x["title"][:140], x.get("price_usd"), x.get("feedback_count"),
     x.get("item_location"), x.get("sold_last_30d"), x.get("distinct_sellers"),
     "model-code" if x.get("match_precise") else "title", x["url"]]
    for x in final
]

payload = json.dumps({"header": header, "rows": rows, "updatedAt": now}).encode()
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=45) as r:
    print("sheet webhook:", r.status, r.read().decode()[:200])
print(f"wrote {len(rows)} product rows to the sheet ({now})")
