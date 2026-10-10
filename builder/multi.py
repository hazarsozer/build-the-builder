"""Several products per call (spec.multi_product): the parts of the graph that differ from one product.

Graph (negotiate onwards):

    prepare_account (+ MP_ACCOUNT_CODE: mp_state, products_text, statements, first ask)
        -> negotiate  [model tool set_product; its pre-hook is the state machine]
             mp_phase == confirmed  -> record_plan -> record_1 [-> retry_1] -> record_2 ... -> wrap_recorded
             mp_phase == done       -> wrap_done
             support declined       -> wrap_<offer-transfer event id>
             close events           -> wrap_<id>       (e.g. a second "do not want to talk")
             transfers              -> transfer_<queue> -> close_<id>   (no notes at all)
        wrap_*  (decides notes and the close, dates.close_reason) -> note_1 -> note_2 -> note_3 -> finish -> close_*

set_product is an api_call tool whose request goes to the read-only get_account route: the model reports a
settled product (agreed / refused / dispute / paid ...) or the confirmation, and the pre-hook validates it,
updates the JSON state in vars and writes the next instruction. Nothing is written to the bank until the
confirmation; promises and notes are then written by engine-run nodes, never by the model.
Verified 10 Oct (probe): a pre-hook sees the model's arguments in config['body'] and its vars writes persist;
a post-hook sees vars but not <tool>_args.
"""
from builder.lib import dates, embed, money
from builder.nodes import all_of, always, assert_no_braces, close_node, code_node, cond_any, eq, go, py_json

SLOTS = 3          # products per call the engine can record / note (the brief allows up to three)
SET_TOOL = "set_product"
SET_MODE = "code"  # "code" (code_exec tool, nothing in the bank ledger) or "api" (get_account route + pre-hook)
PHASE = "mp_phase"


def _const(name, value):
    return "%s = %s\n" % (name, py_json(value))


# ---------------------------------------------------------------- prepare_account addition

MP_ACCOUNT_CODE = r'''
try:
    plans = plan_products([f['product'] for f in facts], today, BANDS, CAL, PRODUCTS)
    order = list(range(len(facts)))
    if MP.get('order') == 'highest_past_due':
        order.sort(key=lambda i: -facts[i]['past_due'])
    elif MP.get('order') == 'most_days_late':
        order.sort(key=lambda i: -facts[i]['days_late'])
    st = dict(today=today.isoformat(), order=[], p=dict(), ga=[plans[i]['product_id'] for i in range(len(facts))])
    lines = []
    for k, i in enumerate(order):
        f = facts[i]
        pl = plans[i]
        pid = pl['product_id']
        att = [a['text'].split(': ', 1)[1] for a in f['attempts']]
        st['order'].append(pid)
        st['p'][pid] = dict(num=k + 1, words=f['words'], full=f['past_due'], floor=f['floor'],
                            dl=pl['deadline'].isoformat(), att=att, status='pending', amount=None, date=None, capped=False)
        n = len(att)
        lines.append('Product ' + str(k + 1) + ' (product_id ' + pid + '): ' + f['words'] + '. Past due ' + fmt(f['past_due']) +
                     ', ' + str(f['days_late']) + ' days late, ' + str(pl['band']).upper() + ' band. Floor (lowest amount you may accept): ' +
                     fmt(f['floor']) + '. Deadline (latest date you may accept): ' + speak(pl['deadline']) + '.\n  Persuasion plan: ' +
                     ' '.join('Attempt ' + str(j + 1) + ': ' + t for j, t in enumerate(att)) + ' ' +
                     NUMS.get(str(n), str(n)).capitalize() + (' persuasion attempt' if n == 1 else ' persuasion attempts') + ' for this product.')
    last = max(pl['deadline'] for pl in plans)
    days = dict()
    d = today
    while d <= last + datetime.timedelta(days=120):
        days[d.isoformat()] = [say_date(d), 1 if is_bd(d, CAL) else 0, next_bd(d, CAL).isoformat(),
                               prev_bd(d, CAL).isoformat()]
        d = d + datetime.timedelta(days=1)
    st['days'] = days
    vars['mp_state'] = json.dumps(st)
    vars['mp_phase'] = 'open'
    vars['mp_msg'] = ''
    vars['mp_readback'] = ''
    vars['rb_answer'] = 'pending'
    vars['products_text'] = '\n'.join(lines)
    vars['products_count'] = str(len(order))
    if len(order) > 1:
        vars['debt_statement_text'] = ' '.join(facts[i]['debt_statement_one'] for i in order)
        vars['first_ask_text'] = NEG.get('first_ask_all') or NEG['first_ask']
    else:
        vars['debt_statement_text'] = facts[order[0]]['debt_statement']
        vars['first_ask_text'] = NEG['first_ask']
    vars['calendar_text'] = calendar_text(last).replace(', AFTER THE DEADLINE', '')
    vars['account_state'] = 'ok'
except Exception as e:
    vars['account_state'] = 'error'
    vars['account_error'] = 'multi: ' + str(e)[:200]
'''


