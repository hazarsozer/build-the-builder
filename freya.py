"""Thin client for the Freya API. Standard library only.

    export FREYA_API_KEY=fak_...            # your workspace key
    export FREYA_API_URL=https://staging-app.freyavoice.ai   # default
"""
import json, os, time, urllib.request, urllib.error, urllib.parse

URL = os.environ.get("FREYA_API_URL", "https://staging-app.freyavoice.ai")
# Cloudflare rejects requests without a browser-like User-Agent (error 1010).
HEADERS = {"Authorization": "Bearer " + os.environ.get("FREYA_API_KEY", ""), "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (build-the-builder)"}

def api(method, path, body=None, **params):
    """Call the API and return the `result` part of its {success, data: {message, result}} envelope."""
    q = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(URL + path + q, data=json.dumps(body).encode() if body is not None else None, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r: out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:1500]}") from None
    return out.get("data", {}).get("result", out) if isinstance(out, dict) else out

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
        state[slot]["version"] = update_workflow(state[slot]["workflow_id"], workflow); f.write_text(json.dumps(state, indent=1))
    else:
        state[slot] = create_workflow(f"{slot} {time.strftime('%m%d-%H%M%S')}", workflow); f.write_text(json.dumps(state, indent=1))
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
    if not newest.get("isPublished"): api("POST", f"/api/v2/versions/workflow/{workflow_id}/publish", {"versionId": newest["id"]})
    return newest["versionNumber"]
def export_workflow(workflow_id, path):
    """Write the workflow as it is on the platform to a file. This file is part of what you submit."""
    w = api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=workspace_id())["workflow"]
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

def run_simulation(agent_id, name, variables_by_persona, max_seconds=420, concurrency=int(os.environ.get("FREYA_SIM_CONCURRENCY", "5")), on_created=None, on_busy=None):
    """Start a text simulation, wait for it, return (simulation_id, iteration_id).

    Optional (builder additions, default behaviour unchanged): on_created(simulation_id) is called as soon as the
    simulation exists; on_busy(waited_seconds) is called each time the workspace is busy, before the 30 s wait (it may
    raise to stop waiting)."""
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
    while True:
        time.sleep(10)
        s = api("GET", f"/api/v2/simulations/{sim_id}"); s = s.get("simulation") or s
        if s.get("status") in ("completed", "failed", "paused"): break
    its = api("GET", f"/api/v2/simulations/{sim_id}/iterations"); its = its.get("iterations") or its.get("items") or its
    return sim_id, its[0]["id"], s.get("status")
