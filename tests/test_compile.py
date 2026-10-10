import copy
import json

from builder.compile import compile as C
from builder.lint import lint

OA = json.load(open("tools/openapi.json"))
A = json.load(open("specs/reference/spec-a.json"))


def wf():
    return C(A, OA, base_url="https://bank.example/v1")


def test_structure():
    w = wf()
    n = w["nodes"]
    for k in ["setup", "opening", "third_party", "identity", "load_account", "prepare_account",
              "negotiate", "retry_promise", "goodbye"]:
        assert k in n
    assert w["initial_node"] == "setup" and "agent_config" not in w


def test_no_brief_constants_in_compiler_source():
    src = open("builder/compile.py").read() + open("builder/nodes.py").read() + open("builder/multi.py").read()
    for s in ["2026-10-26", "minimum_payment", "restructuring", "Freya Bank", "date_of_birth", "phone_last4",
              "collections", "credit card", "Freya", "hardship", "2026-"]:
        assert s not in src


def test_urls_use_base():
    assert "https://bank.example/v1/get_account" in json.dumps(wf())


def test_default_base_url_from_openapi():
    assert OA["servers"][0]["url"] + "/get_account" in json.dumps(C(A, OA))


def test_code_has_no_braces():
    for k, n in wf()["nodes"].items():
        for a in n.get("pre_actions", []):
            code = a["function"]["parameters"]["properties"].get("code", {}).get("default", "")
            assert "{{" not in code and "}}" not in code, k


def test_holiday_changes_follow_spec():
    s = copy.deepcopy(A)
    s["calendar"]["holidays"] = ["2026-10-20"]
    assert "2026-10-20" in json.dumps(C(s, OA))


def test_generic_event_branch():
    s = copy.deepcopy(A)
    s["events"].append({"id": "bankruptcy", "trigger": "says they filed for bankruptcy", "stages": ["negotiate"],
                        "say": "Thank you for telling us.", "action": "close", "outcome": "no_promise", "reason": "dispute"})
    w = C(s, OA)
    assert "close_bankruptcy" in w["nodes"]
    assert "bankruptcy" in json.dumps(w["nodes"]["negotiate"]["extraction_schema"])
    targets = [t["target_node"] for t in w["nodes"]["negotiate"]["transitions"]]
    assert "close_bankruptcy" in targets


def test_generic_transfer_event_branch():
    s = copy.deepcopy(A)
    s["events"].append({"id": "legal", "trigger": "says a lawyer handles this", "stages": ["negotiate"],
                        "say": None, "action": "transfer", "queue": "general", "outcome": "transferred", "reason": "human"})
    w = C(s, OA)
    neg = [t["target_node"] for t in w["nodes"]["negotiate"]["transitions"]]
    tnode = [k for k in neg if k.startswith("transfer_general")]
    assert tnode
    assert "close_legal" in [t["target_node"] for k in tnode for t in w["nodes"][k]["transitions"]]


def test_opening_line_is_verbatim():
    t = wf()["nodes"]["opening"]["on_enter"]["text"]
    assert t == A["opening"]["first_sentence"] + " Am I speaking with {{title}} {{last_name}}?"


def test_lint_clean():
    assert lint(wf(), A) == []


def test_lint_catches_recorded():
    s = copy.deepcopy(A)
    s["opening"]["first_sentence"] = "Hello, your payment is recorded."
    assert lint(C(s, OA), s)


def test_compiler_rephrases_unsafe_recording_clause():
    s = copy.deepcopy(A)
    s["opening"]["first_sentence"] = "Hello, this is a digital assistant calling from Some Bank, and this call is recorded."
    t = C(s, OA)["nodes"]["opening"]["on_enter"]["text"]
    assert t.startswith("Hello, this is a digital assistant calling from Some Bank.")
    assert "This call is monitored and stored for quality and security purposes." in t
    assert "and this call is recorded" not in t


