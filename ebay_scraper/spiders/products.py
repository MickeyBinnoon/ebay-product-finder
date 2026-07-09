"""
eBay product-research spider.

Per candidate:
  homepage (prime cookies)
    -> search results (parse_search)   cheap filters: feedback<=500, not China, price>=$40
      -> item page     (parse_item)    authoritative USD price, item location, Brand/Model/MPN
        -> active listings of the SAME product (parse_count)   -> distinct seller count
          -> SOLD/completed listings of the SAME product (parse_velocity) -> sales in last 30 days

Identifying "the exact same product" (most reliable available signal):
  * Brand + Model when Model is a real model-number (contains a digit) -> "precise":
    every listing under that Brand+Model is the same product.
  * else Brand + distinctive title tokens / title tokens -> "approximate":
    corroborated by >=60% title-token overlap.
  (eBay catalog epid exists on very few listings and its search redirects, so it is
   not a usable count surface; Brand+Model is the dependable identifier here.)

China exclusion: seller registration country is not in eBay's static HTML, so we
exclude on ITEM LOCATION (ships-from) - including masked Chinese cities like
"Shenzhen, Morocco" - the standard public proxy.

Velocity (>=5 sales/mo): counted from eBay's Sold/Completed view, which date-stamps
each sale ("Sold Jul 7, 2026"); we count sales in the trailing 30 days. This is
PRODUCT-level demand (matches "the product must have >=5 sales/month"); price,
feedback and China are per-listing hard filters enforced here.
"""
import datetime
import re
import urllib.parse
import scrapy

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
GENERIC_WORDS = {
    "new", "listing", "set", "for", "the", "with", "and", "pro", "plus", "mini",
    "portable", "electric", "wireless", "deep", "tissue", "home", "use", "black",
    "white", "usb", "rechargeable", "2024", "2025", "2026", "high", "quality", "low", "price",
}
UNIT_TOKEN = re.compile(r"^\d+(qt|l|w|v|ml|oz|in|cm|mm|gb|tb|mp|k|ft|hz|kg|g|wh|ah|mah)$")
# generic spec tokens that look like model codes but identify nothing (resolution,
# waterproof rating, wifi band, refresh rate, capacities). Never treat as identity.
SPEC_TOKENS = {
    "1080p", "720p", "480p", "1440p", "2160p", "4k", "2k", "8k", "5k", "fhd", "uhd",
    "qhd", "hdr", "hd", "ip67", "ip66", "ip65", "ip68", "ip54", "5ghz", "24ghz",
    "60hz", "120hz", "144hz", "240hz", "30000", "20000", "12000", "10000", "usb30",
}
FX_TO_USD = {"USD": 1.0, "ILS": 0.27, "GBP": 1.27, "EUR": 1.08, "CAD": 0.73, "AUD": 0.66, "JPY": 0.0067}
CHINA = ("China", "Hong Kong", "Macau", "Macao")
CHINA_CITIES = (
    "Shenzhen", "Guangzhou", "Shanghai", "Beijing", "Yiwu", "Dongguan", "Hangzhou",
    "Shantou", "Ningbo", "Xiamen", "Nanjing", "Tianjin", "Foshan", "Zhongshan",
    "Chengdu", "Wuhan", "Fuzhou", "Quanzhou", "Jinhua", "Zhengzhou", "Shenz",
)
CHINA_MARKERS = CHINA + CHINA_CITIES

COUNTRIES = sorted([
    "United States", "United Kingdom", "Hong Kong", "South Korea", "Czech Republic",
    "China", "Japan", "Germany", "Canada", "Australia", "Italy", "Spain", "France",
    "Turkey", "Morocco", "Ukraine", "Poland", "Netherlands", "Israel", "India", "Malta",
    "Romania", "Pakistan", "Taiwan", "Thailand", "Vietnam", "Switzerland", "Austria",
    "Belgium", "Sweden", "Portugal", "Greece", "Ireland", "Denmark", "Finland", "Norway",
    "Singapore", "Malaysia", "Philippines", "Indonesia", "Mexico", "Brazil", "Lithuania",
    "Latvia", "Estonia", "Hungary", "Bulgaria", "Slovakia", "Slovenia", "Croatia", "Cyprus",
], key=len, reverse=True)

