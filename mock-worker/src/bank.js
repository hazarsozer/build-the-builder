// Pure bank logic, a 1:1 port of bank/server.py (the Bank class). No Worker APIs here, so it can be unit-run in node.
// A session `s` is a plain JSON object (faults are arrays, not sets) so a Durable Object can store it as is.

export const NO_REASON = ["promise", "paid"];
export const MAX_BODY = 64 * 1024;
export const MAX_CALLS = 500;
export const WRONG_ANSWERS = 3;
export const DEFAULT_HINTS = { day_month_swapped: { when: "swapped_date" } };
export const NOT_A_DATE = "answer is not a date. Send it as YYYY-MM-DD.";
export const FAULT = { ok: false, error: "service unavailable, nothing was recorded" };
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/, HHMM = /^\d{2}:\d{2}$/;

const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const reEscape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Python truthiness (bool(x)): empty list/dict are false.
export const truthy = (x) =>
  Array.isArray(x) ? x.length > 0 : isObj(x) ? Object.keys(x).length > 0 : Boolean(x);
// Python str(x) for the JSON values an agent sends.
export const pystr = (x) =>
  x === null || x === undefined ? "None" : x === true ? "True" : x === false ? "False"
    : typeof x === "string" ? x : typeof x === "number" ? String(x) : JSON.stringify(x);
// Python `x in list` for JSON values.
const pyIn = (x, list) => list.some((v) => v === x || (typeof v === "object" && JSON.stringify(v) === JSON.stringify(x)));

// ---- answer parsing ---------------------------------------------------------------------------------------------

