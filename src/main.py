import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import List, Optional, Set

from dotenv import load_dotenv

from src.analyzer import ListingAnalyzer
from src.evaluator import DealEvaluator
from src.notifier import TelegramNotifier
from src.scraper import VintedScraper

load_dotenv()
log = logging.getLogger("main")

DOMAIN = os.getenv("VINTED_DOMAIN", "ro")
QUERIES: List[str] = [q.strip() for q in os.getenv(
    "SEARCH_QUERIES", "iPhone 13 Pro Max,iPhone 13 Pro,iPhone 13").split(",") if q.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
MAX_ANALYZE_PER_CYCLE = int(os.getenv("MAX_ANALYZE_PER_CYCLE", "15"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "300"))  # ez alatt tok/tartozék/alkatrész, nem telefon
RULES_CURRENCY = os.getenv("RULES_CURRENCY", "RON")
PRICE_TO_ENV = os.getenv("PRICE_TO")
SEEN_PATH = Path("data/seen_ids.json")


class SeenStore:
    """A már feldolgozott hirdetés-ID-k fájlban, hogy újraindítás után se küldjön duplikátumot."""

    def __init__(self, path: Path = SEEN_PATH):
        self.path = path
        self.ids: Set[str] = set()
        if path.exists():
            try:
                self.ids = set(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                log.warning("A %s sérült, üres állapotról indulunk.", path)

    def __contains__(self, item_id) -> bool:
        return str(item_id) in self.ids

    def add(self, item_id) -> None:
        self.ids.add(str(item_id))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # csak az utolsó 20 000 ID-t tartjuk meg
        self.path.write_text(json.dumps(sorted(self.ids)[-20000:]), encoding="utf-8")


async def run_cycle(scraper, analyzer, evaluator, notifier, seen: SeenStore, price_to: Optional[int]) -> None:
    analyzed = 0
    warned_currency = False

    for query in QUERIES:
        items = await asyncio.to_thread(scraper.fetch_newest_items, query, price_to)
        new_items = [i for i in items if i["id"] not in seen]
        log.info("'%s': %d hirdetés, ebből új: %d", query, len(items), len(new_items))

        for item in new_items:
            if item["currency"] != RULES_CURRENCY and not warned_currency:
                log.warning("A hirdetés pénzneme %s, a szabályfájlé %s – ellenőrizd a price_rules.json-t!",
                            item["currency"], RULES_CURRENCY)
                warned_currency = True

            # tartozék / alkatrész árszint, vagy nyilvánvalóan nem profitos -> nincs AI-hívás
            if item["price"] < MIN_PRICE or not evaluator.could_be_profitable(item["price"]):
                seen.add(item["id"])
                continue

            if analyzed >= MAX_ANALYZE_PER_CYCLE:
                break  # a maradék a következő körben kerül sorra (nem jelöljük látottnak)

            try:
                details = await asyncio.to_thread(scraper.fetch_item_details, item)
                description = details.get("description") or item["description"]
                images = details.get("images") or item["images"]

                analysis = await asyncio.to_thread(analyzer.analyze, item["title"], description, images)
                analyzed += 1
                deal = evaluator.evaluate(item["price"], analysis)
                seen.add(item["id"])

                if deal:
                    log.info("DEAL: %s – profit %.0f", item["title"], deal["profit"])
                    await notifier.send_deal(item, deal, analysis)
                else:
                    log.info("Nem deal: %s (%.0f %s)", item["title"], item["price"], item["currency"])
            except Exception:
                log.exception("Hiba a(z) %s hirdetés feldolgozásánál, a következő körben újra próbáljuk.", item["id"])

            await asyncio.sleep(random.uniform(1.0, 3.0))

        seen.save()
        await asyncio.sleep(random.uniform(2.0, 5.0))


async def main() -> None:
    evaluator = DealEvaluator()
    analyzer = ListingAnalyzer()
    notifier = TelegramNotifier()
    scraper = await asyncio.to_thread(VintedScraper, DOMAIN)
    seen = SeenStore()

    price_to = int(PRICE_TO_ENV) if PRICE_TO_ENV else evaluator.max_listing_price()
    await notifier.send_text(f"✅ Vinted deal finder elindult.\nKeresések: {', '.join(QUERIES)}\nÁrplafon: {price_to}")

    while True:
        try:
            await run_cycle(scraper, analyzer, evaluator, notifier, seen, price_to)
        except Exception:
            log.exception("Váratlan hiba a ciklusban.")
        await asyncio.sleep(POLL_SECONDS + random.uniform(0, POLL_SECONDS * 0.25))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Leállítva.")
