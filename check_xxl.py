#!/usr/bin/env python3
"""Voorraadwaker: checkt meerdere shirts/maten op meerdere webshops en stuurt
pushmeldingen via ntfy.sh bij: maat op voorraad, maat uitverkocht,
pagina verwijderd en pagina weer terug."""
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
     "maten": ["M", "XL"]},
    {"naam": "PSV retro thuisshirt 96/97", "type": "generic",
     "url": "https://www.psvfanstore.nl/product/psv-retro-thuisshirt-9697-0001005098",
     "maten": ["XL"]},
    {"naam": "PSV retro shirt 89/90", "type": "generic",
     "url": "https://www.psvfanstore.nl/psv-retro-shirt-89-90",
     "maten": ["XL"]},
]

# Pas na zoveel checks achter elkaar "verwijderd" melden (voorkomt vals alarm bij een storing)
VERWIJDERD_NA = 2
# =============================================================

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = "state.json"
PL_BASE = "https://shop.premierleague.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SOLD_OUT = re.compile(r"unselectable|disabled|out-of-stock|outofstock|sold-?out|not-available|unavailable|uitverkocht|line-through|\bslash\b", re.I)
NOT_FOUND_TITLE = ("404", "not found", "niet gevonden", "bestaat niet", "no longer available", "niet meer beschikbaar")


