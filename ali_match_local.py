"""LOCAL AliExpress matcher via nodriver on a residential IP. Free: no API, no proxy,
no paid tool. `run_local.sh` calls THIS. Writes the ali_enriched.json shape that
sheet_write.py reads.

Reads candidates.json (dropshippable eBay products), finds the best same-product
AliExpress match, and records URL + price + orders + a match confidence.

TWO MATCH TIERS (title token overlap = shared tokens / eBay tokens, in a 0.15-1.3x
price band): "match" = overlap >= 0.5 (confident, high precision); "likely" =
`ALI_LIKELY_MIN` (0.42) <= overlap < 0.5 AND a shared >=5-char product noun - a
near-miss surfaced for HUMAN review, never counted as confident. sheet_write labels
the tiers "match" / "likely" / "none". (An F1-based scorer was tried 2026-07 and
reverted - symmetric F1 penalised long AliExpress titles and matched fewer.)

AliExpress blocks datacenter IPs outright and throttles a *single* residential IP
after a burst of requests, so this file is deliberately gentle and self-protecting:

  * en_US / USD via the site's own currency cookie (aep_usuc_f) -> English titles that
    actually token-match the (English) eBay titles, and real USD prices (no FX guess).
    Injected once, before the first search, with NO warm-up navigation (an extra page
    load was observed to help trip the anti-bot).
  * one shared browser session; generous pacing (ALI_DELAY) with jitter; top-N cap
    (ALI_MAX_CANDIDATES).
  * captcha/"punish" detection with escalating backoff, then a circuit-breaker that
    ABORTS the run (writing partial results) once the IP is clearly flagged - rather
    than hammering it into an hours-long block.

Env knobs:
  ALI_MAX_CANDIDATES (default 8)   - how many top (velocity-sorted) candidates to look up
  ALI_DELAY          (default 20)  - base seconds between candidates (a little jitter added)
  ALI_LIKELY_MIN     (default 0.42)- near-miss "likely - review" floor (set 0.5 to disable)
  ALI_HEADLESS       (default 1)   - 0 to drive a real visible window (less bot-detectable)
  ALI_FORCE_USD      (default 1)   - 0 to skip the en_US/USD currency cookie
"""
import asyncio
import datetime
import json
import os
import random
import re
import sys

import nodriver as uc
from nodriver import cdp

STOP = {"the", "a", "for", "with", "and", "new", "set", "pcs", "pack", "usb", "type",
        "c", "us", "uk", "eu", "2024", "2025", "2026", "hot", "sale", "free"}

MAX_CAND = int(os.environ.get("ALI_MAX_CANDIDATES", "8"))
DELAY = int(os.environ.get("ALI_DELAY", "20"))
HEADLESS = os.environ.get("ALI_HEADLESS", "1") != "0"
FORCE_USD = os.environ.get("ALI_FORCE_USD", "1") != "0"
# Near-miss "likely - review" floor: below the 0.5 confident bar but still worth a human
# look. Surfaced separately, never counted as a confident match. Set to 0.5 to disable.
LIKELY_MIN = float(os.environ.get("ALI_LIKELY_MIN", "0.42"))
# Seller/store enrichment (AliTools-style). OFF by default: it visits each match's ITEM
# page (+1 request/match), which adds to the single-IP throttle budget. Enable once
# validated. Pulls AliExpress's OWN store data - no extension/API needed.
SELLER = os.environ.get("ALI_SELLER", "0") == "1"
SELLER_MAX = int(os.environ.get("ALI_SELLER_MAX", "5"))   # cap enriched matches per run
SELLER_DEBUG = os.environ.get("ALI_SELLER_DEBUG", "0") == "1"  # dump item-page HTML if parse fails

