import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import audit, policy
from .config import get_settings
from .ilmu_client import IlmuClient, IlmuError
from .schemas import Triage, TriageRequest, TriageResponse

settings = get_settings()
_hits: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pooled client for the process — not one per request.
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20)) as http:
        app.state.ilmu = IlmuClient(settings, http)
        yield


app = FastAPI(title="ILMU Service Triage", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _hits[ip]
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
    }


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

    raw, flags = policy.apply(raw, payload.message, payload.customer_tier)

    try:
        validated = Triage.model_validate(raw)
    except ValidationError as exc:
        # The model drifted off-contract. Fail loudly rather than pass junk on.
        raise HTTPException(502, f"Model returned an off-contract response ({request_id})") from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    audit.write(
        settings.audit_log_path,
        request_id=request_id,
        message=payload.message,
        triage=validated.model_dump(),
        flags=flags,
        latency_ms=latency_ms,
        source=source,
        model=settings.ilmu_model,
    )

    return TriageResponse(
        request_id=request_id,
        triage=validated,
        model=settings.ilmu_model,
        latency_ms=latency_ms,
        source=source,
        policy_flags=flags,
    )