def get(url, accept="text/html", ajax=False):
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Language": "nl,en;q=0.8"}
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch_page(url):
    """Haalt een pagina op. Geeft (status, html, eind-url, reden) terug.
    status: 'ok' | 'removed' (404/410) | 'error' (tijdelijk probleem, bv. 403/500/time-out)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html",
                                               "Accept-Language": "nl,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return ("ok", r.read().decode("utf-8", "replace"), r.geturl(), "")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return ("removed", None, url, f"HTTP {e.code}")
        return ("error", None, url, f"HTTP {e.code}")
    except Exception as e:
        return ("error", None, url, f"{e.__class__.__name__}: {e}")


def notify(title, message, click, priority="high", tags="shirt,tada"):
    """Stuurt een pushmelding. Geeft True terug als dat lukte (anders wordt het de volgende run opnieuw geprobeerd)."""
    if not NTFY_TOPIC:
        print("[!] Geen NTFY_TOPIC ingesteld, melding niet verstuurd.")
        return True
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), method="POST",
            headers={"Title": title, "Priority": priority, "Tags": tags, "Click": click})
        urllib.request.urlopen(req, timeout=30)
        print(f"    [+] Melding verstuurd: {title}")
        return True
    except Exception as e:
        print(f"    [!] Melding versturen mislukt ({e.__class__.__name__}), volgende run opnieuw.")
        return False


# ---------------------------------------------------------------- Premier League (SFCC)
def sizes_from_json(data, sizes, results):
    product = data.get("product") or {}
    for attr in product.get("variationAttributes") or []:
        for v in attr.get("values") or []:
            names = {str(v.get(k, "")).upper() for k in ("value", "displayValue", "id")}
            for s in sizes:
                if s in names and v.get("selectable") is not None and results.get(s) is None:
                    results[s] = bool(v["selectable"])


def check_sfcc(url, sizes, page):
    """Geeft {maat: (True/False/None, bron)} terug."""
    sizes = [s.upper() for s in sizes]
    results = {s: None for s in sizes}
    page = html.unescape(page)

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


def size_elements(page, size):
    """Alle HTML-elementen waarvan de tekst precies de maat is: lijst van (tagnaam, volledig element, openingstag)."""
    out = []
    # (?=...) zorgt dat ook elementen binnen andere elementen gevonden worden (bv. <button> in <li>)
    for m in re.finditer(r"(?=(<(button|label|li|option|a|span|div)\b([^>]*)>(.{0,300}?)</\2>))", page, re.S | re.I):
        text = re.sub(r"<[^>]+>", " ", m.group(4))
        if re.sub(r"\s+", " ", html.unescape(text)).strip().upper() == size:
            out.append((m.group(2).lower(), m.group(1), f"<{m.group(2)}{m.group(3)}>"))
    return out


def html_buttons(page, size):
    """Zoekt knopjes/labels met precies de maattekst en kijkt of ze 'uitverkocht' ogen."""
    els = size_elements(page, size)
    if not els:
        return (None, "html")
    knoppen = [e for e in els if e[0] in ("button", "label", "option", "a")]
    gekozen = knoppen or els
    return (not all(SOLD_OUT.search(e[1]) for e in gekozen), "html-knop")


def check_generic(url, sizes, page):
    sizes = [s.upper() for s in sizes]
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


# ---------------------------------------------------------------- Bestaat de pagina nog?
def _norm(u):
    return urllib.parse.unquote(urllib.parse.urlsplit(u).path).rstrip("/").lower()


def _ids(u):
    """Productnummers (6+ cijfers) in een adres, bv. 0001008518 of 701247195."""
    return set(re.findall(r"\d{6,}", urllib.parse.unquote(urllib.parse.urlsplit(u).path)))


def same_product(url, final, baseline):
    """Kwam de site (eventueel na doorsturen) uit op hetzelfde product?"""
    if _norm(final) in {_norm(url), _norm(baseline or url)}:
        return True
    ids = _ids(url)
    return bool(ids) and bool(ids & _ids(final))


def not_found_title(page):
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    t = html.unescape(m.group(1)).lower() if m else ""
    return any(x in t for x in NOT_FOUND_TITLE)


def evaluate(item, baseline):
    """Geeft (signaal, resultaten, eind-url, reden) terug.
    signaal: 'ok' | 'removed' | 'error' (tijdelijk) | 'unknown' (pagina bestaat, maar maten onleesbaar)."""
    url = item["url"]
    status, page, final, reason = fetch_page(url)
    if status != "ok":
        return status, None, final, reason
    kind = item.get("type") or ("sfcc" if "premierleague.com" in url else "generic")
    results = (check_sfcc if kind == "sfcc" else check_generic)(url, item["maten"], page)
    found = any(a is not None for a, _ in results.values())
    if not same_product(url, final, baseline):
        if found and not baseline:
            # Eerste keer: adres stuurt door naar de echte productpagina, die onthouden we.
            return "ok", results, final, f"doorgestuurd naar {final}"
        return "removed", None, final, f"doorgestuurd naar {final}"
    if not found:
        if not_found_title(page):
            return "removed", None, final, "niet-gevonden-pagina"
        return "unknown", None, final, ""
    return "ok", results, final, ""


# ---------------------------------------------------------------- Diagnose
def diagnose(item):
    print(f"\n===== DIAGNOSE: {item['naam']} =====")
    status, page, final, reason = fetch_page(item["url"])
    print(f"Ophalen: {status} {reason}".rstrip())
    if status == "removed":
        print("Deze pagina bestaat nu niet. De waker meldt dat en laat het weten zodra hij terug is.")
        return
    if status != "ok":
        print("Tijdelijk probleem of blokkade, probeer het later nog eens.")
        return
    if final != item["url"]:
        print(f"Doorgestuurd naar: {final}")
    print(f"Pagina: {len(page)} tekens | titel lijkt op niet-gevonden: {not_found_title(page)}")
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
        print(f"Conclusie: {check_generic(item['url'], [s], page)[s]}")
    print("--- alle maten op de pagina (om te vergelijken met wat je zelf op de site ziet) ---")
    for s in ("XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL"):
        els = [e for e in size_elements(page, s) if e[0] == "button"]
        if els:
            klassen = re.search(r'class="([^"]*)"', els[0][2])
            korte = " ".join(t for t in (klassen.group(1).split() if klassen else [])
                             if not t.startswith(("hover:", "border", "min-", "h-", "px", "inline", "items", "justify", "transition", "duration")))
            print(f"{s}: {'OP VOORRAAD' if not SOLD_OUT.search(els[0][1]) else 'uitverkocht'} | classes: {korte[:120]}")


# ---------------------------------------------------------------- Hoofdprogramma
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def process(item, state):
    """Checkt één shirt, stuurt meldingen en werkt de state bij. Geeft het aantal fouten terug."""
    url, naam = item["url"], item["naam"]
    print(f"[i] {naam}")
    pkey = f"{url}|__pagina__"
    pst = dict(state.get(pkey)) if isinstance(state.get(pkey), dict) else {}
    try:
        signal, results, final, reason = evaluate(item, pst.get("final"))
    except Exception as e:
        signal, results, final, reason = "error", None, url, f"{e.__class__.__name__}: {e}"

    if signal == "error":
        print(f"    [!] Pagina ophalen mislukt ({reason}), volgende run opnieuw.")
        return 1

    if signal == "removed":
        pst["misses"] = pst.get("misses", 0) + 1
        if pst.get("status") == "removed":
            print(f"    Pagina nog steeds weg ({reason})")
        elif pst["misses"] >= VERWIJDERD_NA:
            print(f"    Pagina VERWIJDERD ({reason})")
            if notify(f"Pagina verwijderd: {naam}",
                      f"Deze pagina bestaat niet meer ({reason}). Je krijgt een melding zodra hij terug is.",
                      url, "default", "wastebasket"):
                pst["status"] = "removed"
        else:
            print(f"    Pagina lijkt weg ({reason}), check {pst['misses']}/{VERWIJDERD_NA} voordat er een melding komt")
        state[pkey] = pst
        return 0

    if signal == "unknown":
        print("    [!] Pagina bestaat, maar geen enkele maat kon gelezen worden (site veranderd?)")
        pst["misses"] = 0
        state[pkey] = pst
        return 1

    # signal == "ok": de pagina bestaat
    errors = 0
    if reason:
        print(f"    ({reason})")
    back = pst.get("status") == "removed"
    if back:
        op = [s for s, (a, _) in results.items() if a]
        uit = [s for s, (a, _) in results.items() if a is False]
        parts = ([f"Op voorraad: {', '.join(op)}"] if op else []) + ([f"Nog uitverkocht: {', '.join(uit)}"] if uit else [])
        print("    Pagina is TERUG")
        if not notify(f"Pagina terug: {naam}", "De pagina is weer online. " + ". ".join(parts) + ".",
                      url, "high" if op else "default", "tada"):
            return 1  # niets bijwerken, volgende run opnieuw proberen
    state[pkey] = {"status": "ok", "misses": 0, "final": final}

    for size, (available, source) in results.items():
        key = f"{url}|{size}"
        if available is None:
            print(f"    [!] {size}: status onbekend (staat deze maat wel op de pagina?)")
            errors += 1
            continue
        prev = state.get(key)
        print(f"    {size}: {'OP VOORRAAD' if available else 'uitverkocht'} (bron: {source})")
        sent = True
        if not back:  # bij 'pagina terug' staat de maatinfo al in die ene melding
            if available and prev is not True:
                sent = notify(f"Maat {size} op voorraad: {naam}",
                              f"Tik om naar het shirt te gaan. (bron: {source})", url)
            elif not available and prev is True:
                sent = notify(f"Maat {size} uitverkocht: {naam}",
                              "Deze maat is niet meer beschikbaar. Je krijgt weer een melding als hij terugkomt.",
                              url, "default", "x")
        if sent:
            state[key] = available
    return errors


def main():
    if os.environ.get("TESTMELDING", "").lower() == "true":
        notify("Test: voorraadwaker werkt", "Je krijgt een melding zodra een van je maten op voorraad is.",
               WATCHLIST[0]["url"], "default")
        return 0
    if os.environ.get("DIAGNOSE", "").lower() == "true":
        for item in WATCHLIST:
            if (item.get("type") or "generic") == "generic":
                diagnose(item)
        return 0

    state = load_state()
    errors = sum(process(item, state) for item in WATCHLIST)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
