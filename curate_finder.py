"""Apply the 5 hard filters to the spider output and write final.json.

  1. price >= $40                     3. >= 5 sales / month
  2. seller feedback <= 500           4. FEWER than 5 sellers of the exact same product
  5. seller not located in China (ships-from, incl. masked Chinese cities)
"""
import glob
import json

CHINA = ("China", "Hong Kong", "Macau", "Macao", "Shenzhen", "Guangzhou", "Shanghai",
         "Beijing", "Yiwu", "Dongguan", "Hangzhou", "Shantou", "Ningbo", "Xiamen",
         "Foshan", "Zhongshan", "Chengdu", "Wuhan")


def china(x):
    loc = (x.get("item_location") or "") + " " + (x.get("location") or "")
    return any(m in loc for m in CHINA)


def passes(x):
    return (
        x.get("price_usd") is not None and x["price_usd"] >= 40
        and x.get("feedback_count") is not None and 0 <= x["feedback_count"] <= 500
        and not china(x)
        and x.get("sold_last_30d", 0) >= 5                       # velocity
        and 1 <= x.get("distinct_sellers", 999) < 5             # low competition
    )


rows = {}
for f in glob.glob("out/*.json"):
    try:
        for x in json.load(open(f)):
            rows[x["itm_id"]] = x
    except Exception:
        pass

items = list(rows.values())
final = [x for x in items if passes(x)]
# best row per distinct product, most-sold first
best = {}
for x in final:
    k = (x.get("product_query") or x.get("title") or x["itm_id"]).lower()[:50]
    if k not in best or x.get("sold_last_30d", 0) > best[k].get("sold_last_30d", 0):
        best[k] = x
final = sorted(best.values(), key=lambda x: x.get("sold_last_30d", 0), reverse=True)

json.dump(final, open("final.json", "w"), indent=2)
print(f"scraped listings: {len(items)}  ->  qualifying products (all 5 filters): {len(final)}")
for x in final:
    print(f"  ${x['price_usd']} fb={x['feedback_count']} sold30={x['sold_last_30d']} sellers={x['distinct_sellers']} | {x['title'][:56]}")
