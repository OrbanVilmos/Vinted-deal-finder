import html
import logging
import os
from typing import Any, Dict, Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("notifier")


def format_deal(listing: Dict[str, Any], deal: Dict[str, Any], analysis: Optional[Any] = None) -> str:
    cur = listing.get("currency", "")
    esc = html.escape
    lines = [
        f"🔥 <b>{esc(deal['model'])}</b> – becsült profit: <b>{deal['profit']:.0f} {cur}</b>",
        f"📝 {esc(listing['title'])}",
        "",
        f"💰 Ár: {deal['listing_price']:.0f} {cur} + díjak {deal['fees']:.0f} = <b>{deal['total_cost']:.0f} {cur}</b>",
        f"📈 Becsült eladási ár: {deal['estimated_sell_price']:.0f} {cur}",
    ]
    if analysis is not None and getattr(analysis, "storage_gb", None):
        lines.append(f"💾 Tárhely: {analysis.storage_gb} GB")
    if deal.get("battery_health"):
        lines.append(f"🔋 Akku: {deal['battery_health']}%")
    if deal.get("applied_penalties"):
        pen = ", ".join(f"{k} (−{v:.0f})" for k, v in deal["applied_penalties"].items())
        lines.append(f"🔧 Levonások: {esc(pen)}")
    lines += ["", f'🔗 <a href="{esc(listing["url"], quote=True)}">Megnyitás Vinted-en</a>']
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise ValueError("A TELEGRAM_BOT_TOKEN és a TELEGRAM_CHAT_ID nincs beállítva a .env fájlban!")
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    async def _post(self, method: str, payload: Dict[str, Any]) -> bool:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.api}/{method}", json=payload) as resp:
                    if resp.status == 200:
                        return True
                    log.error("Telegram %s hiba: HTTP %s – %s", method, resp.status, (await resp.text())[:300])
        except Exception as e:
            log.error("Telegram %s kivétel: %s", method, e)
        return False

    async def send_text(self, text: str) -> bool:
        return await self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    async def send_deal(self, listing: Dict[str, Any], deal: Dict[str, Any], analysis: Optional[Any] = None) -> bool:
        text = format_deal(listing, deal, analysis)
        images = listing.get("images") or []
        if images and len(text) <= 1024:
            ok = await self._post("sendPhoto", {
                "chat_id": self.chat_id,
                "photo": images[0],
                "caption": text,
                "parse_mode": "HTML",
            })
            if ok:
                return True
        return await self.send_text(text)