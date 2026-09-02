# Deployment handoff

Written for whoever — human or agent — takes this from a demo to a running environment.
Read §1 before you touch anything: this service was built to be demonstrated, and two of
its defaults are wrong for production in ways that fail **silently**.

Repo layout, contracts and design rationale are in [README.md](README.md). This file is
only about running it.

---

## 1. STOP — read this first

### 1.1 The service silently degrades to a fake model if the key is missing

`backend/app/config.py` has this, and it is deliberate for the demo:

```python
@property
def use_mock(self) -> bool:
    return self.ilmu_mock or not self.ilmu_api_key
```

If your secret injection fails — wrong secret name, unmounted volume, typo in the task
definition — the container **starts healthy and serves plausible-looking canned
responses**. No error, no crash, no alert. Every reply begins `[MOCK MODE - ...]`, but a
downstream system consuming JSON will not notice.

**Required before production:**

1. Set `ILMU_MOCK=0` explicitly.
2. Add a fail-fast check at startup. In `lifespan()` in `backend/app/main.py`, before the
   `yield`:
   ```python
   if settings.use_mock and os.getenv("ENVIRONMENT") == "production":
       raise RuntimeError("ILMU_API_KEY missing — refusing to start in mock mode")
   ```
3. Alert on `GET /api/health` returning `"mode": "mock"`. That field exists for exactly
   this purpose — make it a monitored check, not a debugging convenience.

### 1.2 Customer message plaintext is retained by default

`audit_store_content` defaults to `True`, and `compose.yaml` defaults it to `1`. That
means the raw customer message, the summary and the draft reply are all written to SQLite.
It is on for the demo so two runs can be compared side by side in the UI.

**Set `AUDIT_STORE_CONTENT=0` in production.** With it off, the audit row keeps a truncated
SHA-256 of the message and the routing decision — enough to prove what was decided, without
holding personal data. This is a PDPA posture decision, so confirm it with whoever owns
that before flipping it either way.

### 1.3 There is no authentication

`POST /api/triage` is open to anyone who can reach the port. There is a per-IP rate limit
(20/min default) and nothing else. Anyone who finds the endpoint spends the ILMU quota.

**Do not expose this to the internet as-is.** Pick one:

- Behind a VPN / internal load balancer only, or
- An API gateway that enforces auth in front of it (Cloudflare Access, an ALB with OIDC,
  API Gateway with a key), or
- Add auth in the app — an `X-API-Key` dependency for service traffic is ~15 lines.

The gateway option is the fastest and does not require changing this codebase.

### 1.4 `DELETE /api/audit` and `DELETE /api/audit/{id}` exist

They were added so the demo could be reset, and the code says so in a comment. An audit
trail that can be deleted over an unauthenticated HTTP call is not an audit trail. Remove
those two routes, or put them behind the admin role, before this goes anywhere real.

---

## 2. What this service is, operationally

- One process. Python 3.12, FastAPI on uvicorn, single event loop, fully async I/O.
- One outbound dependency: the ILMU API over HTTPS.
- One piece of state: a SQLite file (the audit trail).
- Serves its own frontend as static files from the same origin, so there is no second
  service and nothing to reverse-proxy for the UI.
- Stateless apart from the SQLite file and an in-memory rate-limit table.

Typical request: ~2.0–2.4 s, almost all of it waiting on ILMU.

---

## 3. Configuration reference

Every setting is an environment variable. Names are the uppercase field names from
`backend/app/config.py`; `pydantic-settings` also reads a `.env` file if one is present
(do not ship one — use real secret injection).

| Variable | Default | Production guidance |
|---|---|---|
| `ILMU_API_KEY` | *(empty)* | **Required.** From a secret store, never an image layer. Empty ⇒ mock mode (see §1.1) |
| `ILMU_BASE_URL` | `https://api.ilmu.ai/v1` | Leave as-is unless given a dedicated endpoint |
| `ILMU_MODEL` | `ilmu-v3.1` | Pin an exact model id. Never track a floating alias |
| `ILMU_TIMEOUT_SECONDS` | `25.0` | Keep ≥ 20. Must be below any upstream LB idle timeout |
| `ILMU_MAX_RETRIES` | `2` | 2 is right for a synchronous caller. Raise only behind a queue |
| `ILMU_MOCK` | `false` | Set `0` explicitly (§1.1) |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Only matters if the frontend is served from a different origin. In the single-container topology it is unused — the UI is same-origin |
| `RATE_LIMIT_PER_MINUTE` | `20` | Per IP, per process. See §6 before scaling replicas |
| `AUDIT_DB_PATH` | `/srv/data/audit.db` *(set in Dockerfile)* | Must be on a writable, persistent volume |
| `AUDIT_STORE_CONTENT` | `true` | **Set `0`** (§1.2) |
| `PORT` | `8000` *(set in Dockerfile)* | The container binds `0.0.0.0:$PORT`. Cloud Run sets this for you |

