"""Compile a policy spec + the bank's openapi into a workflow for the voice platform.

    compile(spec, openapi, base_url=None) -> workflow dict

CLI:
    uv run python -m builder.compile <spec.json> <openapi.json> [--base-url URL] [--out FILE]
                                     [--save SLOT] [--state FILE]

Port of the kit sample generator (out/gen_brief_a.py): every brief value comes from the spec.
Platform workarounds kept: post-hooks write what_to_do_next, booleans tested as "true"/"True",
no top-level `and`, values baked as defaults, no brace pairs in code, close_call baked from vars
as a lifecycle tool at end_of_call, a retry_promise node, transfer nodes in front of closes.

Single product: prepare_account computes the facts of every product in a list and exposes the
first one; the multi-product loop (Task 5) builds on product_facts().
"""
import argparse
import json
import re
import sys

from builder.lib import dates, embed, money
from builder.lint import lint, safe_opening
from builder.nodes import (all_of, always, api_tool, body_enum, close_node, code_node, cond_any, eq, go,
                           goodbye_node, gt, is_true, py_json, transfer_node)

STAGES = ["opening", "third_party", "identity", "negotiate"]
# "any" means the customer's own stages; a third party is only covered when a spec names it.
ANY_STAGES = ["opening", "identity", "negotiate"]
# Tools each stage's dialogue model can call (an extra rule naming another tool is dropped there).
STAGE_TOOLS = {"opening": set(), "third_party": {"schedule_callback"}, "identity": {"verify_identity"},
               "negotiate": {"create_promise"}, "any": set()}
TRANSFER_ACTIONS = ("transfer", "insist_once_then_transfer", "offer_transfer_else_close")
_STOP = {"asks", "says", "with", "from", "their", "they", "that", "this", "lower", "new", "for"}
EXTRACT_VAR = {"opening": "line_status", "third_party": "tp_status", "identity": "id_event", "negotiate": "neg_event"}
# Runtime names usable in spec sentences -> the workflow variable that holds them.
ALIAS = {"past_due": "past_due_text", "days_late": "days_late_text", "floor": "floor_text",
         "deadline": "deadline_text", "product": "product_words"}
PROSE = {"amount": "the amount", "date": "the date"}
# Events wired to fixed graph parts instead of an extraction enum value.
STRUCTURAL = {
    "opening": {"unclear"},
    "third_party": {"callback"},
    "identity": {"failed"},
    "negotiate": {"promise_recorded", "refused"},
}
# Generic lead-ins for known event ids, said before the event's own sentence in the farewell.
LEAD_IN = {
    "paid": "Thank you for letting us know.",
    "dispute": "Understood, thank you for telling us.",
    "refused": "I understand.",
    "not_talk": "I understand.",
    "declined": "I understand. Without the security questions I cannot continue this call.",
    "failed": "I am sorry, I could not verify your identity, so I cannot continue this call.",
    "unclear": "I could not confirm who I am speaking with, so I will end the call here.",
    "callback": "Thank you, we will call back then.",
    "wrong_number": "I apologise for the inconvenience.",
}
NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
CB_DAY_WORDS = {"today": "today", "next_business_day": "the next business day", "next_day": "tomorrow",
                "any_business_day": "any business day"}


# ---------------------------------------------------------------- text helpers

_PH = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def tpl(s):
    """Spec sentence -> platform text: {name} becomes {{var}}; amount/date become plain words."""
    def rep(m):
        k = m.group(1)
        if k in PROSE:
            return PROSE[k]
        return "{{" + ALIAS.get(k, k) + "}}"
    return _PH.sub(rep, s or "")


def code_tpl(s):
    """Spec sentence -> farewell template for close nodes: single braces, runtime names aliased."""
    return _PH.sub(lambda m: PROSE[m.group(1)] if m.group(1) in PROSE else "{" + ALIAS.get(m.group(1), m.group(1)) + "}",
                   s or "")


def q(s):
    return '"' + s + '"'


def join_or(items, word="or"):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " " + word + " " + items[-1]


def with_article(s):
    return ("an " if s[:1].lower() in "aeiou" else "a ") + s


def team_words(queue):
    return "a colleague" if queue == "general" else "our " + queue.replace("_", " ") + " team"


def subj(who, trigger):
    """'the person says X' when the trigger starts with a verb, else the trigger alone."""
    w = (trigger.split() or [""])[0].lower()
    return (who + " " + trigger) if (w.endswith("s") or w == "still") and w not in ("this", "is") else trigger


def stages_of(ev):
    st = ev.get("stages") or []
    out = [s for s in STAGES if s in st or ("any" in st and s in ANY_STAGES)]
    return out


def _words(text):
    """Content words, singular, for overlap checks."""
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if len(w) >= 4 and w not in _STOP:
            out.add(w[:-1] if w.endswith("s") else w)
    return out


def rules_for(items, stage, key="stage"):
    out = []
    for it in items:
        st = it.get(key) if key == "stage" else it.get("stages", ["any"])
        st = [st] if isinstance(st, str) else st
        if stage in st:
            out.append(it)
    return out


def bullet_block(title, lines):
    lines = [l for l in lines if l]
    return ("\n" + title + "\n" + "\n".join("- " + l for l in lines)) if lines else ""


# ---------------------------------------------------------------- code snippets

NOW_CODE = r'''
def _now():
    s = str(vars.get('now') or '')
    try:
        return datetime.datetime.fromisoformat(s[:16])
    except Exception:
        return datetime.datetime.now()
nowdt = _now()
today = nowdt.date()
'''

SETUP_CODE = r'''
try:
    hm = nowdt.strftime('%H:%M')
    vars['today_text'] = speak(today)
    vars['now_time'] = hm
    lo = CB['from']
    hi = CB['to']
    inc = CB.get('inclusive', True) is not False
    win = 'between ' + lo + ' and ' + hi + (' (both included)' if inc else ' (both excluded)')
    parts = []
    notes = []
    for kind in CB['days']:
        if kind == 'today':
            if not CB.get('after_now'):
                parts.append('today, ' + speak(today) + ', ' + win)
            elif hm < hi:
                parts.append('today, ' + speak(today) + ', at a time later than ' + hm + ' and ' + win)
            else:
                notes.append('It is already too late for today, so not today.')
        elif kind == 'next_business_day':
            parts.append('on the next business day, ' + speak(next_bd(today, CAL)) + ', ' + win)
        elif kind == 'next_day':
            parts.append('tomorrow, ' + speak(today + datetime.timedelta(days=1)) + ', ' + win)
        elif kind == 'any_business_day':
            parts.append('on any business day (not a weekend day or bank holiday), ' + win)
    vars['callback_rules'] = ('A callback may only be booked ' + ', or '.join(parts) + '. ' + ' '.join(notes) +
        ' Any other day or time is outside the window. Today is ' + speak(today) + ' and the time now is ' + hm + '.')
    vars['setup_state'] = 'ok'
except Exception as e:
    vars['today_text'] = str(vars.get('now') or '')
    vars['callback_rules'] = CB_FALLBACK + ' Now: ' + str(vars.get('now') or '') + '.'
    vars['setup_state'] = 'error'
'''

