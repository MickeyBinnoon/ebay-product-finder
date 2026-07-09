"""final.json -> final.csv (the public artifact the Google Sheet pulls via IMPORTDATA)."""
import csv
import datetime
import json

final = json.load(open("final.json"))
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

HEADER = ["Found (UTC)", "Title", "Price USD", "Seller feedback", "Ships from",
          "Sold /30d", "# sellers", "Match", "eBay link"]

with open("final.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    for x in final:
        w.writerow([
            now, x.get("title", "")[:140], x.get("price_usd"), x.get("feedback_count"),
            x.get("item_location"), x.get("sold_last_30d"), x.get("distinct_sellers"),
            "model-code" if x.get("match_precise") else "title", x.get("url"),
        ])
print(f"wrote final.csv with {len(final)} products ({now})")
