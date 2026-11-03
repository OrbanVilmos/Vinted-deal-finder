import time
import os
from dotenv import load_dotenv

from src.scraper import VintedScraper
from src.analyzer import ListingAnalyzer
from src.evaluator import DealEvaluator

load_dotenv()

# Kizárandó kulcsszavak a kiegészítők kiszűrésére
EXCLUDED_KEYWORDS = [
    "tok", "husa", "case", "cover", "folie", "folia", 
    "cutie", "doboz", "box", "incarcator", "charger", "cablu"
]

def is_potential_candidate(item: dict) -> bool:
    title_lower = item["title"].lower()
    price = item["price"]

    # 1. Kiegészítő kulcsszavak kizárása
    for kw in EXCLUDED_KEYWORDS:
        if kw in title_lower:
            return False

    # 2. Reális ársáv szűrés (350 RON alatt valószínűleg kiegészítő, 1050 felett nincs profit)
    if price < 350 or price > 1050:
        return False

    return True

def run_deal_scanner():
    scraper = VintedScraper(domain="ro")
    analyzer = ListingAnalyzer()
    evaluator = DealEvaluator()

    search_terms = ["iPhone 13 Pro", "iPhone 13 Pro Max"]
    seen_ids = set()

    print("Deal scanner elindítva. Keresés fut...")

    for term in search_terms:
        print(f"\nKeresés: {term}")
        items = scraper.fetch_newest_items(search_text=term, per_page=15)

        for item in items:
            item_id = item["id"]
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            # Szabályalapú szűrés: tokok és irreális árak eldobása
            if not is_potential_candidate(item):
                continue

            print(f"\n[Ígéretes találat] {item['title']} - {item['price']} {item['currency']}")
            print(f"URL: {item['url']}")
            print("AI állapotfelmérés folyamatban...")

            try:
                # Csak a szűrt, ígéretes hirdetéseket küldjük el az AI-nak
                analysis = analyzer.analyze(
                    title=item["title"],
                    description=item.get("description", ""),
                    image_urls=item.get("images", [])
                )

                # Pénzügyi kiértékelés (árak, levonások, profit)
                deal_result = evaluator.evaluate(item["price"], analysis)

                if deal_result:
                    print(">>> JÓ AJÁNLAT TALÁLVA! <<<")
                    print(f"Modell: {deal_result['model']}")
                    print(f"Vételár (díjakkal): {deal_result['total_cost']} RON")
                    print(f"Becsült eladási ár: {deal_result['estimated_sell_price']} RON")
                    print(f"Várható profit: {deal_result['profit']} RON")
                    print(f"Levonások: {deal_result['applied_penalties']}")
                else:
                    print("Nem felelt meg a profitelvárásnak vagy csalás gyanús.")

            except Exception as e:
                print(f"Hiba az elemzés közben: {e}")

if __name__ == "__main__":
    run_deal_scanner()