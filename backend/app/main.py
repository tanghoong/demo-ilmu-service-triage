import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import audit, policy
from .config import get_settings
from .ilmu_client import IlmuClient, IlmuError
from .schemas import Triage, TriageRequest, TriageResponse

settings = get_settings()

# Bounded so a stream of unique client IPs cannot grow this without limit.
_MAX_TRACKED_IPS = 5_000
_hits: OrderedDict[str, deque[float]] = OrderedDict()


@asynccontextmanager
async def lifespan(app: FastAPI):
    audit.init(settings.audit_db_path, store_content=settings.audit_store_content)
    # One pooled client for the process — not one per request.
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20)) as http:
        app.state.ilmu = IlmuClient(settings, http)
        yield
    audit.close()


app = FastAPI(title="ILMU Service Triage", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _hits.get(ip)
    if bucket is None:
        bucket = _hits[ip] = deque()
        while len(_hits) > _MAX_TRACKED_IPS:
            _hits.popitem(last=False)  # evict the least recently seen
    _hits.move_to_end(ip)
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_minute:
        raise HTTPException(429, "Rate limit exceeded. Try again in a minute.")
    bucket.append(now)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": settings.ilmu_model,
        "mode": "mock" if settings.use_mock else "ilmu",
        "stores_content": settings.audit_store_content,
    }


@app.get("/api/audit")
async def audit_list(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """The audit trail, queryable. Every decision this service has ever made."""
    return {"stats": audit.stats(), "recent": audit.recent(limit, offset)}


@app.get("/api/audit/{request_id}")
async def audit_get(request_id: str) -> dict:
    record = audit.get(request_id)
    if record is None:
        raise HTTPException(404, f"No audit record for {request_id}")
    return record


@app.delete("/api/audit/{request_id}")
async def audit_delete(request_id: str) -> dict:
    """A real audit log is append-only; this exists so the demo can be reset."""
    if not audit.delete(request_id):
        raise HTTPException(404, f"No audit record for {request_id}")
    return {"deleted": request_id}


@app.delete("/api/audit")
async def audit_clear() -> dict:
    return {"deleted": audit.clear()}


@app.post("/api/triage", response_model=TriageResponse)
async def triage(payload: TriageRequest, request: Request) -> TriageResponse:
    _rate_limit(request)
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    try:
        raw, source = await request.app.state.ilmu.triage(
            payload.message, payload.channel, payload.customer_tier
        )
    except IlmuError as exc:
        raise HTTPException(502, f"Triage upstream failed ({request_id}): {exc}") from exc

    # Validate BEFORE the rules run: policy.apply indexes and compares these
    # fields, so an off-contract response must fail as a 502 here rather than
    # as an unhandled TypeError inside the rules.
    try:
        validated = Triage.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(502, f"Model returned an off-contract response ({request_id})") from exc

    decided, flags = policy.apply(validated.model_dump(), payload.message, payload.customer_tier)
    final = Triage.model_validate(decided)  # the rules only narrow; prove it

    latency_ms = int((time.perf_counter() - started) * 1000)
    audit.write(
        request_id=request_id,
        message=payload.message,
        channel=payload.channel,
        customer_tier=payload.customer_tier,
        triage=final.model_dump(),
        flags=flags,
        latency_ms=latency_ms,
        source=source,
        model=settings.ilmu_model,
    )

    return TriageResponse(
        request_id=request_id,
        triage=final,
        model=settings.ilmu_model,
        latency_ms=latency_ms,
        source=source,
        policy_flags=flags,
    )


# In the container the built frontend is copied next to the app and served from
# the same origin as /api, so the browser needs no CORS and no second port.
# Mounted last so it never shadows an /api route.
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
