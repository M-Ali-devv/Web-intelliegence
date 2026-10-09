"""Model backends. Ollama is the default. Gemini runs only when AI_PROVIDER=gemini."""

from __future__ import annotations

import os

import httpx


class LLMProvider:
    name: str
    model: str

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class OllamaProvider(LLMProvider):
    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.name = "ollama"
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")

    def complete(self, system: str, user: str) -> str:
        timeout = float(os.getenv("LLM_TIMEOUT", "120"))
        temperature = float(os.getenv("LLM_TEMPERATURE", "0"))
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "options": {"temperature": temperature},
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
        content = response.json().get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Ollama returned an empty response")
        return content


class GeminiProvider(LLMProvider):
    def __init__(self, model: str | None = None):
        self.name = "gemini"
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    def complete(self, system: str, user: str) -> str:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is missing. Put it in the .env file.")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        return response.text


def get_provider() -> LLMProvider:
    kind = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    if kind == "gemini":
        return GeminiProvider()
    if kind == "ollama":
        return OllamaProvider()
    raise RuntimeError(f"Unknown AI_PROVIDER {kind!r}. Use ollama or gemini.")