ACCOUNT_CODE = r'''
def _obj(v):
    if isinstance(v, str) and v.strip()[:1] in ('[', '{'):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def find_products():
    for k in ('acct_products_json', 'acct_products', 'load_account_response.data.products',
              'get_account_response.data.products'):
        p = _obj(vars.get(k))
        if isinstance(p, list) and p and isinstance(p[0], dict):
            return [x for x in p if isinstance(x, dict)]
    one = _obj(vars.get('acct_first_json'))
    if isinstance(one, dict):
        return [one]
    found = dict()
    for k in list(vars.keys()):
        m = re.search(r'products(?:\.|\[)(\d+)\]?\.(\w+)$', str(k))
        if m:
            found.setdefault(int(m.group(1)), dict())[m.group(2)] = vars.get(k)
    return [found[i] for i in sorted(found)]


def fill(t, p, extra):
    def rep(m):
        k = m.group(1)
        if k in extra:
            return str(extra[k])
        if p.get(k) is not None:
            return str(p.get(k))
        v = vars.get(k)
        return '' if v is None else str(v)
    return re.sub(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', rep, t)


def pick_band(days_late):
    for b in BANDS:
        if b['days_late'][0] <= days_late <= b['days_late'][1]:
            return b
    if days_late < min(b['days_late'][0] for b in BANDS):
        return min(BANDS, key=lambda b: b['days_late'][0])
    return max(BANDS, key=lambda b: b['days_late'][1])


def product_facts(p):
    ptype = str(p.get('type') or '')
    cfg = PRODUCTS.get(ptype) or dict(words=ptype.replace('_', ' '), floor_field='')
    past_due = float(p.get('past_due'))
    days_late = int(float(p.get('days_late') or 0))
    words = fill(cfg['words'], p, dict())
    ff = cfg.get('floor_field') or ''
    floor = float(p.get(ff)) if p.get(ff) not in (None, '') else past_due
    if floor > past_due:
        floor = past_due
    band = pick_band(days_late)
    dl = deadline(today, band['deadline'], CAL)
    attempts = []
    for i, a in enumerate(band['attempts']):
        d = attempt_date(today, a['date'], dl, CAL)
        if a['amount'] == 'floor':
            amt = 'propose the floor amount ' + fmt(floor)
        elif a['amount'] == 'full':
            amt = 'propose the full amount ' + fmt(past_due)
        else:
            amt = ('propose the full amount ' + fmt(past_due) + ' (or the floor ' + fmt(floor) +
                   ' if the customer already said the full amount is too much)')
        when = ('on the deadline, ' + speak(d)) if a['date'] == 'deadline' else ('on ' + speak(d))
        attempts.append(dict(date=d, text='Attempt ' + str(i + 1) + ': ' + amt + ' ' + when + '.'))
    n = len(attempts)
    plan = '\n'.join(a['text'] for a in attempts)
    plan += ('\nThis customer is in the ' + str(band['name']).upper() + ' band: ' + NUMS.get(str(n), str(n)) +
             (' persuasion attempt in total. There is no attempt 2.' if n == 1 else ' persuasion attempts in total.'))
    allowed = []
    d = today
    while d <= dl:
        if is_bd(d, CAL):
            allowed.append(d)
        d = d + datetime.timedelta(days=1)
    say = dict(product_words=words, past_due=fmt(past_due), days_late=str(days_late), floor=fmt(floor),
               deadline=speak(dl), product=words)
    return dict(product=p, product_id=str(p.get('product_id') or ''), words=words, past_due=past_due,
                floor=floor, days_late=days_late, band=band, deadline=dl, attempts=attempts, plan=plan,
                allowed=allowed, debt_statement=fill(NEG['debt_statement'], p, say),
                debt_statement_one=fill(NEG.get('debt_statement_per_product') or NEG['debt_statement'], p, say))


def calendar_text(dl):
    out = []
    d = today
    end = dl + datetime.timedelta(days=21)
    while d <= end:
        if is_bd(d, CAL):
            tag = 'business day'
        elif d.isoformat() in CAL['holidays']:
            tag = 'BANK HOLIDAY, not a business day'
        else:
            tag = 'weekend, not a business day'
        if not is_bd(d, CAL) and d <= dl:
            if NEG['non_business_day'] == 'previous_business_day' and prev_bd(d, CAL) >= today:
                tag += '. If the customer names this day, offer the previous business day: ' + speak(prev_bd(d, CAL))
            elif NEG['non_business_day'] == 'reject':
                tag += '. If the customer names this day, say it is not a business day and ask for a business day'
            else:
                tag += '. If the customer names this day, offer the next business day: ' + speak(next_bd(d, CAL))
        if d > dl:
            tag += ', AFTER THE DEADLINE'
        out.append('- ' + speak(d) + ': ' + tag + (' (today)' if d == today else ''))
        d = d + datetime.timedelta(days=1)
    return '\n'.join(out)


def weekday_text():
    ww = NEG['weekday_words']
    out = []
    for i in range(7):
        if ww.get('this') == 'next_occurrence':
            t = today + datetime.timedelta(days=(i - today.weekday()) % 7)
        else:
            t = weekday_ref(today, i, 'this')
        if ww.get('next') == 'next_occurrence':
            n = today + datetime.timedelta(days=(i - today.weekday() - 1) % 7 + 1)
        else:
            n = weekday_ref(today, i, 'next')
        out.append('- "this ' + WD[i] + '" = ' + speak(t) + '; "next ' + WD[i] + '" = ' + speak(n))
    return '\n'.join(out)


try:
    products = [p for p in find_products() if p.get('past_due') not in (None, '')]
    if not products:
        raise ValueError('no product')
    facts = [product_facts(p) for p in products]
    vars['products_count'] = str(len(facts))
    f = facts[0]
    vars['product_id'] = f['product_id'] or 'p1'
    vars['product_words'] = f['words']
    vars['past_due_text'] = fmt(f['past_due'])
    vars['past_due_num'] = f['past_due']
    vars['floor_text'] = fmt(f['floor'])
    vars['floor_num'] = f['floor']
    vars['days_late_text'] = str(f['days_late'])
    vars['band'] = str(f['band']['name'])
    vars['attempts_allowed'] = str(len(f['attempts']))
    vars['deadline_text'] = speak(f['deadline'])
    for i, a in enumerate(f['attempts']):
        vars['attempt' + str(i + 1) + '_text'] = speak(a['date'])
    vars['attempt_plan'] = f['plan']
    vars['allowed_dates_text'] = ', '.join(x.isoformat() for x in f['allowed'])
    vars['calendar_text'] = calendar_text(f['deadline'])
    vars['weekday_text'] = weekday_text()
    vars['debt_statement_text'] = f['debt_statement']
    vars['account_state'] = 'ok'
except Exception as e:
    vars['account_state'] = 'error'
    vars['account_error'] = str(e)[:200]
'''

GET_ACCOUNT_POST = r'''
try:
    import json
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            ps = d.get('products')
            if isinstance(ps, list) and ps and isinstance(ps[0], dict):
                d['first_json'] = json.dumps(ps[0])
                d['products_json'] = json.dumps(ps)
except Exception:
    pass
'''

