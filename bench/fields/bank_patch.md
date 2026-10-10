# What `bank/server.py` would need to serve `bench/fields`

Not applied: the corpus does not touch `bank/`. Line numbers are from `bank/server.py` at commit 61ce078.

## What the bank does today with `bench/fields/openapi.json`

I replayed the expected ledgers of all 8 cases in-process against `Bank(bench/fields/openapi.json)`, and all 8 pass `score_ledger`. The bank still serves the old shapes:

- `verify_identity` returns `{"ok", "verified", "wrong_answers_left", "all_verified"}` (lines 169, 170, 178, 186). Those key names are hard-coded. The openapi response schema is never read.
- `create_promise` ignores `channel`. Unknown body fields pass through `args`, and the bank then:
  - does not check the enum,
  - does not reject a missing `channel` although the schema marks it `required`,
  - does not copy `channel` into the `promises` ledger entry (line 201).

  The value survives only in `ledger.calls[n].args.channel`.

So on our bank an agent built for the renamed fields would read `verified` / `wrong_answers_left` correctly *by accident*. The bench would not test H15. It would also not catch a missing or wrong `channel` (H4).

## Change 1: response keys follow the openapi (H15)

In `Bank.__init__` (after line 110), derive a rename map from the `verify_identity` 200 response schema:

```python
props = ((openapi.get("paths", {}).get("/verify_identity", {}).get("post", {}).get("responses", {})
          .get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("properties")) or {})
RENAMES = {"verified": ["match"], "wrong_answers_left": ["attempts_left"]}   # internal name -> accepted spellings
self.out_keys = {}
for internal, spellings in RENAMES.items():
    if internal not in props:
        hit = next((s for s in spellings if s in props), None)
        if hit: self.out_keys[internal] = hit
```

In `Bank.tool`, after line 154, rename the keys of the 200 body for this tool only:

```python
if tool == "verify_identity" and status == 200 and self.out_keys:
    result = {self.out_keys.get(k, k): v for k, v in result.items()}
```

Keep the internal state names (`s["verified"]`, `s["wrong"]`) and the ledger keys unchanged, because `tools/score.py` reads them. The ledger's `calls[n].result` then shows the renamed keys, the same as the agent saw.

A more general version would also do this:

- drop any 200-body key that the response schema does not list. This covers a "wrong_answers_left absent" variant.
- leave `ok` and `error` in place, because every response schema has them.

## Change 2: `create_promise.channel` (H4)

In `t_create_promise` (line 192), after the `date` check and before the duplicate-promise check:

```python
schema = self.openapi["paths"]["/create_promise"]["post"]["requestBody"]["content"]["application/json"]["schema"]
for f in schema.get("required", []):
    if f not in ("customer_id",) and a.get(f) in (None, ""): return {"ok": False, "error": f + " is required"}
for f, spec in schema.get("properties", {}).items():
    if "enum" in spec and f in a and a[f] not in spec["enum"]: return {"ok": False, "error": f + " must be " + say_or(spec["enum"])}
```

Then add the declared extra fields to the ledger entry on line 201:

```python
extra = {f: a[f] for f in schema.get("properties", {}) if f not in ("customer_id", "product_id", "amount", "date") and f in a}
s["promises"].append({..., "date": a["date"], **extra})
```

There is a caveat about the error text. We have never probed what the official mock says for a missing or invalid extra field. The texts above are our guesses, written in the same style as the existing "date must be YYYY-MM-DD".

## Change 3 (optional): score the channel from the ledger

`tools/score.py` compares promises on `(product_id, amount, date)` only. To make the channel a ledger check instead of a judge `checks` line, `score_ledger` could also compare `channel` whenever the expected promise has one:

```python
key = lambda p: (p["product_id"], round(float(p["amount"]), 2), p["date"]) + ((p["channel"],) if "channel" in p else ())
```

This needs Change 2, so that the ledger promise carries `channel`. The official scorer will not do this, so keep the `checks` line either way.
