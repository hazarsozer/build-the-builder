"""Thin client for the Freya API. Standard library only.

    export FREYA_API_KEY=fak_...            # your workspace key
    export FREYA_API_URL=https://staging-app.freyavoice.ai   # default
"""
import json, os, re, socket, ssl, threading, time, urllib.request, urllib.error, urllib.parse

URL = os.environ.get("FREYA_API_URL", "https://staging-app.freyavoice.ai")
# Cloudflare rejects requests without a browser-like User-Agent (error 1010).
HEADERS = {"Authorization": "Bearer " + os.environ.get("FREYA_API_KEY", ""), "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (build-the-builder)"}

RETRY_S = (5, 10, 20, 30, 30)      # waits between attempts of one api() call on a transient failure (~95 s, ~2 min with the attempts)

def _send(method, path, body, params):
    """One HTTP attempt. HTTP errors become RuntimeError('METHOD path -> code: body'); network errors stay OSErrors."""
    q = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(URL + path + q, data=json.dumps(body).encode() if body is not None else None, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r: out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:1500]}") from None
    return out.get("data", {}).get("result", out) if isinstance(out, dict) else out

def _status(e):
    """'502', '503', ... for an HTTP error from _send, else the exception's class name (never its text: no URLs, no headers)."""
    m = re.search(r" -> (\d{3}):", str(e)) if isinstance(e, RuntimeError) else None
    return m.group(1) if m else type(getattr(e, "reason", None) if isinstance(e, urllib.error.URLError) else e).__name__

def _unhandled(e):
    """The request certainly never reached a handler, so even a non-idempotent POST may be repeated:
    - the auth layer's 'API key validation is temporarily unavailable. Retry shortly.' (seen live as a 503; also
      accepted as 401/403, because an auth outage could surface as either, and the body itself says retry);
    - connection refused or DNS failure: nothing was sent."""
    if isinstance(e, RuntimeError):
        return _status(e) in ("401", "403", "503") and "api key validation is temporarily unavailable" in str(e).lower()
    reason = getattr(e, "reason", e)
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))

def _maybe_handled(e):
    """Transient, but the server may have acted on the request: HTTP 502/503/504, connection reset, timeouts.
    Other 4xx/5xx and certificate failures are not transient and are never retried."""
    if isinstance(e, RuntimeError): return _status(e) in ("502", "503", "504")
    return isinstance(e, OSError) and not isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError)

# POST policy per endpoint. GET, PUT and DELETE are idempotent and retried on every transient failure.
#   /api/v2/workflow?dryRun=true            all: validation only, creates nothing.
#   /api/v2/workflow                        all: a duplicate is an orphan workflow with its own timestamped name; the
#                                           state file keeps the one we got. One workspace slot is cheaper than a brief.
#   /api/v2/versions/workflow/{id}/publish  all: publishing the same versionId again changes nothing.
#   /api/v2/personas                        all: a duplicate is a set of unused personas; callers use the returned ids.
#   /api/v2/simulations/{id}/cancel         all: cancel is idempotent.
#   /api/v2/simulations (create)            only when the request was never handled (_unhandled). After a 502/504 or
#                                           a timeout the simulation may exist: a second one would hold the
#                                           one-simulation-per-workspace slot for up to 40 min, so we raise instead.
#   any other POST                          only when never handled.
def _retryable(method, path, params, e):
    if _unhandled(e): return True
    if not _maybe_handled(e): return False
    if method != "POST": return True
    return params.get("dryRun") == "true" or path in ("/api/v2/workflow", "/api/v2/personas") \
        or bool(re.fullmatch(r"/api/v2/(versions/workflow/[^/]+/publish|simulations/[^/]+/cancel)", path))

class CallAbandoned(Exception):
    """The caller stopped waiting for this thread (builder.main's Bg timed out): no further request, wait or file write."""

# Per-thread caller limits, set by builder.main.Bg in its worker thread. Unbound threads (tools, tests) keep the
# plain behaviour: full RETRY_S backoff with time.sleep.
_call = threading.local()

