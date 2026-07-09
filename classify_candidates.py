"""Split the 5-filter passers into:
  winners.json     - all qualifying eBay products (Tab 1)
  candidates.json  - the DROPSHIPPABLE subset worth checking on AliExpress (Tab 2 seed)

Dropshippable heuristic: generic/unbranded or China-origin-brand goods that AliExpress
actually carries - NOT Western retail brands (Dyson, Dualit, KitchenAid...), which
aren't on AliExpress. Signals: unbranded/no brand, or a short all-caps/no-name brand,
plus a generic-gadget title and a not-premium price.
"""
import json
import re

WESTERN_BRANDS = {
    "dyson", "sage", "breville", "kitchenaid", "russell hobbs", "toshiba", "cuisinart",
    "dualit", "epson", "ninja", "tefal", "delonghi", "de'longhi", "bosch", "philips",
    "panasonic", "kalorik", "oral-b", "nutribullet", "hamilton beach", "black+decker",
    "shark", "instant pot", "keurig", "vitamix", "sony", "samsung", "lg", "bose",
    "logitech", "anker", "makita", "dewalt", "milwaukee", "ryobi", "kitchen aid",
}
# generic dropship gadget cues in titles
GENERIC_CUES = re.compile(
    r"massage gun|fascia|dash cam|security camera|solar (light|camera)|led strip|"
    r"phone (holder|mount|stand)|car (charger|mount|vacuum)|pet (hair|grooming)|"
    r"electric shaver|hair (trimmer|clipper|dryer)|neck massager|robot vacuum|"
    r"portable|mini|wireless earbuds|smart watch|fascial|percussion|gooseneck",
    re.I,
)


def is_dropshippable(x):
    brand = (x.get("brand") or "").strip().lower()
    title = (x.get("title") or "")
    tl = title.lower()
    # exclude genuine Western retail brands by BRAND *or* TITLE - they are not sold
    # on AliExpress, so any "match" is a clone/accessory (e.g. a $20 "Samsung watch").
    if any(b in brand or b in tl for b in WESTERN_BRANDS):
        return False
    generic_brand = brand in ("", "unbranded", "none", "does not apply", "generic", "n/a")
    # short no-name brand (e.g. VGR, ULTIMEA, ANSVICAM) also tends to be AliExpress-sourced
    noname_brand = bool(brand) and len(brand) <= 9 and brand not in WESTERN_BRANDS
    cue = bool(GENERIC_CUES.search(title))
    price = x.get("price_usd") or 0
    return (generic_brand or noname_brand or cue) and price <= 150


def main():
    final = json.load(open("final.json"))
    winners = final  # Tab 1 = every 5-filter passer
    candidates = [x for x in final if is_dropshippable(x)]
    json.dump(winners, open("winners.json", "w"), indent=2)
    json.dump(candidates, open("candidates.json", "w"), indent=2)
    print(f"winners (Tab1): {len(winners)}  |  dropshippable candidates (Tab2 -> AliExpress): {len(candidates)}")
    for x in candidates:
        print(f"  cand: ${x['price_usd']} sold30={x['sold_last_30d']} | {x['title'][:60]}")


if __name__ == "__main__":
    main()
