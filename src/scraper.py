"""Vinted scraper.

A régi /api/v2/catalog/items végpont 404-et ad, ezért a katalógusoldal HTML-jét
töltjük le, és a beágyazott Next.js (RSC) adatból olvassuk ki a hirdetéseket:
    <script>self.__next_f.push([1,"...JSON-szöveg..."])</script>
"""
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from curl_cffi import requests

log = logging.getLogger("scraper")

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[\s*1\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)')
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_LD_JSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------
# Parsolás (hálózat nélkül tesztelhető)
# --------------------------------------------------------------------------
def decode_rsc_payload(html: str) -> str:
    """Az összes self.__next_f.push([1,"..."]) darab összefűzve, JSON-nal dekódolva."""
    parts: List[str] = []
    for m in _PUSH_RE.finditer(html):
        try:
            parts.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def iter_json_objects(html: str) -> Iterator[Any]:
    """Minden JSON-értéket kiad, amit az oldalból ki lehet nyerni."""
    # 1) klasszikus Next.js (pages router)
    m = _NEXT_DATA_RE.search(html)
    if m:
        try:
            yield json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    flight = decode_rsc_payload(html)

    # 2) RSC sorok: "<id>:<JSON>"
    for line in flight.split("\n"):
        _, sep, payload = line.partition(":")
        if sep and payload[:1] in ("[", "{"):
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                pass

    # 3) tartalék: minden '{"id":' kezdetű objektum a dekódolt és a nyers szövegben
    decoder = json.JSONDecoder()
    marker = '{"id":'
    for source in (flight, html):
        pos = 0
        while True:
            idx = source.find(marker, pos)
            if idx == -1:
                break
            try:
                obj, end = decoder.raw_decode(source, idx)
                yield obj
                pos = end
            except json.JSONDecodeError:
                pos = idx + len(marker)


