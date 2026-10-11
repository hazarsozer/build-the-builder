"""freya.api retry on transient failures (no network: urlopen and sleep are faked)."""
import io
import json
import socket
import ssl
import threading
import urllib.error

import pytest

import freya

KEY_DOWN = b'{"message":"API key validation is temporarily unavailable. Retry shortly."}'


class Resp:
    def __init__(self, obj): self.b = json.dumps({"success": True, "data": {"result": obj}}).encode()
    def __enter__(self): return io.BytesIO(self.b)
    def __exit__(self, *a): return False


def http(code, body=b"error code: %d"):
    if b"%d" in body: body = body % code
    return urllib.error.HTTPError("https://x.test/p", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def net(monkeypatch):
    """net(outcomes): each urlopen call consumes one outcome (an exception to raise, or a result object)."""
    state = {"calls": [], "sleeps": []}

    def make(outcomes):
        seq = list(outcomes)

        def urlopen(req, timeout=None):
            state["calls"].append((req.get_method(), req.full_url))
            x = seq.pop(0)
            if callable(x): x = x()          # a request still in flight: blocks, then returns or raises
            if isinstance(x, BaseException): raise x
            return Resp(x)
        monkeypatch.setattr(freya.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(freya.time, "sleep", lambda s: state["sleeps"].append(s))
        return state
    return make


def test_503_then_200_succeeds(net):
    st = net([http(503, KEY_DOWN), {"ok": 1}])
    assert freya.api("PUT", "/api/v2/workflow/w1", {"workflow": {}}) == {"ok": 1}
    assert len(st["calls"]) == 2 and st["sleeps"] == [5]


@pytest.mark.parametrize("code", [502, 503, 504])
def test_gateway_errors_retried_on_get(net, code):
    st = net([http(code), http(code), {"ok": 1}])
    assert freya.api("GET", "/api/v2/workflow/w1") == {"ok": 1}
    assert st["sleeps"] == [5, 10]


def test_connection_errors_and_timeouts_retried_on_get(net):
    st = net([urllib.error.URLError(ConnectionResetError()), TimeoutError("read timed out"), {"ok": 1}])
    assert freya.api("GET", "/api/v2/workspaces") == {"ok": 1}
    assert len(st["calls"]) == 3


@pytest.mark.parametrize("code", [400, 401, 404, 409, 422])
def test_4xx_not_retried(net, code):
    st = net([http(code, b'{"message":"bad"}'), {"ok": 1}])
    with pytest.raises(RuntimeError, match=str(code)):
        freya.api("PUT", "/api/v2/workflow/w1", {})
    assert len(st["calls"]) == 1 and st["sleeps"] == []


def test_temporarily_unavailable_body_retried_under_any_code(net):
    st = net([http(401, KEY_DOWN), {"ok": 1}])
    assert freya.api("GET", "/api/v2/workspaces") == {"ok": 1}
    assert len(st["calls"]) == 2


def test_exhaustion_raises_the_error(net):
    st = net([http(503, KEY_DOWN) for _ in range(6)])
    with pytest.raises(RuntimeError, match=r"PUT /api/v2/workflow/w1 -> 503: .*temporarily unavailable"):
        freya.api("PUT", "/api/v2/workflow/w1", {})
    assert len(st["calls"]) == 6 and st["sleeps"] == [5, 10, 20, 30, 30]


def test_retry_line_names_method_path_status_and_no_secrets(net, monkeypatch, capsys):
    monkeypatch.setitem(freya.HEADERS, "Authorization", "Bearer fak_SECRET")
    net([http(502), {"ok": 1}])
    freya.api("GET", "/api/v2/workflow/w1", workspaceId="ws1")
    out = capsys.readouterr().err   # diagnostics go to stderr (stdout carries --save JSON)
    assert "GET /api/v2/workflow/w1" in out and "502" in out
    assert "fak_SECRET" not in out and "Bearer" not in out and out.count("\n") == 1


# -- POST policy -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("err", [http(502), http(504), http(503), TimeoutError("read"), urllib.error.URLError(ConnectionResetError())])
def test_simulation_create_not_retried_when_it_may_have_run(net, err):
    st = net([err, {"simulation": {"id": "dup"}}])
    with pytest.raises((RuntimeError, OSError)):
        freya.api("POST", "/api/v2/simulations", {"agentId": "a"})
    assert len(st["calls"]) == 1


@pytest.mark.parametrize("err", [http(503, KEY_DOWN), urllib.error.URLError(ConnectionRefusedError()),
                                 urllib.error.URLError(socket.gaierror(-3, "name resolution"))])
def test_simulation_create_retried_when_rejected_before_handling(net, err):
    st = net([err, {"simulation": {"id": "s1"}}])
    assert freya.api("POST", "/api/v2/simulations", {"agentId": "a"}) == {"simulation": {"id": "s1"}}
    assert len(st["calls"]) == 2


def test_simulation_concurrency_409_still_handled_by_run_simulation(net, monkeypatch):
    monkeypatch.setattr(freya, "_now", lambda: 0.0)
    st = net([http(409, b"too many concurrent simulations"), {"simulation": {"id": "s1"}},
              {"simulation": {"status": "completed"}}, {"iterations": [{"id": "it"}]}])
    assert freya.run_simulation("ag", "n", {"p": {}}) == ("s1", "it", "completed")
    assert [c[0] for c in st["calls"]] == ["POST", "POST", "GET", "GET"]


@pytest.mark.parametrize("path, params", [
    ("/api/v2/simulations/s1/cancel", {}),                    # idempotent
    ("/api/v2/workflow", {"dryRun": "true"}),                 # validation only, creates nothing
    ("/api/v2/workflow", {}),                                 # duplicate = an orphan workflow, see freya._retryable
    ("/api/v2/personas", {}),                                 # duplicate = orphan personas; ids come from the response
    ("/api/v2/versions/workflow/w1/publish", {}),             # same versionId published again
])
def test_retried_posts(net, path, params):
    st = net([http(502), {"ok": 1}])
    assert freya.api("POST", path, {}, **params) == {"ok": 1}
    assert len(st["calls"]) == 2


def test_polling_does_not_stack_retries(net, monkeypatch):
    """A22's poller owns retries for polling GETs: one api() attempt per poll, its own backoff (2, 4, ...)."""
    clock = [0.0]
    monkeypatch.setattr(freya, "_now", lambda: clock[0])
    st = net([{"simulation": {"id": "s1"}}, http(502), {"simulation": {"status": "completed"}}, {"iterations": [{"id": "it"}]}])
    monkeypatch.setattr(freya.time, "sleep", lambda s: (st["sleeps"].append(s), clock.__setitem__(0, clock[0] + s)))
    assert freya.run_simulation("ag", "n", {"p": {}})[2] == "completed"
    assert st["sleeps"] == [10, 2]


# -- fix round 1 -------------------------------------------------------------------------------------------------

def test_auth_message_only_counts_under_auth_or_503_codes(net):
    st = net([http(400, b'{"message":"field x: temporarily unavailable"}'), {"ok": 1}])
    with pytest.raises(RuntimeError, match="400"):
        freya.api("GET", "/api/v2/workspaces")
    assert len(st["calls"]) == 1
    st = net([http(400, KEY_DOWN), {"ok": 1}])
    with pytest.raises(RuntimeError, match="400"):
        freya.api("POST", "/api/v2/simulations", {})
    assert len(st["calls"]) == 2


def test_auth_message_under_403_retried_even_for_simulation_create(net):
    st = net([http(403, KEY_DOWN), {"simulation": {"id": "s1"}}])
    assert freya.api("POST", "/api/v2/simulations", {}) == {"simulation": {"id": "s1"}}
    assert len(st["calls"]) == 2


def test_certificate_error_not_retried(net):
    st = net([urllib.error.URLError(ssl.SSLCertVerificationError("bad cert")), {"ok": 1}])
    with pytest.raises(OSError):
        freya.api("GET", "/api/v2/workspaces")
    assert len(st["calls"]) == 1


def test_retried_create_is_logged_and_the_second_id_kept(net, tmp_path, capsys):
    sf = tmp_path / "state.json"
    net([http(504), {"workflow": {"id": "w2", "agentId": "a2"}}])
    out = freya.save_workflow("slot", {}, state_file=str(sf))
    assert out == {"workflow_id": "w2", "agent_id": "a2"}
    assert json.loads(sf.read_text())["slot"] == {"workflow_id": "w2", "agent_id": "a2"}
    log = capsys.readouterr().err
    assert "POST /api/v2/workflow" in log and "duplicate" in log


def _update_outcomes(published_after):
    llm = {"agent_config": {"llmConfig": dict(freya.AGENT_LLM)}}
    return [{"ok": 1},                                                             # PUT
            {"workflow": {"workflow": llm}},                                       # GET workflow
            {"versions": [{"id": "v3", "versionNumber": 3, "isPublished": False}]},
            http(502),                                                             # publish: done, answer lost
            http(409, b'{"message":"version already published"}'),                # publish retry
            {"versions": [{"id": "v3", "versionNumber": 3, "isPublished": published_after}]}]


def test_publish_retry_conflict_is_success_when_the_version_is_published(net, monkeypatch):
    monkeypatch.setattr(freya, "_ws", "ws1")
    st = net(_update_outcomes(True))
    assert freya.update_workflow("w1", {}) == 3
    assert [c[0] for c in st["calls"]] == ["PUT", "GET", "GET", "POST", "POST", "GET"]


def test_publish_failure_raises_when_the_version_is_not_published(net, monkeypatch):
    monkeypatch.setattr(freya, "_ws", "ws1")
    net(_update_outcomes(False))
    with pytest.raises(RuntimeError, match="409"):
        freya.update_workflow("w1", {})


def _in_flight(release, outcome):
    def go():
        release.wait(5)
        return outcome
    return go


def test_bounded_timeout_stops_the_retrying_call(net):
    from builder import main as M
    release = threading.Event()
    st = net([_in_flight(release, http(503, KEY_DOWN)), {"ok": 1}, {"ok": 1}])
    bg = M.Bg(lambda: freya.api("PUT", "/api/v2/workflow/w1", {}), deadline_s=0.2)
    with pytest.raises(M.BudgetStop):
        bg.result(0.2)
    release.set()
    assert bg._done.wait(5)
    assert len(st["calls"]) == 1 and st["sleeps"] == []
    assert isinstance(bg.error, freya.CallAbandoned)


def test_abandoned_save_writes_no_state_file(net, tmp_path):
    from builder import main as M
    sf = tmp_path / "state.json"; release = threading.Event()
    st = net([_in_flight(release, {"workflow": {"id": "w1", "agentId": "a1"}}), {"ok": 1}])
    bg = M.Bg(lambda: freya.save_workflow("slot", {}, state_file=str(sf)), deadline_s=0.2)
    with pytest.raises(M.BudgetStop):
        bg.result(0.2)
    release.set()
    assert bg._done.wait(5)
    assert len(st["calls"]) == 1 and not sf.exists()
    assert isinstance(bg.error, freya.CallAbandoned)


def test_no_wait_that_would_end_after_the_deadline(net):
    from builder import main as M
    st = net([http(502), {"ok": 1}])
    bg = M.Bg(lambda: freya.api("GET", "/api/v2/workflow/w1"), deadline_s=3)      # first wait is 5 s
    with pytest.raises(RuntimeError, match="502"):
        bg.result(2)
    assert len(st["calls"]) == 1


def test_bounded_passes_its_timeout_as_the_deadline(net):
    from builder import main as M
    st = net([http(502), {"ok": 1}])
    with pytest.raises(RuntimeError, match="502"):
        M.bounded(lambda: freya.api("GET", "/api/v2/workflow/w1"), 3)
    assert len(st["calls"]) == 1


def test_unbound_calls_keep_the_full_retry(net):
    """Outside a Bg (tools, tests), behaviour is unchanged: time.sleep backoff, no deadline."""
    st = net([http(502), {"ok": 1}])
    assert freya.api("GET", "/api/v2/workflow/w1") == {"ok": 1}
    assert st["sleeps"] == [5]


# -- stdout stays machine-readable (results/regate.sh: `builder.compile --save | json.load`) -------------------------

def test_retry_during_compile_save_keeps_stdout_pure_json(net, tmp_path, capsys):
    from builder import compile as comp
    sf = tmp_path / "state.json"
    net([http(504), {"workflow": {"id": "w9", "agentId": "a9"}}])
    assert comp.main(["specs/reference/spec-a.json", "tools/openapi.json", "--save", "ours-A", "--state", str(sf)]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out) == {"workflow_id": "w9", "agent_id": "a9"}
    assert "retry 1/5" in cap.err and "504" in cap.err


def test_poll_and_busy_diagnostics_go_to_stderr(net, monkeypatch, capsys):
    net([http(503), {"ok": 1}])
    monkeypatch.setattr(freya, "_now", lambda: 0.0)
    assert freya._poll_api("s1", 0.0, 1000.0, "GET", "/api/v2/simulations/s1") == {"ok": 1}
    cap = capsys.readouterr()
    assert cap.out == "" and "transient error" in cap.err
    monkeypatch.setattr(freya, "api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("too many concurrent simulations")))
    with pytest.raises(RuntimeError, match="busy"):
        freya.run_simulation("a1", "n", {}, on_busy=lambda w: (_ for _ in ()).throw(RuntimeError("busy")) if w else None)
    cap = capsys.readouterr()
    assert cap.out == "" and "another simulation is running" in cap.err
