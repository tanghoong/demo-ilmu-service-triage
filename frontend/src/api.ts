import type { AuditPage, TriageRequest, TriageResponse } from "./types";

/** The only backend this app knows about is our own. No ILMU key in the bundle. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await errorText(res));
  return res.json() as Promise<T>;
}

/** FastAPI returns `detail` as a string for HTTPException and a list for 422. */
async function errorText(res: Response): Promise<string> {
  try {
    const { detail } = await res.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => `${d.loc?.slice(1).join(".") ?? "input"}: ${d.msg}`).join("; ");
    }
  } catch {
    /* fall through to the status line */
  }
  return `Request failed (${res.status} ${res.statusText})`;
}

export const triage = (body: TriageRequest) =>
  call<TriageResponse>("/api/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const health = () =>
  call<{ mode: string; model: string; stores_content: boolean }>("/api/health");

export const auditPage = (limit = 25) => call<AuditPage>(`/api/audit?limit=${limit}`);

export const auditDelete = (requestId: string) =>
  call<{ deleted: string }>(`/api/audit/${requestId}`, { method: "DELETE" });

export const auditClear = () => call<{ deleted: number }>("/api/audit", { method: "DELETE" });
