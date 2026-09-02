import asyncio
import json
import re

import httpx

from .config import Settings
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schemas import Triage

# Non-greedy from the first "{" so trailing prose after the object cannot be
# swallowed into an invalid slice.
_JSON_BLOCK = re.compile(r"\{.*?\}(?=\s*$)", re.DOTALL)

# Keys that describe a value for humans or add constraints the API's strict
# schema validator rejects. Pydantic still enforces the real bounds on the way
# out, so dropping them here costs nothing.
_STRIP_KEYS = ("title", "description", "minimum", "maximum", "default")


def _wire_schema(schema: dict) -> dict:
    """Strip annotations the strict validator rejects, at every level."""
    cleaned = {k: v for k, v in schema.items() if k not in _STRIP_KEYS}
    if cleaned.get("type") == "object":
        cleaned["additionalProperties"] = False
        cleaned["properties"] = {
            name: _wire_schema(sub) for name, sub in cleaned.get("properties", {}).items()
        }
    for container in ("$defs", "definitions"):
        if container in cleaned:
            cleaned[container] = {k: _wire_schema(v) for k, v in cleaned[container].items()}
    if "items" in cleaned:
        cleaned["items"] = _wire_schema(cleaned["items"])
    return cleaned


# Grammar-constrained decoding: ILMU masks any token that would violate this
# schema at each decode step, so the model cannot emit an off-contract object.
# Generated from the same Pydantic model the API returns, so the prompt and the
# response contract can never drift apart.
TRIAGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "triage",
        "strict": True,
        "schema": _wire_schema(Triage.model_json_schema()),
    },
}


class IlmuError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Belt and braces: json_schema mode should make this unnecessary."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text.strip())
        if not match:
            raise IlmuError("ILMU returned no parsable JSON object") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise IlmuError(f"ILMU returned malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise IlmuError(f"ILMU returned {type(parsed).__name__}, expected a JSON object")
    return parsed


class IlmuClient:
    """Thin, OpenAI-compatible wrapper around the ILMU chat endpoint.

    Owns: auth, timeout, bounded retry with backoff, schema-constrained decoding
    and response parsing. Nothing in here is reachable from the browser.
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
            "response_format": TRIAGE_RESPONSE_FORMAT,
            "messages": [
                # The system prompt is identical on every call, so ILMU's prompt
                # cache serves it: watch `cached_tokens` climb across requests.
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
                return _extract_json(_content_of(resp)), "ilmu"
            except httpx.HTTPStatusError as exc:  # 4xx: retrying will not help
                raise IlmuError(f"ILMU rejected the request: {exc.response.status_code}") from exc
            except (
                httpx.TimeoutException, httpx.TransportError, IlmuError,
                KeyError, IndexError, TypeError, ValueError,  # malformed envelope
            ) as exc:
                last_error = exc
                if attempt < self._s.ilmu_max_retries:
                    await asyncio.sleep(0.4 * (2**attempt))  # 0.4s, 0.8s
                continue

        raise IlmuError(f"ILMU unavailable after {self._s.ilmu_max_retries + 1} attempts: {last_error}")


def _content_of(resp: httpx.Response) -> str:
    """A 200 is not a promise of a well-formed envelope. Fail as a retryable error."""
    body = resp.json()  # ValueError on non-JSON -> caught as retryable above
    choices = body.get("choices") or []
    if not choices:
        raise IlmuError("ILMU returned no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise IlmuError("ILMU returned no message content")
    return content


def _mock_triage(message: str) -> dict:
    """Deterministic stand-in so the demo runs with no key and no network."""
    lowered = message.lower()
    words = set(re.findall(r"[a-z]+", lowered))
    zh = bool(re.search(r"[一-鿿]", message))
    ms = bool(words & {"saya", "tidak", "boleh", "tolong", "bil", "akaun", "kalau"})
    manglish = bool(words & {"lah", "lor", "ah", "cannot", "wah"})
    lang = "zh" if zh else "ms" if ms else "manglish" if manglish else "en"
    angry = bool(words & {"refund", "lawyer", "bnm", "mcmc", "terrible", "scam"})
    billing = bool(words & {"bill", "charge", "charged", "bil", "refund", "invoice"}) or "扣款" in message
    return {
        "language": lang,
        "category": "billing" if billing else "technical",
        "priority": "P1" if angry else "P3",
        "sentiment": "angry" if angry else "neutral",
        "summary_en": f"[MOCK] Customer message about: {message.strip()[:90]}",
        "reply_draft": "[MOCK MODE - set ILMU_API_KEY to call the real model] "
                       "Thanks for reaching out. We're checking your account now and will revert shortly.",
        "suggested_queue": "billing_ops" if angry else "tier1_support",
        "needs_human": angry,
        "confidence": 0.42,
    }
