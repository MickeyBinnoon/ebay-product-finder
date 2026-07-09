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
import time
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


def search_one(query, tok):
    """One actor run for ONE query (this actor returns 0 for multi-query runs).
    Returns the product list for that query."""
    url = (f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
           f"?token={tok}&maxItems={MAX_ITEMS}")
    body = json.dumps({"queries": [query]}).encode()
    last = None
    # An empty result usually means AliExpress throttled Apify's shared free IP;
    # wait longer each retry so a fresh/recovered IP serves the next attempt.
    for wait in (0, 15, 30, 45):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                items = json.load(r)
            if items:
                return items
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
    if last:
        raise last
    return []


def field(it, *names):
    for n in names:
        if it.get(n) not in (None, ""):
            return it[n]
    return None


def normalize(it):
    """Pull the fields we use out of one Apify result item."""
    price = field(it, "sale_price", "salePrice", "price", "min_price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "title": field(it, "title", "product_title", "name") or "",
        "price": price,
        "url": field(it, "url", "product_url", "productUrl", "link"),
        "orders": field(it, "orders_count", "orders", "trade_count", "sold"),
        "image": field(it, "image_url", "image", "thumbnail"),
    }


def main():
    tok = token()
    candidates = json.load(open("candidates.json")) if os.path.exists("candidates.json") else []
    if not candidates:
        json.dump([], open("ali_enriched.json", "w"))
        print("no candidates")
        return

    # The free plan reliably serves only a handful of scrapes per session before
    # AliExpress throttles Apify's shared IPs, so look up the TOP candidates (already
    # velocity-sorted) one at a time, spaced out. Raise the cap on a paid Apify plan.
    cap = int(os.environ.get("APIFY_MAX_CANDIDATES", "8"))
    out = []
    for i, x in enumerate(candidates):
        if i >= cap:
            out.append({**x, "ali_match": None, "ali_confident": False,
                        "ali_source": "apify:thirdwatch", "ali_skipped": "over daily cap"})
            continue
        try:
            items = [normalize(it) for it in search_one(query_for(x), tok)]
        except Exception as e:  # noqa: BLE001
            items = []
            print(f"  [{i}] apify error: {type(e).__name__}: {e}", file=sys.stderr)
        et = toks(x["title"])
        ebay = x.get("price_usd") or 0
        # same-product price band: an arbitrage match is CHEAPER on AliExpress, not a
        # tiny fraction (accessory) nor pricier (different/bigger product).
        lo, hi = 0.15 * ebay, 1.3 * ebay
        best = None
        for p in items:
            if p["price"] is None or not (lo <= p["price"] <= hi):
                continue
            ov = len(et & toks(p["title"])) / max(len(et), 1)
            if best is None or ov > best["overlap"]:
                best = {"overlap": round(ov, 2), "ali_url": p["url"],
                        "ali_price": round(p["price"], 2), "ali_orders": p["orders"],
                        "ali_title": p["title"][:120], "ali_image": p["image"]}
        confident = bool(best and best["overlap"] >= 0.5 and best.get("ali_url"))
        out.append({**x, "ali_items_seen": len(items),
                    "ali_match": best if confident else None,
                    "ali_confident": confident, "ali_source": "apify:thirdwatch"})
        print(f"  [{i}] {'MATCH' if confident else 'weak '} items={len(items)} "
              f"ov={best['overlap'] if best else '-'} ${best['ali_price'] if best else '-'} | {x['title'][:38]}",
              flush=True)
        time.sleep(int(os.environ.get("APIFY_DELAY", "6")))  # space out vs the free-plan/AliExpress throttle
    json.dump(out, open("ali_enriched.json", "w"), indent=2)
    print(f"enriched {len(out)}, {sum(1 for r in out if r['ali_confident'])} confident matches")


if __name__ == "__main__":
    main()