---

## 4. Build and run

### 4.1 Image

Multi-stage. The node stage builds the frontend and is discarded; only the Python runtime
ships. Result is ~246 MB.

```bash
docker build -t ilmu-service-triage:$(git rev-parse --short HEAD) .
```

Tag by commit SHA, not `latest` — the audit trail records which model made each decision,
and you want to be able to correlate that with which build was running.

Runtime facts the image already handles:
- Runs as uid **10001** (`appuser`), not root.
- `HEALTHCHECK` hits `/api/health` on `$PORT`.
- `CMD` binds `0.0.0.0:$PORT`.
- Creates `/srv/data` owned by `appuser`.

### 4.2 Volume permission trap

`AUDIT_DB_PATH` points at `/srv/data`. The image chowns that directory to uid 10001, but
**a volume mounted over it may come back root-owned**, and the process cannot write. It
will fail at startup in `audit.init()`.

- Docker/Compose named volume: inherits the image's ownership. Fine.
- Kubernetes PVC: set `securityContext.fsGroup: 10001`.
- ECS with EFS: set the access point POSIX user to uid/gid 10001.

Verify after deploy:
```bash
docker exec <container> sh -c 'touch /srv/data/.wtest && rm /srv/data/.wtest && echo writable'
```

### 4.3 Compose (single host)

`compose.yaml` maps host `8100` → container `8000`. Host 8000 was already taken on the dev
machine; change the host side freely, leave the container side alone.

```bash
echo "ILMU_API_KEY=sk-..." > .env
echo "AUDIT_STORE_CONTENT=0" >> .env
docker compose up -d --build
```

---

## 5. The upstream constraint that shapes everything

**ILMU PAYG is 60 requests per minute, per model, per organisation — shared across every
API key in the org.** There is currently no token-per-minute limit.

Three consequences, and the deploying agent must internalise all three:

1. **Scaling replicas does not increase throughput.** Ten containers still share 60 RPM.
   Horizontal scaling buys availability, not capacity.
2. **60 RPM is one request per second.** Support traffic is bursty. Any spike above that
   returns 429s straight to the caller today.
3. **Other teams share your quota.** A colleague's batch job can exhaust it.

ILMU returns `x-ratelimit-remaining-requests` on every response and `Retry-After` on a 429.

**Known gap:** the retry loop in `backend/app/ilmu_client.py` uses fixed exponential backoff
(0.4 s, 0.8 s) and **ignores `Retry-After`, with no jitter**. Under real load, synchronised
retries make a 429 storm worse. Fixing this is the highest-value change in the file and it
is roughly five lines. Do it before load, not after.

Also worth exporting `x-ratelimit-remaining-requests` as a gauge — it is the leading
indicator for every capacity incident this service will ever have.

---

## 6. Scaling: two things that break silently at 2+ replicas

Both are single-process assumptions that do not announce themselves when violated.

### 6.1 The rate limiter is in-process

`_hits` in `backend/app/main.py` is a per-process `OrderedDict` (LRU-bounded at 5,000 IPs).
With N replicas behind a load balancer, the effective limit becomes **N × 20/min per IP**,
and it resets whenever a container restarts. Move it to Redis before running more than one
replica, or accept that the limit is advisory.

### 6.2 SQLite is a single-writer database

The audit trail is SQLite in WAL mode. That is correct for one container. **Do not point
two replicas at the same volume** — network filesystems (EFS, NFS, most k8s RWX volumes) do
not implement the locking SQLite needs, and the failure mode is corruption, not an error.

Migration path when you need more than one replica: swap `backend/app/audit.py` for a
Postgres-backed implementation. The schema is plain SQL in `_SCHEMA`, the module is
~150 lines, and the rest of the app only calls `init/write/recent/get/delete/clear/stats`.
It is a contained change.

**Recommendation:** run one replica until there is a measured reason not to. At 60 RPM
upstream and 43 MB of RAM at twelve concurrent requests, a single container is not the
bottleneck and scaling out adds two failure modes for no throughput.

---

## 7. Platform notes