# ---------------------------------------------------------------- set_product hooks

SET_PRE = r'''
def _num(x):
    try:
        return float(str(x).replace(',', '').strip().lstrip('$').strip())
    except Exception:
        return None


def _date(x):
    t = str(x if x is not None else '').strip()[:10]
    return t if re.match(r'^\d{4}-\d{2}-\d{2}$', t) else None


def _say(d):
    return DAYS.get(d, [d])[0]


def _bd(d):
    return d in DAYS and DAYS[d][1] == 1


def _targets(st, ref, open_only):
    r = str(ref if ref is not None else '').strip()
    rl = r.lower()
    if rl in ('all', 'every', 'everything', 'both', 'all products', '*'):
        return [q for q in st['order'] if st['p'][q]['status'] in open_only], True
    if r in st['p']:
        return [r], False
    for q in st['order']:
        if q.lower() == rl:
            return [q], False
    m = re.match(r'^(?:product\s*)?(\d+)$', rl)
    if m and 1 <= int(m.group(1)) <= len(st['order']):
        return [st['order'][int(m.group(1)) - 1]], False
    hits = [q for q in st['order'] if rl and (rl in st['p'][q]['words'].lower() or st['p'][q]['words'].lower() in rl)]
    if len(hits) == 1:
        return hits, False
    return [], False


def _readback(st):
    parts = []
    for q in st['order']:
        P = st['p'][q]
        if P['status'] != 'agreed':
            continue
        s = fmt(P['amount']) + ' for your ' + P['words'] + ' on ' + _say(P['date'])
        if P.get('capped'):
            s += ', because that is the latest date I can accept for it'
        parts.append(s)
    if len(parts) > 1:
        body = '; '.join(parts[:-1]) + '; and ' + parts[-1]
    else:
        body = parts[0]
    return 'To confirm, you will pay ' + body + '. ' + CFG['confirm']


def _next(st):
    pend = [q for q in st['order'] if st['p'][q]['status'] == 'pending']
    if pend:
        P = st['p'][pend[0]]
        return 'open', ('Next is the ' + P['words'] + ' (product_id ' + pend[0] + '). If the customer already gave an answer for it, call ' +
                        TOOL + ' for it now. Otherwise make attempt 1 of its plan: ' + P['att'][0] + ' Do not ask about today again.')
    if [q for q in st['order'] if st['p'][q]['status'] == 'agreed']:
        return 'readback', 'Every product is settled. The read-back is spoken automatically: say nothing more.'
    return 'done', 'Every product is settled and there is nothing to confirm. Say nothing more.'


def _agree(st, b, today):
    targets, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
    if not targets:
        return None, 'Unknown product_id. Use one of ' + ', '.join(st['order']) + ', or ALL.'
    d = _date(b.get('date'))
    if d is None:
        return None, 'NOT accepted: give the date as YYYY-MM-DD.'
    if d < today:
        return None, 'NOT accepted: that date is in the past. Ask for a date from today on.'
    if d not in DAYS:
        return None, 'NOT accepted: ' + d + ' is not a date that can be used. Ask the customer for the exact day again.'
    raw = str(b.get('amount') if b.get('amount') is not None else 'full').strip().lower()
    shared = every and len(targets) > 1
    new = dict()
    errs = []
    for q in targets:
        P = st['p'][q]
        if raw in ('full', 'all', '', 'none', 'past_due', 'past due'):
            amt = P['full']
        elif raw in ('floor', 'minimum', 'min'):
            amt = P['floor']
        else:
            amt = _num(raw)
        if amt is None or amt != amt or amt <= 0:
            errs.append('give the amount for the ' + P['words'] + ' as a number, or full or floor')
            continue
        if amt > P['full'] + 0.005:
            errs.append(fmt(amt) + ' is more than the past-due amount of the ' + P['words'] + ' (' + fmt(P['full']) + '); offer ' + fmt(P['full']))
            continue
        if amt < P['floor'] - 0.005 and CFG['below'] != 'accept':
            if CFG['below'] == 'offer_floor_once':
                errs.append(fmt(amt) + ' is below the floor of the ' + P['words'] + ': say it cannot be accepted and offer the floor ' + fmt(P['floor']) + ' once')
            else:
                errs.append(fmt(amt) + ' is below the floor of the ' + P['words'] + ': say it cannot be accepted')
            continue
        dl = P['dl']
        dd = d
        capped = False
        if d <= dl and not _bd(d):
            if CFG['nbd'] == 'previous_business_day' and DAYS[d][3] >= today:
                errs.append(_say(d) + ' is not a business day: offer the business day before it, ' + _say(DAYS[d][3]))
            elif CFG['nbd'] == 'reject':
                errs.append(_say(d) + ' is not a business day: say so and ask for a business day')
            elif DAYS[d][2] <= dl:
                errs.append(_say(d) + ' is not a business day: offer the next business day, ' + _say(DAYS[d][2]))
            else:
                errs.append(_say(d) + ' is not a business day and the next business day is after the deadline of the ' + P['words'] +
                            ': offer its deadline, ' + _say(dl))
            continue
        if d > dl:
            if shared and CFG['cap']:
                dd = dl
                capped = True
            elif CFG['after'] == 'reject':
                errs.append(_say(d) + ' is after the deadline of the ' + P['words'] + ': say it cannot be accepted and ask for a date on or before ' + _say(dl))
                continue
            else:
                errs.append(_say(d) + ' is after the deadline of the ' + P['words'] + ': say the latest date you can accept for it is ' + _say(dl) + ' and offer that date')
                continue
        new[q] = (round(amt, 2), dd, capped)
    if errs:
        return None, 'NOT accepted, nothing changed: ' + '; '.join(errs) + '.'
    for q, v in new.items():
        st['p'][q].update(status='agreed', amount=v[0], date=v[1], capped=v[2])
    return st, 'Noted for now (nothing is registered before the confirmation).'


def _refuse(st, b):
    targets, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
    if every and len(targets) > 1:
        return None, 'Go through the products one at a time, in order. Each product has its own persuasion plan.'
    if not targets:
        return None, 'Unknown product_id. Use one of ' + ', '.join(st['order']) + '.'
    q = targets[0]
    P = st['p'][q]
    n = len(P['att'])
    k = _num(b.get('attempts_made'))
    k = 0 if (k is None or k != k) else int(k)
    if k < n:
        return None, ('Not yet: the ' + P['words'] + ' has ' + str(n) + ' persuasion attempts and you have made ' + str(k) +
                      '. Make attempt ' + str(k + 1) + ' now: ' + P['att'][k] + ' Never repeat an offer the customer already refused.')
    P.update(status='refused', amount=None, date=None, capped=False)
    return st, 'Noted. Make no more offers for the ' + P['words'] + '.'


def _note(st, b, out):
    targets, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
    if not targets:
        return None, 'Unknown product_id. Use one of ' + ', '.join(st['order']) + ', or ALL.'
    for q in targets:
        st['p'][q].update(status=out, amount=None, date=None, capped=False)
    return st, 'Noted. ' + CFG['say'].get(out, '')


try:
    import json, re
    b = ARGS()
    if not isinstance(b, dict):
        b = dict()
    st = json.loads(str(vars.get('mp_state') or '{}'))
    today = st['today']
    DAYS = st.get('days') or dict()
    out = str(b.get('outcome') or '').strip().lower()
    phase = str(vars.get('mp_phase') or 'open')
    if out == 'review':
        phase, nxt = _next(st)
        msg = ('Not every product is settled yet. ' + nxt) if phase == 'open' else nxt
    else:
        if out == 'agreed':
            new, ack = _agree(st, b, today)
        elif out == 'refused':
            new, ack = _refuse(st, b)
        elif out in CFG['notes']:
            new, ack = _note(st, b, out)
        else:
            new, ack = None, 'Unknown outcome. Use one of: agreed, refused, ' + ', '.join(CFG['notes']) + ', review.'
        if new is None:
            msg = ack
        else:
            st = new
            phase, nxt = _next(st)
            msg = (ack + ' ' + nxt).strip()
    if phase == 'readback':
        vars['mp_readback'] = _readback(st)
        vars['rb_answer'] = 'pending'
    vars['mp_state'] = json.dumps(st)
    vars['mp_phase'] = phase
    vars['mp_msg'] = msg
except Exception as e:
    vars['mp_msg'] = 'There was a technical problem with that step (' + str(e)[:60] + '). Call ' + TOOL + ' again with the same values.'
'''