DEFAULT_QUERIES = ["massage gun", "dash cam", "mechanical keyboard", "air fryer"]


def to_int(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([kKmM]?)", s)
    if not m:
        return None
    return int(round(float(m.group(1)) * {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}.get(m.group(2), 1)))


def clean_country(raw):
    if not raw:
        return None
    raw = raw.strip()
    for c in COUNTRIES:
        if raw.startswith(c):
            return c
    return raw.split("  ")[0].strip()[:30] or None


class ProductsSpider(scrapy.Spider):
    name = "products"
    allowed_domains = ["ebay.com"]

    def __init__(self, queries=None, max_pages="2", min_price="40", max_feedback="500", *a, **kw):
        super().__init__(*a, **kw)
        self.queries = [q.strip() for q in queries.split("||")] if queries else DEFAULT_QUERIES
        self.max_pages = int(max_pages)
        self.min_price = float(min_price)
        self.max_feedback = int(max_feedback)
        self.seen_itm = set()

    # ---------- prime + search ----------
    def start_requests(self):
        yield scrapy.Request("https://www.ebay.com/", callback=self.after_home, dont_filter=True)

    def after_home(self, response):
        for q in self.queries:
            for pg in range(1, self.max_pages + 1):
                url = ("https://www.ebay.com/sch/i.html?_nkw=" + urllib.parse.quote_plus(q)
                       + "&_udlo=150&LH_BIN=1&_sop=12&_ipg=60&_pgn=" + str(pg))
                yield scrapy.Request(url, callback=self.parse_search, cb_kwargs={"query": q}, dont_filter=True)

    def _parse_card(self, card):
        href = card.css("a[href*='/itm/']::attr(href)").get()
        if not href or "/itm/123456" in href:
            return None
        m = re.search(r"/itm/(\d+)", href)
        if not m:
            return None
        text = " ".join(t.strip() for t in card.css("::text").getall() if t.strip())
        fb = re.search(r"([A-Za-z0-9_.\-]+)\s+([\d.]+)%\s+positive\s+\(([\d,]+(?:\.\d+)?[kKmM]?)\)", text)
        loc = re.search(r"Located in ([A-Z][A-Za-z ]+)", text)
        sold = re.search(r"([\d,]+)\+?\s+sold", text)
        title = card.css(".su-item-card__title ::text").get() or (text.split("Opens in")[0] or text)[:120]
        return {
            "itm_id": m.group(1),
            "url": "https://www.ebay.com/itm/" + m.group(1),
            "title": title.replace("New Listing", "").strip(),
            "seller": fb.group(1) if fb else None,
            "feedback_count": to_int(fb.group(3)) if fb else None,
            "location": clean_country(loc.group(1)) if loc else None,
            "sold_total_search": to_int(sold.group(1)) if sold else 0,
            "price_text": (card.css(".su-item-card__price ::text").get() or "").strip(),
        }

    def parse_search(self, response, query):
        for card in response.css(".su-card-container"):
            rec = self._parse_card(card)
            if not rec or rec["itm_id"] in self.seen_itm:
                continue
            if rec["feedback_count"] is None or rec["feedback_count"] > self.max_feedback:
                continue
            if rec["location"] and any(cn in rec["location"] for cn in CHINA):
                continue
            approx = self._approx_usd(rec["price_text"])
            if approx is not None and approx < self.min_price * 0.7:
                continue
            self.seen_itm.add(rec["itm_id"])
            rec["query"] = query
            yield scrapy.Request(rec["url"], callback=self.parse_item, cb_kwargs={"rec": rec}, dont_filter=True)

    # ---------- item detail ----------
    def _approx_usd(self, price_text):
        m = re.search(r"(US\s*\$|ILS|GBP|EUR|CAD|AUD|\$|£|€)\s*([\d,]+(?:\.\d{2})?)", price_text or "")
        if not m:
            return None
        val = float(m.group(2).replace(",", ""))
        rate = {"US $": 1, "US$": 1, "$": 1, "ILS": FX_TO_USD["ILS"], "GBP": FX_TO_USD["GBP"], "£": FX_TO_USD["GBP"],
                "EUR": FX_TO_USD["EUR"], "€": FX_TO_USD["EUR"], "CAD": FX_TO_USD["CAD"], "AUD": FX_TO_USD["AUD"]}.get(m.group(1).strip(), 1)
        return val * rate

    def _extract_usd(self, html):
        pairs = re.findall(r'"convertedFromValue":\s*"?([\d.]+)"?,\s*"convertedFromCurrency":\s*"USD"', html)
        pairs += re.findall(r'"convertedFromCurrency":\s*"USD",\s*"convertedFromValue":\s*"?([\d.]+)"?', html)
        if pairs:
            return round(max(float(p) for p in pairs), 2), "USD (convertedFrom, exact)"
        m = re.search(r'"price":\s*"?([\d.]+)"?,\s*"priceCurrency":\s*"([A-Z]{3})"', html) \
            or re.search(r'"priceCurrency":\s*"([A-Z]{3})",\s*"price":\s*"?([\d.]+)"?', html)
        if not m:
            return None, None
        a, b = m.groups()
        val, cur = (float(a), b) if a.replace(".", "").isdigit() else (float(b), a)
        return (round(val, 2), "USD (native)") if cur == "USD" else (round(val * FX_TO_USD.get(cur, 1.0), 2), f"{cur}->USD approx")

    def parse_item(self, response, rec):
        html = response.text
        usd, cur_src = self._extract_usd(html)
        rec["price_usd"], rec["price_src"] = usd, cur_src
        if usd is None or usd < self.min_price:
            return
        li = re.search(r"Located in:?\s*([A-Za-z ,.'-]+)", html)
        rec["item_location"] = li.group(1).strip()[:60] if li else rec.get("location")
        if rec["item_location"] and any(cn in rec["item_location"] for cn in CHINA_MARKERS):
            return
        for label in ("Brand", "MPN", "UPC", "Model"):
            v = self._specific(response, label)
            if v:
                rec[label.lower()] = v
        yield self._count_request(rec)

    def _specific(self, response, label):
        val = response.xpath(
            f'//*[normalize-space(text())="{label}"]/following::*[1]/descendant-or-self::text()'
        ).get()
        return val.strip() if val and val.strip() and val.strip() != label else None

    # ---------- shared product identity ----------
    def _identity_tokens(self, rec):
        """Real model-code tokens from Model/MPN ONLY (title is spec-laden and unreliable):
        letter + >=2 consecutive digits, e.g. vcf126, op350uk, ksm90 - excluding units/specs."""
        src = " ".join(str(rec.get(k) or "") for k in ("model", "mpn"))
        toks = set()
        for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/]{2,}", src):
            norm = re.sub(r"[^a-z0-9]", "", t.lower())
            if (len(norm) >= 4 and re.search(r"[a-z]", norm) and re.search(r"\d{2}", norm)
                    and not UNIT_TOKEN.match(norm) and norm not in SPEC_TOKENS):
                toks.add(norm)
        return toks

    def _brand(self, rec):
        b = (rec.get("brand") or "").strip()
        return "" if b.lower() in ("", "unbranded", "none", "does not apply", "n/a", "-", "no", "generic") else b

    def _product_query(self, rec):
        idt = self._identity_tokens(rec)
        # the code must actually appear in the listing's OWN title, else it can't
        # reliably match competing listings -> demote to approximate title matching.
        own = re.sub(r"[^a-z0-9]", "", (rec.get("title") or "").lower())
        idt = {t for t in idt if t in own}
        brand = self._brand(rec)
        if idt:
            code = max(idt, key=len)
            return (brand + " " + code).strip(), idt, True
        toks = [t for t in re.findall(r"[A-Za-z0-9]+", rec["title"]) if t.lower() not in GENERIC_WORDS]
        q = (brand + " " + " ".join(toks[:5])).strip() if brand else " ".join(toks[:6])
        return q, set(), False

    def _match(self, card_title, idt, ttoks, threshold):
        """A candidate card is the same product if it carries a model-code token,
        else if its title overlaps the product title above `threshold`."""
        norm = re.sub(r"[^a-z0-9]", "", (card_title or "").lower())
        if idt:
            return any(tok in norm for tok in idt)
        ct = set(re.findall(r"[a-z0-9]+", (card_title or "").lower()))
        return bool(ttoks) and len(ttoks & ct) / max(len(ttoks), 1) >= threshold

    # ---------- distinct sellers of the same product (active listings) ----------
    def _count_request(self, rec):
        q, idt, precise = self._product_query(rec)
        return scrapy.Request(
            "https://www.ebay.com/sch/i.html?_nkw=" + urllib.parse.quote_plus(q) + "&_ipg=240",
            callback=self.parse_count,
            cb_kwargs={"rec": rec, "q": q, "idt": list(idt), "precise": precise}, dont_filter=True)

    def parse_count(self, response, rec, q, idt, precise):
        idt = set(idt)
        ttoks = set(re.findall(r"[a-z0-9]+", rec["title"].lower()))
        sellers = {}
        for card in response.css(".su-card-container"):
            c = self._parse_card(card)
            if not c or not c["seller"]:
                continue
            if not self._match(c["title"], idt, ttoks, 0.6):
                continue
            sellers.setdefault(c["seller"], c["location"])
        rec["product_query"] = q
        rec["match_precise"] = precise
        rec["match_basis"] = f"model-code {sorted(idt)}" if idt else "title-token overlap >=0.6"
        rec["distinct_sellers"] = len(sellers)
        rec["competing_sellers"] = list(sellers.keys())[:25]
        rec.pop("price_text", None)
        yield self._velocity_request(rec, q, list(idt), precise)

    # ---------- velocity: real sales in the last 30 days (Sold/Completed) ----------
    def _velocity_request(self, rec, q, idt, precise):
        return scrapy.Request(
            "https://www.ebay.com/sch/i.html?_nkw=" + urllib.parse.quote_plus(q) + "&LH_Sold=1&LH_Complete=1&_ipg=240",
            callback=self.parse_velocity,
            cb_kwargs={"rec": rec, "idt": idt, "precise": precise, "vq": q}, dont_filter=True)

    @staticmethod
    def _ord(mon, day, year):
        return datetime.date(int(year), MONTHS[mon], int(day)).toordinal() if mon in MONTHS else None

    def parse_velocity(self, response, rec, idt, precise, vq):
        idt = set(idt)
        today = datetime.date.today().toordinal()
        ttoks = set(re.findall(r"[a-z0-9]+", rec["title"].lower()))
        n30 = matched = total = 0
        for card in response.css(".su-card-container"):
            title = card.css(".su-item-card__title ::text").get() or ""
            txt = " ".join(t.strip() for t in card.css("::text").getall() if t.strip())
            dm = re.search(r"Sold\s+([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})", txt)
            if not dm:
                continue
            total += 1
            if not self._match(title or txt, idt, ttoks, 0.45):
                continue
            matched += 1
            o = self._ord(*dm.groups())
            if o and today - 30 <= o <= today + 1:
                n30 += 1
        rec["velocity_query"] = vq
        rec["velocity_precise"] = precise
        rec["sold_entries_on_page"] = total
        rec["sold_matched_product"] = matched
        rec["sold_last_30d"] = n30
        rec["velocity_verified"] = n30 >= 5
        rec.pop("sold_total_search", None)
        yield rec