def bind_call(stop, deadline=None):
    """api() in the current thread stops once `stop` (a threading.Event) is set, and never starts a retry wait that
    would end after `deadline` (a time.monotonic() value; None: no deadline)."""
    _call.stop, _call.deadline = stop, deadline

def _check_stopped():
    stop = getattr(_call, "stop", None)
    if stop is not None and stop.is_set(): raise CallAbandoned("the caller stopped waiting")

def _wait(seconds):
    stop = getattr(_call, "stop", None)
    if stop is None: time.sleep(seconds)
    elif stop.wait(seconds): raise CallAbandoned("the caller stopped waiting")

def api(method, path, body=None, *, retry=True, **params):
    """Call the API and return the `result` part of its {success, data: {message, result}} envelope.

    Transient failures are retried with RETRY_S backoff, per _retryable; when they run out, or the next wait would end
    after the caller's deadline (bind_call), the last error is raised. Once the caller has stopped waiting, CallAbandoned
    is raised before any further request or wait. retry=False makes one attempt (the simulation poller has its own loop)."""
    for n in range(len(RETRY_S) + 1):
        _check_stopped()
        try: return _send(method, path, body, params)
        except (RuntimeError, OSError) as e:
            if not retry or n == len(RETRY_S) or not _retryable(method, path, params, e): raise
            _check_stopped()
            deadline = getattr(_call, "deadline", None)
            if deadline is not None and _now() + RETRY_S[n] >= deadline: raise
            dup = method == "POST" and path == "/api/v2/workflow" and params.get("dryRun") != "true" and not _unhandled(e)
            print(f"freya: {method} {path} -> {_status(e)}; retry {n + 1}/{len(RETRY_S)} in {RETRY_S[n]} s"
                  + ("; the failed create may have made a duplicate workflow" if dup else ""), flush=True)
            _wait(RETRY_S[n])

_ws = None
def workspace_id():
    """The workspace your key belongs to. List routes need it as a query parameter."""
    global _ws
    if not _ws:
        r = api("GET", "/api/v2/workspaces"); items = r.get("workspaces") or r.get("items") or r
        _ws = os.environ.get("FREYA_WORKSPACE_ID") or items[0]["id"]
    return _ws

def _positions(workflow):
    """The record-level `nodePositions` the dashboard's editor reads first, copied from the nodes' own `position`
    (builder/layout.py). Empty when no node has one, so a workflow without a layout sends nothing new."""
    pos = {k: {"x": n["position"]["x"], "y": n["position"]["y"]}
           for g in ("nodes", "global_nodes") for k, n in (workflow.get(g) or {}).items()
           if isinstance(n, dict) and isinstance(n.get("position"), dict) and {"x", "y"} <= set(n["position"])}
    return {"nodePositions": pos} if pos else {}

def create_workflow(name, workflow):
    """Create a workflow. Freya creates its agent with it. Returns {workflow_id, agent_id}."""
    w = api("POST", "/api/v2/workflow", {"name": name, "workflow": workflow, **_positions(workflow)})["workflow"]
    return {"workflow_id": w["id"], "agent_id": w["agentId"]}

def save_workflow(slot, workflow, state_file=".freya-agents.json"):
    """Create the workflow the first time, update the same one afterwards. Use this in your builder.

    `slot` is your own label, for example the brief's file name. A workspace holds a limited number of workflows,
    so a builder that creates a new one on every run stops working after a few runs."""
    import pathlib
    f = pathlib.Path(state_file); state = json.loads(f.read_text()) if f.exists() else {}
    if slot in state:
        state[slot]["version"] = update_workflow(state[slot]["workflow_id"], workflow)
    else:
        state[slot] = create_workflow(f"{slot} {time.strftime('%m%d-%H%M%S')}", workflow)
    _check_stopped()                 # an abandoned save must not overwrite the state file a later save wrote
    f.write_text(json.dumps(state, indent=1))
    return state[slot]
