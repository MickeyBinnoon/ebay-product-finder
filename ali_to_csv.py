"""ali_enriched.json -> ali.csv (Tab 2: dropshippable eBay products + AliExpress match)."""
import csv
import datetime
import json
import os

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
rows = json.load(open("ali_enriched.json")) if os.path.exists("ali_enriched.json") else []

HEADER = ["Found (UTC)", "eBay title", "eBay $", "Sold /30d", "# sellers",
          "AliExpress match", "AliExpress $", "AliExpress orders", "Confidence",
          "eBay link", "AliExpress link"]

with open("ali.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    for x in rows:
        m = x.get("ali_match") or {}
        w.writerow([
            now, x.get("title", "")[:120], x.get("price_usd"), x.get("sold_last_30d"),
            x.get("distinct_sellers"), (m.get("ali_title") or "")[:100], m.get("ali_price"),
            m.get("ali_orders"), "match" if x.get("ali_confident") else "none",
            x.get("url"), m.get("ali_url", ""),
        ])
print(f"wrote ali.csv with {len(rows)} candidates ({now})")
