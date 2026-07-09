"""LOCAL AliExpress matcher (runs on the Mac, residential IP, via nodriver).

Reads candidates.json (dropshippable eBay products), finds the best same-product
match on AliExpress, and writes ali_enriched.json with AliExpress URL + price +
orders + a match confidence. Paced to avoid AliExpress throttling.

100% free / open-source (nodriver). No API, no proxy. Residential IP required -
that is why this step runs on the Mac, not in the cloud.
"""
import asyncio
import json
import re
import sys

import nodriver as uc

STOP = {"the", "a", "for", "with", "and", "new", "set", "pcs", "pack", "usb", "type",
        "c", "us", "uk", "eu", "2024", "2025", "2026", "hot", "sale", "free"}

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
      text: txt.slice(0,220) });
  });
  return JSON.stringify(out.slice(0,30));
})()
"""


def toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in STOP and len(t) > 1)


def price_usd(text):
    m = re.search(r"US ?\$\s?([\d.,]+)", text) or re.search(r"\$\s?([\d.,]+)", text)
    return float(m.group(1).replace(",", "")) if m else None


def orders(text):
    m = re.search(r"([\d.,]+)\+?\s*(?:sold|orders)", text, re.I)
    if not m:
        return None
    v = m.group(1).replace(",", "")
    return int(float(v) * (1000 if "k" in text.lower()[m.start():m.end()] else 1)) if v.replace(".", "").isdigit() else None


def query_for(x):
    brand = (x.get("brand") or "").strip()
    bad = brand.lower() in ("", "unbranded", "none", "does not apply", "generic", "n/a")
    t = [w for w in re.findall(r"[A-Za-z0-9]+", x["title"]) if w.lower() not in STOP]
    base = " ".join(t[:6])
    return (f"{brand} {base}".strip() if not bad else base)[:70]


async def match_one(browser, x, ebay_toks):
    q = query_for(x)
    url = "https://www.aliexpress.com/w/wholesale-" + re.sub(r"\s+", "-", q) + ".html"
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
    if not cards:
        content = await page.get_content()
        blocked = bool(re.search(r"punish|_____tmd_____|captcha|slider", content, re.I))
        print(f"    (0 cards; page {len(content)}b, blocked={blocked})", file=sys.stderr)
    best = None
    for c in cards:
        ov = len(ebay_toks & toks(c["title"])) / max(len(ebay_toks), 1)
        p = price_usd(c["text"])
        score = ov + (0.15 if p and p < (x.get("price_usd") or 1e9) else 0)
        if best is None or score > best["score"]:
            best = {"score": round(score, 3), "overlap": round(ov, 2), "ali_url": c["url"],
                    "ali_price": p, "ali_orders": orders(c["text"]), "ali_title": c["title"]}
    return best, len(cards)


async def main():
    candidates = json.load(open("candidates.json"))
    if not candidates:
        json.dump([], open("ali_enriched.json", "w"))
        print("no dropshippable candidates to match")
        return
    browser = await uc.start(headless=True, browser_args=["--lang=en-US", "--window-size=1400,3000"])
    out = []
    for i, x in enumerate(candidates):
        et = toks(x["title"])
        try:
            best, n = await match_one(browser, x, et)
        except Exception as e:
            best, n = None, 0
            print(f"  [{i}] ERROR {type(e).__name__}: {e}", file=sys.stderr)
        confident = bool(best and best["overlap"] >= 0.5 and best.get("ali_price"))
        row = {**x, "ali_cards_seen": n, "ali_match": best if confident else None,
               "ali_confident": confident}
        out.append(row)
        print(f"  [{i}] {'MATCH' if confident else 'weak '} cards={n} ov={best['overlap'] if best else '-'} "
              f"ali=${best['ali_price'] if best else '-'} | {x['title'][:44]}")
        await asyncio.sleep(12)  # pace to avoid throttle
    json.dump(out, open("ali_enriched.json", "w"), indent=2)
    browser.stop()
    print(f"enriched {len(out)} candidates, {sum(1 for r in out if r['ali_confident'])} confident matches")


uc.loop().run_until_complete(main())
