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

## Run it (one command)

```bash
cp backend/.env.example backend/.env    # add your ILMU_API_KEY
npm install                             # once
npm run dev                             # starts API :8000 + web :5173 together
```

Open http://localhost:5173. `npm run dev:api` / `npm run dev:web` run either half alone.

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

Every call appends one line to `audit.log.jsonl`. The customer's text is stored as a
truncated SHA-256, not in plaintext — PDPA-friendly by default, still enough to prove what
was classified and why:

```json
{"ts":"2026-09-02T12:39:11+0800","request_id":"9dad2e6b23be","message_sha256":"68830097e098d526",
 "model":"ilmu-v3.1","latency_ms":2449,"priority":"P1","queue":"retention","needs_human":true,
 "confidence":0.42,"policy_flags":["regulator_mentioned","low_confidence"]}
```

## What I'd add next

| Next | Why |
|---|---|
| Redis for rate limits + response cache | the in-memory bucket dies with the process and doesn't survive multiple replicas |
| Idempotency key on `/api/triage` | WhatsApp webhooks retry; we shouldn't pay for the same triage twice |
| Ship the audit JSONL to a warehouse | measure per-language accuracy, then tune the prompt against real traffic |
| Streaming for the draft reply | agents see the first tokens in ~300 ms instead of waiting for the full JSON |
| Per-tenant key + quota | multi-tenant BPO, one deployment |
