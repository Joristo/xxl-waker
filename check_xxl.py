#!/usr/bin/env python3
"""Voorraadwaker: checkt meerdere shirts/maten in de Premier League Shop en stuurt
een pushmelding via ntfy.sh zodra een maat (weer) op voorraad is."""
import html, json, os, re, sys, urllib.parse, urllib.request

# ===== Pas hier aan welke shirts en maten je wilt volgen =====
WATCHLIST = [
    {"naam": "Paars/zwart shirt",
     "url": "https://shop.premierleague.com/en/premier-league-x-guinness-football-jersey-purple-and-black/701247195-black%2Fpurple.html",
     "maten": ["XXL"]},
    {"naam": "Goud/zwart shirt",
     "url": "https://shop.premierleague.com/en/premier-league-x-guinness-football-jersey-gold-and-black/701247194-black.html",
     "maten": ["M", "XXL"]},
]
# =============================================================

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = "state.json"
BASE = "https://shop.premierleague.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SOLD_OUT = re.compile(r"unselectable|disabled|out-of-stock|outofstock|sold-?out|not-available|unavailable", re.I)


def get(url, accept="text/html"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "en", "X-Requested-With": "XMLHttpRequest"})
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


def sizes_from_json(data, sizes, results):
    product = data.get("product") or {}
    for attr in product.get("variationAttributes") or []:
        for v in attr.get("values") or []:
            names = {str(v.get(k, "")).upper() for k in ("value", "displayValue", "id")}
            for s in sizes:
                if s in names and v.get("selectable") is not None and results.get(s) is None:
                    results[s] = bool(v["selectable"])


def check_product(url, sizes):
    """Geeft {maat: True/False/None} terug (None = kon niet bepalen)."""
    sizes = [s.upper() for s in sizes]
    results = {s: None for s in sizes}
    page = html.unescape(get(url))

    urls = set(re.findall(r'(?:https://[^"\'\s<>]+)?/on/demandware\.store/[^"\'\s<>]*Product-Variation\?[^"\'\s<>]+', page))
    site = re.search(r"/on/demandware\.store/(Sites-[^/]+-Site)/([^/]+)/", page)
    pid = urllib.parse.unquote(url.rstrip("/").split("/")[-1].removesuffix(".html"))
    if site:
        urls.add(f"{BASE}/on/demandware.store/{site.group(1)}/{site.group(2)}/Product-Variation?"
                 + urllib.parse.urlencode({"pid": pid, "quantity": 1}))

    for u in urls:
        if all(r is not None for r in results.values()):
            break
        full = u if u.startswith("http") else BASE + u
        try:
            sizes_from_json(json.loads(get(full, accept="application/json")), sizes, results)
        except Exception as e:
            print(f"    variatie-URL overgeslagen ({e.__class__.__name__})")

    for s in sizes:
        if results[s] is None:
            tags = re.findall(rf'<[^>]*data-attr-value=["\']{s}["\'][^>]*>', page, re.I)
            tags += re.findall(rf'<(?:button|option|a|li|input)[^>]*>\s*{s}\s*<', page, re.I)
            if tags:
                results[s] = not all(SOLD_OUT.search(t) for t in tags)
    return results


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

    state = load_state()
    errors = 0
    for item in WATCHLIST:
        print(f"[i] {item['naam']}")
        try:
            results = check_product(item["url"], item["maten"])
        except Exception as e:
            print(f"    [!] Pagina ophalen mislukt: {e}")
            errors += 1
            continue
        for size, available in results.items():
            key = f"{item['url']}|{size}"
            if available is None:
                print(f"    [!] {size}: status onbekend")
                errors += 1
                continue
            print(f"    {size}: {'OP VOORRAAD' if available else 'uitverkocht'}")
            if available and state.get(key) is not True:
                notify(f"Maat {size} op voorraad: {item['naam']}",
                       "Tik om direct naar het shirt te gaan.", item["url"])
            state[key] = available

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
