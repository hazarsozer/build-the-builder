"""freya.run_simulation polling resilience (no network: api, sleep and the clock are faked)."""
import urllib.error

import pytest

import freya


def make(monkeypatch, statuses, iters=None):
    """statuses: status strings or exceptions, consumed per GET /simulations/<id>; the last one repeats."""
    st = list(statuses); clock = [0.0]; calls = []

    def api(method, path, body=None, **params):
        calls.append(path)
        if method == "POST": return {"simulation": {"id": "sim-7"}}
        if path.endswith("/iterations"):
            if iters:
                x = iters.pop(0)
                if isinstance(x, Exception): raise x
            return {"iterations": [{"id": "it-1"}]}
        x = st.pop(0) if len(st) > 1 else st[0]
        if isinstance(x, Exception): raise x
        return {"simulation": {"status": x}}
    monkeypatch.setattr(freya, "api", api)
    monkeypatch.setattr(freya.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(freya, "_now", lambda: clock[0])
    return calls


def e502(): return RuntimeError("GET /api/v2/simulations/sim-7 -> 502: error code: 502")


def test_502_twice_then_completed(monkeypatch):
    calls = make(monkeypatch, [e502(), e502(), "completed"])
    assert freya.run_simulation("ag", "n", {"p": {}}) == ("sim-7", "it-1", "completed")
    assert len(calls) == 5


def test_urlerror_and_timeout_retried(monkeypatch):
    make(monkeypatch, [urllib.error.URLError("reset"), TimeoutError("t"), ConnectionResetError(), "completed"])
    assert freya.run_simulation("ag", "n", {"p": {}})[2] == "completed"


def test_429_retried(monkeypatch):
    make(monkeypatch, [RuntimeError("GET x -> 429: slow down"), "completed"])
    assert freya.run_simulation("ag", "n", {"p": {}})[2] == "completed"


def test_iterations_fetch_retried(monkeypatch):
    make(monkeypatch, ["completed"], iters=[e502()])
    assert freya.run_simulation("ag", "n", {"p": {}}) == ("sim-7", "it-1", "completed")


def test_404_raises(monkeypatch):
    make(monkeypatch, [RuntimeError("GET x -> 404: not found")])
    with pytest.raises(RuntimeError, match="404"):
        freya.run_simulation("ag", "n", {"p": {}})


@pytest.mark.parametrize("status", ["cancelled", "canceled"])
def test_cancelled_is_terminal(monkeypatch, status):
    make(monkeypatch, [status])
    assert freya.run_simulation("ag", "n", {"p": {}}) == ("sim-7", "it-1", status)


def test_deadline_running_forever(monkeypatch):
    make(monkeypatch, ["running"])
    with pytest.raises(TimeoutError) as ei:
        freya.run_simulation("ag", "n", {"p": {}}, deadline_s=100)
    assert ei.value.simulation_id == "sim-7" and "sim-7" in str(ei.value)


def test_deadline_during_persistent_502(monkeypatch):
    make(monkeypatch, [e502()])
    with pytest.raises(TimeoutError) as ei:
        freya.run_simulation("ag", "n", {"p": {}}, deadline_s=100)
    assert ei.value.simulation_id == "sim-7"