# Extract product cards from a rendered AliExpress search page. Walk up from each
# /item/ link to the smallest ancestor that carries the card's text, and grab its
# image + visible text (which includes the price and "orders/sold" count).
CARDS_JS = r"""
(() => {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/item/"]').forEach(a => {
    const m = a.href.match(/\/item\/(\d+)\.html/); if (!m) return;
    const id = m[1]; if (seen.has(id)) return; seen.add(id);
    let node = a, best = a, bestLen = (a.innerText||"").length;
    for (let i=0;i<5 && node;i++){ node=node.parentElement; if(!node) break;
      const t=(node.innerText||"").length; if(t>bestLen && t<400){best=node;bestLen=t;} }
    const img = best.querySelector('img');
    const txt = (best.innerText||"").replace(/\s+/g," ").trim();
    out.push({ id, url:"https://www.aliexpress.com/item/"+id+".html",
      title:(a.getAttribute("title")|| (img&&img.alt) || txt).slice(0,140),
      image:(img&&(img.src||img.getAttribute("data-src")))||null,
      text: txt.slice(0,220) });
  });
  return JSON.stringify(out.slice(0,30));
})()
"""

# A captcha / anti-bot page renders 0 cards; its <title> is distinctive ("Captcha
# Interception", "punish"...). We only treat a page as BLOCKED when it has 0 cards
# AND one of these markers - a normal results page can contain the substring
# "verify"/"slider" in its scripts, so matching raw HTML would false-positive.
BLOCK_RE = re.compile(r"captcha|punish|_____tmd_____|are you a robot|access denied|"
                      r"unusual traffic|slider|nc_1_wrapper", re.I)

# AliExpress ranks cheap ACCESSORIES (eartips, cases, "charging box for AirPods") into
# product searches; they share the product's words and sit at the low end of the price
# band, but are NOT the product. Exclude any card whose title looks like an accessory.
ACCESSORY_RE = re.compile(
    r"\b(ear ?tips?|silicone tips?|charging (?:box|case|dock|cable)|screen protector|"
    r"replacement|sticker|decal|lanyard|pouch|carrying (?:case|bag)|for airpods)\b", re.I)


def toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in STOP and len(t) > 1)


def shared_noun(inter):
    """A real shared product noun (>=5-char, non-spec token) - guards the 'likely' tier
    so a near-miss can't qualify on generic scraps like 'pro'/'blue'/'4k' alone."""
    return any(len(t) >= 5 and not t.isdigit() and not re.fullmatch(r"\d+[a-z]+", t) for t in inter)


# With FORCE_USD the cards show US$; keep the other symbols as a fallback in case the
# currency cookie is stripped and the session localizes (e.g. this Mac's IP -> ILS).
_FX = {"US$": 1.0, "$": 1.0, "ILS": 0.27, "₪": 0.27, "€": 1.08, "£": 1.27}
_CUR_RE = re.compile(r"(US ?\$|ILS|₪|€|£|\$)\s?([\d.,]+)")


def price_usd(text):
    m = _CUR_RE.search(text or "")
    if not m:
        return None
    sym = m.group(1).replace(" ", "")
    try:
        val = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    return round(val * _FX.get(sym, 1.0), 2)


def orders(text):
    m = re.search(r"([\d.,]+)\+?\s*(?:sold|orders)", text or "", re.I)
    if not m:
        return None
    v = m.group(1).replace(",", "")
    return int(float(v) * (1000 if "k" in (text or "").lower()[m.start():m.end()] else 1)) if v.replace(".", "").isdigit() else None


# ---- seller / store enrichment: reads AliExpress's OWN store data off the item page,
# the same source the AliTools extension reads (store name, positive-feedback %, seller
# rating, open date -> age -> >=6-month flag). Best-effort + defensive: a failure never
# breaks matching, it just leaves seller fields blank. NEEDS live validation against the
# current item-page structure before enabling in the daily run.
_JSON_BLOB = [r"window\.runParams\s*=\s*({.*?})\s*;?\s*</script>",
              r"window\.runParams\.data\s*=\s*({.*?})\s*;",
              r"__INIT_DATA__\s*=\s*({.*?})\s*;", r"__INITIAL_DATA__\s*=\s*({.*?})\s*;"]


