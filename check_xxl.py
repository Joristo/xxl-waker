#!/usr/bin/env python3
"""Voorraadwaker: checkt meerdere shirts/maten op meerdere webshops en stuurt
een pushmelding via ntfy.sh zodra een maat (weer) op voorraad is."""
import html, json, os, re, sys, urllib.error, urllib.parse, urllib.request

# ===== Pas hier aan welke shirts en maten je wilt volgen =====
# type "sfcc"    = Premier League Shop (leest de voorraad-data van de shop zelf)
# type "generic" = andere webshops (zoekt in JSON-LD, Nuxt-data en HTML-knoppen)
WATCHLIST = [
    {"naam": "Paars/zwart shirt", "type": "sfcc",
     "url": "https://shop.premierleague.com/en/premier-league-x-guinness-football-jersey-purple-and-black/701247195-black%2Fpurple.html",
     "maten": ["XXL"]},
    {"naam": "Goud/zwart shirt", "type": "sfcc",
     "url": "https://shop.premierleague.com/en/premier-league-x-guinness-football-jersey-gold-and-black/701247194-black.html",
     "maten": ["M", "XXL"]},
    {"naam": "PSV Dillen thuisshirt", "type": "generic",
     "url": "https://www.psvfanstore.nl/product/psv-coen-dillen-thuisshirt-shortsleeve-0001008518",
     "maten": ["M"]},
]
# =============================================================

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = "state.json"
PL_BASE = "https://shop.premierleague.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SOLD_OUT = re.compile(r"unselectable|disabled|out-of-stock|outofstock|sold-?out|not-available|unavailable|uitverkocht|line-through", re.I)


def get(url, accept="text/html", ajax=False):
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Language": "nl,en;q=0.8"}
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def notify(title, message, click, priority="high"):
    if not NTFY_TOPIC:
        print("[!] Geen NTFY_TOPIC ingesteld, melding niet verstuurd.")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), method="POST",
        headers={"Title": title, "Priority": priority, "Tags": "shirt,tada", "Click": click})
    urllib.request.urlopen(req, timeout=30)
    print("    [+] Melding verstuurd.")


# ---------------------------------------------------------------- Premier League (SFCC)
def sizes_from_json(data, sizes, results):
    product = data.get("product") or {}
    for attr in product.get("variationAttributes") or []:
        for v in attr.get("values") or []:
            names = {str(v.get(k, "")).upper() for k in ("value", "displayValue", "id")}
            for s in sizes:
                if s in names and v.get("selectable") is not None and results.get(s) is None:
                    results[s] = bool(v["selectable"])


def check_sfcc(url, sizes):
    """Geeft {maat: (True/False/None, bron)} terug."""
    sizes = [s.upper() for s in sizes]
    results = {s: None for s in sizes}
    page = html.unescape(get(url))

    urls = set(re.findall(r'(?:https://[^"\'\s<>]+)?/on/demandware\.store/[^"\'\s<>]*Product-Variation\?[^"\'\s<>]+', page))
    site = re.search(r"/on/demandware\.store/(Sites-[^/]+-Site)/([^/]+)/", page)
    pid = urllib.parse.unquote(url.rstrip("/").split("/")[-1].removesuffix(".html"))
    if site:
        urls.add(f"{PL_BASE}/on/demandware.store/{site.group(1)}/{site.group(2)}/Product-Variation?"
                 + urllib.parse.urlencode({"pid": pid, "quantity": 1}))
    for u in urls:
        if all(r is not None for r in results.values()):
            break
        full = u if u.startswith("http") else PL_BASE + u
        try:
            sizes_from_json(json.loads(get(full, accept="application/json", ajax=True)), sizes, results)
        except Exception as e:
            print(f"    variatie-URL overgeslagen ({e.__class__.__name__})")
    out = {s: (results[s], "shop-voorraaddata") for s in sizes}
    for s in sizes:
        if out[s][0] is None:
            out[s] = html_buttons(page, s)
    return out