def walk_dicts(node: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_dicts(v)


def looks_like_listing(d: Dict[str, Any]) -> bool:
    return (
        isinstance(d.get("id"), (int, str))
        and "price" in d
        and ("title" in d or "url" in d or "photo" in d)
    )


def extract_listings(html: str) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for obj in iter_json_objects(html):
        for d in walk_dicts(obj):
            if looks_like_listing(d):
                found.setdefault(str(d["id"]), d)
    return list(found.values())


def extract_images(item: Dict[str, Any]) -> List[str]:
    photos = item.get("photos")
    if isinstance(photos, list):
        urls = [p.get("url") for p in photos if isinstance(p, dict) and p.get("url")]
        if urls:
            return urls
    photo = item.get("photo")
    if isinstance(photo, dict) and photo.get("url"):
        return [photo["url"]]
    return []


def normalize_item(item: Dict[str, Any], base_url: str) -> Optional[Dict[str, Any]]:
    raw_price = item.get("price")
    currency = item.get("currency", "RON")
    price_val = 0.0
    if isinstance(raw_price, dict):
        try:
            price_val = float(raw_price.get("amount", 0.0))
        except (TypeError, ValueError):
            price_val = 0.0
        currency = raw_price.get("currency_code", currency)
    elif isinstance(raw_price, (int, float, str)):
        try:
            price_val = float(raw_price)
        except ValueError:
            price_val = 0.0

    if not item.get("id") or price_val <= 0:
        return None

    url = item.get("url") or item.get("path") or f"/items/{item['id']}"
    if isinstance(url, str) and url.startswith("/"):
        url = f"{base_url}{url}"

    return {
        "id": item["id"],
        "title": item.get("title") or "Ismeretlen termék",
        "description": item.get("description") or "",
        "price": price_val,
        "currency": currency,
        "url": url,
        "images": extract_images(item),
    }


def parse_item_page(html: str, item_id: Any) -> Dict[str, Any]:
    """Leírás + képek a hirdetés oldaláról (ld+json, majd RSC tartalék)."""
    description = ""
    images: List[str] = []

    for m in _LD_JSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and d.get("@type") == "Product":
                description = d.get("description") or description
                img = d.get("image")
                if isinstance(img, str):
                    images = [img]
                elif isinstance(img, list):
                    images = [i if isinstance(i, str) else (i or {}).get("url") for i in img]
                    images = [i for i in images if i]

    if not description or not images:
        for obj in iter_json_objects(html):
            for d in walk_dicts(obj):
                if str(d.get("id")) == str(item_id):
                    description = description or d.get("description") or ""
                    images = images or extract_images(d)

    return {"description": description, "images": images}


# --------------------------------------------------------------------------
# Hálózati réteg
# --------------------------------------------------------------------------
class VintedScraper:
    def __init__(self, domain: str = "ro"):
        self.domain = domain
        self.base_url = f"https://www.vinted.{domain}"
        self.session = requests.Session(impersonate="chrome120")
        self._init_session()

    def _init_session(self) -> None:
        headers = dict(NAV_HEADERS, **{"Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"})
        try:
            resp = self.session.get(self.base_url, headers=headers, timeout=15)
            token = bool(self.session.cookies.get_dict().get("access_token_web"))
            log.info("Session kész (HTTP %s) | token: %s", resp.status_code, token)
        except Exception as e:
            log.error("Hiba az inicializáláskor: %s", e)

    def _get(self, url: str, **kwargs):
        """GET, 401/403 esetén session-újítással egyszer újrapróbálva."""
        headers = dict(NAV_HEADERS, Referer=self.base_url)
        resp = self.session.get(url, headers=headers, timeout=20, **kwargs)
        if resp.status_code in (401, 403):
            log.warning("HTTP %s – session újítás", resp.status_code)
            self._init_session()
            resp = self.session.get(url, headers=headers, timeout=20, **kwargs)
        return resp

    def fetch_newest_items(
        self,
        search_text: str = "iPhone 13 Pro",
        price_to: Optional[int] = None,
        per_page: int = 30,
    ) -> List[Dict[str, Any]]:
        params = {"search_text": search_text, "order": "newest_first"}
        if price_to:
            params["price_to"] = str(price_to)

        try:
            resp = self._get(f"{self.base_url}/catalog", params=params)
        except Exception as e:
            log.error("Katalógus lekérési hiba: %s", e)
            return []

        if resp.status_code != 200:
            log.error("Katalógus: HTTP %s", resp.status_code)
            return []

        html = resp.text
        raw = extract_listings(html)
        items = [n for n in (normalize_item(r, self.base_url) for r in raw) if n]
        log.info("'%s': %d nyers, %d használható hirdetés (%d bájt)",
                 search_text, len(raw), len(items), len(html))

        if not items:
            Path("debug_catalog.html").write_text(html, encoding="utf-8")
            log.warning("0 hirdetés – HTML elmentve: debug_catalog.html | next_f darabok: %d | "
                        '"price" előfordulás: %d | /items/ linkek: %d',
                        html.count("self.__next_f.push"), html.count('"price"'), html.count("/items/"))
        return items[:per_page]

    def fetch_item_details(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Teljes leírás és képek a hirdetés oldaláról. Hiba esetén üres dict."""
        try:
            resp = self._get(item["url"])
            if resp.status_code != 200:
                return {}
            return parse_item_page(resp.text, item["id"])
        except Exception as e:
            log.error("Részletek lekérési hiba (%s): %s", item.get("id"), e)
            return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    scraper = VintedScraper(domain="ro")
    print("Hirdetések lekérése teszt jelleggel...")
    found = scraper.fetch_newest_items(search_text="iPhone 13 Pro", per_page=5)
    print(f"Sikeresen lekérve: {len(found)} darab hirdetés.")
    for it in found[:5]:
        print(f"- [{it['price']} {it['currency']}] {it['title']} ({len(it['images'])} kép) -> {it['url']}")