def validate_workflow(name, workflow): return api("POST", "/api/v2/workflow", {"name": name, "workflow": workflow}, dryRun="true")
def update_workflow(workflow_id, workflow):
    """Update a workflow and publish the new version. Returns the published version number.

    An update alone only saves a new, unpublished version. Simulations and calls keep running the published one,
    so an update that is not published tests your old phone agent."""
    api("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": workflow, **_positions(workflow)})
    # The platform fills in a default agent_config. If its model is not the event's model, a simulation still runs
    # (it overrides the model), but a chat turn or a real call cannot start. Set the model and save once more.
    w = api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=workspace_id())["workflow"]["workflow"]
    ac = w.get("agent_config") or {}; llm = ac.get("llmConfig") or {}
    if (llm.get("provider"), llm.get("model")) != (AGENT_LLM["provider"], AGENT_LLM["model"]):
        ac["llmConfig"] = {**llm, **AGENT_LLM}; api("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": {**w, "agent_config": ac}, **_positions(w)})
    vs = api("GET", f"/api/v2/versions/workflow/{workflow_id}", workspaceId=workspace_id())["versions"]
    newest = max(vs, key=lambda v: v["versionNumber"])
    if not newest.get("isPublished"):
        try: api("POST", f"/api/v2/versions/workflow/{workflow_id}/publish", {"versionId": newest["id"]})
        except RuntimeError:
            # A publish whose answer was lost (502/504) and then retried can fail with a 4xx although it worked.
            again = api("GET", f"/api/v2/versions/workflow/{workflow_id}", workspaceId=workspace_id())["versions"]
            if not any(v.get("id") == newest["id"] and v.get("isPublished") for v in again): raise
    return newest["versionNumber"]
def export_workflow(workflow_id, path):
    """Write the workflow as it is on the platform to a file. This file is part of what you submit."""
    w = api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=workspace_id())["workflow"]
    _check_stopped()
    open(path, "w").write(json.dumps({"workflow_id": w["id"], "agent_id": w.get("agentId"), "updated_at": w.get("updatedAt"), "workflow": w["workflow"],
                                         **({"nodePositions": w["nodePositions"]} if w.get("nodePositions") else {})}, indent=1)); return path
def create_personas(personas):        return api("POST", "/api/v2/personas", {"personas": personas})
def get_iteration(sim_id, it_id):
    """One iteration with ALL its runs. The route returns 30 runs by default and 100 at most, so this reads page by page."""
    out, off = None, 0
    while True:
        r = api("GET", f"/api/v2/simulations/{sim_id}/iterations/{it_id}", limit=100, offset=off)
        if out is None: out = r
        else: out["runs"] += r["runs"]
        off += 100
        if len(r["runs"]) < 100: return out
def cancel_simulation(sim_id):
    """Cancel a simulation (builder addition). Returns {success, cancelledJobs, cancelledIterations}; the simulation then reads completed."""
    return api("POST", f"/api/v2/simulations/{sim_id}/cancel", {})
def list_simulations():
    """The workspace's simulations, each with `id` and `status` (builder addition)."""
    r = api("GET", "/api/v2/simulations", workspaceId=workspace_id())
    return (r.get("simulations") or r.get("items") or []) if isinstance(r, dict) else r
def get_call(call_id):
    """One simulated call: `messages` holds the transcript, tool calls included. Run ids and call ids come from get_iteration()['runs']."""
    return api("GET", f"/api/v2/call/{call_id}")["call"]

# The model every team's phone agent runs on: Freya's model on the event's own GPU box. Do not change it. (Organisers can point a test at another model with the two variables.)
AGENT_LLM = {"provider": os.environ.get("FREYA_AGENT_PROVIDER", "experimental-5"), "model": os.environ.get("FREYA_AGENT_MODEL", "experimental-5")}
SIM_LLM = {"provider": "google", "model": "gemini-3-flash-preview"}        # plays the customer and judges