# ---------------------------------------------------------------- Generieke webshops
BOOL_KEYS = {"instock", "in_stock", "isinstock", "available", "isavailable", "purchasable",
             "orderable", "buyable", "selectable", "beschikbaar", "canbuy", "salable", "is_salable"}
NUM_KEYS = {"stock", "quantity", "qty", "inventory", "voorraad", "stocklevel", "availablequantity",
            "stockquantity", "inventoryquantity"}
STATUS_KEYS = {"availability", "stockstatus", "stock_status", "status", "availabilitystatus"}
SIZE_KEYS = {"size", "maat", "name", "label", "value", "title", "option", "variant", "sizename",
             "displayvalue", "text", "sku", "code"}


def _size_match(val, size):
    v = str(val).strip().upper()
    return v == size or bool(re.search(rf"(?:^|[\s_\-/:]){re.escape(size)}$", v))


def _stock_from_dict(d):
    """True/False als dit dict iets zegt over voorraad, anders None."""
    for k, v in d.items():
        kl = str(k).lower()
        if kl in STATUS_KEYS and isinstance(v, str):
            t = v.lower().replace(" ", "").replace("_", "").replace("-", "")
            if any(x in t for x in ("outofstock", "uitverkocht", "soldout", "unavailable", "nietbeschikbaar", "nietopvoorraad")):
                return False
            if any(x in t for x in ("instock", "opvoorraad", "available", "beschikbaar", "limitedavailability")):
                return True
    for k, v in d.items():
        kl = str(k).lower()
        if kl in BOOL_KEYS and isinstance(v, (bool, int)) and not isinstance(v, float):
            return bool(v)
        if kl in NUM_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            return v > 0
    return None


def walk_for_size(node, size, found):
    if isinstance(node, dict):
        if any(str(k).lower() in SIZE_KEYS and isinstance(v, (str, int)) and _size_match(v, size)
               for k, v in node.items()):
            st = _stock_from_dict(node)
            if st is not None:
                found.append((st, node))
        for v in node.values():
            walk_for_size(v, size, found)
    elif isinstance(node, list):
        for v in node:
            walk_for_size(v, size, found)


def jsonld_blocks(page):
    out = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            out.append(json.loads(html.unescape(m.group(1)).strip()))
        except Exception:
            pass
    return out


def nuxt_resolve(arr):
    """Zet Nuxt 3's __NUXT_DATA__ (devalue-formaat met index-verwijzingen) om in gewone data."""
    cache, busy = {}, set()
    wrappers = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}

    def res(i):
        if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(arr):
            return None
        if i in cache:
            return cache[i]
        if i in busy:
            return None
        busy.add(i)
        v = arr[i]
        if isinstance(v, list):
            if len(v) == 2 and isinstance(v[0], str) and v[0] in wrappers:
                r = res(v[1])
            elif v and isinstance(v[0], str) and not all(isinstance(x, int) for x in v[1:]):
                r = v
            else:
                r = [res(x) if isinstance(x, int) and not isinstance(x, bool) else x for x in v]
        elif isinstance(v, dict):
            r = {k: (res(x) if isinstance(x, int) and not isinstance(x, bool) else x) for k, x in v.items()}
        else:
            r = v
        busy.discard(i)
        cache[i] = r
        return r

    return res(0)