### Cloud Run
Works as-is. It sets `PORT`; the image already honours it. Two caveats:
- **The filesystem is ephemeral.** SQLite on the container filesystem is lost on every new
  instance. Either mount a GCS/Filestore volume or move the audit trail to Postgres first.
- Set `--min-instances=1` if cold starts matter; startup is fast but the first ILMU call
  pays TLS setup.
- Set `--concurrency=40` or so. The default (80) is fine for I/O-bound work but you will
  hit ILMU's 60 RPM long before that matters.

### ECS / Fargate
- Task role + Secrets Manager for `ILMU_API_KEY`; do not use plain `environment` entries.
- EFS access point with POSIX uid/gid 10001 (§4.2), or drop to Postgres.
- Target group health check → `/api/health`, 10 s interval.

### Kubernetes
- `securityContext: { runAsUser: 10001, fsGroup: 10001 }`.
- Liveness and readiness both on `/api/health`. There is no separate readiness signal —
  see §8.
- Do **not** set `replicas > 1` without doing §6 first.
- The container does not need a writable root filesystem, only `/srv/data`. You can set
  `readOnlyRootFilesystem: true` if you also mount `/tmp` — verify, as pip-installed
  packages occasionally want scratch space.

---

## 8. Health, readiness and shutdown

- `GET /api/health` returns `{status, model, mode, stores_content}` and does **not** call
  ILMU. It answers "is this process alive", not "is ILMU reachable". Keep it that way — a
  health check that depends on a third party takes you down when they blip.
- There is **no separate readiness endpoint**. The process is ready as soon as
  `audit.init()` has run, which is before the server accepts connections, so liveness is a
  reasonable stand-in.
- **No graceful shutdown of in-flight work.** On SIGTERM, uvicorn stops accepting new
  connections but in-flight ILMU calls are dropped — the caller gets a connection reset and
  the audit row is never written. With ~2 s requests and rolling deploys this is a real,
  if small, window. `lifespan` can be extended to drain; until then set
  `terminationGracePeriodSeconds: 30` and deploy during quiet periods.

---

## 9. Observability to add (there is none today)

The service currently emits only uvicorn's access log. The audit table is doing double duty
as telemetry, which is convenient and wrong long-term — it answers "what did we decide",
not "why was p99 bad at 3pm".

Minimum viable, in priority order:

1. **Structured JSON logs** with `request_id` as the correlation key. `request_id` is
   already generated per request and returned to the caller and written to the audit row —
   it just is not logged.
2. **Alert on `/api/health` `mode == "mock"`** (§1.1). This is the single highest-value
   alert on the list.
3. **Gauge on `x-ratelimit-remaining-requests`** from ILMU responses (§5).
4. **Counter on 502s from `/api/triage`**, split by cause — upstream failure vs
   off-contract model output. They mean different things and need different responses.
5. **Histogram of `latency_ms`**, which the audit table already records. p50 is ~2.0–2.4 s;
   anything above 5 s means ILMU is degraded, not you.
6. OpenTelemetry span around the ILMU call specifically. That is where 95%+ of wall time is.

---

## 10. Cost model

Measured against the real API, `ilmu-v3.1`:

- 449 prompt tokens, ~150 completion tokens per triage.
- **448 of 449 prompt tokens hit ILMU's cache** — the system prompt is byte-identical on
  every call, and ILMU caches automatically with no markers required.

At published rates (RM 4.00/1M input, RM 0.40/1M cached input, RM 16.00/1M output):

| | |
|---|---|
| Per triage | ~RM 0.0026 |
| Per 1,000 | ~RM 2.60 |
| 10,000/day | ~RM 26/day, ~RM 780/month |
| Without prompt caching | ~RM 4.20 per 1,000 (≈39% more) |

Output tokens dominate the remaining cost. If cost becomes a concern, cap the draft reply
length in the prompt before tuning anything else. Do **not** restructure the system prompt
without checking `prompt_tokens_details.cached_tokens` afterwards — changing its prefix
breaks caching and quietly raises the bill by ~39%.

---

## 11. Runbook

