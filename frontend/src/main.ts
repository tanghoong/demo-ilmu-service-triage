import { auditClear, auditDelete, auditPage, health, triage } from "./api";
import type { AuditRecord, AuditStats, TriageResponse } from "./types";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const message = $<HTMLTextAreaElement>("message");
const channel = $<HTMLSelectElement>("channel");
const tier = $<HTMLSelectElement>("tier");
const submit = $<HTMLButtonElement>("submit");
const result = $<HTMLElement>("result");
const historyEl = $<HTMLElement>("history");
const compareEl = $<HTMLElement>("compare");
const statsEl = $<HTMLElement>("stats");
const pagerEl = $<HTMLElement>("pager");

const PAGE_SIZE = 8;

let records: AuditRecord[] = [];
let total = 0;
let page = 0;
/** Kept across pages so two runs can be compared even when they are not adjacent. */
const pinned = new Map<string, AuditRecord>();

const SAMPLES: Array<[string, string]> = [
  ["BM · billing", "Bil saya bulan ni RM320, tapi biasa RM90 je. Tolong semak, kalau tak saya report kat MCMC."],
  ["Manglish · down", "Wah your app cannot open since morning lah, I got client meeting how ah?"],
  ["中文 · 退款", "我上个月已经取消订阅了，为什么还继续扣款？我要退款。"],
  ["EN · info", "Hi, what are your support hours over the Merdeka weekend?"],
];

$("samples").innerHTML = SAMPLES.map(
  ([label], i) => `<button class="ghost" data-i="${i}">${label}</button>`,
).join("");

$("samples").addEventListener("click", (e) => {
  const i = (e.target as HTMLElement).dataset.i;
  if (i !== undefined) message.value = SAMPLES[Number(i)][1];
});

health()
  .then((h) => ($("mode").textContent = `${h.mode} · ${h.model}`))
  .catch(() => ($("mode").textContent = "backend offline"));

submit.addEventListener("click", async () => {
  if (message.value.trim().length < 3) return;
  submit.disabled = true;
  submit.textContent = "…";
  result.innerHTML = `<p class="hint">Calling backend…</p>`;

  try {
    renderResult(await triage({
      message: message.value,
      channel: channel.value,
      customer_tier: tier.value,
    }));
    page = 0; // the new run is on the first page
    await loadHistory();
  } catch (err) {
    result.innerHTML = `<p class="error-inline">${esc((err as Error).message)}</p>`;
  } finally {
    submit.disabled = false;
    submit.textContent = "Triage";
  }
});

$("refresh").addEventListener("click", () => void loadHistory());

$("clear").addEventListener("click", async () => {
  if (!confirm("Delete every audit record?")) return;
  await auditClear();
  pinned.clear();
  page = 0;
  await loadHistory();
});

historyEl.addEventListener("click", async (e) => {
  const el = e.target as HTMLElement;

  const del = el.closest<HTMLElement>("[data-delete]");
  if (del) {
    await auditDelete(del.dataset.delete!);
    pinned.delete(del.dataset.delete!);
    await loadHistory();
    return;
  }

  const row = el.closest<HTMLElement>("[data-pick]");
  if (!row) return;
  const id = row.dataset.pick!;
  if (pinned.has(id)) {
    pinned.delete(id);
  } else {
    if (pinned.size >= 2) pinned.delete([...pinned.keys()][0]); // keep the two most recent picks
    pinned.set(id, records.find((r) => r.request_id === id)!);
  }
  drawHistory();
});

pagerEl.addEventListener("click", (e) => {
  const step = (e.target as HTMLElement).dataset.page;
  if (!step) return;
  page = Math.max(0, page + Number(step));
  void loadHistory();
});

function renderResult(r: TriageResponse): void {
  const t = r.triage;
  result.innerHTML = `
    ${pills(t.priority, t.language, t.category, t.sentiment, t.suggested_queue, t.needs_human)}
    <h3>Summary (ops, EN)</h3>
    <p>${esc(t.summary_en)}</p>
    <h3>Draft reply (customer's language)</h3>
    <p class="reply">${esc(t.reply_draft)}</p>
    <h3>Server-side policy flags</h3>
    <div class="pills">${flagPills(r.policy_flags)}</div>
    <div class="meta">
      <code>${esc(r.request_id)}</code> · ${r.latency_ms} ms ·
      ${esc(r.source)} · ${esc(r.model)} · confidence ${t.confidence.toFixed(2)}
    </div>`;
}

async function loadHistory(): Promise<void> {
  try {
    const res = await auditPage(PAGE_SIZE, page * PAGE_SIZE);
    // A delete can empty the last page; step back rather than showing nothing.
    if (!res.recent.length && page > 0) {
      page -= 1;
      return loadHistory();
    }
    records = res.recent;
    total = res.stats.total;
    renderStats(res.stats);
    drawHistory();
  } catch (err) {
    historyEl.innerHTML = `<p class="error-inline">${esc((err as Error).message)}</p>`;
  }
}

function renderStats(s: AuditStats): void {
  $("history-count").textContent = s.total ? String(s.total) : "";
  if (!s.total) {
    statsEl.innerHTML = "";
    return;
  }
  const tile = (label: string, value: string) =>
    `<span class="tile"><span class="tile-v">${esc(value)}</span><span class="tile-l">${esc(label)}</span></span>`;
  const pairs = (o: Record<string, number>) =>
    Object.entries(o).map(([k, v]) => `${k} ${v}`).join(" · ");

  statsEl.innerHTML =
    tile("need human", `${Math.round(s.human_review_rate * 100)}%`) +
    tile("p50", `${s.p50_latency_ms}ms`) +
    tile("priority", pairs(s.by_priority)) +
    tile("lang", pairs(s.by_language));
}