def _deep(obj, *keys):
    """First scalar value under any dict key matching (case-insensitive) one of `keys`."""
    want = tuple(k.lower() for k in keys)
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and k.lower() in want and isinstance(v, (str, int, float)):
                    return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return None


def _months_since(open_val):
    """Store age in months from an openTime (ms epoch / year / date string). None if unknown."""
    if open_val in (None, ""):
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        s = str(open_val)
        if s.isdigit() and len(s) >= 12:                     # ms epoch
            dt = datetime.datetime.fromtimestamp(int(s) / 1000, datetime.timezone.utc)
        else:
            m = re.search(r"(19|20)\d{2}", s)                # any year in the string
            if not m:
                return None
            dt = datetime.datetime(int(m.group(0)), 1, 1, tzinfo=datetime.timezone.utc)
        return max(0, int((now - dt).days / 30.44))
    except Exception:  # noqa: BLE001
        return None


async def seller_info(browser, url):
    """Visit one item page and pull the store block. Returns a dict (best-effort) or None."""
    page = await browser.get(url)
    await asyncio.sleep(4)
    content = await page.get_content()
    if not content or (BLOCK_RE.search(content[:4000]) and "storeName" not in content):
        return None
    data = None
    for pat in _JSON_BLOB:
        m = re.search(pat, content, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                break
            except Exception:  # noqa: BLE001
                continue
    s = {}
    if data:
        s["store_name"] = _deep(data, "storeName", "companyName")
        s["positive_rate"] = _deep(data, "positiveRate", "sellerPositiveRate", "positiveFeedbackRate")
        s["rating"] = _deep(data, "sellerScore", "evarageStar", "averageStar", "storeRating")
        s["open_time"] = _deep(data, "openTime", "storeOpenTime", "sellerOpenTime", "gmtCreate")
        s["store_url"] = _deep(data, "storeURL", "storeUrl")
    # visible-HTML fallbacks
    if not s.get("positive_rate"):
        m = re.search(r"([\d.]+)\s*%\s*Positive\s*Feedback", content, re.I)
        if m:
            s["positive_rate"] = m.group(1)
    if not s.get("store_name"):
        m = re.search(r'"storeName"\s*:\s*"([^"]{2,80})"', content)
        if m:
            s["store_name"] = m.group(1)
    age = _months_since(s.get("open_time"))
    s["age_months"] = age
    s["established_6mo"] = None if age is None else (age >= 6)
    got = any(s.get(k) for k in ("store_name", "positive_rate", "rating", "age_months"))
    if not got and SELLER_DEBUG:
        # capture the page so the extractor can be refined against the real structure
        mid = re.search(r"/item/(\d+)", url)
        dbg = f"seller_debug_{mid.group(1) if mid else 'x'}.html"
        try:
            open(dbg, "w").write((content or "")[:200000])
            print(f"    [seller debug] no fields parsed; wrote {dbg} ({len(content or '')}b, "
                  f"json_blob={'Y' if data else 'N'})", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
    return s if got else None


def query_for(x):
    brand = (x.get("brand") or "").strip()
    bad = brand.lower() in ("", "unbranded", "none", "does not apply", "generic", "n/a")
    t = [w for w in re.findall(r"[A-Za-z0-9]+", x["title"]) if w.lower() not in STOP]
    base = " ".join(t[:6])
    # don't double up the brand if it is already the first title token (e.g. "VGR VGR ...")
    if not bad and not base.lower().startswith(brand.lower()):
        base = f"{brand} {base}"
    return base.strip()[:70]


async def set_locale(browser):
    """Force en_US + USD via the site's own currency cookie, injected with the CDP
    `url` form so NO page navigation is needed (a warm-up load helped trip the bot
    gate in testing). Best-effort: never fatal."""
    if not FORCE_USD:
        return
    try:
        blank = await browser.get("about:blank")   # local only; not an AliExpress hit
        for name, value in [("aep_usuc_f", "site=glo&c_tp=USD&region=US&b_locale=en_US"),
                            ("intl_locale", "en_US")]:
            await blank.send(cdp.network.set_cookie(
                name=name, value=value, url="https://www.aliexpress.com", path="/"))
    except Exception as e:  # noqa: BLE001
        print(f"  (locale cookie set failed, continuing localized: {type(e).__name__})", file=sys.stderr)


async def fetch_cards(browser, query):
    """One AliExpress search navigation. Returns (cards, blocked, title)."""
    url = "https://www.aliexpress.com/w/wholesale-" + re.sub(r"\s+", "-", query) + ".html"
    page = await browser.get(url)
    await asyncio.sleep(6)
    for _ in range(2):
        await page.scroll_down(500)
        await asyncio.sleep(1.5)
    raw = await page.evaluate(CARDS_JS)
    try:
        cards = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, json.JSONDecodeError):
        cards = []
    title = ""
    try:
        content = await page.get_content()
        m = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        title = (m.group(1).strip() if m else "")
        blocked = not cards and bool(BLOCK_RE.search(title) or BLOCK_RE.search(content[:4000]))
    except Exception:  # noqa: BLE001
        blocked = not cards
    return cards, blocked, title


async def match_one(browser, x):
    """Fetch with escalating backoff on a block. Returns (best, n_cards, blocked)."""
    q = query_for(x)
    cards, blocked = [], False
    for wait in (0, 30, 60):   # retry the SAME query, backing off, only while blocked
        if wait:
            await asyncio.sleep(wait)
        cards, blocked, title = await fetch_cards(browser, q)
        if cards or not blocked:
            break   # got results, or a genuine empty result (not worth retrying)
        print(f"    (blocked: {title[:40]!r}; backing off)", file=sys.stderr)

    et = toks(x["title"])
    ebay = x.get("price_usd") or 0
    # same-product price band: an arbitrage match is CHEAPER on AliExpress, not a tiny
    # fraction (accessory) nor pricier (a different/bigger item).
    lo, hi = 0.15 * ebay, 1.3 * ebay
    best = None
    for c in cards:
        if ACCESSORY_RE.search(c.get("title") or ""):
            continue   # skip eartips/cases/"for AirPods" etc. - not the product
        p = price_usd(c.get("text"))
        if p is None or not (lo <= p <= hi):
            continue
        inter = et & toks(c["title"])
        ov = len(inter) / max(len(et), 1)
        if best is None or ov > best["overlap"]:
            best = {"overlap": round(ov, 2), "noun": shared_noun(inter), "ali_url": c["url"],
                    "ali_price": round(p, 2), "ali_orders": orders(c.get("text")),
                    "ali_title": c["title"][:120], "ali_image": c.get("image")}
    return best, len(cards), blocked


async def main():
    candidates = json.load(open("candidates.json")) if os.path.exists("candidates.json") else []
    if not candidates:
        json.dump([], open("ali_enriched.json", "w"))
        print("no dropshippable candidates to match")
        return

    browser = await uc.start(headless=HEADLESS,
                             browser_args=["--lang=en-US", "--window-size=1400,3000"])
    await set_locale(browser)

    out = []
    consecutive_blocks = 0
    aborted = False
    for i, x in enumerate(candidates):
        if aborted or i >= MAX_CAND:
            reason = "aborted - IP throttled" if aborted else "over daily cap"
            out.append({**x, "ali_items_seen": 0, "ali_match": None, "ali_confident": False,
                        "ali_source": "local:nodriver", "ali_skipped": reason})
            continue

        try:
            best, n, blocked = await match_one(browser, x)
        except Exception as e:  # noqa: BLE001
            best, n, blocked = None, 0, False
            print(f"  [{i}] ERROR {type(e).__name__}: {e}", file=sys.stderr)

        # circuit breaker: if the IP is clearly flagged (repeated blocks even after
        # backoff), stop hitting AliExpress and write partial results.
        if blocked and n == 0:
            consecutive_blocks += 1
            out.append({**x, "ali_items_seen": 0, "ali_match": None, "ali_confident": False,
                        "ali_source": "local:nodriver", "ali_blocked": True})
            print(f"  [{i}] BLOCK ({consecutive_blocks}) | {x['title'][:44]}", flush=True)
            if consecutive_blocks >= 2:
                aborted = True
                print("  circuit-breaker: IP throttled, aborting remaining lookups", file=sys.stderr)
            continue

        consecutive_blocks = 0
        confident = bool(best and best["overlap"] >= 0.5 and best.get("ali_url"))
        # "likely - review" tier: a near-miss below the confident bar that is still in the
        # price band AND shares a real product noun. Surfaced for human judgement only.
        likely = bool(best and not confident and best["overlap"] >= LIKELY_MIN
                      and best.get("noun") and best.get("ali_url"))
        out.append({**x, "ali_items_seen": n,
                    "ali_match": best if (confident or likely) else None,
                    "ali_confident": confident, "ali_likely": likely,
                    "ali_source": "local:nodriver"})
        tag = "MATCH " if confident else ("LIKELY" if likely else "weak  ")
        print(f"  [{i}] {tag} cards={n} "
              f"ov={best['overlap'] if best else '-'} ali=${best['ali_price'] if best else '-'} "
              f"| {x['title'][:40]}", flush=True)

        if i + 1 < min(len(candidates), MAX_CAND):
            await asyncio.sleep(DELAY + random.uniform(0, 8))   # pace + jitter vs throttle

    # second pass: seller enrichment for the actionable (match/likely) rows. Off unless
    # ALI_SELLER=1 - each visit is +1 request, so it's capped and only for real matches.
    if SELLER and not aborted:
        enriched = 0
        for row in out:
            if enriched >= SELLER_MAX:
                break
            if not (row.get("ali_confident") or row.get("ali_likely")):
                continue
            m = row.get("ali_match") or {}
            if not m.get("ali_url"):
                continue
            try:
                s = await seller_info(browser, m["ali_url"])
            except Exception as e:  # noqa: BLE001
                s = None
                print(f"    seller_info error: {type(e).__name__}: {e}", file=sys.stderr)
            if s:
                row["ali_seller"] = s
                enriched += 1
                # HARD FILTER: demote a match whose store is KNOWN to be < 6 months old.
                # Only filters on a *confirmed* too-new store; unknown age (None) is kept,
                # so a failed extraction can never wrongly drop a good match.
                if s.get("established_6mo") is False:
                    row["ali_confident"] = False
                    row["ali_likely"] = False
                    row["ali_seller_dropped"] = "store <6mo"
                print(f"    seller | {str(s.get('store_name'))[:28]:28} pos={s.get('positive_rate')} "
                      f"rating={s.get('rating')} age_mo={s.get('age_months')} >=6mo={s.get('established_6mo')}"
                      f"{'  [DROPPED <6mo]' if row.get('ali_seller_dropped') else ''}", flush=True)
            await asyncio.sleep(DELAY + random.uniform(0, 8))

    json.dump(out, open("ali_enriched.json", "w"), indent=2)
    try:
        browser.stop()
    except Exception:  # noqa: BLE001
        pass
    print(f"enriched {len(out)}, {sum(1 for r in out if r['ali_confident'])} confident "
          f"+ {sum(1 for r in out if r.get('ali_likely'))} likely (review)"
          + (" (RUN ABORTED - IP throttled)" if aborted else ""))


if __name__ == "__main__":
    uc.loop().run_until_complete(main())
