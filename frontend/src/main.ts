import { health, triage } from "./api";
import type { TriageResponse } from "./types";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const message = $<HTMLTextAreaElement>("message");
const channel = $<HTMLSelectElement>("channel");
const tier = $<HTMLSelectElement>("tier");
const submit = $<HTMLButtonElement>("submit");
const result = $<HTMLElement>("result");

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
  const target = e.target as HTMLElement;
  const i = target.dataset.i;
  if (i !== undefined) message.value = SAMPLES[Number(i)][1];
});

health()
  .then((h) => ($("mode").textContent = `${h.mode} · ${h.model}`))
  .catch(() => ($("mode").textContent = "backend offline"));

submit.addEventListener("click", async () => {
  if (message.value.trim().length < 3) return;
  submit.disabled = true;
  submit.textContent = "Triaging…";
  result.innerHTML = `<div class="panel muted">Calling backend…</div>`;

  try {
    render(await triage({
      message: message.value,
      channel: channel.value,
      customer_tier: tier.value,
    }));
  } catch (err) {
    result.innerHTML = `<div class="panel error">${(err as Error).message}</div>`;
  } finally {
    submit.disabled = false;
    submit.textContent = "Triage";
  }
});

function render(r: TriageResponse): void {
  const t = r.triage;
  const flags = r.policy_flags.length
    ? r.policy_flags.map((f) => `<span class="flag">${f}</span>`).join("")
    : `<span class="muted">none</span>`;

  result.innerHTML = `
    <div class="panel">
      <div class="pills">
        <span class="pill ${t.priority}">${t.priority}</span>
        <span class="pill">${t.language}</span>
        <span class="pill">${t.category}</span>
        <span class="pill">${t.sentiment}</span>
        <span class="pill">→ ${t.suggested_queue}</span>
        ${t.needs_human ? `<span class="pill human">human review</span>` : ""}
      </div>

      <h3>Summary (ops, EN)</h3>
      <p>${escapeHtml(t.summary_en)}</p>

      <h3>Draft reply (customer's language)</h3>
      <p class="reply">${escapeHtml(t.reply_draft)}</p>

      <h3>Server-side policy flags</h3>
      <div class="pills">${flags}</div>

      <footer class="meta">
        request_id <code>${r.request_id}</code> ·
        ${r.latency_ms} ms · ${r.source} · ${r.model} ·
        confidence ${t.confidence.toFixed(2)}
      </footer>
    </div>`;
}

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}
