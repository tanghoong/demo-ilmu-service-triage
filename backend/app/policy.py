"""Business rules that must NOT be delegated to a model.

The model classifies; these deterministic rules decide. This is the part an
auditor or a regulator can read, and it is why the LLM call sits behind our API
instead of in the browser.
"""

import re

# Whole-word matching only. Substring matching is a trap here: "sue" inside
# "issue" would otherwise force every English "I have an issue" to P1.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "regulator_mentioned": ("bnm", "bank negara", "mcmc", "kpdn", "tribunal", "ombudsman"),
    "legal_threat": ("lawyer", "peguam", "sue", "suing", "saman", "legal action", "律师", "起诉"),
    "refund_requested": ("refund", "refunded", "bayar balik", "chargeback", "退款"),
    "pii_present": ("ic number", "nric", "passport", "kad pengenalan"),
}

# CJK has no word boundaries, so those terms are matched as plain substrings.
_CJK = re.compile(r"[一-鿿]")


def _compile(term: str) -> re.Pattern[str]:
    if _CJK.search(term):
        return re.compile(re.escape(term))
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


_PATTERNS = {flag: [_compile(t) for t in terms] for flag, terms in KEYWORDS.items()}


def apply(triage: dict, message: str, customer_tier: str) -> tuple[dict, list[str]]:
    flags: list[str] = [
        flag for flag, patterns in _PATTERNS.items()
        if any(p.search(message) for p in patterns)
    ]

    # Hard escalations — never overridable by the model.
    if {"regulator_mentioned", "legal_threat"} & set(flags):
        triage["priority"] = "P1"
        triage["needs_human"] = True
        triage["suggested_queue"] = "retention"

    if "refund_requested" in flags:
        triage["needs_human"] = True

    if customer_tier == "enterprise" and triage["priority"] in ("P3", "P4"):
        triage["priority"] = "P2"
        flags.append("enterprise_sla_uplift")

    # Low model confidence must reach a human, not a customer.
    if triage["confidence"] < 0.6:
        triage["needs_human"] = True
        flags.append("low_confidence")

    return triage, flags
