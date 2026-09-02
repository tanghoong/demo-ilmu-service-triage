import hashlib
import json
import time
from pathlib import Path


def _fingerprint(message: str) -> str:
    """Store a hash, not the customer's words. PDPA-friendly by default."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]


def write(path: str, *, request_id: str, message: str, triage: dict,
          flags: list[str], latency_ms: int, source: str, model: str) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_id": request_id,
        "message_sha256": _fingerprint(message),
        "message_chars": len(message),
        "model": model,
        "source": source,
        "latency_ms": latency_ms,
        "language": triage.get("language"),
        "category": triage.get("category"),
        "priority": triage.get("priority"),
        "queue": triage.get("suggested_queue"),
        "needs_human": triage.get("needs_human"),
        "confidence": triage.get("confidence"),
        "policy_flags": flags,
    }
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
