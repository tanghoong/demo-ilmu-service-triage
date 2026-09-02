export interface Triage {
  language: string;
  category: string;
  sentiment: string;
  priority: "P1" | "P2" | "P3" | "P4";
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

/** One row of the server-side audit trail. */
export interface AuditRecord {
  id: number;
  ts: string;
  request_id: string;
  message_sha256: string;
  message_chars: number;
  model: string;
  source: string;
  latency_ms: number;
  channel: string | null;
  customer_tier: string | null;
  language: string | null;
  category: string | null;
  sentiment: string | null;
  priority: string | null;
  queue: string | null;
  needs_human: boolean;
  confidence: number | null;
  policy_flags: string[];
  summary_en: string | null;
  reply_draft: string | null;
  message_text: string | null;
}

export interface AuditStats {
  total: number;
  by_priority: Record<string, number>;
  by_language: Record<string, number>;
  human_review_rate: number;
  p50_latency_ms: number;
}

export interface AuditPage {
  stats: AuditStats;
  recent: AuditRecord[];
}
