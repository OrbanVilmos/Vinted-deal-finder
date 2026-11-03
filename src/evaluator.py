import json
from pathlib import Path
from typing import Dict, Any, Optional

class DealEvaluator:
    def __init__(self, config_path: str = "config/price_rules.json"):
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"A konfigurációs fájl nem található: {config_path}")
            
        with open(path, "r", encoding="utf-8") as f:
            self.rules: Dict[str, Any] = json.load(f)

    @staticmethod
    def calculate_vinted_fees(item_price: float) -> float:
        buyer_protection = 3.5 + (item_price * 0.05)
        shipping_estimate = 14.0
        return round(buyer_protection + shipping_estimate, 2)

    def could_be_profitable(self, listing_price: float) -> bool:
        """Olcsó előszűrő: a legjobb esetben (hibák nélkül) is lehet-e profit?
        Ha nem, nem érdemes az OpenAI-t meghívni."""
        total_cost = listing_price + self.calculate_vinted_fees(listing_price)
        return any(
            float(rule["base_sell_price"]) - total_cost >= rule["min_profit"]
            for rule in self.rules.values()
        )

    def max_listing_price(self) -> int:
        """Felső becslés a Vinted price_to szűrőhöz (díjak nélkül)."""
        return int(max(float(r["base_sell_price"]) - r["min_profit"] for r in self.rules.values()))

    def evaluate(self, listing_price: float, analysis: Any) -> Optional[Dict[str, Any]]:
        
        if analysis.is_scam:
            return None

        model_name = analysis.detected_model
        if not model_name or model_name not in self.rules:
            return None

        rule = self.rules[model_name]
        fees = self.calculate_vinted_fees(listing_price)
        total_cost = round(listing_price + fees, 2)

        market_ceiling = float(rule["base_sell_price"])
        applied_penalties: Dict[str, float] = {}

        for defect in analysis.detected_defects:
            if defect in rule["penalties"]:
                penalty_amount = rule["penalties"][defect]
                market_ceiling -= penalty_amount
                applied_penalties[defect] = penalty_amount

        if analysis.battery_health and analysis.battery_health < 80:
            if "battery_under_80" not in applied_penalties:
                penalty_amount = rule["penalties"].get("battery_under_80", 150)
                market_ceiling -= penalty_amount
                applied_penalties["battery_under_80"] = penalty_amount

        projected_profit = round(market_ceiling - total_cost, 2)

        if projected_profit >= rule["min_profit"]:
            return {
                "model": model_name,
                "listing_price": listing_price,
                "fees": fees,
                "total_cost": total_cost,
                "estimated_sell_price": market_ceiling,
                "profit": projected_profit,
                "applied_penalties": applied_penalties,
                "battery_health": analysis.battery_health
            }

        return None