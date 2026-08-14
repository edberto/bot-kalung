"""LLM client — retained only for the Settings connection self-test.

DO/permit extraction was removed with the folder-construction refactor; the app
is otherwise fully deterministic. Two providers are supported: the Anthropic API
and a local Ollama instance.
"""

from __future__ import annotations

ANTHROPIC_MODEL = "claude-haiku-4-5"

# Indonesian error messages surfaced in the UI.
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
        """Settings "Test Connection". Never raises."""
        try:
            reply = self.complete("Reply with the single word: OK", max_tokens=16)
        except LLMError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            return False, f"Kesalahan tidak terduga: {exc}"
        if reply.strip():
            return True, "✓ Koneksi berhasil"
        return False, "Model tidak mengembalikan jawaban."