API_ARGS = r'''
def ARGS():
    return config.get('body') if isinstance(config, dict) else None
'''

CODE_ARGS = r'''
def ARGS():
    try:
        return params
    except NameError:
        return None
'''

# a code_exec tool returns data null; _result is tried as the result channel, mp_msg is also in the prompt
CODE_RESULT = r'''
try:
    _result = dict(ok=True, what_to_do_next=str(vars.get('mp_msg') or 'Continue.'), phase=str(vars.get('mp_phase') or 'open'))
except Exception:
    pass
'''

SET_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        result['data'] = dict(ok=True, what_to_do_next=str(vars.get('mp_msg') or 'Continue.'), phase=str(vars.get('mp_phase') or 'open'))
except Exception:
    pass
'''


# ---------------------------------------------------------------- engine-run chain

RECORD_PLAN = r'''
import json
try:
    st = json.loads(str(vars.get('mp_state') or '{}'))
    ags = [q for q in st['order'] if st['p'][q]['status'] == 'agreed']
except Exception:
    st, ags = dict(order=[], p=dict()), []
for i in range(1, SLOTS + 1):
    if i <= len(ags):
        P = st['p'][ags[i - 1]]
        vars['rec' + str(i) + '_pid'] = ags[i - 1]
        vars['rec' + str(i) + '_amount'] = P['amount']
        vars['rec' + str(i) + '_date'] = P['date']
        vars['rec_has_' + str(i)] = 'yes'
    else:
        vars['rec' + str(i) + '_pid'] = ''
        vars['rec_has_' + str(i)] = 'no'
