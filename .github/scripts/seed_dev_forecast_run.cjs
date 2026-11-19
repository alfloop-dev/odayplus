// Dev only: ingest the exported daily store revenue into ForecastOps and run one
// forecast job per store, as the dedicated dev operator (dev-forecast-bot,
// operations_manager only) through the web BFF -- the same audited API path an
// operator uses. Prints counts and alert levels only; never prints secrets.
//
// Env: WEB_BASE_URL, FORECAST_BOT_USERNAME, FORECAST_BOT_PASSWORD.
// Usage: node seed_dev_forecast_run.cjs forecast-series.json
"use strict";
const fs = require("node:fs");
const crypto = require("node:crypto");

const BASE = (process.env.WEB_BASE_URL || "").replace(/\/+$/, "");
const MIN_DAYS = 28;
const ATTEMPTS = 6;

function client() {
  let cookie = "";
  return async (method, path, body, extra = {}) => {
    for (let attempt = 1; ; attempt++) {
      let status = 0;
      let payload = null;
      try {
        const res = await fetch(BASE + path, {
          method, redirect: "manual",
          headers: { accept: "application/json", origin: BASE, ...(cookie ? { cookie } : {}),
            ...(body ? { "content-type": "application/json" } : {}), ...extra },
          body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(120000),
        });
        const sc = res.headers.getSetCookie?.() ?? [];
        if (sc.length) cookie = sc.map((c) => c.split(";")[0]).join("; ");
        status = res.status;
        try { payload = await res.json(); } catch { /* non-JSON */ }
      } catch (error) {
        payload = { error: { code: "FETCH_FAILED", message: String(error?.message || error) } };
      }
      // GETs and idempotency-keyed forecast jobs are safe to resend; the BFF
      // gives up after 10s while a cold API is still loading the model.
      const retryable = status === 0 || [502, 503, 504].includes(status);
      const safe = method === "GET" || Boolean(extra.retrySafe);
      if (!retryable || !safe || attempt === ATTEMPTS) return { status, payload };
      await new Promise((r) => setTimeout(r, 10000));
    }
  };
}
const brief = (p) => JSON.stringify(p?.error?.code ?? p?.detail ?? p?.message ?? "").slice(0, 300);

(async () => {
  if (!BASE || !process.env.FORECAST_BOT_USERNAME || !process.env.FORECAST_BOT_PASSWORD) {
    throw new Error("missing WEB_BASE_URL / FORECAST_BOT_USERNAME / FORECAST_BOT_PASSWORD");
  }
  const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const lastDay = data.window[1];
  const origin = data.prediction_origin_time;
  const byStore = new Map();
  for (const o of data.observations) {
    if (!byStore.has(o.store_id)) byStore.set(o.store_id, []);
    byStore.get(o.store_id).push(o);
  }
  // Only the contiguous run ending at the last closed day, lineage on every day.
  const eligible = [];
  for (const [storeId, rows] of [...byStore.entries()].sort()) {
    const byDate = new Map(rows.map((r) => [r.business_date, r]));
    const run = [];
    let cursor = new Date(lastDay + "T00:00:00Z");
    while (byDate.has(cursor.toISOString().slice(0, 10))) {
      const r = byDate.get(cursor.toISOString().slice(0, 10));
      if (!r.source_snapshot_ids.length) break;
      run.unshift(r);
      cursor = new Date(cursor.getTime() - 86400000);
    }
    if (run.length >= MIN_DAYS) eligible.push({ storeId, run });
    else console.log(`SKIP ${storeId}: contiguous lineage-backed days to ${lastDay} = ${run.length} (< ${MIN_DAYS})`);
  }
  console.log(`eligible stores ${eligible.length}/${byStore.size}; origin ${origin}`);
  if (eligible.length === 0) throw new Error("no store has 28 contiguous days ending at the last closed day");

  const c = client();
  const login = await c("POST", "/login", {
    username: process.env.FORECAST_BOT_USERNAME, password: process.env.FORECAST_BOT_PASSWORD, returnTo: "/operator",
  });
  if (login.status !== 200) throw new Error(`login HTTP ${login.status} ${brief(login.payload)}`);

  const ingest = await c("POST", "/api/v1/forecastops/timeseries", { observations: eligible.flatMap((e) => e.run) });
  console.log(`INGEST HTTP ${ingest.status} series=${ingest.payload?.count ?? ""} ${ingest.status >= 300 ? brief(ingest.payload) : ""}`);
  if (ingest.status >= 300) throw new Error("timeseries ingest failed");

  let ok = 0;
  const levels = {};
  for (const e of eligible) {
    const key = crypto.createHash("sha256").update(`dev-forecast-${e.storeId}-${origin}`).digest("hex").slice(0, 32);
    const body = {
      inputs: [{ store_id: e.storeId, observations: e.run, prediction_origin_time: origin }],
      prediction_origin_time: origin,
      idempotency_key: key,
    };
    const r = await c("POST", "/api/v1/forecastops/forecast-jobs", body, { retrySafe: true });
    const p = r.payload || {};
    const alert = (p.alerts || [])[0] || {};
    const level = alert.alert_level ? `${alert.alert_level}/${alert.alert_reason_code}` : "";
    if (level) levels[level] = (levels[level] || 0) + 1;
    console.log(`FORECAST ${e.storeId} HTTP ${r.status} status=${p.status ?? ""} model=${p.model_binding?.model_version ?? ""} alert=${level} ${r.status >= 300 ? brief(p) : ""}`);
    if (r.status < 300 && p.status === "succeeded") ok += 1;
  }
  console.log(`FORECAST ok ${ok}/${eligible.length} alert_levels=${JSON.stringify(levels)}`);
  if (ok !== eligible.length) throw new Error(`${eligible.length - ok} forecast job(s) did not succeed`);
})().catch((e) => { console.error(`FAILED: ${e.message}`); process.exitCode = 1; });