def nuxt_blocks(page):
    out = []
    m = re.search(r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    if m:
        try:
            out.append(nuxt_resolve(json.loads(html.unescape(m.group(1)))))
        except Exception:
            pass
    m = re.search(r'window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>', page, re.S)
    if m:
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            pass
    return out


def html_buttons(page, size):
    """Zoekt knopjes/labels met precies de maattekst en kijkt of ze 'uitverkocht' ogen."""
    tags = []
    for m in re.finditer(r"<(button|label|li|option|a|span|div)\b([^>]*)>(.{0,300}?)</\1>", page, re.S | re.I):
        text = re.sub(r"<[^>]+>", " ", m.group(3))
        if re.sub(r"\s+", " ", html.unescape(text)).strip().upper() == size:
            tags.append(m.group(0)[:300])
    if not tags:
        return (None, "html")
    return (not all(SOLD_OUT.search(t) for t in tags), "html-knop")


def check_generic(url, sizes, page=None):
    sizes = [s.upper() for s in sizes]
    page = page if page is not None else get(url)
    ld, nuxt = jsonld_blocks(page), nuxt_blocks(page)
    out = {}
    for s in sizes:
        res = (None, "onbekend")
        for label, blocks in (("json-ld", ld), ("nuxt-data", nuxt)):
            found = []
            for b in blocks:
                walk_for_size(b, s, found)
            if found:
                res = (any(f[0] for f in found), label)
                break
        if res[0] is None:
            res = html_buttons(page, s)
        out[s] = res
    return out


def diagnose(item):
    print(f"\n===== DIAGNOSE: {item['naam']} =====")
    try:
        page = get(item["url"])
    except urllib.error.HTTPError as e:
        print(f"HTTP-fout {e.code}: de site blokkeert waarschijnlijk geautomatiseerd verkeer.")
        return
    print(f"Pagina: {len(page)} tekens")
    for name in ("nuxt", "shopify", "magento", "lightspeed", "woocommerce", "cloudflare", "demandware"):
        if name in page.lower():
            print(f"Platformhint: {name}")
    ld, nuxt = jsonld_blocks(page), nuxt_blocks(page)
    print(f"JSON-LD blokken: {len(ld)} | Nuxt-data blokken: {len(nuxt)}")
    for s in item["maten"]:
        s = s.upper()
        print(f"--- maat {s} ---")
        for label, blocks in (("json-ld", ld), ("nuxt-data", nuxt)):
            found = []
            for b in blocks:
                walk_for_size(b, s, found)
            for st, node in found[:3]:
                print(f"{label}: {st} <- {json.dumps(node, ensure_ascii=False)[:300]}")
        tags = [m.group(0)[:250] for m in re.finditer(r"<(button|label|li|option|a|span|div)\b([^>]*)>(.{0,300}?)</\1>", page, re.S | re.I)
                if re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(3)))).strip().upper() == s]
        for t in tags[:4]:
            print(f"html: {t}")
        print(f"Conclusie: {check_generic(item['url'], [s], page)[s]}")
    apis = sorted(set(re.findall(r'["\'](/api/[^"\'\s<>]{3,80}|https?://[^"\'\s<>]*(?:api|graphql)[^"\'\s<>]{0,60})["\']', page)))
    print("API-adressen op de pagina:", apis[:10] or "geen")
    ctx = re.search(r".{0,80}Check winkelvoorraad.{0,80}", page, re.S | re.I)
    if ctx:
        print("Winkelvoorraad-context:", re.sub(r"\s+", " ", ctx.group(0)))


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    if os.environ.get("TESTMELDING", "").lower() == "true":
        notify("Test: voorraadwaker werkt", "Je krijgt een melding zodra een van je maten op voorraad is.",
               WATCHLIST[0]["url"], "default")
        return 0
    if os.environ.get("DIAGNOSE", "").lower() == "true":
        for item in WATCHLIST:
            if item.get("type") == "generic":
                diagnose(item)
        return 0

    state = load_state()
    errors = 0
    for item in WATCHLIST:
        print(f"[i] {item['naam']}")
        try:
            checker = check_sfcc if item.get("type", "sfcc") == "sfcc" else check_generic
            results = checker(item["url"], item["maten"])
        except urllib.error.HTTPError as e:
            print(f"    [!] Pagina ophalen mislukt: HTTP {e.code}")
            errors += 1
            continue
        except Exception as e:
            print(f"    [!] Pagina ophalen mislukt: {e}")
            errors += 1
            continue
        for size, (available, source) in results.items():
            key = f"{item['url']}|{size}"
            if available is None:
                print(f"    [!] {size}: status onbekend")
                errors += 1
                continue
            print(f"    {size}: {'OP VOORRAAD' if available else 'uitverkocht'} (bron: {source})")
            if available and state.get(key) is not True:
                notify(f"Maat {size} op voorraad: {item['naam']}",
                       f"Tik om naar het shirt te gaan. (bron: {source})", item["url"])
            state[key] = available

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
