"""Replay a corpus brief's expected records on our bank (in process, no HTTP) and score them.

Checks that each case's seed, the brief's outcome table (seed.reasons) and the mutated openapi enums agree:
an agent that does exactly what `expected` says gets a passing ledger.
"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from bank.server import Bank
from score import score_ledger


def replay(brief_dir):
    brief_dir = pathlib.Path(brief_dir)
    openapi = json.loads((brief_dir / "openapi.json").read_text())
    for c in json.loads((brief_dir / "cases.json").read_text()):
        bank = Bank(openapi)
        status, r = bank.session({"seed": c["seed"], "case": c["id"]})
        assert status == 200 and r["ok"], (c["id"], r)
        cid, exp = r["variables"]["customer_id"], c["expected"]

        def call(tool, **args):
            for _ in range(3):                          # a faulted call (HTTP 500) is retried, as the briefs say
                st, res = bank.tool(tool, {"customer_id": cid, **args})
                if st == 200: break
            assert st == 200 and res.get("ok"), (c["id"], tool, args, res)
            return res

        reached = not (c.get("third_party") or exp["callbacks"] or exp["outcome"] in ("not_verified", "identity_failed"))
        if reached:
            for field, answer in c["identity"].items(): assert call("verify_identity", field=field, answer=answer)["verified"]
            ids = [p["product_id"] for p in call("get_account")["products"]]
            for p in exp["promises"]:
                assert p["product_id"] in ids, (c["id"], p)
                call("create_promise", product_id=p["product_id"], amount=p["amount"], date=p["date"])
            for n in exp["notes"]: call("add_note", product_id=n["product_id"], reason=n["reason"])
            for t in exp["transfers"]: call("transfer_to_agent", queue=t["queue"])
        for cb in exp["callbacks"]: call("schedule_callback", date=cb["date"], time=cb["time"])
        call("close_call", outcome=exp["outcome"], **({"reason": exp["reason"]} if exp["reason"] else {}))
        _, led = bank.ledger(cid)
        s = score_ledger(c, led)
        assert s["pass"], (c["id"], s["failed"])
        # the seed's faults are the case's faults, and they fired
        for f in c.get("faults") or []:
            assert led["failures"].get(f["tool"]) == f["fail_first_n"], (c["id"], led["failures"])
        assert 2 <= len(c["checks"]) <= 4 and c["script"], c["id"]
        # the close is accepted only with a reason from the table: a wrong one is refused
        if exp["reason"]:
            b2 = Bank(openapi); _, r2 = b2.session({"seed": c["seed"]})
            _, bad = b2.tool("close_call", {"customer_id": r2["variables"]["customer_id"], "outcome": exp["outcome"], "reason": "refused_" + exp["reason"]})
            assert not bad["ok"], (c["id"], "bank accepted an unknown reason")