BACKOFF_S = (2, 4, 8, 15, 30)      # waits between retries of a transient error while polling; the last one repeats
SIM_DEADLINE_S = 40 * 60

def _now(): return time.monotonic()

class SimulationTimeout(TimeoutError):
    """Polling a simulation outlived its deadline. `.simulation_id` lets the caller cancel it."""
    def __init__(self, simulation_id, waited):
        super().__init__(f"simulation {simulation_id} still not finished after {waited:.0f} s")
        self.simulation_id = simulation_id

def _transient(e):
    """HTTP 429/5xx (api() raises RuntimeError 'METHOD path -> code: ...') or a network error."""
    if isinstance(e, RuntimeError):
        m = re.search(r" -> (\d{3}):", str(e))
        return bool(m) and (m.group(1) == "429" or m.group(1).startswith("5"))
    return isinstance(e, OSError)      # URLError, timeouts and connection resets are all OSErrors

def _poll_api(sim_id, t0, t_end, method, path, **params):
    """api() with retries on transient errors and short backoff, within the polling deadline."""
    n = 0
    while True:
        try: return api(method, path, retry=False, **params)
        except (RuntimeError, OSError) as e:
            if not _transient(e): raise
            wait = BACKOFF_S[min(n, len(BACKOFF_S) - 1)]; n += 1
            if _now() + wait >= t_end: raise SimulationTimeout(sim_id, _now() - t0) from e
            print(f"transient error on {path} ({e}); retrying in {wait} s", flush=True); time.sleep(wait)

def run_simulation(agent_id, name, variables_by_persona, max_seconds=420, concurrency=int(os.environ.get("FREYA_SIM_CONCURRENCY", "5")), on_created=None, on_busy=None, deadline_s=SIM_DEADLINE_S):
    """Start a text simulation, wait for it, return (simulation_id, iteration_id).

    Optional (builder additions, default behaviour unchanged): on_created(simulation_id) is called as soon as the
    simulation exists; on_busy(waited_seconds) is called each time the workspace is busy, before the 30 s wait (it may
    raise to stop waiting).

    Polling retries transient errors (429/5xx, network) with backoff, treats cancelled as finished, and gives up after
    deadline_s seconds with SimulationTimeout (a TimeoutError carrying .simulation_id)."""
    waited = 0
    while True:                      # a workspace runs one simulation at a time: wait for the running one instead of failing
        try:
            sim = api("POST", "/api/v2/simulations", {
                "agentId": agent_id, "name": name, "mode": "chat", "selectedPersonaIds": list(variables_by_persona),
                "customerVariablesMap": variables_by_persona, "maxConcurrentCalls": concurrency, "runsPerPersona": 1, "maxCallDurationSeconds": max_seconds,
                "assistantLlmOverride": AGENT_LLM, "routingLlmOverride": AGENT_LLM, "extractionLlmOverride": AGENT_LLM,
                "personaLlmOverride": SIM_LLM, "analysisLlmOverride": SIM_LLM})
            break
        except RuntimeError as e:
            if "concurrent simulations" not in str(e): raise
            if on_busy: on_busy(waited)
            print("another simulation is running in this workspace, waiting 30 s", flush=True); time.sleep(30); waited += 30
    sim_id = (sim.get("simulation") or sim)["id"]
    if on_created: on_created(sim_id)
    t0 = _now(); t_end = t0 + deadline_s
    while True:
        time.sleep(10)
        s = _poll_api(sim_id, t0, t_end, "GET", f"/api/v2/simulations/{sim_id}"); s = s.get("simulation") or s
        if s.get("status") in ("completed", "failed", "paused", "cancelled", "canceled"): break
        if _now() >= t_end: raise SimulationTimeout(sim_id, _now() - t0)
    its = _poll_api(sim_id, t0, t_end + 120, "GET", f"/api/v2/simulations/{sim_id}/iterations"); its = its.get("iterations") or its.get("items") or its
    return sim_id, its[0]["id"], s.get("status")
