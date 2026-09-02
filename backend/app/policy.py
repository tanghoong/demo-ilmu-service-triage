"""Business rules that must NOT be delegated to a model.

The model classifies; these deterministic rules decide. This is the part an
auditor or a regulator can read, and it is why the LLM call sits behind our API
instead of in the browser.
"""

KEYWORDS = {
    "regulator_mentioned": ("bnm", "bank negara", "mcmc", "kpdn", "tribunal", "ombudsman"),
    "legal_threat": ("lawyer", "peguam", "sue", "saman", "legal action", "律师"),
    "refund_requested": ("refund", "bayar balik", "chargeback", "退款"),
    "pii_present": ("ic number", "nric", "passport", "kad pengenalan"),
}


def apply(triage: dict, message: str, customer_tier: str) -> tuple[dict, list[str]]:
    flags: list[str] = []
    lowered = message.lower()

    for flag, words in KEYWORDS.items():
        if any(w in lowered for w in words):
            flags.append(flag)

    # Hard escalations — never overridable by the model.
    if {"regulator_mentioned", "legal_threat"} & set(flags):
        triage["priority"] = "P1"
        triage["needs_human"] = True
        triage["suggested_queue"] = "retention"

    if "refund_requested" in flags:
        triage["needs_human"] = True

    if customer_tier == "enterprise" and triage.get("priority") in ("P3", "P4"):
        triage["priority"] = "P2"
        flags.append("enterprise_sla_uplift")

    # Low model confidence must reach a human, not a customer.
    if float(triage.get("confidence", 0)) < 0.6:
        triage["needs_human"] = True
        flags.append("low_confidence")

    return triage, flags