| Symptom | Likely cause | Action |
|---|---|---|
| Replies start `[MOCK MODE - ...]` | `ILMU_API_KEY` not injected | §1.1. Check `/api/health` → `mode` |
| `502 Triage upstream failed (<id>)` | ILMU timeout/5xx/429 after 2 retries | Check ILMU status and `x-ratelimit-remaining-requests`. `<id>` correlates to the audit row |
| `502 Model returned an off-contract response` | Schema validation failed | Model or `response_format` changed. Check `ILMU_MODEL` is still valid via `GET /v1/models` |
| `429 Rate limit exceeded` from *us* | Our own per-IP limiter | Raise `RATE_LIMIT_PER_MINUTE` or fix the caller |
| Container unhealthy on boot, logs show sqlite error | `/srv/data` not writable by uid 10001 | §4.2 |
| `sqlite3.OperationalError: no such column` | Old volume, newer image | `_migrate()` in `audit.py` handles added columns; if you see this, a column was added without being registered in `_ADDED_COLUMNS` |
| Latency jumps to 25 s then 502 | ILMU degraded, hitting the full timeout | Nothing to fix locally. This is the case a queue would absorb |
| Costs up ~40% with no traffic change | Prompt prefix changed, cache no longer hits | Diff `prompts.py`, check `cached_tokens` in a response |

Useful one-liners:

```bash
# Which mode is it actually in?
curl -s https://<host>/api/health

# Recent decisions and rollup stats
curl -s 'https://<host>/api/audit?limit=20'

# Confirm the model id is still served
curl -s https://api.ilmu.ai/v1/models -H "Authorization: Bearer $ILMU_API_KEY"

# Inspect the audit DB inside the container
docker exec <c> python -c "import sqlite3;print(sqlite3.connect('/srv/data/audit.db').execute('select count(*) from triage_audit').fetchone())"
```

---

## 12. Post-deploy verification

Run all of these. The first two are the ones that catch silent misconfiguration.

```bash
HOST=https://<your-host>

# 1. Must report mode "ilmu", NOT "mock", and stores_content false
curl -s $HOST/api/health
# expect: {"status":"ok","model":"ilmu-v3.1","mode":"ilmu","stores_content":false}

# 2. A real triage — source must be "ilmu" and the reply must not say MOCK
curl -s -X POST $HOST/api/triage -H 'Content-Type: application/json' \
  -d '{"message":"Bil saya bulan ni RM320, tapi biasa RM90 je. Tolong semak, kalau tak saya report kat MCMC.","channel":"whatsapp","customer_tier":"standard"}'
# expect: priority P1, suggested_queue "retention", needs_human true,
#         policy_flags ["regulator_mentioned"], source "ilmu"

# 3. The deterministic rule fires regardless of the model
#    (this is the correctness check that matters — it must be P1 even though
#     the message is calm)
curl -s -X POST $HOST/api/triage -H 'Content-Type: application/json' \
  -d '{"message":"Just checking, I may raise this with MCMC later.","channel":"web","customer_tier":"free"}'
# expect: priority P1, needs_human true

# 4. Audit persistence survives a restart
curl -s "$HOST/api/audit?limit=1" | grep -q request_id && echo "audit ok"
# restart the container, then re-run — total must not reset to 0

# 5. The UI is served from the same origin
curl -s -o /dev/null -w '%{http_code}\n' $HOST/     # 200, text/html

# 6. Bad input is rejected cleanly, not with a 500
curl -s -o /dev/null -w '%{http_code}\n' "$HOST/api/audit?limit=-1"   # 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST $HOST/api/triage \
  -H 'Content-Type: application/json' -d '{"message":"x"}'            # 422
```

If check 3 does not return P1, the deterministic policy layer is not running and the whole
compliance argument for this service is void. Treat it as a release blocker.

---

## 13. Ordered checklist

Blocking, in this order:

- [ ] `ILMU_API_KEY` injected from a secret store; `ILMU_MOCK=0`; fail-fast added (§1.1)
- [ ] `AUDIT_STORE_CONTENT=0` confirmed with the data owner (§1.2)
- [ ] Authentication in front of `/api/triage` (§1.3)
- [ ] `DELETE /api/audit*` removed or admin-gated (§1.4)
- [ ] `/srv/data` on a persistent volume, writable by uid 10001 (§4.2)
- [ ] Single replica, or §6 addressed first
- [ ] Alert on `/api/health` `mode == "mock"`
- [ ] Post-deploy checks in §12 all pass, especially check 3

Strongly recommended, next:

- [ ] Honour `Retry-After` and add jitter to the retry loop (§5)
- [ ] Structured logs keyed on `request_id` (§9)
- [ ] `x-ratelimit-remaining-requests` gauge (§9)
- [ ] Image tagged by commit SHA, not `latest` (§4.1)
- [ ] `terminationGracePeriodSeconds: 30` pending real drain support (§8)

Deferred until there is a measured reason:

- [ ] Postgres instead of SQLite (needed only for multi-replica)
- [ ] Redis rate limiter (same trigger)
- [ ] Queue + `202 Accepted` contract (needed when webhooks call this, not before)