def test_lint_catches_missing_node_and_bad_outcome():
    w = wf()
    w["nodes"]["identity"]["transitions"][0]["target_node"] = "nowhere"
    code = w["nodes"]["close_paid"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    code["default"] = code["default"].replace("'paid'", "'bogus'")
    errs = lint(w, A)
    assert any("nowhere" in e for e in errs) and any("bogus" in e for e in errs)


def test_farewells_end_with_goodbye():
    from builder.lint import farewell_of
    w = wf()
    closes = [k for k in w["nodes"] if k.startswith("close_")]
    assert len(closes) >= 15
    for k in closes:
        assert farewell_of(w["nodes"][k]).rstrip(".!").lower().endswith("goodbye"), k


def test_close_call_lifecycle_tool():
    t = wf()["tools"][0]
    assert t["config"]["alwaysRunsAt"] == ["end_of_call"]
    body = t["function"]["parameters"]["properties"]["body"]["properties"]
    oa = OA["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert body["outcome"]["enum"] == oa["outcome"]["enum"]
    assert body["outcome"]["default"] == "{{outcome}}"


def test_no_top_level_and():
    for k, n in wf()["nodes"].items():
        for t in n.get("transitions", []):
            assert t["condition"].get("logic") != "and", k


def test_no_null_values():
    def walk(x, p):
        assert x is not None, p
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, p + "." + k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, p + "[%d]" % i)
    walk(wf(), "")


def test_validate_against_freya_dry_run():  # live; skipped without FREYA_API_KEY
    import os
    import pytest
    if not os.environ.get("FREYA_API_KEY"):
        pytest.skip()
    import freya
    freya.validate_workflow("dryrun", wf())


# ---------------------------------------------------------------- code nodes run on sample data

def _code(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _run_account(now, product, spec=A):
    w = C(spec, OA)
    v = {"now": now, "title": "Mr", "last_name": "Reyes",
         "acct_first_json": json.dumps(product)}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    return v


CARD = {"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6,
        "minimum_payment": 35.0}


def test_prepare_account_early_band():
    v = _run_account("2026-10-14T11:00", CARD)
    assert v["account_state"] == "ok"
    assert v["product_words"] == "credit card ending 4821"
    assert v["past_due_text"] == "420.00" and v["floor_text"] == "35.00"
    assert "2026-11-02" in v["deadline_text"]
    assert "2026-10-16" in v["attempt_plan"] and "Attempt 2" not in v["attempt_plan"]
    assert "2026-10-26" not in v["allowed_dates_text"]
    assert v["debt_statement_text"].startswith(
        "I am calling from the collections team about a late payment on your credit card ending 4821.")
    assert "{" not in v["debt_statement_text"]


def test_prepare_account_late_band():
    p = dict(CARD, days_late=20)
    v = _run_account("2026-10-14T11:00", p)
    assert "2026-10-23" in v["deadline_text"]
    assert "Attempt 2" in v["attempt_plan"] and "2026-10-23" in v["attempt_plan"].split("Attempt 2")[1]


def test_prepare_account_reads_products_list():
    w = C(A, OA)
    v = {"now": "2026-10-14T11:00", "acct_products": [dict(CARD, type="car_loan", instalment_amount=210.5)]}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert v["product_words"] == "car loan" and v["floor_text"] == "210.50"


def test_prepare_account_examples_from_spec():
    for ex in A["examples"]:
        v = _run_account(ex["today"] + "T11:00", dict(CARD, days_late=ex["days_late"]))
        if ex["kind"] == "deadline":
            assert ex["expect"] in v["deadline_text"], ex
        else:
            assert ex["expect"] in v["attempt%d_text" % ex["attempt_index"]], ex


def test_prepare_account_error_without_product():
    w = C(A, OA)
    v = {"now": "2026-10-14T11:00"}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    exec(_code(w["nodes"]["prepare_account"]), {"vars": v})
    assert v["account_state"] == "error"


def test_setup_callback_rules():
    w = C(A, OA)
    v = {"now": "2026-10-16T11:00"}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    assert v["setup_state"] == "ok"
    assert "2026-10-16" in v["callback_rules"] and "2026-10-19" in v["callback_rules"]
    assert "10:00" in v["callback_rules"] and "19:00" in v["callback_rules"]
    v = {"now": "2026-10-16T19:30"}
    exec(_code(w["nodes"]["setup"]), {"vars": v})
    assert "not today" in v["callback_rules"]


def test_close_node_farewell_fills_vars():
    w = C(A, OA)
    v = {"title": "Mr", "last_name": "Reyes"}
    exec(_code(w["nodes"]["close_refused"]), {"vars": v})
    assert v["outcome"] == "no_promise" and v["reason"] == "refused"
    assert "Mr Reyes" in v["farewell"] and v["farewell"].endswith("Goodbye.")
    v = {}
    exec(_code(w["nodes"]["close_refused"]), {"vars": v})
    assert "  " not in v["farewell"] and " ," not in v["farewell"] and " ." not in v["farewell"]


# ---------------------------------------------------------------- fix round 1

def _neg_enum_desc(w):
    return w["nodes"]["negotiate"]["extraction_schema"][0]


def test_any_stage_excludes_third_party():
    w = wf()
    assert "transfer_general" not in [t["target_node"] for t in w["nodes"]["third_party"]["transitions"]]
    assert "human" not in w["nodes"]["third_party"]["extraction_schema"][0]["enum_values"]
    s = copy.deepcopy(A)
    [e for e in s["events"] if e["id"] == "human"][0]["stages"] = ["any", "third_party"]
    assert "transfer_general" in [t["target_node"] for t in C(s, OA)["nodes"]["third_party"]["transitions"]]


def test_banned_offer_overlapping_transfer_trigger_is_not_excluded_from_it():
    e = _neg_enum_desc(wf())
    desc = e["description"]
    restr = desc.split("restructuring:")[1].split(" human:")[0]
    assert "instalment" not in restr.split("Saying")[1]
    disc = desc.split("discount_request:")[1]
    assert "instalment" not in disc and "discount" in disc
    assert "instalment" not in wf()["nodes"]["negotiate"]["task_message"].split("If asked for")[1].split("\n")[0]


def test_counted_event_is_a_tool_call_transition():
    n = wf()["nodes"]["negotiate"]
    assert "not_talk" not in n["extraction_schema"][0]["enum_values"]
    tc = [t for t in n["transitions"] if t.get("is_tool_call") and t["target_node"] == "close_not_talk"]
    assert tc and "second time" in tc[0]["description"]
    assert "transition_to_close_not_talk" in n["task_message"]
    assert "paid" in n["extraction_schema"][0]["enum_values"]


def test_identity_formats_year_and_postcode():
    s = copy.deepcopy(A)
    s["identity"]["questions"] = [
        {"field": "postcode", "ask": "What is your postcode?", "format": "postcode"},
        {"field": "birth_year", "ask": "What year were you born?", "format": "year"}]
    oa = copy.deepcopy(OA)
    oa["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["field"]["enum"] = ["postcode", "birth_year"]
    w = C(s, oa)
    n = w["nodes"]["identity"]
    tool = n["tools"][0]
    consts = [l for l in tool["post_process_code"].split("\n") if l.startswith(("UNREADABLE", "NEXT_Q"))]
    descs = [p.get("description", "") for p in tool["function"]["parameters"]["properties"]["body"]["properties"].values()]
    blob = json.dumps([n["task_message"], n["on_enter"]["text"], consts, descs])
    assert "None" not in blob
    assert "postcode, letter by letter" in blob and "four-digit year" in blob


def test_extra_rules_naming_other_tools_are_dropped():
    s = copy.deepcopy(A)
    s["extra_rules"] += [{"stage": "identity", "text": "After both questions pass, call get_account."},
                         {"stage": "any", "text": "Every call ends with exactly one close_call."},
                         {"stage": "negotiate", "text": "Call create_promise once per product."}]
    w = C(s, OA)
    assert "get_account" not in w["nodes"]["identity"]["task_message"]
    assert "close_call" not in w["system_prompt"]
    assert "Call create_promise once per product." in w["nodes"]["negotiate"]["task_message"]


def test_negotiate_prompt_size():
    # Our own prompt budget for the reference spec, not a platform limit (controller ruling, Task 7b M6).
    assert len(wf()["nodes"]["negotiate"]["task_message"]) <= 4000


def test_reference_specs_still_valid():
    from builder.validate import validate
    for f in ("specs/reference/spec-a.json", "specs/reference/spec-b.json"):
        assert validate(json.load(open(f)), OA) == [], f


# ---------------------------------------------------------------- live gate fixes (G1-G3)

MONITORED = "This call is monitored and stored for quality and security purposes."


def test_g1_recording_clause_becomes_monitored():
    s = copy.deepcopy(A)
    s["opening"]["first_sentence"] = "Hello, this is a digital assistant calling from Some Bank. This call is being recorded for quality and security purposes."
    t = C(s, OA)["nodes"]["opening"]["on_enter"]["text"]
    assert MONITORED in t and "record" not in t.lower()


def test_g1_reference_specs_say_monitored():
    for f in ("specs/reference/spec-a.json", "specs/reference/spec-b.json"):
        assert MONITORED in json.load(open(f))["opening"]["first_sentence"], f


def test_g1_no_record_word_in_spoken_templates():
    import re
    w = wf()
    for k in ("opening", "identity", "negotiate"):
        n = w["nodes"][k]
        assert "record" not in n.get("on_enter", {}).get("text", "").lower(), k
        for quoted in re.findall(r'"([^"]*)"', n["task_message"]):
            assert "record" not in quoted.lower(), (k, quoted)
    neg = w["nodes"]["negotiate"]["task_message"]
    assert "latest date you can accept" in neg and "Dates you may accept" in neg


def test_g1_lint_errors_on_record_in_spoken_text():
    w = wf()
    w["nodes"]["opening"]["on_enter"]["text"] = "Hello. " + "This call is being recorded for quality and security."
    assert any("record" in e and not e.startswith("warning") for e in lint(w, A))
    w = wf()
    w["nodes"]["negotiate"]["task_message"] += '\n- Say "I have recorded it."'
    assert any("record" in e for e in lint(w, A))


def _human_transition(node):
    return [t for t in node["transitions"] if t["target_node"].startswith("transfer_general")][0]


def test_g2_human_transfer_requires_asked_flag():
    w = wf()
    for k in ("opening", "identity", "negotiate"):
        n = w["nodes"][k]
        c = json.dumps(_human_transition(n)["condition"])
        assert '"human_asked"' in c and '"human_insists"' in c, k
        assert _human_transition(n)["condition"]["conditions"][0]["logic"] == "and"
        enum = n["extraction_schema"][0]["enum_values"]
        assert "human_first_request" in enum and "human_insists" in enum and "human" not in enum
        assert "human_asked" not in [x["name"] for x in n["extraction_schema"]]
        first = [t for t in n["transitions"] if "human_first_request" in json.dumps(t["condition"])][0]
        assert first["target_node"] == "human_ask_" + k
        code = _code(w["nodes"]["human_ask_" + k])
        v = {}
        exec(code, {"vars": v})
        assert v["human_asked"] == "yes"
        hold = w["nodes"]["human_hold_" + k]
        assert hold["on_enter"]["text"] == "Can I help you with that first?"
        ht = {t["target_node"]: json.dumps(t["condition"]) for t in hold["transitions"]}
        back = "human_back_" + k
        assert '"insists"' in ht["transfer_general"] and '"other"' in ht[back]
        assert hold["max_turn_transition"] == back
        var = n["extraction_schema"][0]["name"]
        v = {var: "human_first_request"}
        exec(_code(w["nodes"][back]), {"vars": v})
        assert v[var] in ("pending", "continue") and v["human_reply"] == "pending"
        assert w["nodes"][back]["transitions"][-1]["target_node"] == k


def test_g2_say_rules_listed_as_not_events():
    w = wf()
    for k in ("opening", "identity", "negotiate"):
        d = w["nodes"][k]["extraction_schema"][0]["description"]
        assert "robot or AI" in d and "scam" in d and "NOT any transfer or close event" in d, k


def test_g3_no_figure_restated():
    v = _run_account("2026-10-14T11:00", dict(CARD, days_late=20))
    plan = v["attempt_plan"]
    assert "only the date" in plan.split("Attempt 2")[1]
    neg = wf()["nodes"]["negotiate"]["task_message"]
    assert "Never restate a figure already offered" in neg and "A read-back is not an offer" in neg
