"""Audit trail in SQLite.

One row per triage decision. The customer's text is stored as a truncated
SHA-256, never in plaintext — enough to prove what was classified and to spot
duplicate messages, without holding personal data (PDPA).

SQLite because the audit trail has to be *queryable* ("show me every P1 the
model wanted to auto-close last week"), and because it needs no extra
infrastructure to deploy. Same schema moves to Postgres when it outgrows one box.
"""

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triage_audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    request_id     TEXT    NOT NULL UNIQUE,
    message_sha256 TEXT    NOT NULL,
    message_chars  INTEGER NOT NULL,
    model          TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    latency_ms     INTEGER NOT NULL,
    language       TEXT,
    category       TEXT,
    priority       TEXT,
    queue          TEXT,
    needs_human    INTEGER,
    confidence     REAL,
    policy_flags   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts       ON triage_audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_priority ON triage_audit(priority, needs_human);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def init(db_path: str) -> None:
    global _conn
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while triage requests are still writing.
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript(_SCHEMA)
    _conn.commit()


def close() -> None:
    if _conn is not None:
        _conn.close()


def _fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]


def write(*, request_id: str, message: str, triage: dict, flags: list[str],
          latency_ms: int, source: str, model: str) -> None:
    assert _conn is not None, "audit.init() must run at startup"
    row = (
        time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        request_id,
        _fingerprint(message),
        len(message),
        model,
        source,
        latency_ms,
        triage.get("language"),
        triage.get("category"),
        triage.get("priority"),
        triage.get("suggested_queue"),
        int(bool(triage.get("needs_human"))),
        triage.get("confidence"),
        ",".join(flags),
    )
    # Writes are short; one lock keeps them serialised without a pool.
    with _lock:
        _conn.execute(
            "INSERT INTO triage_audit (ts, request_id, message_sha256, message_chars,"
            " model, source, latency_ms, language, category, priority, queue,"
            " needs_human, confidence, policy_flags)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
        _conn.commit()


def recent(limit: int = 20) -> list[dict]:
    assert _conn is not None
    cur = _conn.execute(
        "SELECT ts, request_id, message_sha256, model, source, latency_ms, language,"
        " category, priority, queue, needs_human, confidence, policy_flags"
        " FROM triage_audit ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in cur.fetchall()]


def stats() -> dict:
    """The numbers an ops lead actually asks for."""
    assert _conn is not None
    total = _conn.execute("SELECT COUNT(*) FROM triage_audit").fetchone()[0]
    if not total:
        return {"total": 0, "by_priority": {}, "by_language": {}, "human_review_rate": 0.0, "p50_latency_ms": 0}
    by_priority = {r[0]: r[1] for r in _conn.execute(
        "SELECT priority, COUNT(*) FROM triage_audit GROUP BY priority ORDER BY priority")}
    by_language = {r[0]: r[1] for r in _conn.execute(
        "SELECT language, COUNT(*) FROM triage_audit GROUP BY language ORDER BY 2 DESC")}
    human = _conn.execute("SELECT COUNT(*) FROM triage_audit WHERE needs_human=1").fetchone()[0]
    p50 = _conn.execute(
        "SELECT latency_ms FROM triage_audit ORDER BY latency_ms LIMIT 1 OFFSET ?",
        (total // 2,)).fetchone()
    return {
        "total": total,
        "by_priority": by_priority,
        "by_language": by_language,
        "human_review_rate": round(human / total, 3),
        "p50_latency_ms": p50[0] if p50 else 0,
    }
