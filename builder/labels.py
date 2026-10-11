"""Readable labels for the dashboard (presentation only: no condition, target or code changes).

    describe(wf)   # in place

- every `always` transition gets a short label naming its target instead of "always": "Next: prepare account" when
  it is the node's only way on, "Otherwise: close the call (service error)" after conditional routes;
- every code node's code_exec pre-action gets one line saying what the step does;
- a pre-action api tool whose description is only its own name gets one line saying what the call does.

Node names are the builder's own (compile.py, multi.py), so the step descriptions key on them. Event ids inside a
name (close_<event>, wrap_<event>, <event>_offer) are shown as words.
"""
import re

# pre-action api tools (not model-visible) whose description would otherwise be the tool name
API_PURPOSE = {
    "get_account": "Fetch the customer's account and overdue products.",
    "create_promise": "Record a payment promise for one product.",
    "add_note": "Add a note to the customer's account.",
    "transfer_to_agent": "Transfer the call to a team at the bank.",
}

# (node-name pattern, description); first match wins. {0}, {1} are the groups as words.
CODE_PURPOSE = [
    (r"setup", "Reads the call variables (customer, clock) and sets safe defaults for the call."),
    (r"setup_retry", "Runs the call setup once more if the first run did not finish."),
    (r"prepare_account", "Works out the facts of every overdue product (amounts, dates, offer plan) from the account data."),
    (r"opening_ask", "Counts a confirmation ask the person did not answer (small talk is not counted); after the last one, closes the call as unclear."),
    (r"id_decline_gate", "Sends a decline back to the identity check when it carried an answer to check, or before the one explanation was given; otherwise closes the call."),
    (r"record_plan", "Lists the confirmed promises to record, one slot per product."),
    (r"readback_reset", "Reopens the negotiation after the customer did not confirm the read-back."),
    (r"negotiate_end", "Ends the negotiation: reads back the agreed promises, or wraps up when nothing was agreed."),
    (r"finish", "Routes to the close that matches the result of the call."),
    (r"wrap_recorded", "Works out the result of the call after the promises were recorded, and the notes to write."),
    (r"wrap_done", "Works out the result of the call when no promise was recorded, and the notes to write."),
    (r"wrap_(.+)", "Works out the result of the call for: {0}, and the notes to write."),
    (r"after_record", "Counts the visits to the anything-else step after the promises were recorded; past the cap, ends the call as nothing else."),
    (r"after_record_done", "Clears the spoken sentence and goes on to the notes and the close after the promises were recorded."),
    (r"(.+)_offer_after_record", "Marks the {0} offer as made after the promises were recorded, before it is spoken."),
    (r"close_(.+)_transfer", "Sets the outcome, reason and farewell after the transfer for: {0}."),
    (r"close_(.+)", "Sets the outcome, reason and farewell for: {0}."),
    (r"(.+)_ask_(.+)", "Marks that the agent asked once whether it can help first ({0}, {1} step)."),
    (r"(.+)_back_(.+)", "Clears the reply and returns to the {1} step."),
    (r"(.+)_offer", "Marks the {0} offer as made, before it is spoken."),
    (r"(.+)_back", "Returns to the negotiation: the customer named a payment during the {0} offer."),
]


def words(name):
    return name.replace("_", " ").strip()


def code_purpose(name):
    for pat, text in CODE_PURPOSE:
        m = re.fullmatch(pat, name)
        if m:
            return text.format(*[words(g) for g in m.groups()])
    return "Code step: %s." % words(name)


def target_label(target, nodes, otherwise=False):
    """"Next: <target>" for a node's only way on; "Otherwise: <target>" for the fallback after conditional routes."""
    lead = "Otherwise: " if otherwise else "Next: "
    nd = nodes.get(target) or {}
    if nd.get("node_type") == "end_call":
        return lead + "end the call"
    m = re.fullmatch(r"close_(.+)", target)
    if m:
        return lead + "close the call (%s)" % words(m.group(1))
    return lead + words(target)


def describe(wf):
    nodes = dict(wf.get("nodes") or {}, **(wf.get("global_nodes") or {}))
    for name, nd in nodes.items():
        trs = nd.get("transitions") or []
        for t in trs:
            if (t.get("condition") or {}).get("operator") == "always" and t.get("description") == "always":
                t["description"] = target_label(t.get("target_node"), nodes, otherwise=len(trs) > 1)
        for pa in nd.get("pre_actions") or []:
            fn = pa.get("function") or {}
            if fn.get("handler") == "__function__:code_exec" and not fn.get("description"):
                fn["description"] = code_purpose(name)
            elif fn.get("description") == fn.get("name") and fn.get("name") in API_PURPOSE:
                fn["description"] = API_PURPOSE[fn["name"]]
    return wf