export function parseDate(text) {
  if (typeof text !== "string") return null;
  let m = text.match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (m) return [+m[1], +m[2], +m[3]];
  m = text.match(/(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/);
  if (m) return [+m[3], +m[2], +m[1]];
  const month = (w) => { const i = MONTHS.indexOf(w.slice(0, 3).toLowerCase()); return i >= 0 ? i + 1 : null; };
  m = text.match(/(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([A-Za-z]{3,})\.?,?\s+(\d{4})/);
  if (m && month(m[2])) return [+m[3], month(m[2]), +m[1]];
  m = text.match(/([A-Za-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})/);
  if (m && month(m[1])) return [+m[3], month(m[1]), +m[2]];
  return null;
}

function requestSchema(openapi, tool) {
  try { return openapi.paths["/" + tool].post.requestBody.content["application/json"].schema || {}; } catch { return {}; }
}

function enumOf(openapi, tool, prop) {
  try { return requestSchema(openapi, tool).properties[prop].enum ?? null; } catch { return null; }
}

export function fieldKinds(openapi, identity) {
  let desc = "";
  try { desc = requestSchema(openapi, "verify_identity").properties.answer.description || ""; } catch { desc = ""; }
  const kinds = {};
  for (const f of Object.keys(identity)) {
    const m = desc.match(new RegExp("For " + reEscape(f) + ":([^.]*(?:\\.\\d[^.]*)?)"));
    const said = (m ? m[1] : "").toLowerCase();
    if (said.includes("yyyy-mm-dd") || (!said && f.includes("date"))) kinds[f] = "date";
    else if (said.includes("digit") || (!said && /last4|digits|number|year/.test(f))) kinds[f] = "digits";
    else kinds[f] = "text";
  }
  return kinds;
}

// JavaScript Number(x) for the values an agent sends, as server.py's jsnum; null when not a finite number.
export function jsnum(x) {
  if (typeof x === "boolean") return Number(x);
  if (typeof x === "number") return Number.isFinite(x) ? x : null;
  if (typeof x === "string") {
    const s = x.trim();
    if (s === "") return 0;
    if (!/^[+-]?(\d[\d_]*\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s) || /_(_|$|\.)|^[+-]?_/.test(s)) return null;
    const v = Number(s.replace(/_/g, ""));
    return Number.isFinite(v) ? v : null;
  }
  return null;
}

const sayOr = (items) => items.length === 1 ? items[0] : items.slice(0, -1).join(", ") + " or " + items[items.length - 1];

// ---- config -----------------------------------------------------------------------------------------------------

export class Contract {
  constructor(openapi) {
    this.openapi = openapi || {};
    this.tools = Object.keys(this.openapi.paths || {}).map((p) => p.replace(/^\/+|\/+$/g, ""));
    this.fields = enumOf(this.openapi, "verify_identity", "field") || [];
    this.outcomes = enumOf(this.openapi, "close_call", "outcome");
    this.noteReasons = enumOf(this.openapi, "add_note", "reason") || [];
    this.queues = enumOf(this.openapi, "transfer_to_agent", "queue") || [];
  }
}

// A public-test-set case as a seed. Its faults [{"tool", "fail_first_n"}] become call numbers.
export function seedFromCase(c) {
  const faults = {};
  for (const f of c.faults || []) {
    const n = f.fail_first_n ?? 1;
    faults[f.tool] = Array.from({ length: Math.max(0, n) }, (_, i) => i + 1);
  }
  return { variables: c.variables, identity: c.identity, products: c.products, faults };
}

// ---- the bank ---------------------------------------------------------------------------------------------------

export function newSession(contract, seed, cid, caseId) {
  const variables = { customer_id: cid, ...(seed.variables || {}) };
  const identity = { ...(seed.identity || {}) };
  const products = (seed.products || []).map((p, i) => ({ product_id: `p${i + 1}`, ...p }));
  const reasons = seed.reasons;
  const policy = isObj(seed.identity_policy) ? seed.identity_policy : {};
  const hints = isObj(seed.hints) ? seed.hints : DEFAULT_HINTS;
  const aliases = isObj(seed.response_aliases) ? seed.response_aliases : {};
  const faults = {};
  for (const [t, ns] of Object.entries(seed.faults || {})) faults[t] = [...new Set(ns)];
  return {
    case: caseId ?? null, reasons: isObj(reasons) ? { ...reasons } : null, variables, identity,
    kinds: fieldKinds(contract.openapi, identity), products, faults,
    max_wrong: Math.trunc(Number(policy.max_wrong || WRONG_ANSWERS)), scope: policy.scope || "total",
    hints: { ...hints }, hints_used: [], aliases: { ...aliases }, wrong_by: {},
    tool_calls: {}, verified: {}, wrong: 0, hinted: false, promises: [], notes: [],
    callbacks: [], transfers: [], outcome: null, reason: null, failures: {}, calls: [],
    closed: false, last_at: Date.now(),
  };
}

export function ledger(s) {
  const out = { ok: true, case: s.case, now: s.variables.now ?? null, verified: s.verified, wrong: s.wrong, hinted: s.hinted };
  for (const k of ["promises", "notes", "callbacks", "transfers", "outcome", "reason", "failures", "calls", "last_at"]) out[k] = s[k];
  return JSON.parse(JSON.stringify(out));
}

// Runs one tool call on session s (mutated in place). Returns [status, body].
export function callTool(contract, s, tool, body) {
  if (s.calls.length >= MAX_CALLS) return [200, { ok: false, error: "too many calls" }];
  const args = {};
  for (const [k, v] of Object.entries(body)) if (k !== "customer_id") args[k] = v;
  const n = (s.tool_calls[tool] = (s.tool_calls[tool] || 0) + 1);
  if (!(tool in s.failures)) s.failures[tool] = 0;
  let status, result;
  if (s.closed) [status, result] = [200, { ok: false, error: "the call is already closed" }];
  else if ((s.faults[tool] || []).includes(n)) { [status, result] = [500, { ...FAULT }]; s.failures[tool] += 1; }
  else {
    status = 200;
    const fn = TOOLS[tool] || tUnknown;
    result = fn(contract, s, args);
    const ren = s.aliases[tool] || {};
    if (Object.keys(ren).length) {
      const r = {};
      for (const [k, v] of Object.entries(result)) r[ren[k] ?? k] = v;
      result = r;
    }
  }
  s.calls.push({ n: s.calls.length + 1, tool, args, status, result });
  s.last_at = Date.now();
  return [status, JSON.parse(JSON.stringify(result))];
}

const tUnknown = () => ({ ok: true });
const allVerified = (s) => Object.keys(s.identity).every((f) => s.verified[f]);

function tVerifyIdentity(c, s, a) {
  const field = a.field, answer = a.answer;
  const allowed = [...new Set([...c.fields, ...Object.keys(s.identity)])];
  if (!allowed.includes(field)) return { ok: false, error: "field must be " + sayOr(allowed) };
  const per = s.scope === "per_question";
  const left = () => s.max_wrong - (per ? (s.wrong_by[field] || 0) : s.wrong);
  const over = per ? Object.values(s.wrong_by).some((n) => n >= s.max_wrong) : s.wrong >= s.max_wrong;
  if (s.verified[field]) return { ok: true, verified: true, wrong_answers_left: left() };
  if (over) return { ok: false, verified: false, wrong_answers_left: 0, error: "identity check is over" };
  const truth = pystr(s.identity[field] ?? ""), kind = s.kinds[field] || "text";
  const rule = (when) => Object.entries(s.hints).find(([h, r]) => isObj(r) && r.when === when && !s.hints_used.includes(h))?.[0] ?? null;
  const hinted = (h) => {
    s.hinted = true; s.hints_used.push(h);
    return { ok: true, verified: false, wrong_answers_left: left(), hint: h };
  };
  let ok, got;
  if (kind === "date") {
    const g = parseDate(answer), want = parseDate(truth);
    if (g === null) return { ok: false, error: NOT_A_DATE };
    if (want && g[0] === want[0] && g[1] === want[1] && g[2] === want[2]) ok = true;
    else if (want && g[0] === want[0] && g[2] === want[1] && g[1] === want[2] && rule("swapped_date")) return hinted(rule("swapped_date"));
    else ok = false;
  } else if (kind === "digits") {
    got = pystr(answer ?? "").replace(/[\s,.\-]/g, "");
    ok = got === truth;
  } else {
    got = pystr(answer ?? "").replace(/\s/g, "").toLowerCase();
    ok = got === truth.replace(/\s/g, "").toLowerCase();
  }
  if (!ok && kind !== "date" && got && rule("prefix")) {
    const want = truth.replace(/[\s,.\-]/g, "").toLowerCase();
    if (want.startsWith(got.toLowerCase()) && got.length < want.length) return hinted(rule("prefix"));
  }
  if (ok) s.verified[field] = true;
  else { s.wrong += 1; s.wrong_by[field] = (s.wrong_by[field] || 0) + 1; }
  return { ok: true, verified: ok, wrong_answers_left: left(), all_verified: allVerified(s) };
}

function tGetAccount(c, s) {
  if (!allVerified(s)) return { ok: false, error: "identity not verified" };
  return { ok: true, products: s.products };
}

function tCreatePromise(c, s, a) {
  if (!allVerified(s)) return { ok: false, error: "identity not verified" };
  const product = s.products.find((p) => p.product_id === a.product_id);
  if (!product) return { ok: false, error: "unknown product_id" };
  const amount = jsnum(a.amount);
  if (amount === null || amount <= 0) return { ok: false, error: "amount must be a positive number" };
  if (typeof a.date !== "string" || !ISO_DATE.test(a.date)) return { ok: false, error: "date must be YYYY-MM-DD" };
  const schema = requestSchema(c.openapi, "create_promise");
  const props = schema.properties || {};
  const extraF = Object.keys(props).filter((f) => !["customer_id", "product_id", "amount", "date"].includes(f));
  for (const f of extraF) {
    const spec = props[f] || {};
    if ((schema.required || []).includes(f) && (a[f] === undefined || a[f] === null || a[f] === "")) return { ok: false, error: f + " is required" };
    if (truthy(spec.enum) && f in a && !pyIn(a[f], spec.enum)) return { ok: false, error: f + " must be " + sayOr(spec.enum) };
  }
  const extra = {};
  for (const f of extraF) if (f in a) extra[f] = a[f];
  if (s.promises.some((p) => p.product_id === product.product_id)) return { ok: false, error: "this product already has a promise" };
  const pid = `pr${s.promises.length + 1}`;
  s.promises.push({ promise_id: pid, product_id: product.product_id, type: product.type ?? null, amount, date: a.date, ...extra });
  return { ok: true, promise_id: pid };
}

function tAddNote(c, s, a) {
  if (c.noteReasons.length && !pyIn(a.reason, c.noteReasons)) return { ok: false, error: "unknown reason" };
  s.notes.push({ product_id: a.product_id ?? null, reason: a.reason ?? null, text: truthy(a.text) ? a.text : "" });
  return { ok: true };
}

function tScheduleCallback(c, s, a) {
  const d = a.date, t = a.time;
  if (!(typeof d === "string" && ISO_DATE.test(d) && typeof t === "string" && HHMM.test(t)))
    return { ok: false, error: "date must be YYYY-MM-DD and time HH:MM" };
  s.callbacks.push({ date: d, time: t });
  return { ok: true };
}

function tTransferToAgent(c, s, a) {
  if (c.queues.length && !pyIn(a.queue, c.queues)) return { ok: false, error: "unknown queue" };
  s.transfers.push({ queue: a.queue ?? null });
  return { ok: true };
}

function tCloseCall(c, s, a) {
  const outcome = a.outcome, reason = a.reason;
  const table = s.reasons;
  const known = [...(c.outcomes || []), ...Object.keys(table || {})];
  if (typeof outcome !== "string" || (known.length && !known.includes(outcome))) return { ok: false, error: "unknown outcome" };
  let need;
  if (table !== null && outcome in table) need = [...(table[outcome] || [])];
  else if (NO_REASON.includes(outcome)) need = [];
  else need = null; // no table for this outcome: any non-empty reason
  if (need === null) {
    if (!(typeof reason === "string" && reason.trim())) return { ok: false, error: "this outcome needs a reason" };
  } else if (need.length ? !pyIn(reason, need) : truthy(reason)) {
    return { ok: false, error: "this outcome needs one of these reasons: " + need.join(", ") };
  }
  s.outcome = outcome;
  s.reason = need !== null && need.length === 0 ? null : (reason ?? null);
  s.closed = true;
  return { ok: true };
}

const TOOLS = {
  verify_identity: tVerifyIdentity, get_account: tGetAccount, create_promise: tCreatePromise, add_note: tAddNote,
  schedule_callback: tScheduleCallback, transfer_to_agent: tTransferToAgent, close_call: tCloseCall,
};
