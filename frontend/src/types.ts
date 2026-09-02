export interface Triage {
  language: string;
  category: string;
  priority: "P1" | "P2" | "P3" | "P4";
  sentiment: string;
  summary_en: string;
  reply_draft: string;
  suggested_queue: string;
  needs_human: boolean;
  confidence: number;
}

export interface TriageResponse {
  request_id: string;
  triage: Triage;
  model: string;
  latency_ms: number;
  source: "ilmu" | "mock";
  policy_flags: string[];
}

export interface TriageRequest {
  message: string;
  channel: string;
  customer_tier: string;
}
