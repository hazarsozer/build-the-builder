"""freya.run_simulation's optional on_created / on_busy hooks (no network: api and sleep are faked)."""
import pytest

import freya


class Api:
    def __init__(self, busy=0):
        self.busy, self.calls = busy, []

    def __call__(self, method, path, body=None, **params):
        self.calls.append((method, path))
        if method == "POST" and path == "/api/v2/simulations":
            if self.busy:
                self.busy -= 1
                raise RuntimeError("POST /api/v2/simulations -> 409: too many concurrent simulations")
            return {"simulation": {"id": "sim-9"}}
        if path == "/api/v2/simulations/sim-9":
            return {"simulation": {"status": "completed"}}
        if path.endswith("/iterations"):
            return {"iterations": [{"id": "it-1"}]}
        raise AssertionError(path)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(freya.time, "sleep", lambda s: None)

    def make(busy=0):
        api = Api(busy)
        monkeypatch.setattr(freya, "api", api)
        return api
    return make


def test_default_behaviour_unchanged(fake):
    fake(busy=1)
    assert freya.run_simulation("ag", "n", {"p": {}}) == ("sim-9", "it-1", "completed")


def test_on_created_called_before_waiting(fake):
    api = fake()
    seen = []
    freya.run_simulation("ag", "n", {"p": {}}, on_created=lambda sid: seen.append((sid, len(api.calls))))
    assert seen == [("sim-9", 1)]          # right after the create call, before any status poll


def test_on_busy_reports_wait_and_can_stop(fake):
    fake(busy=3)
    waits = []
    freya.run_simulation("ag", "n", {"p": {}}, on_busy=waits.append)
    assert waits == [0, 30, 60]

    fake(busy=10)

    def stop(w):
        if w >= 60:
            raise TimeoutError("busy")
    with pytest.raises(TimeoutError):
        freya.run_simulation("ag", "n", {"p": {}}, on_busy=stop)
