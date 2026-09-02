import asyncio
import json
import re

import httpx

from .config import Settings
from .prompts import SYSTEM_PROMPT, build_user_prompt

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class IlmuError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or a code fence. Be tolerant, once."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        raise IlmuError("ILMU returned no parsable JSON object")
    return json.loads(match.group(0))


class IlmuClient:
    """Thin, OpenAI-compatible wrapper around the ILMU chat endpoint.

    Owns: auth, timeout, bounded retry with backoff, and response parsing.
    Nothing in here is reachable from the browser.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._s = settings
        self._http = client

    async def triage(self, message: str, channel: str, customer_tier: str) -> tuple[dict, str]:
        if self._s.use_mock:
            return _mock_triage(message), "mock"

        payload = {
            "model": self._s.ilmu_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(message, channel, customer_tier)},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._s.ilmu_api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self._s.ilmu_max_retries + 1):
            try:
                resp = await self._http.post(
                    f"{self._s.ilmu_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self._s.ilmu_timeout_seconds,
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise IlmuError(f"retryable upstream status {resp.status_code}")
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return _extract_json(content), "ilmu"
            except (httpx.TimeoutException, httpx.TransportError, IlmuError, KeyError) as exc:
                last_error = exc
                if attempt < self._s.ilmu_max_retries:
                    await asyncio.sleep(0.4 * (2**attempt))  # 0.4s, 0.8s
                continue
            except httpx.HTTPStatusError as exc:  # 4xx: retrying will not help
                raise IlmuError(f"ILMU rejected the request: {exc.response.status_code}") from exc

        raise IlmuError(f"ILMU unavailable after {self._s.ilmu_max_retries + 1} attempts: {last_error}")


def _mock_triage(message: str) -> dict:
    """Deterministic stand-in so the demo runs with no key and no network."""
    lowered = message.lower()
    zh = any("一" <= ch <= "鿿" for ch in message)
    ms = any(w in lowered for w in ("saya", "tidak", "boleh", "tolong", "bil", "akaun"))
    lang = "zh" if zh else "ms" if ms else "manglish" if "lah" in lowered or "cannot" in lowered else "en"
    angry = any(w in lowered for w in ("refund", "lawyer", "bnm", "mcmc", "terrible", "scam", "投诉"))
    return {
        "language": lang,
        "category": "billing" if any(w in lowered for w in ("bill", "charge", "bil", "refund")) else "technical",
        "priority": "P1" if angry else "P3",
        "sentiment": "angry" if angry else "neutral",
        "summary_en": f"[MOCK] Customer message about: {message.strip()[:90]}",
        "reply_draft": "[MOCK MODE — set ILMU_API_KEY to call the real model] "
                       "Thanks for reaching out. We're checking your account now and will revert shortly.",
        "suggested_queue": "billing_ops" if angry else "tier1_support",
        "needs_human": angry,
        "confidence": 0.42,
    }
