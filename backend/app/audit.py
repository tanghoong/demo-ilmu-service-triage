"""Audit trail in SQLite.

One row per triage decision. The customer's text is stored as a truncated
SHA-256, never in plaintext — enough to prove what was classified and to spot
duplicate messages, without holding personal data (PDPA).

The model's *output* (summary and draft reply) is retained only when
AUDIT_STORE_CONTENT is on. It is on for the demo so decisions can be compared
side by side, and off by default in production, where the queue and the flags
are the record that matters.

SQLite because the audit trail has to be queryable ("show me every P1 the model
wanted to auto-close last week"), and because it needs no extra infrastructure.
The same schema moves to Postgres when it outgrows one box.
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
    channel        TEXT,
    customer_tier  TEXT,
    language       TEXT,
    category       TEXT,
    sentiment      TEXT,
    priority       TEXT,
    queue          TEXT,
    needs_human    INTEGER,
    confidence     REAL,
    policy_flags   TEXT,
    summary_en     TEXT,
    reply_draft    TEXT,
    message_text   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts       ON triage_audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_priority ON triage_audit(priority, needs_human);
"""

_COLUMNS = (
    "ts, request_id, message_sha256, message_chars, model, source, latency_ms,"
    " channel, customer_tier, language, category, sentiment, priority, queue,"
    " needs_human, confidence, policy_flags, summary_en, reply_draft, message_text"
)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_store_content = False


def init(db_path: str, store_content: bool = False) -> None:
    global _conn, _store_content
    _store_content = store_content
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    # WAL lets the history view read while triage requests are still writing.
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.executescript(_SCHEMA)
    _migrate(_conn)
    _conn.commit()


# Columns added after the first release. CREATE TABLE IF NOT EXISTS silently
# does nothing on an existing table, so a volume from an older build keeps the
# old shape until we add them explicitly.
_ADDED_COLUMNS = {
    "channel": "TEXT",
    "customer_tier": "TEXT",
    "sentiment": "TEXT",
    "summary_en": "TEXT",
    "reply_draft": "TEXT",
    "message_text": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    present = {r["name"] for r in conn.execute("PRAGMA table_info(triage_audit)")}
    for column, decl in _ADDED_COLUMNS.items():
        if column not in present:
            conn.execute(f"ALTER TABLE triage_audit ADD COLUMN {column} {decl}")


def close() -> None:
    if _conn is not None:
        _conn.close()


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("audit.init() must run at startup")
    return _conn


def _fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["needs_human"] = bool(d["needs_human"])
    d["policy_flags"] = [f for f in (d.get("policy_flags") or "").split(",") if f]
    return d


def write(*, request_id: str, message: str, channel: str, customer_tier: str,
          triage: dict, flags: list[str], latency_ms: int, source: str, model: str) -> None:
    row = (
        time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        request_id,
        _fingerprint(message),
        len(message),
        model,
        source,
        latency_ms,
        channel,
        customer_tier,
        triage["language"],
        triage["category"],
        triage["sentiment"],
        triage["priority"],
        triage["suggested_queue"],
        int(triage["needs_human"]),
        triage["confidence"],
        ",".join(flags),
        triage["summary_en"] if _store_content else None,
        triage["reply_draft"] if _store_content else None,
        message if _store_content else None,
    )
    # Writes are short; one lock keeps them serialised without a pool.
    with _lock:
        db = _db()
        db.execute(
            f"INSERT INTO triage_audit ({_COLUMNS}) VALUES ({','.join('?' * 20)})", row
        )
        db.commit()


def recent(limit: int = 20, offset: int = 0) -> list[dict]:
    cur = _db().execute(
        f"SELECT id, {_COLUMNS} FROM triage_audit ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [_row(r) for r in cur.fetchall()]


def get(request_id: str) -> dict | None:
    cur = _db().execute(
        f"SELECT id, {_COLUMNS} FROM triage_audit WHERE request_id = ?", (request_id,)
    )
    row = cur.fetchone()
    return _row(row) if row else None


def delete(request_id: str) -> bool:
    with _lock:
        db = _db()
        changed = db.execute(
            "DELETE FROM triage_audit WHERE request_id = ?", (request_id,)
        ).rowcount
        db.commit()
    return changed > 0


def clear() -> int:
    with _lock:
        db = _db()
        removed = db.execute("DELETE FROM triage_audit").rowcount
        db.commit()
    return removed


def stats() -> dict:
    db = _db()
    total = db.execute("SELECT COUNT(*) FROM triage_audit").fetchone()[0]
    if not total:
        return {"total": 0, "by_priority": {}, "by_language": {},
                "human_review_rate": 0.0, "p50_latency_ms": 0}
    return {
        "total": total,
        "by_priority": {r[0]: r[1] for r in db.execute(
            "SELECT priority, COUNT(*) FROM triage_audit GROUP BY priority ORDER BY priority")},
        "by_language": {r[0]: r[1] for r in db.execute(
            "SELECT language, COUNT(*) FROM triage_audit GROUP BY language ORDER BY 2 DESC")},
        "human_review_rate": round(
            db.execute("SELECT COUNT(*) FROM triage_audit WHERE needs_human=1").fetchone()[0] / total, 3),
        "p50_latency_ms": db.execute(
            "SELECT latency_ms FROM triage_audit ORDER BY latency_ms LIMIT 1 OFFSET ?",
            (total // 2,)).fetchone()[0],
    }
