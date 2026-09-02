import type { TriageRequest, TriageResponse } from "./types";

/** The only backend this app knows about is our own. No ILMU key in the bundle. */
export async function triage(body: TriageRequest): Promise<TriageResponse> {
  const res = await fetch("/api/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<TriageResponse>;
}

export async function health(): Promise<{ mode: string; model: string }> {
  const res = await fetch("/api/health");
  return res.json();
}