VERIFY_PRE = r'''
try:
    import re
    MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
              'october', 'november', 'december']
    def _month(w):
        w = w.lower()[:3]
        for i, m in enumerate(MONTHS):
            if m[:3] == w:
                return i + 1
        return 0
    def _date(a, order):
        a = a.strip().rstrip('.')
        if re.match(r'^\d{4}-\d{2}-\d{2}$', a):
            return a
        m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([A-Za-z]+),?\s+(\d{4})$', a)
        if m and _month(m.group(2)):
            return '%s-%02d-%02d' % (m.group(3), _month(m.group(2)), int(m.group(1)))
        m = re.match(r'^([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$', a)
        if m and _month(m.group(1)):
            return '%s-%02d-%02d' % (m.group(3), _month(m.group(1)), int(m.group(2)))
        m = re.match(r'^(\d{1,4})[./ -](\d{1,2})[./ -](\d{1,4})$', a)
        if m:
            x, y, z = m.group(1), m.group(2), m.group(3)
            if order == 'YMD' or len(x) == 4:
                yr, mo, dy = x, y, z
            elif order == 'MDY':
                yr, mo, dy = z, x, y
            else:
                yr, mo, dy = z, y, x
            if len(yr) == 4 and 1 <= int(mo) <= 12 and 1 <= int(dy) <= 31:
                return '%s-%02d-%02d' % (yr, int(mo), int(dy))
        return a
    b = config.get('body')
    if isinstance(b, dict):
        q = FORMATS.get(str(b.get('field') or ''))
        a = str(b.get('answer') if b.get('answer') is not None else '').strip()
        if q and a:
            fm = q.get('format')
            if fm == 'date':
                b['answer'] = _date(a, q.get('date_order') or 'DMY')
            elif fm == 'digits':
                dg = ''.join(ch for ch in a if ch.isdigit())
                n = int(q.get('length') or 0)
                if n and len(dg) >= n:
                    b['answer'] = dg[-n:]
                elif dg:
                    b['answer'] = dg
            elif fm == 'year':
                m = re.search(r'\d{4}', a)
                if m:
                    b['answer'] = m.group(0)
            elif fm == 'postcode':
                b['answer'] = a.upper().replace(' ', '')
            else:
                b['answer'] = a
except Exception:
    pass
'''

VERIFY_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        left = d.get('wrong_answers_left')
        hint = d.get('hint')
        if str(result.get('status', 'success')) != 'success' or (d.get('ok') is None and d.get('verified') is None):
            d['what_to_do_next'] = 'The security service had a technical problem and nothing was checked. Say so in one short sentence and call verify_identity again with the same answer.'
        elif d.get('all_verified') is True:
            d['what_to_do_next'] = 'All answers are verified. Say only: Thank you.'
        elif hint and hint in HINTS:
            d['what_to_do_next'] = 'This is NOT a wrong answer (' + str(hint).replace('_', ' ') + '). ' + HINTS[hint] + ' Then call verify_identity again.'
        elif d.get('ok') is False:
            d['what_to_do_next'] = 'The answer could not be read. This is NOT a wrong answer. Ask the customer to repeat it (' + UNREADABLE + '). Then call verify_identity again.'
        elif d.get('verified') is True:
            d['what_to_do_next'] = 'This answer is correct. ' + NEXT_Q + ' ' + NOT_YET
        elif str(left) == '0':
            d['what_to_do_next'] = 'That was the last wrong answer allowed. The check has ended. Do not ask anything else.'
        else:
            d['what_to_do_next'] = 'This answer does not match our records. Say so and ask the SAME question again. Wrong answers left: ' + str(left) + '.'
except Exception:
    pass
'''

PROMISE_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        good = str(result.get('status', 'success')) == 'success' and d.get('ok') is True
        if good:
            d['promise_status'] = 'recorded'
            d['what_to_do_next'] = 'The promise is recorded.'
        else:
            n = int(vars.get('promise_fail_n') or 0) + 1
            vars['promise_fail_n'] = n
            d['promise_status'] = 'failed'
            d['recorded'] = False
            if n <= RETRIES:
                d['what_to_do_next'] = 'NOT RECORDED: the service failed. Do NOT say the promise is recorded. Tell the customer in one short sentence that there was a technical problem and you are trying again, and call create_promise again right away with exactly the same amount and date.'
            else:
                d['what_to_do_next'] = 'NOT RECORDED: the service failed again. Do NOT say the promise is recorded. Do not try again.'
except Exception:
    pass
'''

CALLBACK_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        if str(result.get('status', 'success')) == 'success' and d.get('ok') is True:
            d['cb_status'] = 'booked'
            d['what_to_do_next'] = 'The callback is booked.'
        else:
            d['cb_status'] = 'failed'
            d['what_to_do_next'] = 'NOT BOOKED: the service failed. Do not say it is booked. Say there was a technical problem and call schedule_callback once more with the same date and time.'
except Exception:
    pass
'''

RETRY_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        a = vars.get('create_promise_args.promise_amount')
        if a is None:
            a = b.get('amount')
        b['amount'] = float(a)
        dt = vars.get('create_promise_args.promise_date')
        if dt:
            b['date'] = str(dt)[:10]
        pid = vars.get('product_id')
        if pid:
            b['product_id'] = str(pid)
except Exception:
    pass
'''

CLOSE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict) and b.get('outcome') in REASONLESS:
        b.pop('reason', None)
except Exception:
    pass
