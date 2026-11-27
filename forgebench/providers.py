from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Minimal provider interface for optional LLM-backed feature synthesis."""

    name: str

    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Request a structured JSON object from the model."""


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    return json.loads(cleaned)


class OpenAICompatibleProvider(LLMProvider):
    """Works with OpenAI, OpenRouter, Groq, or any OpenAI-compatible chat endpoint."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1/chat/completions",
        timeout_sec: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout_sec = timeout_sec

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return _extract_json_block(content)


class GeminiProvider(LLMProvider):
    """Calls Google Gemini REST API directly via standard library HTTP."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout_sec: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["candidates"][0]["content"]["parts"][0]["text"]
        return _extract_json_block(content)


class OllamaProvider(LLMProvider):
    """Connects to a local Ollama server for fully private/on-prem LLM synthesis."""

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        base_url: str = "http://127.0.0.1:11434/api/chat",
        timeout_sec: int = 120,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout_sec = timeout_sec

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return _extract_json_block(body["message"]["content"])


def build_provider(
    provider_name: str | None = None,
    model: str | None = None,
) -> LLMProvider | None:
    """Resolve an optional LLM provider from explicit CLI args or environment variables."""
    choice = (provider_name or os.getenv("FORGEBENCH_LLM_PROVIDER", "offline")).strip().lower()
    if choice in ("", "offline", "none", "ast"):
        return None

    if choice == "openai":
        key = os.getenv("FORGEBENCH_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OpenAI provider selected but OPENAI_API_KEY / FORGEBENCH_API_KEY is not set.")
        return OpenAICompatibleProvider(
            api_key=key,
            model=model or os.getenv("FORGEBENCH_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("FORGEBENCH_BASE_URL", "https://api.openai.com/v1/chat/completions"),
        )

    if choice == "gemini":
        key = os.getenv("FORGEBENCH_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Gemini provider selected but GEMINI_API_KEY / FORGEBENCH_API_KEY is not set.")
        return GeminiProvider(
            api_key=key,
            model=model or os.getenv("FORGEBENCH_MODEL", "gemini-2.5-flash"),
        )

    if choice == "ollama":
        return OllamaProvider(
            model=model or os.getenv("FORGEBENCH_MODEL", "qwen2.5-coder:7b"),
            base_url=os.getenv("FORGEBENCH_OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
        )

    raise ValueError(f"Unknown LLM provider '{choice}'. Supported: offline, openai, gemini, ollama.")
