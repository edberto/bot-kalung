"""LLM client — used ONLY for DO field extraction and IP permit expiry parsing.

PRD Section 8: every other piece of app logic is deterministic. Two providers are
supported: the Anthropic API (recommended) and a local Ollama instance.
"""

from __future__ import annotations

import json
import re
from typing import Any

# PRD Section 13 names Claude Haiku explicitly; extraction is a short, simple,
# high-volume task where Haiku is the right tier.
ANTHROPIC_MODEL = "claude-haiku-4-5"

DO_EXTRACTION_PROMPT = """You are extracting fields from a shipping Booking Confirmation (Delivery Order) document.
Extract the following fields and return them as a JSON object with exactly these keys:
{{
  "booking_number": "string - the number labelled BOOKING NO. or BOOKING NUMBER. It is NOT the APPLICATION NO., rate agreement number, contract number or CS reference number, any of which may sit on the same line",
  "vessel_name": "string - vessel name only, no voyage code",
  "voyage": "string - voyage code only",
  "etd_belawan": "YYYY-MM-DD - ETD at the port of loading (Belawan)",
  "destination_port": "string - port of discharging, city name only",
  "destination_country": "string - country of port of discharging",
  "container_quantity": integer,
  "container_size_raw": "string - full container type as written, e.g. 40' HI-CUBE",
  "empty_pickup_location": "string - empty container pickup location, or null if not found"
}}
Note on layout: these documents use two columns, so the extracted text can place
a value on the line BEFORE its label. A label ending in a colon with nothing
after it takes its value from the preceding line. For example:
    084600048570 APPLICATION NO.:26070303078721
    BOOKING NO. :
means the booking number is 084600048570 and 26070303078721 is the application
number.
Return only valid JSON. No explanation. If a field cannot be found, use null.
--- DOCUMENT TEXT BELOW ---
{raw_text}"""

PERMIT_EXPIRY_PROMPT = """Extract the expiry date from this import permit document.
Return a JSON object: {{ "expiry_date": "YYYY-MM-DD" }}
If you cannot find an expiry date, return: {{ "expiry_date": null }}
Return only valid JSON. No explanation.
--- DOCUMENT TEXT BELOW ---
{permit_text}"""

# Indonesian error messages surfaced in the UI (PRD Section 1.4 / 14).
ERR_AUTH = "Kunci API tidak valid. Periksa kembali kunci API di Pengaturan."
ERR_CONNECTION = "Tidak dapat terhubung. Periksa koneksi internet Anda."
ERR_RATE_LIMIT = "Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi."
ERR_OLLAMA = "Tidak dapat terhubung ke Ollama. Pastikan Ollama berjalan di komputer ini."


class LLMError(Exception):
    """Carries an Indonesian, user-presentable message."""


class LLMClient:
    def __init__(self, provider: str, api_key: str = "",
                 ollama_model: str = "llama3",
                 ollama_url: str = "http://localhost:11434"):
        self.provider = provider
        self.api_key = api_key
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url.rstrip("/")

    @classmethod
    def from_settings(cls, settings) -> "LLMClient":
        return cls(
            provider=settings.get("llm_provider"),
            api_key=settings.get("llm_api_key") or "",
            ollama_model=settings.get("ollama_model") or "llama3",
            ollama_url=settings.get("ollama_url") or "http://localhost:11434",
        )

    # -- transport ------------------------------------------------------

    def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        if self.provider == "ollama":
            return self._complete_ollama(prompt, max_tokens)
        return self._complete_anthropic(prompt, max_tokens)

    def _complete_anthropic(self, prompt: str, max_tokens: int) -> str:
        import anthropic

        if not self.api_key:
            raise LLMError("Kunci API belum diisi. Buka Pengaturan untuk mengisinya.")

        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError(ERR_AUTH) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("Kunci API tidak memiliki izin yang diperlukan.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(ERR_RATE_LIMIT) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(ERR_CONNECTION) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Kesalahan API ({exc.status_code}): {exc.message}") from exc

        return "".join(b.text for b in response.content if b.type == "text")

    def _complete_ollama(self, prompt: str, max_tokens: int) -> str:
        import requests

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
                timeout=120,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise LLMError(ERR_OLLAMA) from exc
        except requests.exceptions.Timeout as exc:
            raise LLMError("Ollama tidak merespons (waktu habis).") from exc
        except requests.exceptions.HTTPError as exc:
            raise LLMError(f"Ollama mengembalikan kesalahan: {exc}") from exc

        return response.json().get("response", "")

    # -- public operations ----------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        """PRD Section 3 Step 2 / Section 11 Tab 2. Never raises."""
        try:
            reply = self.complete("Reply with the single word: OK", max_tokens=16)
        except LLMError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            return False, f"Kesalahan tidak terduga: {exc}"
        if reply.strip():
            return True, "✓ Koneksi berhasil"
        return False, "Model tidak mengembalikan jawaban."

    def extract_do_fields(self, raw_text: str) -> dict[str, Any]:
        """PRD Section 8.2. Raises LLMError; caller falls back to manual entry."""
        reply = self.complete(DO_EXTRACTION_PROMPT.format(raw_text=raw_text))
        return _parse_json(reply)

    def extract_permit_expiry(self, permit_text: str) -> str | None:
        """PRD Section 5. Returns an ISO date string or None."""
        reply = self.complete(
            PERMIT_EXPIRY_PROMPT.format(permit_text=permit_text), max_tokens=200
        )
        return _parse_json(reply).get("expiry_date")


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerate models that wrap JSON in prose or a markdown fence."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError("Jawaban model bukan JSON yang valid.") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise LLMError("Jawaban model bukan JSON yang valid.") from None
    if not isinstance(data, dict):
        raise LLMError("Jawaban model bukan objek JSON.")
    return data
