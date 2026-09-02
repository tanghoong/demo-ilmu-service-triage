# ilmu-service-triage

Server-side ILMU triage API for multilingual (BM / EN / 中文 / Manglish) customer-support
messages. The **FastAPI backend owns the ILMU key, the prompt, the retries and the audit
log**; the TypeScript frontend only ever calls our own `/api`.

## Why this shape

A support desk in Malaysia gets the same complaint in four languages. The model is good at
*reading* the message; it should not be trusted to *decide* what happens next. So:

- **ILMU classifies** — language, category, sentiment, a one-line English summary for ops,
  and a draft reply in the customer's own language.
- **Our server decides** — deterministic rules in [`policy.py`](backend/app/policy.py)
  force P1 + human review whenever a regulator (BNM / MCMC / KPDN), legal action, or a
  refund is mentioned, and whenever model confidence drops below 0.6.

That split is the whole point: the part a regulator or an auditor needs to read is Python
you can diff, not a prompt.

## Architecture

```
Browser (TS/Vite)  ──POST /api/triage──►  FastAPI          ──►  ILMU /chat/completions
  no key, no                               • key + prompt        (server-side only)
  ILMU hostname                            • timeout 25s, 2 retries w/ backoff
  in the bundle                            • Pydantic contract validation
                                           • deterministic policy rules
                                           • per-IP rate limit
                                           • JSONL audit log (message hashed, not stored)
```

## Run it

**Docker (how it deploys):**

```bash
echo "ILMU_API_KEY=sk-..." > .env
docker compose up -d --build      # one container, one port
```

Open **http://localhost:8100**. The image builds the frontend in a node stage and copies
the static bundle into the Python stage, so the browser gets the UI and the API from the
same origin — no CORS, no second service, nothing to reverse-proxy.

**Local dev (hot reload):**

```bash
cp backend/.env.example backend/.env    # add your ILMU_API_KEY
npm install && npm run dev              # API :8100 + Vite :5173 together
```

`npm run dev:api` / `npm run dev:web` run either half alone. With no key set the service
falls back to a deterministic stub, so the demo never depends on credentials.

With no key set, the service runs a deterministic stub so the demo never depends on
credentials. `GET /api/health` tells you which mode you're in.

## The contract

`POST /api/triage`

```json
{ "message": "Bil saya bulan ni RM320, tapi biasa RM90 je. Tolong semak, kalau tak saya report kat MCMC.",
  "channel": "whatsapp", "customer_tier": "standard" }
```

```json
{ "request_id": "9dad2e6b23be",
  "triage": { "language": "ms", "category": "billing", "priority": "P1",
              "sentiment": "angry", "summary_en": "...", "reply_draft": "...",
              "suggested_queue": "retention", "needs_human": true, "confidence": 0.42 },
  "model": "ilmu-v3.1", "latency_ms": 2449, "source": "ilmu",
  "policy_flags": ["regulator_mentioned", "low_confidence"] }
```

The response shape is enforced twice. First at decode time: the client sends
`response_format: {"type": "json_schema"}` built from the *same* Pydantic model the API
returns, so ILMU masks any token that would violate the schema and the prompt can never
drift from the contract. Then again on the way out, by validating with that model — if a
response still arrives off-contract the caller gets a 502 with a request id, never a
malformed payload.

## Audit trail

Every decision is a row in SQLite (`/srv/data/audit.db`, on a named volume so it survives
restarts). The customer's text is stored as a truncated SHA-256, never in plaintext —
PDPA-friendly by default, still enough to prove what was classified and to spot duplicates.

SQLite because an audit trail has to be *queryable* — "show me every P1 the model wanted to
auto-close last week" is a `WHERE`, not a `grep` over log files — and because it ships with
zero extra infrastructure. The same schema moves to Postgres when it outgrows one box.

`GET /api/audit` returns the trail plus the numbers an ops lead actually asks for:

```json
{"total": 4,
 "by_priority": {"P1": 2, "P2": 1, "P4": 1},
 "by_language": {"ms": 1, "en": 1, "zh": 1, "manglish": 1},
 "human_review_rate": 0.5,
 "p50_latency_ms": 2042}
```

## Deployment footprint

Measured on the running container, not estimated:

| | |
|---|---|
| Image | 246 MB (python:3.12-slim; frontend built in a discarded node stage) |
| RAM, idle | 42 MB |
| RAM, 12 concurrent requests | 43 MB |
| CPU, 12 concurrent requests | under 1% of one core |
| 12 concurrent triages | 2.7 s wall, p50 2.3 s |

The service is I/O-bound — nearly all wall time is the ILMU round trip, and the process is
async, so concurrency costs sockets rather than CPU. **1 vCPU / 1 GB RAM is enough**, and
that leaves ~10x headroom on RAM. The container runs as a non-root user (uid 10001) and has
a `HEALTHCHECK`, so it drops into ECS/Cloud Run/Kubernetes as-is.

## What I'd add next

| Next | Why |
|---|---|
| Redis for rate limits + response cache | the in-memory bucket dies with the process and isn't shared across replicas |
| Idempotency key on `/api/triage` | WhatsApp webhooks retry; we shouldn't pay for the same triage twice |
| Postgres + a warehouse sink for the audit table | measure per-language accuracy, then tune the prompt against real traffic |
| Ground the reply draft in a retrieved KB | it hallucinated support hours in testing — the draft must only assert facts from retrieved context |
| Streaming for the draft reply | agents see the first tokens in ~300 ms instead of waiting 2 s for the full JSON |
| Per-tenant key + quota | multi-tenant BPO, one deployment |
