"""AliExpress matching via the Apify 'thirdwatch/aliexpress-product-scraper' actor.

Cloud-friendly replacement for the local nodriver matcher: Apify scrapes AliExpress
on their residential IPs, so this runs anywhere (GitHub Actions or the Mac) with no
CAPTCHA/throttling. Reads candidates.json, writes ali_enriched.json (same shape as
ali_match_local.py, so sheet_write.py is unchanged).

Auth: APIFY_TOKEN env var, else ~/.config/apify/token. The token value is never
printed. Pay-per-result: maxItems caps how many products (and cost) per search.
"""
import json
import os
import re
import sys
import urllib.request

ACTOR = "thirdwatch~aliexpress-product-scraper"
MAX_ITEMS = int(os.environ.get("APIFY_MAX_ITEMS", "8"))   # cap results (= cost) per search
STOP = {"the", "a", "for", "with", "and", "new", "set", "pcs", "pack", "usb", "type",
        "us", "uk", "eu", "hot", "sale", "free", "low", "price"}


def token():
    t = os.environ.get("APIFY_TOKEN")
    if t:
        return t.strip()
    p = os.path.expanduser("~/.config/apify/token")
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("no APIFY_TOKEN env var and no ~/.config/apify/token")


def toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in STOP and len(t) > 1)


def query_for(x):
    brand = (x.get("brand") or "").strip()
    bad = brand.lower() in ("", "unbranded", "none", "does not apply", "generic", "n/a")
    t = [w for w in re.findall(r"[A-Za-z0-9]+", x["title"]) if w.lower() not in STOP]
    base = " ".join(t[:6])
    # don't double up the brand if it is already the first title token (e.g. "VGR VGR ...")
    if not bad and not base.lower().startswith(brand.lower()):
        base = f"{brand} {base}"
    return base.strip()[:70]


def search(query, tok):
    url = (f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
           f"?token={tok}&maxItems={MAX_ITEMS}")
    body = json.dumps({"queries": [query]}).encode()
    last = None
    for attempt in (1, 2, 3):  # the actor occasionally 400s on a cold run; retry
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
    raise last


def field(it, *names):
    for n in names:
        if it.get(n) not in (None, ""):
            return it[n]
    return None


def main():
    tok = token()
    candidates = json.load(open("candidates.json")) if os.path.exists("candidates.json") else []
    out = []
    for i, x in enumerate(candidates):
        q = query_for(x)
        try:
            items = search(q, tok) or []
        except Exception as e:
            items = []
            print(f"  [{i}] apify error: {type(e).__name__}: {e}", file=sys.stderr)
        et = toks(x["title"])
        ebay_price = x.get("price_usd") or 0
        # a same-product match can't cost a tiny fraction of the eBay price - that is
        # an accessory (strap/case/part), not the product. Require >= 20% of eBay.
        floor = 0.2 * ebay_price
        best = None
        for it in items:
            title = field(it, "title", "product_title", "name") or ""
            price = field(it, "sale_price", "salePrice", "price", "min_price")
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None
            if price is None or price < floor:   # skip accessories / unpriced
                continue
            ov = len(et & toks(title)) / max(len(et), 1)
            if best is None or ov > best["overlap"]:
                best = {"overlap": round(ov, 2),
                        "ali_url": field(it, "url", "product_url", "productUrl", "link"),
                        "ali_price": round(price, 2),
                        "ali_orders": field(it, "orders_count", "orders", "trade_count", "sold"),
                        "ali_title": title[:120],
                        "ali_image": field(it, "image_url", "image", "thumbnail")}
        confident = bool(best and best["overlap"] >= 0.5 and best.get("ali_url"))
        out.append({**x, "ali_cards_seen": len(items),
                    "ali_match": best if confident else None,
                    "ali_confident": confident, "ali_source": "apify:thirdwatch"})
        print(f"  [{i}] {'MATCH' if confident else 'weak '} items={len(items)} "
              f"ov={best['overlap'] if best else '-'} ${best['ali_price'] if best else '-'} | {x['title'][:40]}")
    json.dump(out, open("ali_enriched.json", "w"), indent=2)
    print(f"enriched {len(out)}, {sum(1 for r in out if r['ali_confident'])} confident matches")


if __name__ == "__main__":
    main()
