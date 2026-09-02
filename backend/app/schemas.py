from typing import Literal

from pydantic import BaseModel, Field

Language = Literal["ms", "en", "zh", "manglish", "other"]
Category = Literal["billing", "technical", "account", "complaint", "sales", "other"]
Priority = Literal["P1", "P2", "P3", "P4"]
Queue = Literal["billing_ops", "tier1_support", "tier2_engineering", "retention", "sales"]


class TriageRequest(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    channel: Literal["email", "whatsapp", "web", "phone"] = "web"
    customer_tier: Literal["free", "standard", "enterprise"] = "standard"


class Triage(BaseModel):
    """The contract the frontend codes against — stable regardless of model wording."""

    language: Language
    category: Category
    priority: Priority
    sentiment: Literal["angry", "frustrated", "neutral", "positive"]
    summary_en: str
    reply_draft: str          # written back in the customer's own language
    suggested_queue: Queue
    needs_human: bool
    confidence: float = Field(ge=0.0, le=1.0)


class TriageResponse(BaseModel):
    request_id: str
    triage: Triage
    model: str
    latency_ms: int
    source: Literal["ilmu", "mock"]
    policy_flags: list[str] = []
