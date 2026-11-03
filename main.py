import os
import sys
import time
from dotenv import load_dotenv

from src.scraper import VintedScraper
from src.analyzer import ListingAnalyzer
from src.evaluator import DealEvaluator
from src.notifier import TelegramNotifier

# Környezeti változók (.env) betöltése
load_dotenv()

# Kizárandó szavak a címből (tokok, fóliák, dobozok, kábelek)
EXCLUDED_KEYWORDS = [
    "tok", "husa", "case", "cover", "coque", "etui", "funda",
    "folie", "folia", "szklo", "szkła", "glass", "screen protector",
    "cutie", "doboz", "box", "boite", "caja",
    "incarcator", "charger", "cablu", "cable", "kabel",
    "camera lens", "lentila"
]

def is_hardware_candidate(item: dict) -> bool:
    """
    Gyorsszűrő: eldobja a kiegészítőket és az irreális árú hirdetéseket,
    így nem pazarolunk OpenAI API tokent feleslegesen.
    """
    title_lower = item.get("title", "").lower()
    price = item.get("price", 0.0)

    # 1. Kiegészítő kulcsszavak azonnali kizárása
    for kw in EXCLUDED_KEYWORDS:
        if kw in title_lower:
            return False

    # 2. Ársáv szűrő:
    # 350 RON alatt telefon nem létezik (biztosan tok, alkatrész vagy fólia)
    # 1050 RON felett pedig nem marad meg a kitűzött profitmargin
    if price < 350.0 or price > 1050.0:
        return False

    return True


def run_bot():
    print("========================================")
    print("  Vinted Deal Finder & AI Fraud Shield  ")
    print("========================================")

    scraper = VintedScraper(domain="ro")
    analyzer = ListingAnalyzer()
    evaluator = DealEvaluator(config_path="config/price_rules.json")
    try:
        notifier = TelegramNotifier()
    except Exception:
        notifier = None

    search_targets = [
        "iPhone 13 Pro Max",
        "iPhone 13 Pro",
        "iPhone 13"
    ]

    seen_item_ids = set()

    print("[SYSTEM] Figyelő modul aktív. Keresések indítása...")

    for target in search_targets:
        print(f"\n[SCAN] Keresés: '{target}'...")
        items = scraper.fetch_newest_items(search_text=target, per_page=15)

        for item in items:
            item_id = item["id"]
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)

            # Szabályalapú előszűrés
            if not is_hardware_candidate(item):
                continue

            print(f"\n>>> [ÍGÉRETES JELÖLT] {item['title']} - {item['price']} {item['currency']}")
            print(f"    Link: {item['url']}")
            print("    [AI] Képek és állapot vizsgálata folyamatban...")

            try:
                # 1. AI multimodális elemzés (Képek + leírás)
                analysis = analyzer.analyze(
                    title=item["title"],
                    description=item.get("description", ""),
                    image_urls=item.get("images", [])
                )

                if analysis.is_scam:
                    print(f"    [AI FIGYELMEZTETÉS] Átverés gyanú! Ok: {analysis.scam_reason}")
                    continue

                # 2. Pénzügyi döntési logika
                deal = evaluator.evaluate(item["price"], analysis)
                if deal:
                    print(f"    [PROFITÁBILIS DEAL] Várható haszon: +{deal['profit']} RON!")
                    print(f"    Összes költség: {deal['total_cost']} RON | Eladási cél: {deal['estimated_sell_price']} RON")
                    
                    # 3. Értesítés küldése Telegramra, ha be van állítva
                    if notifier:
                        notifier.send_deal_alert(deal, item["url"], item.get("images", []))
                    else:
                        print("    [TELEGRAM] Értesítő nincs konfigurálva, csak terminál kiírás.")

            except Exception as err:
                print(f"    [HIBA] Feldolgozási hiba ennél a terméknél: {err}")

        # Udvariassági szünet a keresési kifejezések között
        time.sleep(2)


if __name__ == "__main__":
    run_bot()