'''


def _const(name, value):
    return "%s = %s\n" % (name, py_json(value))


# ---------------------------------------------------------------- compiler

class _Compiler:
    def __init__(self, spec, openapi, base_url):
        self.s = spec
        self.oa = openapi
        self.base = (base_url or openapi["servers"][0]["url"]).rstrip("/")
        self.addr = tpl(spec["address_as"])
        self.events = {e["id"]: e for e in spec["events"]}
        self.fail_ev = spec["confirmation"].get("failure_event") or ""
        self.nodes = {}
        self.transfers = {}   # (queue, close) -> node key

    # ---- helpers
    def tool(self, name, props, required, **kw):
        return api_tool(name, self.base, self.oa, props, required, **kw)

    def has(self, ev_id):
        return ev_id in self.events

    def close_key(self, ev_id):
        return "close_" + ev_id

    def prelude(self):
        return ("import datetime, json, re\n" + embed.source(dates, money) + "\n" +
                _const("CAL", self.s["calendar"]) + NOW_CODE)

    def transfer_to(self, queue, close, ev_id):
        key = (queue, close)
        if key not in self.transfers:
            name = "transfer_" + queue
            if name in self.transfers.values():
                name = "transfer_" + queue + "_" + ev_id
            self.transfers[key] = name
            self.nodes[name] = transfer_node(name, queue, close, self.base, self.oa)
        return self.transfers[key]

    def stage_events(self, stage):
        skip = set(STRUCTURAL.get(stage, set()))
        if stage == "negotiate":
            skip.add(self.fail_ev)
        return [e for e in self.s["events"] if stage in stages_of(e) and e["id"] not in skip]

    def banned_for(self, stage):
        """Banned offers minus those that share a content word with a transfer event's trigger in this stage
        (e.g. "instalment plan" vs a plan/instalments transfer), so asking for them routes to the transfer."""
        trig = set()
        for e in self.stage_events(stage):
            if e["action"] in TRANSFER_ACTIONS:
                trig |= _words(e["trigger"])
        return [b for b in self.s["negotiation"].get("banned_offers") or [] if not (_words(b) & trig)]

    def counted_close(self, stage, ev):
        """A close event that must happen N>1 times: extraction cannot count, so the dialogue model calls it."""
        return (stage == "negotiate" and ev["action"] in ("close", "note_and_continue")
                and (ev.get("times") or 1) > 1)

    def fallback_close(self, stage, prefer):
        for p in prefer:
            if self.has(p):
                return self.close_key(p)
        for e in self.s["events"]:
            if stage in stages_of(e) and e["action"] in ("close", "note_and_continue"):
                return self.close_key(e["id"])
        return self.close_key(self.s["events"][0]["id"])

    # ---- farewells
    def farewell(self, ev, kind):
        """kind: close | transfer | else."""
        parts = []
        eid = ev["id"]
        say = ev.get("say") or (self.s["third_party"].get("fixed_sentences") or {}).get(eid)
        if eid == "failed" and not say:
            say = self.s["identity"].get("failed_advice")
        if kind == "else":
            parts.append("I understand.")
        elif kind == "transfer" or ev["action"] in ("transfer", "insist_once_then_transfer"):
            if ev["action"] == "transfer" and say:
                parts.append(code_tpl(say))
            parts.append("I am transferring you to " + team_words(ev.get("queue") or "general") + " now.")
        else:
            if eid == "promise_recorded":
                parts.append("Your promise is recorded.")
                if self.s["confirmation"].get("success_sentence"):
                    parts.append(code_tpl(self.s["confirmation"]["success_sentence"]))
            elif eid == self.fail_ev:
                parts.append("I am sorry, there was a technical problem.")
            elif eid in LEAD_IN:
                parts.append(LEAD_IN[eid])
            if say and say not in parts:
                parts.append(code_tpl(say))
        named = set(stages_of(ev)) <= {"negotiate"} and kind != "transfer"
        if self.s.get("closing", {}).get("thank_and_goodbye", True) is not False:
            parts.append("Thank you for your time, " + self.s["address_as"] + ". Goodbye." if named
                         else "Thank you for your time. Goodbye.")
        else:
            parts.append("Goodbye.")
        return " ".join(parts)

    def build_closes(self):
        for ev in self.s["events"]:
            if ev["action"] == "offer_transfer_else_close":
                self.nodes[self.close_key(ev["id"])] = close_node(
                    self.close_key(ev["id"]), ev.get("else_outcome") or "no_promise", ev.get("else_reason") or "",
                    self.farewell(ev, "else"))
                self.nodes[self.close_key(ev["id"]) + "_transfer"] = close_node(
                    self.close_key(ev["id"]) + "_transfer", ev["outcome"], ev.get("reason") or "",
                    self.farewell(ev, "transfer"))
            elif ev.get("outcome"):
                self.nodes[self.close_key(ev["id"])] = close_node(
                    self.close_key(ev["id"]), ev["outcome"], ev.get("reason") or "", self.farewell(ev, "close"))
        fe = self.events.get(self.fail_ev) or {}
        self.nodes["close_account_error"] = close_node(
            "close_account_error", fe.get("outcome") or "no_promise", fe.get("reason") or "",
            "I am sorry, because of a technical problem I cannot see the account details right now. "
            "Thank you for your time. Goodbye.")

    # ---- event wiring per stage
    def event_values(self, stage):
        """(enum values, description parts, transitions) for the events of a conversation stage."""
        values, desc, trans = [], [], []
        who = "the customer" if stage == "negotiate" else "the person"
        banned = self.banned_for(stage)
        for ev in self.stage_events(stage):
            eid, act, trig = ev["id"], ev["action"], ev["trigger"]
            times = ev.get("times") or 1
            if self.counted_close(stage, ev):
                nth = "" if ("second" in trig or "twice" in trig) else " (for the %s time)" % ORD.get(times, str(times))
                trans.append({"target_node": self.close_key(eid), "is_tool_call": True,
                              "description": "Call this when the customer %s%s. The goodbye is spoken automatically." % (trig, nth),
                              "condition": {"operator": "matches_intent", "description": "The customer %s%s" % (trig, nth)}})
                continue
            extra = (" It must be the %s time; an earlier time is NOT %s." % (ORD.get(times, str(times)), eid)
                     if times > 1 and "second" not in trig and "twice" not in trig else "")
            if act == "insist_once_then_transfer":
                values.append(eid)
                desc.append("%s: %s, the agent already asked once whether it can help, and they insist again." % (eid, subj(who, trig)))
                t = self.transfer_to(ev["queue"], self.close_key(eid), eid)
                trans.append(go(t, "Insists: " + eid, cond_any(eq(EXTRACT_VAR[stage], eid))))
            elif act == "offer_transfer_else_close":
                team = team_words(ev["queue"]).replace("our ", "the ")
                values += [eid, eid + "_accepted", eid + "_declined"]
                desc.append("%s: %s (and does not answer an offer of %s)." % (eid, subj(who, trig), team))
                desc.append("%s_accepted: in the agent's previous message it offered to connect them with %s and they say yes to that." % (eid, team))
                desc.append("%s_declined: the agent offered to connect them with %s and in this message they refuse it. "
                            "This wins over every other value, even if the same message also asks for something else or says they cannot pay." % (eid, team))
                t = self.transfer_to(ev["queue"], self.close_key(eid) + "_transfer", eid)
                trans.append(go(t, eid + ": transfer accepted", all_of(eq(EXTRACT_VAR[stage], eid + "_accepted"), eq(eid + "_seen", "yes"))))
                trans.append(go(self.close_key(eid), eid + ": transfer declined", all_of(eq(EXTRACT_VAR[stage], eid + "_declined"), eq(eid + "_seen", "yes"))))
            elif act == "transfer":
                values.append(eid)
                not_it = ("Saying they cannot pay or that the amount is too much%s is NOT %s."
                          % ((", or asking for " + join_or(with_article(b) for b in banned)) if banned else "", eid)) if stage == "negotiate" else ""
                desc.append(("%s: %s EXPLICITLY %s.%s %s" % (eid, who, trig, extra, not_it)) if subj(who, trig) != trig else ("%s: %s.%s %s" % (eid, trig, extra, not_it)))
                t = self.transfer_to(ev["queue"], self.close_key(eid), eid)
                trans.append(go(t, "Transfer: " + eid, cond_any(eq(EXTRACT_VAR[stage], eid))))
            else:  # close, note_and_continue (single product: close)
                values.append(eid)
                desc.append("%s: %s.%s" % (eid, subj(who, trig), extra))
                trans.append(go(self.close_key(eid), "Close: " + eid, cond_any(eq(EXTRACT_VAR[stage], eid))))
        return values, desc, trans

    def seen_vars(self, stage):
        out = []
        for ev in self.stage_events(stage):
            if ev["action"] == "offer_transfer_else_close":
                out.append({"name": ev["id"] + "_seen", "type": "string", "min_confidence": 0.6, "enum_values": ["yes", "no"],
                            "description": "yes if anywhere in the conversation the customer %s. Otherwise no." % ev["trigger"]})
        return out

    def event_guidance(self, stage):
        lines = []
        for ev in self.stage_events(stage):
            ins = (ev.get("instruction") or "").strip()
            if ev["action"] == "insist_once_then_transfer":
                lines.append("If %s: %s %s" % (
                    subj("the customer" if stage == "negotiate" else "the person", ev["trigger"]),
                    ("say %s" % q(tpl(ev["say"])) + ("" if ev["say"].rstrip()[-1:] in ".?!" else ".")) if ev.get("say") else "ask once whether you can help first.",
                    ins or "Transfer only if they insist."))
            elif ev["action"] == "offer_transfer_else_close":
                extra = (" This comes before persuasion: make no persuasion attempt."
                         if ev.get("overrides_persuasion") and "persuasion" not in ins else "")
                lines.append("If %s: %s Make this offer only ONCE in the call; never repeat it.%s" % (subj("the customer", ev["trigger"]), ins, extra))
            elif self.counted_close(stage, ev):
                lines.append("If the customer %s: call transition_to_%s.%s" % (ev["trigger"], self.close_key(ev["id"]), (" " + ins) if ins else ""))
            elif ins:
                lines.append("If %s: %s" % (subj("the customer" if stage == "negotiate" else "the person", ev["trigger"]), ins))
        return lines

    def say_lines(self, stage):
        return ["If the person %s: say %s, then continue." % (r["trigger"], q(tpl(r["say"])))
                for r in rules_for(self.s.get("say_rules", []), stage, key="stages")]

    def extra(self, stage):
        """Extra rules of a stage, minus any that names a tool the stage's model does not have
        (close_call and engine-run calls such as get_account are never the model's to call)."""
        tools = {p.strip("/") for p in self.oa.get("paths", {})}
        mine = STAGE_TOOLS.get(stage, set())
        out = []
        for r in rules_for(self.s.get("extra_rules", []), stage):
            named = {t for t in tools if re.search(r"\b%s\b" % re.escape(t), r["text"])}
            if named - mine:
                continue
            out.append(tpl(r["text"]))
        return out

    # ---- prompts
    def system_prompt(self):
        s, neg = self.s, self.s["negotiation"]
        banned = neg.get("banned_offers") or []
        lines = [
            "You are %s, on an outbound phone call for %s. Speak English. One or two short sentences per turn. Be polite and calm." % (s["assistant_identity"], s["bank_name"]),
            "Address the customer as %s." % self.addr,
            "Hard rules for every step:",
            "- Never invent figures, dates, reasons or terms. If you do not have some information, say you do not have that information.",
        ]
        if banned:
            lines.append("- Never offer %s, or any term not given in your instructions." % join_or([with_article(b) for b in banned]))
        lines.append("- Never say that something is recorded, booked or done unless the tool result says so.")
        lines += ["- " + l for l in self.say_lines("any")]
        lines += ["- After every tool result, speak to the customer or call the next tool. Never end a turn with nothing. Call one tool per message.",
                  "- When a tool result contains what_to_do_next, follow it."]
        if neg.get("never_ask_how_much"):
            lines.append('- Never ask "how much can you pay?".')
        ex = self.extra("any")
        if ex:
            lines.append("Rules from the brief:")
            lines += ["- " + l for l in ex]
        return "\n".join(lines)

    def banned_words(self):
        return join_or([q(w) for w in self.s["opening"].get("banned_before_verify", [])])

    def opening_task(self):
        op = self.s["opening"]
        cq = tpl(op["confirm_question"])
        lines = [
            "You opened the call and asked %s. The identity of the person on the line is not confirmed." % q(cq),
            "Do NOT say why you are calling. Never say %s, an amount or the product." % self.banned_words(),
            "- Only %s confirm it. %s do not." % (join_or([q(a) for a in op.get("confirm_accept", [])]),
                                                  join_or([q(a) for a in op.get("confirm_reject", [])], "and")),
            "- If the person asks a question (who is calling, is this a robot, is this a scam), answer it briefly, then ask again: %s" % q(cq),
            "- If they do not confirm, ask %s again. You may ask it %s times in total." % (q(cq), NUM.get(op.get("max_asks", 2), str(op.get("max_asks", 2)))),
        ]
        lines += ["- " + l for l in self.event_guidance("opening") + self.say_lines("opening")]
        lines.append(bullet_block("Rules from the brief:", self.extra("opening")))
        lines.append("No tool call is allowed in this step.")
        return "\n".join(l for l in lines if l)

    def cb_summary(self):
        cb = self.s["third_party"]["callback"]
        days = join_or([CB_DAY_WORDS.get(d, d) for d in cb["days"]])
        inc = " (both included)" if cb.get("inclusive", True) is not False else " (both excluded)"
        return "%s, between %s and %s%s%s" % (days, cb["from"], cb["to"], inc,
                                              ", later than the current time" if cb.get("after_now") else "")

    def third_party_task(self):
        tp = self.s["third_party"]
        lines = ["The person on the line is NOT %s." % self.addr]
        if tp.get("say_nothing_beyond_opening", True):
            lines.append("Do not say why you are calling: never say %s, the product or an amount, even if they ask or offer to pay. "
                         "They may not answer any security question. If they ask what it is about, say you can only discuss it with %s personally."
                         % (self.banned_words(), self.addr))
        retries = tp.get("window_retries", 1)
        lines += [
            "Ask when %s can be reached." % self.addr,
            "Callback window: {{callback_rules}}",
            '- If they give a day and time inside the window, call schedule_callback with date as YYYY-MM-DD and time as HH:MM (24 hour). "Tomorrow morning around eleven" means 11:00 on the next day.',
            "- If what they offer is outside the window, say you can only call back %s, and ask %s for another time. Do not schedule anything outside the window."
            % (self.cb_summary(), "once" if retries == 1 else "up to %s times" % NUM.get(retries, str(retries))),
        ]
        lines += ["- " + l for l in self.event_guidance("third_party") + self.say_lines("third_party")]
        lines.append(bullet_block("Rules from the brief:", self.extra("third_party")))
        lines.append("Never call schedule_callback with a time outside the window.")
        return "\n".join(l for l in lines if l)

    def answer_format(self, qq):
        f = qq["format"]
        if f == "date":
            order = qq.get("date_order", "DMY")
            ex = {"DMY": ('"02/11/1979" -> 1979-11-02', "day, then month, then year"),
                  "MDY": ('"11/02/1979" -> 1979-11-02', "month, then day, then year"),
                  "YMD": ('"1979/11/02" -> 1979-11-02', "year, then month, then day")}[order]
            return ('field %s with the answer as YYYY-MM-DD. Convert spoken dates yourself (e.g. "14 March 1988" -> 1988-03-14). '
                    "A date given only as numbers is %s (%s)" % (qq["field"], ex[1], ex[0]))
        if f in ("digits", "year", "postcode") and (f != "digits" or qq.get("length")):
            return "field %s with %s" % (qq["field"], self.format_short(qq) if f != "digits"
                                         else "exactly %s digits" % NUM.get(qq["length"], str(qq["length"])))
        return "field %s with the answer as the customer said it" % qq["field"]

    @staticmethod
    def format_words(x):
        f = x.get("format")
        if f == "date":
            return "a date as day, month as a word, year"
        if f == "digits":
            return ("the %s digits" % NUM.get(x["length"], str(x["length"]))) if x.get("length") else "the digits"
        if f == "year":
            return "the four-digit year"
        if f == "postcode":
            return "the postcode, letter by letter"
        return "the answer"

    @staticmethod
    def format_short(x):
        f = x.get("format")
        if f == "date":
            return "YYYY-MM-DD"
        if f == "digits":
            return ("%s digits" % x["length"]) if x.get("length") else "digits only"
        if f == "year":
            return "a four-digit year"
        if f == "postcode":
            return "the postcode in capitals without spaces"
        return "as said"

    def identity_task(self):
        idn = self.s["identity"]
        qs = idn["questions"]
        n = len(qs)
        lines = ["The person confirmed they are %s. Now verify their identity with %s security question%s, one at a time, in this order:"
                 % (self.addr, NUM.get(n, str(n)), "" if n == 1 else "s")]
        lines += ["%d. %s" % (i + 1, q(x["ask"])) for i, x in enumerate(qs)]
        lines.append("Call verify_identity once for every answer: " + "; or ".join(self.answer_format(x) for x in qs) + ".")
        lines += [
            "Follow the what_to_do_next of each result exactly. A wrong answer may be asked again.",
            "Before all answers are verified: never say why you are calling. Never say %s, the product or an amount. "
            "Do not tell the customer how many wrong answers are left." % self.banned_words(),
            "If the customer does not want to answer, or asks what the call is about instead of answering: say %s once, and ask again. Do not explain a second time."
            % q(idn["decline_explanation"]),
        ]
        lines += ["- " + l for l in self.event_guidance("identity") + self.say_lines("identity")]
        lines.append(bullet_block("Rules from the brief:", self.extra("identity")))
        return "\n".join(l for l in lines if l)

    def negotiate_task(self):
        neg, conf = self.s["negotiation"], self.s["confirmation"]
        banned = neg.get("banned_offers") or []
        nbd = {"next_business_day": "A weekend day or holiday that is not after the deadline: offer the NEXT business day after it (the calendar names it), not the deadline.",
               "previous_business_day": "A weekend day or holiday that is not after the deadline: offer the business day BEFORE it (the calendar names it), not the deadline.",
               "reject": "A weekend day or holiday: say it is not a business day and ask for a business day."}[neg.get("non_business_day", "next_business_day")]
        aft = {"offer_deadline": "A date after the deadline: say the latest date you can record is the deadline ({{deadline_text}}) and offer it.",
               "reject": "A date after the deadline: say it cannot be recorded and ask for a date on or before the deadline ({{deadline_text}})."}[neg.get("after_deadline", "offer_deadline")]
        vague = {"ask_once_then_deadline": 'A vague period ("next week", "end of the month", "when my salary comes", "the week after next"): ask once for the exact day. If they still give no day, propose the deadline.',
                 "propose_deadline": 'A vague period ("next week", "end of the month", "when my salary comes"): propose the deadline.',
                 "ask_until_exact": 'A vague period ("next week", "end of the month", "when my salary comes"): ask for the exact day.'}[neg.get("vague_date", "ask_once_then_deadline")]
        below = {"offer_floor_once": "Amount from the floor up to the full amount: accept it. Below the floor: say it cannot be recorded and offer the floor once.",
                 "reject": "Amount from the floor up to the full amount: accept it. Below the floor: say it cannot be recorded.",
                 "accept": "Any amount above zero up to the full amount: accept it."}[neg.get("below_floor", "offer_floor_once")]
        fo = neg.get("floor_offer") or {}
        times = fo.get("times", 1)
        if times:
            too_much = "The customer says the full amount is too much: offer the floor {{floor_text}} %s" % ("once" if times == 1 else "up to %s times" % NUM.get(times, str(times)))
            too_much += (", and add: %s" % q(tpl(fo["sentence_suffix"]))) if fo.get("sentence_suffix") else "."
        else:
            too_much = "The customer says the full amount is too much: do not offer a lower amount; treat it as a refusal."
        if neg.get("never_ask_how_much"):
            too_much += " Never ask how much they can pay; you propose every amount."
        refuse = ("A flat refusal or a non-answer (including \"no\" to paying today with no date and no amount, refusing the floor, refusing the deadline) "
                  "uses one persuasion attempt from the persuasion plan. Make the next attempt in the plan. Never repeat an offer the customer already refused.")
        if self.has("refused"):
            refuse += " When all attempts in the plan are used and the customer still refuses, call transition_to_close_refused."
        first_ask = tpl(neg["first_ask"])
        t = """The customer is verified. You have already said: "{{debt_statement_text}} %s"

FACTS (computed, use them exactly)
- Today: {{today_text}}. Current time {{now_time}}.
- Product: {{product_words}}.
- Full past-due amount: {{past_due_text}}.
- Floor (lowest amount you may record): {{floor_text}}. Any amount from {{floor_text}} up to {{past_due_text}} (both included) can be accepted.
- Deadline (latest date you may record): {{deadline_text}}.
- Dates you may record (business days from today to the deadline): {{allowed_dates_text}}
- Persuasion plan:
{{attempt_plan}}
- Weekday words:
{{weekday_text}}
- Calendar:
{{calendar_text}}

HOW TO GET A PROMISE
1. The customer names a date or an amount: that is never a refusal. Check it:
   - Date must be one of the dates you may record. %s %s
   - %s
   - %s
2. %s
3. %s
4. When you have an amount and a date the customer accepts, read back the product, the amount and the date and ask %s. If they change the date or amount, check it again and read it back again.
5. Only after a clear yes, call create_promise with amount (number) and date (YYYY-MM-DD). Do not say it is recorded yourself; the result decides.""" % (
            first_ask, nbd, aft, vague, below, too_much, refuse, q(tpl(conf["question"])))
        if conf.get("change_note"):
            t += "\n6. If the customer asks to change or cancel a recorded promise, say: %s" % q(tpl(conf["change_note"]))
        other = self.event_guidance("negotiate")
        if self.banned_for("negotiate"):
            other.append("If asked for %s: say you cannot offer it." % join_or(with_article(b) for b in self.banned_for("negotiate")))
        other += self.say_lines("negotiate")
        other.append("A question you cannot answer from these facts: say you do not have that information.")
        t += "\n" + bullet_block("OTHER SITUATIONS", other)
        t += "\n" + bullet_block("RULES FROM THE BRIEF", self.extra("negotiate"))
        return t.rstrip()

    # ---- nodes
    def setup_node(self):
        cb = self.s["third_party"]["callback"]
        code = (self.prelude() + _const("CB", cb) +
                "CB_FALLBACK = %r\n" % ("A callback may be booked only " + self.cb_summary() + ".") + SETUP_CODE)
        return code_node("setup", code, "opening")

    def opening_node(self):
        op = self.s["opening"]
        first, _ = safe_opening(op["first_sentence"])
        values, desc, trans = self.event_values("opening")
        unclear = self.fallback_close("opening", ["unclear"])
        max_asks = op.get("max_asks", 2)
        accept = join_or([a for a in op.get("confirm_accept", [])])
        reject = join_or(op.get("confirm_reject", []))
        return {
            "name": "opening", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": first + " " + tpl(op["confirm_question"])},
            "on_enter_first_entry_only": True,
            "task_message": self.opening_task(),
            "extraction_schema": [{
                "name": "line_status", "type": "string", "min_confidence": 0.6,
                "enum_values": ["pending", "confirmed", "third_party"] + values + ["not_confirmed"],
                "description": " ".join(
                    ["Who is on the line, judged from the person's LATEST message.",
                     "confirmed: they said %s, confirming they are the customer." % accept,
                     "third_party: someone else answered (spouse, relative, colleague) and the customer is a person they know."]
                    + desc +
                    ["not_confirmed: anything else that does not confirm (%s, a question, an unclear answer)." % reject,
                     "pending: no message yet."]),
            }],
            "max_node_turns": max_asks + 1, "max_turn_transition": unclear,
            "transitions": [
                go("identity", "Confirmed", cond_any(eq("line_status", "confirmed"))),
                go("third_party", "Someone else answered", cond_any(eq("line_status", "third_party"))),
            ] + trans + [
                go(unclear, "Still not confirmed after the last ask", all_of(
                    eq("line_status", "not_confirmed"), gt("opening.total_turns", max_asks - 1))),
            ],
        }

    def third_party_node(self):
        values, desc, trans = self.event_values("third_party")
        no_cb = self.fallback_close("third_party", ["no_callback"])
        summary = self.cb_summary()
        tr = []
        if self.has("callback"):
            tr.append(go(self.close_key("callback"), "Callback booked",
                         cond_any(eq("cb_status", "booked"), *is_true("schedule_callback_response.data.ok")["conditions"])))
        return {
            "name": "third_party", "node_type": "conversation",
            "task_message": self.third_party_task(),
            "tools": [self.tool("schedule_callback", {
                "date": {"description": "Callback date, YYYY-MM-DD: %s." % summary, "default": "{{cb_date}}"},
                "time": {"description": "Callback time HH:MM, 24 hour, inside the callback window.", "default": "{{cb_time}}"},
            }, ["date", "time"], description="Book a callback. CALL it as soon as the person gives a day and time inside the callback window.",
                mapping=[{"path": "data.cb_status", "variable": "cb_status"}], post=CALLBACK_POST)],
            "extraction_schema": [{
                "name": "tp_status", "type": "string", "min_confidence": 0.6,
                "enum_values": ["continue"] + values,
                "description": " ".join(desc + ["continue: anything else."]),
            }],
            "max_node_turns": 12, "max_turn_transition": no_cb,
            "transitions": tr + trans,
        }

    def identity_node(self):
        idn = self.s["identity"]
        qs = idn["questions"]
        n = len(qs)
        values, desc, trans = self.event_values("identity")
        first = qs[0]["ask"]
        if n == 1:
            enter = "Thank you. For your security I need to ask you one question. " + first
        else:
            enter = ("Thank you. For your security I need to ask you %s questions. First, %s"
                     % (NUM.get(n, str(n)), first[:1].lower() + first[1:]))
        unreadable = "; ".join("for %s: %s" % (q(x["ask"]), self.format_words(x)) for x in qs)
        next_q = " ".join("If it was the answer to %s, now ask: %s" % (q(qs[i]["ask"]), q(qs[i + 1]["ask"])) for i in range(n - 1))
        if not next_q:
            next_q = "Say only: Thank you."
        not_yet = "Do not mention %s yet." % (join_or(self.s["opening"].get("banned_before_verify", []), "or") or "why you are calling")
        post = ("import json\n" + _const("HINTS", idn.get("non_counting_hints") or {}) + "UNREADABLE = %r\nNEXT_Q = %r\nNOT_YET = %r\n"
                % (unreadable, next_q, not_yet) + VERIFY_POST)
        pre = "import json\n" + _const("FORMATS", {x["field"]: {k: v for k, v in x.items() if k in ("format", "length", "date_order")} for x in qs}) + VERIFY_PRE
        answer_desc = "; ".join("%s: %s" % (x["field"], self.format_short(x)) for x in qs)
        failed = self.fallback_close("identity", ["failed"])
        declined = self.fallback_close("identity", ["declined"])
        return {
            "name": "identity", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": enter},
            "on_enter_first_entry_only": True,
            "task_message": self.identity_task(),
            "tools": [self.tool("verify_identity", {
                "field": {"description": "Which question this answer is for: %s." % join_or([x["field"] for x in qs]), "default": "{{field}}"},
                "answer": {"description": answer_desc + ".", "default": "{{answer}}"},
            }, ["field", "answer"], description="Check one security answer. CALL it every time the customer answers a security question.",
                retries=1, mapping=[{"path": "data.all_verified", "variable": "id_all"}, {"path": "data.wrong_answers_left", "variable": "id_left"}],
                pre=pre, post=post)],
            "extraction_schema": [{
                "name": "id_event", "type": "string", "min_confidence": 0.6,
                "enum_values": ["continue"] + values,
                "description": " ".join(desc + ["continue: anything else, including a first refusal."]),
            }],
            "max_node_turns": 20, "max_turn_transition": declined,
            "transitions": [
                go("load_account", "All answers verified", is_true("id_all", "verify_identity_response.data.all_verified")),
                go(failed, "Too many wrong answers", cond_any(eq("id_left", "0"), eq("verify_identity_response.data.wrong_answers_left", "0"))),
            ] + trans,
        }

    def account_nodes(self):
        ga = self.tool("get_account", {}, [], retries=2, post=GET_ACCOUNT_POST)
        self.nodes["load_account"] = {
            "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "prepare_account",
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
            "pre_actions": [ga],
            "response_mapping": [{"path": "data.products", "variable": "acct_products"},
                                 {"path": "data.first_json", "variable": "acct_first_json"},
                                 {"path": "data.products_json", "variable": "acct_products_json"}],
            "transitions": [always("prepare_account")],
        }
        neg = self.s["negotiation"]
        code = (self.prelude() + _const("PRODUCTS", self.s["products"]) + _const("BANDS", self.s["bands"]) +
                _const("NEG", {k: neg.get(k) for k in ("debt_statement", "debt_statement_per_product", "weekday_words", "non_business_day") if neg.get(k) is not None}) +
                _const("NUMS", {str(k): v for k, v in NUM.items()}) + ACCOUNT_CODE)
        self.nodes["prepare_account"] = code_node("prepare_account", code, "negotiate",
                                                  [go("close_account_error", "No account data", cond_any(eq("account_state", "error")))])

    def negotiate_node(self):
        neg, conf = self.s["negotiation"], self.s["confirmation"]
        banned = self.banned_for("negotiate")
        values, desc, trans = self.event_values("negotiate")
        structural = ["continue", "cannot_pay_or_too_much"] + (["discount_request"] if banned else [])
        sdesc = ["cannot_pay_or_too_much: they say they cannot pay (today or at all) or that the amount is too much, without asking for anything else."]
        if banned:
            sdesc.append("discount_request: they ask for %s." % join_or(with_article(b) for b in banned))
        retries = conf.get("retries", 1)
        fail = self.close_key(self.fail_ev) if self.has(self.fail_ev) else "close_account_error"
        tr = []
        if self.has("promise_recorded"):
            tr.append(go(self.close_key("promise_recorded"), "Promise recorded",
                         cond_any(eq("promise_status", "recorded"), *is_true("create_promise_response.data.ok")["conditions"])))
        if retries >= 1:
            tr.append(go("retry_promise", "First promise call failed with an HTTP error", all_of(
                eq("create_promise_failure_count", "1"), eq("create_promise_response.status", "error"))))
        tr.append(go(fail, "Promise failed after the retries", cond_any(
            gt("promise_fail_n", retries), gt("create_promise_failure_count", retries))))
        tr += trans
        if self.has("refused"):
            ev = self.events["refused"]
            tr.append({"target_node": self.close_key("refused"), "is_tool_call": True,
                       "description": "Call this when every persuasion attempt in the plan has been made and the customer still refuses. The goodbye is spoken automatically.",
                       "condition": {"operator": "matches_intent", "description": "Customer: " + ev["trigger"]}})
        return {
            "name": "negotiate", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": "Thank you, you are verified. {{debt_statement_text}} " + tpl(neg["first_ask"])},
            "on_enter_first_entry_only": True,
            "task_message": self.negotiate_task(),
            "tools": [self.tool("create_promise", {
                "product_id": {"default": "{{product_id}}"},
                "amount": {"description": "Promised amount, a number such as 420.00. From the floor up to the full past-due amount.", "default": "{{promise_amount}}"},
                "date": {"description": "Promised payment date, YYYY-MM-DD, one of the dates you may record.", "default": "{{promise_date}}"},
            }, ["amount", "date"], description="Record the payment promise. CALL it only after the customer said yes to your read-back of product, amount and date.",
                mapping=[{"path": "data.promise_status", "variable": "promise_status"}], post=PROMISE_POST.replace("RETRIES", str(int(retries))))],
            "extraction_schema": [{
                "name": "neg_event", "type": "string", "min_confidence": 0.6,
                "enum_values": structural[:1] + values + structural[1:],
                "description": " ".join(["Judge the customer's LATEST message."] + desc + sdesc +
                                        ["continue: anything else (a date, an amount, a question, a confirmation, an agreement)."]),
            }] + self.seen_vars("negotiate"),
            "max_node_turns": 40, "max_turn_transition": self.fallback_close("negotiate", ["refused"]),
            "transitions": tr,
        }

    def retry_node(self):
        fail = self.close_key(self.fail_ev) if self.has(self.fail_ev) else "close_account_error"
        ok = self.close_key("promise_recorded") if self.has("promise_recorded") else fail
        rp = self.tool("create_promise", {
            "product_id": {"default": "{{product_id}}"},
            "amount": {"default": "{{create_promise_args.promise_amount}}"},
            "date": {"default": "{{create_promise_args.promise_date}}"},
        }, ["amount", "date"], pre=RETRY_PRE)
        return {
            "name": "retry_promise", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": fail,
            "on_enter": {"type": "tts", "text": "I am sorry, there was a technical problem on our side. Let me try that once more."},
            "disable_inactivity_monitor": True,
            "pre_actions": [rp],
            "response_mapping": [{"path": "data.ok", "variable": "retry_ok"}],
            "transitions": [go(ok, "Retry worked", is_true("retry_ok", "retry_promise_response.data.ok")), always(fail)],
        }

    def close_call_tool(self):
        enum = body_enum(self.oa, "close_call", "outcome")
        by = {}
        for o in self.s["outcomes"]:
            by.setdefault(o["outcome"], [])
            if o.get("reason") and o["reason"] not in by[o["outcome"]]:
                by[o["outcome"]].append(o["reason"])
        reasonless = sorted({o["outcome"] for o in self.s["outcomes"] if not o.get("reason")})
        rdesc = " ".join("%s: %s." % (k, ", ".join(v) if v else "leave out") for k, v in by.items())
        t = self.tool("close_call", {
            "outcome": {"enum": enum, "default": "{{outcome}}", "description": "How the call ended: one of " + ", ".join(enum) + "."},
            "reason": {"default": "{{reason}}", "description": "Reason by outcome. " + rdesc},
        }, ["outcome"], description="Record how the call ended.", pre="import json\n" + _const("REASONLESS", reasonless) + CLOSE_PRE)
        t["config"]["alwaysRunsAt"] = ["end_of_call"]
        return t

    def build(self):
        n = self.nodes
        n["setup"] = self.setup_node()
        n["opening"] = self.opening_node()
        n["third_party"] = self.third_party_node()
        n["identity"] = self.identity_node()
        self.account_nodes()
        n["negotiate"] = self.negotiate_node()
        n["retry_promise"] = self.retry_node()
        self.build_closes()
        n["goodbye"] = goodbye_node()
        test_vars = {"customer_id": "c_test"} if "customer_id" in self.s.get("call_variables", []) else {}
        return {
            "initial_node": "setup",
            "system_prompt": self.system_prompt(),
            "nodes": n,
            "global_nodes": {},
            "tools": [self.close_call_tool()],
            "test_variables": test_vars,
        }


def compile(spec, openapi, base_url=None):  # noqa: A001 - the interface name is fixed
    return _Compiler(spec, openapi, base_url).build()


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m builder.compile")
    ap.add_argument("spec")
    ap.add_argument("openapi")
    ap.add_argument("--base-url")
    ap.add_argument("--out")
    ap.add_argument("--save", metavar="SLOT")
    ap.add_argument("--state", default=".freya-agents.json", help="slot state file for --save")
    a = ap.parse_args(argv)
    spec = json.load(open(a.spec))
    oa = json.load(open(a.openapi))
    wf = compile(spec, oa, a.base_url)
    problems = lint(wf, spec)
    for p in problems:
        print(p, file=sys.stderr)
    errors = [p for p in problems if not p.startswith("warning:")]
    if a.out:
        with open(a.out, "w") as f:
            json.dump(wf, f, indent=1)
        print("wrote %s (%d nodes)" % (a.out, len(wf["nodes"])), file=sys.stderr)
    if a.save:
        if errors:
            print("lint errors: not saving", file=sys.stderr)
            return 1
        import freya
        print(json.dumps(freya.save_workflow(a.save, wf, state_file=a.state)))
    elif not a.out:
        print(json.dumps(wf, indent=1))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
