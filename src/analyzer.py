import os
from typing import List, Optional
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

# Környezeti változók (.env) betöltése
load_dotenv()


class ListingAnalysis(BaseModel):
    """Szigorú kimeneti séma az AI válaszához."""
    detected_model: Optional[str] = Field(
        default=None,
        description="A pontos modellnév a konfigurációból: 'iPhone 13 Pro Max', 'iPhone 13 Pro', 'iPhone 13', vagy None ha más típus."
    )
    storage_gb: Optional[int] = Field(
        default=None,
        description="A telefon tárhelye GB-ban, ha azonosítható (pl. 128, 256)."
    )
    battery_health: Optional[int] = Field(
        default=None,
        description="Akkukapacitás (Battery Health %) ha látható a beállítások fotón vagy szerepel a szövegben."
    )
    detected_defects: List[str] = Field(
        default_factory=list,
        description="Fizikai és szoftveres hibák listája. Lehetséges kulcsok: battery_under_80, screen_scratches, non_original_display, back_glass_damaged, housing_scratches, no_box"
    )
    face_id_functional: bool = Field(
        default=True,
        description="False, ha a szöveg említi, hogy a Face ID hibás vagy nem működik."
    )
    is_scam: bool = Field(
        default=False,
        description="True, ha a hirdetés átverés gyanús: lopott/katalógus fotók, külső platformra terelés (WhatsApp/email), irreális ajánlat."
    )
    scam_reason: Optional[str] = Field(
        default=None,
        description="Rövid magyarázat magyarul, ha a hirdetés scam gyanús."
    )


class ListingAnalyzer:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Az OPENAI_API_KEY nincs beállítva a .env fájlban!")
        self.client = OpenAI(api_key=api_key)

    def analyze(self, title: str, description: str, image_urls: List[str]) -> ListingAnalysis:
        """
        Multimodális elemzést futtat: feldolgozza a szöveges leírást és a csatolt képeket.
        """
        # Szöveges prompt felépítése
        user_content = [
            {
                "type": "text",
                "text": (
                    f"Hirdetés címe: {title}\n"
                    f"Leírás: {description}\n\n"
                    "Elemezd a fenti hirdetést és a csatolt fényképeket hardveres állapotfelmérés szempontjából.\n"
                    "Szabályok:\n"
                    "1. Ne próbálj piaci árat tippelni!\n"
                    "2. Csak a megadott lehetséges hibakulcsokat használd a detected_defects tömbben.\n"
                    "3. Figyeld az utángyártott kijelző jeleit (vastag kávák, hiányzó True Tone, Apple hibaüzenet a képen).\n"
                    "4. Ha az akku 80% alatti vagy szervizt ír, vedd fel a 'battery_under_80' hibát.\n"
                    "5. Ha gyanúsan külső oldalra vagy privát chatre akar terelni, jelöld scam-nek."
                )
            }
        ]

        # Képek csatolása (költséghatékonyság miatt legfeljebb az első 4 kép)
        for url in image_urls[:4]:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": url,
                    "detail": "low"  # Alacsony felbontás elegendő a hibákhoz és minimális tokenköltséget generál
                }
            })

        response = self.client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tapasztalt okostelefon hardver-diagnosztikai és csalásszűrő szakértő vagy. "
                        "A feladatod a beérkező használt telefon hirdetések szigorú, kritikus vizsgálata."
                    )
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            response_format=ListingAnalysis,
            temperature=0.1
        )

        return response.choices[0].message.parsed