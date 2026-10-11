// Freya Bank statement-debt mock (final case). Serves the bank's test environment exactly:
// POST /voice/v1/<service> with {data, call}; the answer for each test customer / card / week comes
// from data/test-customers.json (status code + body). Unknown keys get the contract's 404, or a 400
// INVALID_ARGUMENTS_ERROR where the service has no 404. Token, headers, `call` and `aiFlow` are not checked.
import DB from "../data/test-customers.json";

const CORS = { "Access-Control-Allow-Origin": "*" };
const BASE = "/voice/v1/";

const CUSTOMERS = new Map(DB.customers.map((c) => [String(c.customerNo), c]));
const CARDS = new Map();  // masked card number -> customer
for (const c of DB.customers) {
  for (const svc of ["cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change"]) {
    for (const card of Object.keys(c.services[svc] || {})) CARDS.set(card, c);
  }
}

// Lookup key per service (test-customers.md, "How the JSON is laid out")
const BY = {
  "cards.list": "customer",
  "cards.balance": "card",
  "statement.week.get": "card",
  "statement.week.decide": "card+week",
  "statement.week.change": "card+week",
  "contact.email.get": "customer",
  "statement.pdf.send": "customer",
};
// Required data fields per service (contract section 5)
const REQUIRED = {
  "cards.list": ["customerNo"],
  "cards.balance": ["cardNumber"],
  "statement.week.get": ["cardNumber"],
  "statement.week.decide": ["cardNumber", "newStatementWeek", "statementDay"],
  "statement.week.change": ["cardNumber", "newStatementWeek", "statementDay"],
  "contact.email.get": ["customerNo", "intAddressInfoType"],
  "statement.pdf.send": ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
};
// Not-found answer per service (contract section 4); null = no 404, use 400
const NOT_FOUND = {
  "cards.list": ["FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı."],
  "cards.balance": ["FB_IVR_CHANNEL_120", "Kart bakiye servisi hatası: kart bulunamadı"],
  "statement.week.get": ["FB_IVR_CHANNEL_121", "Kart bulunamadı"],
  "statement.pdf.send": ["FB_IVR_CHANNEL_107", "Pdf dokümanı bulunamadı"],
};

function json(body, status = 200) {
  return Response.json(body, { status, headers: CORS });
}

function guid() {
  return crypto.randomUUID().replace(/-/g, "");
}

function errorBody(code, message, type, rootCause) {
  const g = guid();
  return {
    result: {
      guid: g, returnCode: code, messageText: message, code, message, type,
      details: { guid: g, rootCause, errorOrigin: "ivr-gateway-test:ivr-channel-v0" },
    },
  };
}

function invalid(message) {
  return json(errorBody("INVALID_ARGUMENTS_ERROR", message, "ERROR", "InvalidArgumentsException"), 400);
}

function notFound(service) {
  const nf = NOT_FOUND[service];
  if (!nf) return invalid("record not found");
  return json(errorBody(nf[0], nf[1], "WARNING", "NotFoundException"), 404);
}

// The panel's "Test API Request" button sends variables unfilled, e.g. "{{customerNumber}}".
function isTemplate(v) {
  return typeof v === "string" && (v.includes("{{") || v.includes("%7B%7B"));
}

function empty(v) {
  return v === undefined || v === null || String(v).trim() === "";
}

function fieldName(f) {
  return f.replace(/([A-Z])/g, " $1").toLowerCase();
}

async function answer(leaf) {
  if (leaf.delaySeconds) await new Promise((r) => setTimeout(r, leaf.delaySeconds * 1000));
  return json(leaf.response, leaf.http);
}

async function serve(service, body) {
  const data = (body && typeof body.data === "object" && body.data) || {};
  // Template probe from the Freya panel: answer with the first customer's record
  if (Object.values(data).some(isTemplate)) {
    const c = DB.customers[0];
    const card = Object.keys(c.services["cards.balance"] || {})[0];
    let leaf = c.services[service];
    if (leaf && BY[service] !== "customer") leaf = leaf[card];
    if (leaf && BY[service] === "card+week") leaf = Object.values(leaf)[0];
    return leaf ? json(leaf.response, leaf.http) : notFound(service);
  }
  for (const f of REQUIRED[service]) {
    if (empty(data[f])) return invalid(fieldName(f) + " cannot be empty");
  }
  const by = BY[service];
  if (by === "customer") {
    const c = CUSTOMERS.get(String(data.customerNo).trim());
    const leaf = c && c.services[service];
    return leaf ? answer(leaf) : notFound(service);
  }
  const card = String(data.cardNumber).trim();
  const c = CARDS.get(card);
  let leaf = c && (c.services[service] || {})[card];
  if (leaf && by === "card+week") leaf = leaf[String(data.newStatementWeek).trim()];
  return leaf ? answer(leaf) : notFound(service);
}

async function readBody(request) {
  try { return await request.json(); } catch { return null; }
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: {
        ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*",
      } });
    }
    if (request.method === "GET" && url.pathname === "/health") return json({ ok: true, customers: CUSTOMERS.size });
    if (request.method === "POST" && url.pathname === "/oauth2/token") {
      return json({ access_token: "test-environment-token", token_type: "Bearer", expires_in: 1800 });
    }
    if (request.method === "POST" && url.pathname.startsWith(BASE)) {
      const service = url.pathname.slice(BASE.length);
      if (BY[service]) {
        const body = await readBody(request);
        if (!body) return invalid("request body must be JSON with a data object");
        return serve(service, body);
      }
    }
    return json({ error: "not found", paths: ["GET /health", "POST /oauth2/token",
      ...Object.keys(BY).map((s) => "POST " + BASE + s)] }, 404);
  },
};