'''

REC_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        b['product_id'] = str(vars.get('rec' + SLOT + '_pid') or b.get('product_id') or '')
        b['amount'] = float(vars.get('rec' + SLOT + '_amount') or b.get('amount'))
        b['date'] = str(vars.get('rec' + SLOT + '_date') or b.get('date') or '')[:10]
except Exception:
    pass
'''

REC_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        good = str(result.get('status', 'success')) == 'success' and d.get('ok') is True
        vars['rec' + SLOT + '_status'] = 'recorded' if good else 'failed'
except Exception:
    pass
'''

NOTE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        b['product_id'] = str(vars.get('note' + SLOT + '_pid') or b.get('product_id') or '')
        b['reason'] = str(vars.get('note' + SLOT + '_reason') or b.get('reason') or '')
except Exception:
    pass
'''

WRAP_CODE = r'''
import json
def _true(k):
    return str(vars.get(k)).lower() == 'true'
try:
    st = json.loads(str(vars.get('mp_state') or '{}'))
    order = st.get('order') or []
except Exception:
    st, order = dict(p=dict()), []
recorded = []
for i in range(1, SLOTS + 1):
    s = str(i)
    pid = str(vars.get('rec' + s + '_pid') or '')
    if pid and RECORDING and (vars.get('rec' + s + '_status') == 'recorded' or _true('rec' + s + '_ok') or _true('rec' + s + '_retry_ok')):
        recorded.append(pid)
notes = []
failed = []
for q in (st.get('ga') or order):
    P = st['p'][q]
    if q in recorded:
        continue
    stt = P.get('status')
    if stt == 'agreed':
        r = FAIL_NOTE if RECORDING else END_NOTE
        if RECORDING:
            failed.append(P['words'])
    elif stt == 'pending':
        r = END_NOTE
    else:
        r = stt
    if NOTES_ON and r in REASONS:
        notes.append((q, r))
outcome, reason = close_reason(notes, len(recorded), None, RULE, ALL_PAID, FALLBACK)
key = str(outcome) + '|' + str(reason or '')
vars['mp_close'] = CLOSE_BY.get(key, CLOSE_BY.get('*'))
for i in range(1, SLOTS + 1):
    s = str(i)
    if i <= len(notes):
        vars['note' + s + '_pid'] = notes[i - 1][0]
        vars['note' + s + '_reason'] = notes[i - 1][1]
        vars['note_has_' + s] = 'yes'
    else:
        vars['note' + s + '_pid'] = ''
        vars['note_has_' + s] = 'no'
said = [LEAD.get(vars['mp_close'], '')]
words = [st['p'][q]['words'] for q in order if q in recorded]
if words:
    said.append(('Your promise for your ' + words[0] + ' is recorded.') if len(words) == 1 else
                ('Your promises for your ' + ', your '.join(words[:-1]) + ' and your ' + words[-1] + ' are recorded.'))
    said.append(SUCCESS)
for w in failed:
    said.append(FAIL_SAY + ' This concerns your ' + w + '.')
vars['mp_said'] = ' '.join(x for x in said if x).strip()
'''