function drawHistory(): void {
  if (!records.length) {
    historyEl.innerHTML = `<p class="hint">Nothing triaged yet.</p>`;
    pagerEl.innerHTML = "";
    compareEl.innerHTML = "";
    return;
  }
  historyEl.innerHTML = `<div class="table-scroll"><table class="hist">
    <thead><tr>
      <th></th><th>Time</th><th>Message</th><th>Lang</th><th>Pri</th><th>Queue</th>
      <th>Tier</th><th>Human</th><th>Conf</th><th>Flags</th><th>ms</th><th></th>
    </tr></thead>
    <tbody>${records.map(rowHtml).join("")}</tbody>
  </table></div>`;
  drawPager();
  drawCompare();
}

function drawPager(): void {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = page * PAGE_SIZE + 1;
  const to = Math.min(total, from + records.length - 1);
  pagerEl.innerHTML = `
    <button class="ghost" data-page="-1" ${page === 0 ? "disabled" : ""}>← Prev</button>
    <button class="ghost" data-page="1" ${page >= pages - 1 ? "disabled" : ""}>Next →</button>
    <span>${from}–${to} of ${total} · page ${page + 1}/${pages}</span>
    <span class="spacer"></span>
    <span>${pinned.size}/2 picked for comparison</span>`;
}

function rowHtml(r: AuditRecord): string {
  const on = pinned.has(r.request_id);
  const preview = r.message_text ?? `sha ${r.message_sha256}`;
  return `<tr data-pick="${esc(r.request_id)}" class="${on ? "picked" : ""}">
    <td><input type="checkbox" ${on ? "checked" : ""} tabindex="-1" /></td>
    <td class="mono">${esc(r.ts.slice(11, 19))}</td>
    <td class="msg" title="${esc(preview)}">${esc(preview)}</td>
    <td>${esc(r.language ?? "—")}</td>
    <td><span class="pill ${esc(r.priority ?? "")}">${esc(r.priority ?? "—")}</span></td>
    <td>${esc(r.queue ?? "—")}</td>
    <td>${esc(r.customer_tier ?? "—")}</td>
    <td>${r.needs_human ? "yes" : "no"}</td>
    <td class="mono">${r.confidence?.toFixed(2) ?? "—"}</td>
    <td>${r.policy_flags.length ? flagPills(r.policy_flags) : `<span class="muted">—</span>`}</td>
    <td class="mono">${r.latency_ms}</td>
    <td><button class="link" data-delete="${esc(r.request_id)}" title="Delete this record">✕</button></td>
  </tr>`;
}

function drawCompare(): void {
  if (pinned.size !== 2) {
    compareEl.innerHTML = pinned.size === 1
      ? `<p class="hint" style="margin-top:10px">Tick one more row to compare — picks survive paging.</p>`
      : "";
    return;
  }
  const [a, b] = [...pinned.values()];

  const field = (label: string, x?: string | null, y?: string | null) => {
    const differs = (x ?? "") !== (y ?? "");
    return `<tr class="${differs ? "differs" : ""}">
      <th>${esc(label)}</th><td>${esc(x ?? "—")}</td><td>${esc(y ?? "—")}</td></tr>`;
  };

  compareEl.innerHTML = `
    <div class="cmp-head"><h3 style="margin:0">Comparing two runs</h3>
      <span class="hint">highlighted rows diverged</span></div>
    <div class="table-scroll"><table class="cmp">
      <thead><tr><th></th>
        <th class="mono">${esc(a.request_id)}</th>
        <th class="mono">${esc(b.request_id)}</th></tr></thead>
      <tbody>
        ${field("message", a.message_text, b.message_text)}
        ${field("tier", a.customer_tier, b.customer_tier)}
        ${field("channel", a.channel, b.channel)}
        ${field("language", a.language, b.language)}
        ${field("category", a.category, b.category)}
        ${field("sentiment", a.sentiment, b.sentiment)}
        ${field("priority", a.priority, b.priority)}
        ${field("queue", a.queue, b.queue)}
        ${field("needs human", String(a.needs_human), String(b.needs_human))}
        ${field("confidence", a.confidence?.toFixed(2), b.confidence?.toFixed(2))}
        ${field("policy flags", a.policy_flags.join(", ") || "none", b.policy_flags.join(", ") || "none")}
        ${field("latency ms", String(a.latency_ms), String(b.latency_ms))}
        ${field("summary", a.summary_en, b.summary_en)}
        ${field("draft reply", a.reply_draft, b.reply_draft)}
      </tbody>
    </table></div>`;
}

function pills(pri: string, lang: string, cat: string, sent: string, queue: string, human: boolean): string {
  return `<div class="pills">
    <span class="pill ${esc(pri)}">${esc(pri)}</span>
    <span class="pill">${esc(lang)}</span>
    <span class="pill">${esc(cat)}</span>
    <span class="pill">${esc(sent)}</span>
    <span class="pill">→ ${esc(queue)}</span>
    ${human ? `<span class="pill human">human review</span>` : ""}
  </div>`;
}

const flagPills = (flags: string[]) =>
  flags.length
    ? flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")
    : `<span class="muted">none</span>`;

function esc(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

void loadHistory();
