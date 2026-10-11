# Task A22 report

Status: done. Base 1167f06.

- freya.py: `_poll_api` retries 429/5xx RuntimeErrors and any OSError (URLError, timeouts, resets) with backoff 2/4/8/15/30 s (last repeats); other 4xx raise. Applies to the status polling and the iterations fetch.
- 'cancelled'/'canceled' are terminal. New kwarg `deadline_s` (default 2400 s). On expiry raises `freya.SimulationTimeout` (a `TimeoutError`) with `.simulation_id` and the id in the message. The busy-workspace loop is unchanged.
- builder/selftest.run: optional `deadline_s` passthrough (only forwarded when set). builder/main.py not changed: it already wraps phases in its own `bounded(...)` budget, so it was not wired.
- tests/test_freya_polling.py: 9 offline tests (502 x2, URLError/timeout/reset, 429, iterations retry, 404 raises, cancelled/canceled, deadline while running, deadline during persistent 502).
- Suite: 1028 passed, 6 skipped.

Concerns: main's orphan-cancel logic does not yet catch SimulationTimeout specifically (it can read `.simulation_id`); the id is also already known to main through on_created.
