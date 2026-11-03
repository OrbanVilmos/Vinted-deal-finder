import os
import time
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import requests

load_dotenv()


class ListingAnalysis(BaseModel):
    detected_model: Optional[str] = Field(
        default=None,
        description="A pontos modellnév: 'iPhone 13 Pro Max', 'iPhone 13 Pro', 'iPhone 13', vagy None ha más típus."
    )
    storage_gb: Optional[int] = Field(
        default=None,
        description="A telefon tárhelye GB-ban, ha azonosítható (pl. 128, 256, 512)."
    )
    battery_health: Optional[int] = Field(
        default=None,
        description="Akkukapacitás százalékban (pl. 78, 85, 92), ha látható képen vagy szerepel a leírásban."
    )
    detected_defects: List[str] = Field(
        default_factory=list,
        description="Hibák listája. Érvényes kulcsok: battery_under_80, screen_scratches, non_original_display, back_glass_damaged, housing_scratches, no_box"
    )
    face_id_functional: bool = Field(
        default=True,
        description="False, ha a Face ID hibás vagy nem működik."
    )
    is_scam: bool = Field(
        default=False,
        description="True, ha kamu hirdetés, lopott fotók, kamu ár, vagy külső platformra (WhatsApp/külső link) akar terelni."
    )
    scam_reason: Optional[str] = Field(
        default=None,
        description="Rövid magyarázat magyarul, ha a hirdetés átverés gyanús."
    )


class ListingAnalyzer:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("A GEMINI_API_KEY nincs beállítva a .env fájlban!")
        self.client = genai.Client(api_key=api_key)

    def _download_image(self, url: str) -> Optional[types.Part]:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
                return types.Part.from_bytes(data=resp.content, mime_type=content_type)
        except Exception:
            pass
        return None

    def analyze(self, title: str, description: str, image_urls: List[str]) -> ListingAnalysis:
        prompt_text = (
            f"Hirdetés címe: {title}\n"
            f"Leírás: {description}\n\n"
            "Elemezd a hirdetést és a fotókat okostelefon felújítási és viszonteladási szempontból.\n"
            "Szabályok:\n"
            "1. Árat ne becsülj!\n"
            "2. A detected_defects tömbbe KIZÁRÓLAG ezeket a kulcsokat teheted: "
            "['battery_under_80', 'screen_scratches', 'non_original_display', 'back_glass_damaged', 'housing_scratches', 'no_box']\n"
            "3. Figyeld az utángyártott kijelző jeleit (vastag kávák, True Tone hiánya, beállítások hibaüzenet).\n"
            "4. Ha az akku 80% alatti vagy szervizt jelez, vedd fel a 'battery_under_80' hibát.\n"
            "5. CSALÁSSZŰRÉS (is_scam=True és scam_reason kötelező):\n"
            "   - Megtagadja a Vinted fizetést/szállítást (pl. 'do not ship with buy now', 'nu trimit prin', 'nie wysyłam przez kup teraz', 'nem postázom').\n"
            "   - Külső fizetést kér (Revolut, utalás, WhatsApp).\n"
            "   - Az ár feltűnően túl szép, hogy igaz legyen gyanús indoklással."
        )

        contents = [prompt_text]
        for url in image_urls[:3]:
            img_part = self._download_image(url)
            if img_part:
                contents.append(img_part)

        # Garantáltan elérhető, nem túlterhelt modellek
        candidate_models = [
            "gemini-flash-lite-latest",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash"
        ]

        last_error = None
        for model_name in candidate_models:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ListingAnalysis,
                        temperature=0.1
                    ),
                )
                return ListingAnalysis.model_validate_json(response.text)
            except Exception as err:
                err_str = str(err)
                if "503" in err_str or "UNAVAILABLE" in err_str or "404" in err_str:
                    print(f"    [AI] {model_name} nem elérhető / terhelt. Váltás a következő modellre...")
                    last_error = err
                    time.sleep(1)
                    continue
                raise err

        raise RuntimeError(f"Egyik modell sem válaszolt: {last_error}")