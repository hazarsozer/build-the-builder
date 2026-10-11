"""freya.api retry on transient failures (no network: urlopen and sleep are faked)."""
import io
import json
import socket
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
    out = capsys.readouterr().out
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
    ("/api/v2/workflow", {}),                                 # duplicate = an orphan workflow, see freya._post_policy
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