# ---------------------------------------------------------------- builders

def enabled(spec):
    mp = spec.get("multi_product") or {}
    return bool((mp.get("notes") or {}).get("enabled")) or any(e.get("action") == "note_and_continue" for e in spec["events"])


class Multi:
    def __init__(self, c):
        self.c = c
        self.s = c.s
        self.mp = self.s.get("multi_product") or {}
        notes = self.mp.get("notes") or {}
        self.notes_on = bool(notes.get("enabled"))
        self.reasons = list(notes.get("reasons") or [])
        ev = self.s["events"]
        self.fail_ev = c.fail_ev
        # outcomes the model reports per product: note_and_continue events of negotiate (not the service failure)
        self.note_outcomes = []
        for e in ev:
            if e["action"] == "note_and_continue" and "negotiate" in c.stages_of(e) and e["id"] != self.fail_ev:
                r = e.get("per_product_note") or e["id"]
                if r not in self.note_outcomes:
                    self.note_outcomes.append(r)
        self.refused_note = self.note_for(c.events.get("refused"), "refused")
        fe = c.events.get(self.fail_ev) or {}
        self.fail_note = fe.get("per_product_note") or "service_error"

    def note_for(self, ev, default):
        if not ev:
            return default
        if ev.get("per_product_note"):
            return ev["per_product_note"]
        if ev.get("reason") in self.reasons:
            return ev["reason"]
        return default

    # ---- routing helpers
    def close_by(self):
        """(outcome|reason) -> close node, from the spec outcome table."""
        out = {}
        for o in self.s["outcomes"]:
            k = "%s|%s" % (o["outcome"], o.get("reason") or "")
            out.setdefault(k, "close_" + o["event"])
        return out

    def wrap_targets(self):
        """Close nodes a wrap can route to."""
        by = self.close_by()
        keys = ["promise|"]
        if self.mp.get("all_paid_outcome"):
            keys.append("%s|" % self.mp["all_paid_outcome"])
        keys += ["no_promise|" + r for r in self.reasons]
        out = []
        for k in keys:
            if k in by and by[k] not in out:
                out.append(by[k])
        return out

    # ---- nodes
    def set_tool(self, mode=None):
        """mode "code": a code_exec tool (arguments arrive as `params`, verified 10 Oct); "api": an api_call to
        the read-only get_account route with the state machine in its pre-hook."""
        mode = mode or SET_MODE
        outcomes = ["agreed"] + [o for o in ["refused"] + self.note_outcomes if o != "agreed"]
        outcomes = list(dict.fromkeys(outcomes)) + ["review"]
        conf = self.s["confirmation"]
        neg = self.s["negotiation"]
        say = {}
        for e in self.s["events"]:
            r = e.get("per_product_note")
            if r in outcomes and r not in say and e["action"] == "note_and_continue":
                parts = []
                if e.get("say"):
                    parts.append('Say: "%s"' % e["say"])
                if e.get("instruction"):
                    parts.append(e["instruction"])
                say[r] = " ".join(parts)
        cfg = {"confirm": conf["question"], "nbd": neg.get("non_business_day", "next_business_day"),
               "after": neg.get("after_deadline", "offer_deadline"), "below": neg.get("below_floor", "offer_floor_once"),
               "cap": self.mp.get("shared_date_after_deadline", "cap_to_product_deadline_and_say_so") != "reject",
               "notes": [o for o in outcomes if o not in ("agreed", "refused", "review")], "say": say}
        head = "import json, re\n" + embed.source(money) + "\nDAYS = dict()\n" + _const("CFG", cfg) + "TOOL = %r\n" % SET_TOOL
        props = {
            "product_id": {"type": "string", "description": "The product_id from PRODUCTS (for example p1), or ALL when the customer gives one answer for every product not yet settled."},
            "outcome": {"type": "string", "enum": outcomes, "description": "agreed: the customer accepted an amount and a date for it. refused: its last persuasion attempt was refused. "
                        + " ".join("%s: %s." % (o, self.outcome_words(o)) for o in cfg["notes"])
                        + " review: every product is settled and the promises must be read back again (with product_id ALL)."},
            "amount": {"type": "string", "description": "For agreed: the amount as a number such as 220.00, or full, or floor."},
            "date": {"type": "string", "description": "For agreed: the payment date, YYYY-MM-DD."},
            "attempts_made": {"type": "number", "description": "For refused: how many persuasion attempts of this product's plan you made."},
        }
        desc = ("Report a settled product. CALL it every time a product is settled (agreed, refused, "
                + ", ".join(cfg["notes"]) + "). Then follow what_to_do_next.")
        if mode == "api":
            body = {k: dict(v, default="{{sp_%s}}" % k) for k, v in props.items()}
            return self.c.tool(SET_TOOL, body, ["product_id", "outcome"], path="get_account", description=desc,
                               mapping=[{"path": "data.phase", "variable": PHASE}], pre=head + API_ARGS + SET_PRE, post=SET_POST)
        code = head + CODE_ARGS + SET_PRE + CODE_RESULT
        assert_no_braces(code, SET_TOOL)
        return {"type": "function", "config": {"timeoutMs": 12000},
                "function": {"name": SET_TOOL, "handler": "__function__:code_exec", "description": desc,
                             "parameters": {"type": "object", "properties": dict(
                                 {"code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}, **props),
                                 "required": ["product_id", "outcome"]}}}

    def outcome_words(self, o):
        for e in self.s["events"]:
            if e.get("per_product_note") == o and e["action"] == "note_and_continue":
                return "the customer " + e["trigger"]
        return o

    def account_code(self):
        return _const("MP", {"order": self.mp.get("order", "get_account")}) + MP_ACCOUNT_CODE

    def wrap(self, name, end_note, recording=False, fallback=None):
        c = self.c
        fe = c.events.get(self.fail_ev) or {}
        by = self.close_by()
        targets = self.wrap_targets()
        fb = fallback or ("no_promise", self.refused_note)
        fb_key = "%s|%s" % (fb[0], fb[1] or "")
        by_used = {k: v for k, v in by.items() if v in targets}
        by_used["*"] = by.get(fb_key) if by.get(fb_key) in targets else (targets[-1] if targets else "close_account_error")
        lead = {}
        for t in targets:
            eid = t[len("close_"):]
            ev = c.events.get(eid) or {}
            parts = []
            if eid not in ("promise_recorded", self.fail_ev):
                parts.append(c.lead_in(eid))
                if ev.get("say"):
                    parts.append(ev["say"])
            lead[t] = " ".join(parts)
        code = ("import json\n" + embed.source(dates, money) + "\n" + "SLOTS = %d\n" % SLOTS +
                "RECORDING = %s\n" % bool(recording) + "END_NOTE = %r\nFAIL_NOTE = %r\n" % (end_note, self.fail_note) +
                "NOTES_ON = %s\n" % self.notes_on + _const("REASONS", self.reasons) +
                "RULE = %r\nALL_PAID = %r\n" % (self.mp.get("no_promise_reason", "first_non_paid_note"), self.mp.get("all_paid_outcome") or "") +
                _const("FALLBACK", list(fb)) + _const("CLOSE_BY", by_used) + _const("LEAD", lead) +
                "SUCCESS = %r\nFAIL_SAY = %r\n" % (self.s["confirmation"].get("success_sentence") or "", fe.get("say") or "") + WRAP_CODE)
        first = "note_1" if self.notes_on else "finish"
        tr = [go(first, "Notes to write", cond_any(eq("note_has_1", "yes")))] if self.notes_on else []
        return code_node(name, code, "finish", tr)

    def build(self, nodes):
        c = self.c
        # records
        nodes["record_plan"] = code_node("record_plan", "SLOTS = %d\n" % SLOTS + RECORD_PLAN, "wrap_recorded",
                                         [go("record_1", "A promise to record", cond_any(eq("rec_has_1", "yes")))])
        for i in range(1, SLOTS + 1):
            s = str(i)
            nxt = "record_%d" % (i + 1) if i < SLOTS else None
            pre = "SLOT = %r\n" % s + REC_PRE
            post = "SLOT = %r\n" % s + REC_POST

            def tool():
                return c.tool("create_promise", {
                    "product_id": {"default": "{{rec%s_pid}}" % s},
                    "amount": {"default": "{{rec%s_amount}}" % s},
                    "date": {"default": "{{rec%s_date}}" % s},
                }, ["product_id", "amount", "date"], pre=pre, post=post)
            ok = [eq("rec%s_status" % s, "recorded"), eq("rec%s_ok" % s, "true"), eq("rec%s_ok" % s, "True")]
            tr = []
            if nxt:
                tr += [go(nxt, "Recorded; next promise", all_of(o, eq("rec_has_%d" % (i + 1), "yes"))) for o in ok]
            tr += [go("wrap_recorded", "Recorded; no more promises", cond_any(*ok)), always("retry_%s" % s)]
            nodes["record_" + s] = {
                "name": "record_" + s, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "retry_" + s,
                "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [tool()],
                "response_mapping": [{"path": "data.ok", "variable": "rec%s_ok" % s}], "transitions": tr}
            rtr = ([go(nxt, "Next promise", cond_any(eq("rec_has_%d" % (i + 1), "yes")))] if nxt else []) + [always("wrap_recorded")]
            nodes["retry_" + s] = {
                "name": "retry_" + s, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "wrap_recorded",
                "on_enter": {"type": "tts", "text": "I am sorry, there was a technical problem on our side. Let me try that once more."},
                "disable_inactivity_monitor": True, "pre_actions": [tool()],
                "response_mapping": [{"path": "data.ok", "variable": "rec%s_retry_ok" % s}], "transitions": rtr}
        # the single read-back: spoken by the engine, confirmation extracted
        q = '"%s"' % self.s["confirmation"]["question"]
        nodes["readback"] = {
            "name": "readback", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": "{{mp_readback}}"},
            "task_message": ("You just read back every promise and asked %s. If the customer asks a question, answer it briefly "
                             "from the read-back and ask %s again. Nothing is registered yet: never say anything is recorded. "
                             "No tool call is allowed in this step." % (q, q)),
            "extraction_schema": [{
                "name": "rb_answer", "type": "string", "min_confidence": 0.6, "enum_values": ["pending", "yes", "change", "other"],
                "description": ("The customer's LATEST message, an answer to the read-back of the promises. yes: a clear yes, they confirm. "
                                "change: no, or they want a different amount or date, or refuse, or raise anything else to settle "
                                "(a human, a plan, a personal difficulty, a paid or disputed product). other: only a question about the read-back. "
                                "pending: no answer yet.")}],
            "max_node_turns": 4, "max_turn_transition": "readback_reset",
            "transitions": [go("record_plan", "Confirmed", cond_any(eq("rb_answer", "yes"))),
                            go("readback_reset", "Not confirmed", cond_any(eq("rb_answer", "change")))]}
        nodes["readback_reset"] = code_node("readback_reset", "vars['mp_phase'] = 'open'\nvars['rb_answer'] = 'pending'\n", "negotiate")
        # wraps
        nodes["wrap_recorded"] = self.wrap("wrap_recorded", self.refused_note, recording=True)
        refused = c.events.get("refused") or {}
        nodes["wrap_done"] = self.wrap("wrap_done", self.refused_note,
                                       fallback=(refused.get("outcome") or "no_promise", refused.get("reason") or self.refused_note))
        for ev in self.s["events"]:
            if "negotiate" not in c.stages_of(ev):
                continue
            if ev["action"] == "offer_transfer_else_close":
                nodes["wrap_" + ev["id"]] = self.wrap("wrap_" + ev["id"], self.note_for(ev, ev.get("else_reason") or "refused"),
                                                      fallback=(ev.get("else_outcome") or "no_promise", ev.get("else_reason")))
            elif ev["action"] == "close" and ev["id"] not in ("promise_recorded",):
                nodes["wrap_" + ev["id"]] = self.wrap("wrap_" + ev["id"], self.note_for(ev, self.refused_note),
                                                      fallback=(ev.get("outcome"), ev.get("reason")))
        # notes
        if self.notes_on:
            for i in range(1, SLOTS + 1):
                s = str(i)
                t = c.tool("add_note", {"product_id": {"default": "{{note%s_pid}}" % s},
                                        "reason": {"default": "{{note%s_reason}}" % s}},
                           ["product_id", "reason"], retries=1, pre="SLOT = %r\n" % s + NOTE_PRE)
                tr = ([go("note_%d" % (i + 1), "Next note", cond_any(eq("note_has_%d" % (i + 1), "yes")))] if i < SLOTS else []) + [always("finish")]
                nodes["note_" + s] = {
                    "name": "note_" + s, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "finish",
                    "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [t], "transitions": tr}
        targets = self.wrap_targets()
        nodes["finish"] = code_node("finish", "vars['mp_finished'] = 'yes'\n", targets[-1] if targets else "close_account_error",
                                    [go(t, "Close: " + t, cond_any(eq("mp_close", t))) for t in targets])
        # close nodes reached from a wrap speak the computed summary
        for t in targets:
            eid = t[len("close_"):]
            ev = c.events.get(eid) or {}
            outcome, reason = ev.get("outcome"), ev.get("reason")
            if ev.get("action") == "offer_transfer_else_close":
                outcome, reason = ev.get("else_outcome") or "no_promise", ev.get("else_reason")
            nodes[t] = close_node(t, outcome, reason or "", "{mp_said} " + c.thanks(named=True))
