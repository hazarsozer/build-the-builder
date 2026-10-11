// The bank mock as a Cloudflare Worker: same routes and bodies as bank/server.py.
//   POST /v1/<tool>                        tools named in the openapi paths of config.json
//   POST /v1/practice/sessions             {"seed": {...}} or {"case": "<id>"} -> {"ok": true, "variables": {customer_id, ...}}
//   GET  /v1/practice/ledger/<customer_id>
//   GET  /health
// One Durable Object per customer_id holds that customer's session, so the ledger is consistent across requests.
// config.json (bundled as a JSON module) = {"openapi": {...}, "cases": [...], "idle_reset_s": 600}; write it with
// make_config.py. A case with "customer_id" (or variables.customer_id, or its id) is also reachable without a session
// call: the first request for that id seeds it from the case, and it reseeds after idle_reset_s without calls.
// x-team-key and any other auth header is accepted and ignored.
import config from "../config.json";
import { Contract, MAX_BODY, callTool, ledger, newSession, seedFromCase } from "./bank.js";

const contract = new Contract(config.openapi);
const CASES = config.cases || [];
const IDLE_RESET_MS = (config.idle_reset_s ?? 600) * 1000;

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const caseSeed = (c) => (c.seed && typeof c.seed === "object" ? c.seed : seedFromCase(c));

// A case reachable by a fixed customer_id: its own customer_id, variables.customer_id, or its id.
function presetFor(cid) {
  const c = CASES.find((c) => c.customer_id === cid || c.variables?.customer_id === cid || c.id === cid);
  if (!c) return null;
  const seed = caseSeed(c);
  return { seed: { ...seed, variables: { ...(seed.variables || {}), customer_id: cid } }, case: c.id ?? null };
}

export class Session {
  constructor(state) { this.state = state; }

  async fetch(request) {
    const url = new URL(request.url);
    const cid = request.headers.get("x-cid");
    const body = request.method === "POST" ? await request.json() : {};
    let s = await this.state.storage.get("s");
    if (url.pathname === "/init") {
      s = newSession(contract, body.seed, cid, body.case);
      s.preset = false;
      await this.state.storage.put("s", s);
      return json(200, { ok: true, variables: s.variables });
    }
    const preset = presetFor(cid);
    if (preset && (!s || (s.preset && Date.now() - s.last_at > IDLE_RESET_MS))) {
      s = newSession(contract, preset.seed, cid, preset.case);
      s.preset = true;
      await this.state.storage.put("s", s);
    }
    if (url.pathname === "/ledger") {
      if (!s) return json(404, { ok: false, error: "unknown customer_id for this team key" });
      return json(200, ledger(s));
    }
    if (!s) return json(404, { ok: false, error: "unknown customer_id" });
    const tool = url.pathname.slice("/tool/".length);
    const [status, result] = callTool(contract, s, tool, body);
    await this.state.storage.put("s", s);
    return json(status, result);
  }
}

const stub = (env, cid) => env.SESSIONS.get(env.SESSIONS.idFromName(cid));
const toDO = (env, cid, path, body) =>
  stub(env, cid).fetch("https://do" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "x-cid": cid, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

async function session(env, body) {
  let seed, caseId;
  if ("seed" in body) { seed = body.seed && typeof body.seed === "object" ? body.seed : {}; caseId = body.case ?? null; }
  else {
    const c = CASES.find((c) => c.id === body.case);
    if (!c) return json(404, { ok: false, error: "unknown practice case" });
    seed = caseSeed(c); caseId = body.case;
  }
  const cid = "c_" + crypto.randomUUID().slice(0, 12);
  return toDO(env, cid, "/init", { seed, case: caseId });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "");
    if (request.method === "GET") {
      if (path === "/health") return json(200, { ok: true });
      if (path.startsWith("/v1/practice/ledger/")) {
        const cid = decodeURIComponent(path.split("/").pop());
        return cid ? toDO(env, cid, "/ledger") : json(404, { ok: false, error: "unknown customer_id for this team key" });
      }
      return json(404, { ok: false, error: "not found" });
    }
    if (request.method !== "POST") return json(404, { ok: false, error: "not found" });
    const len = Number(request.headers.get("Content-Length") ?? 0);
    if (!(len >= 0) || len > MAX_BODY) return json(413, { ok: false, error: "request body too large" });
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_BODY) return json(413, { ok: false, error: "request body too large" });
    let body;
    try { body = JSON.parse(raw || "{}"); } catch { body = {}; }
    if (body === null || typeof body !== "object" || Array.isArray(body)) body = {};
    if (path === "/v1/practice/sessions") return session(env, body);
    const tool = path.startsWith("/v1/") ? path.slice(4) : null;
    if (tool !== null && contract.tools.includes(tool)) {
      const cid = body.customer_id;
      if (typeof cid !== "string" || !cid) return json(404, { ok: false, error: "unknown customer_id" });
      return toDO(env, cid, "/tool/" + tool, body);
    }
    return json(404, { ok: false, error: "not found" });
  },
};
