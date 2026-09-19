#!/usr/bin/env python3
"""Checkt of een maat van een product in de Premier League Shop weer op voorraad is
en stuurt dan een pushmelding via ntfy.sh. Alleen standaard Python, geen installs nodig."""
import html, json, os, re, sys, urllib.parse, urllib.request

PRODUCT_URL = os.environ.get("PRODUCT_URL") or (
    "https://shop.premierleague.com/en/premier-league-x-guinness-football-jersey-"
    "purple-and-black/701247195-black%2Fpurple.html")
SIZE = (os.environ.get("SIZE") or "XXL").upper()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BASE = "https://shop.premierleague.com"
SOLD_OUT = re.compile(r"unselectable|disabled|out-of-stock|outofstock|sold-?out|not-available|unavailable", re.I)


def get(url, accept="text/html"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "en", "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def notify(title, message, priority="high"):
    if not NTFY_TOPIC:
        print("[!] Geen NTFY_TOPIC ingesteld, melding niet verstuurd.")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), method="POST",
        headers={"Title": title, "Priority": priority, "Tags": "shirt,tada", "Click": PRODUCT_URL})
    urllib.request.urlopen(req, timeout=30)
    print("[+] Melding verstuurd.")


def size_from_variation_json(data):
    product = data.get("product") or {}
    for attr in product.get("variationAttributes") or []:
        for v in attr.get("values") or []:
            names = {str(v.get(k, "")).upper() for k in ("value", "displayValue", "id")}
            if SIZE in names:
                sel = v.get("selectable")
                print(f"    JSON: maat {SIZE} selectable={sel}")
                if sel is not None:
                    return bool(sel)
    return None


def check():
    page = html.unescape(get(PRODUCT_URL))
    print(f"[i] Pagina opgehaald ({len(page)} tekens)")

    urls = set(re.findall(r'(?:https://[^"\'\s<>]+)?/on/demandware\.store/[^"\'\s<>]*Product-Variation\?[^"\'\s<>]+', page))
    site = re.search(r"/on/demandware\.store/(Sites-[^/]+-Site)/([^/]+)/", page)
    pid = urllib.parse.unquote(PRODUCT_URL.rstrip("/").split("/")[-1].removesuffix(".html"))
    if site:
        urls.add(f"{BASE}/on/demandware.store/{site.group(1)}/{site.group(2)}/Product-Variation?"
                 + urllib.parse.urlencode({"pid": pid, "quantity": 1}))
    print(f"[i] {len(urls)} variatie-URL('s) gevonden")

    for u in sorted(urls, key=lambda x: SIZE not in x.upper()):
        full = u if u.startswith("http") else BASE + u
        try:
            data = json.loads(get(full, accept="application/json"))
        except Exception as e:
            print(f"    overgeslagen ({e.__class__.__name__}): {full[:120]}")
            continue
        result = size_from_variation_json(data)
        if result is not None:
            return result

    tags = re.findall(rf'<[^>]*data-attr-value=["\']{SIZE}["\'][^>]*>', page, re.I)
    tags += re.findall(rf'<(?:button|option|a|li|input)[^>]*>\s*{SIZE}\s*<', page, re.I)
    for t in tags:
        print(f"    HTML: {t[:160]}")
    if tags:
        return not all(SOLD_OUT.search(t) for t in tags)
    return None


if __name__ == "__main__":
    if "--test" in sys.argv:
        notify("Test: voorraadwaker werkt", f"Je krijgt een melding zodra {SIZE} op voorraad is.", "default")
        sys.exit(0)

    available = check()
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"available={'true' if available else 'false'}\n")

    if available is None:
        print(f"[!] Kon de status van maat {SIZE} niet bepalen (site veranderd of geblokkeerd?).")
        sys.exit(1)
    if available:
        print(f"[+] {SIZE} is OP VOORRAAD!")
        notify(f"Maat {SIZE} is weer op voorraad!", "Tik om direct naar het Guinness-shirt te gaan.")
    else:
        print(f"[-] {SIZE} nog uitverkocht.")
