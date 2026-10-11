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
from builder import multi as multi_mod
from builder.multi import Multi, enabled as multi_enabled
from builder.nodes import (all_of, always, api_tool, body_enum, body_schema, close_node, code_node,
                           cond_any, eq, farewell_default_code, go, goodbye_node, gt, is_true, py_json, transfer_node)

STAGES = ["opening", "third_party", "identity", "negotiate"]
# "any" means the customer's own stages; a third party is only covered when a spec names it.
ANY_STAGES = ["opening", "identity", "negotiate"]
# Tools each stage's dialogue model can call (an extra rule naming another tool is dropped there).
STAGE_TOOLS = {"opening": set(), "third_party": {"schedule_callback"}, "identity": {"verify_identity"},
               "negotiate": {"create_promise"}, "any": set()}
STAGE_TOOLS_ALL = set().union(*STAGE_TOOLS.values())
PROMISE_FIELDS = ("product_id", "amount", "date")
# Call variables every brief so far has; any other spec.call_variables entry is declared in test_variables.
STANDARD_VARS = ("customer_id", "first_name", "last_name", "title", "now")
TRANSFER_ACTIONS = ("transfer", "insist_once_then_transfer", "offer_transfer_else_close")
_STOP = {"asks", "says", "with", "from", "their", "they", "that", "this", "lower", "new", "for"}
EXTRACT_VAR = {"opening": "line_status", "third_party": "tp_status", "identity": "id_event", "negotiate": "neg_event",
               "readback": "rb_answer"}
# Value a stage's extraction variable is reset to when the conversation comes back to it.
EXTRACT_DEFAULT = {"opening": "pending", "third_party": "continue", "identity": "continue", "negotiate": "continue",
                   "readback": "pending"}
# L1 (PUB-06): an amount said once is pointed back to, never offered again.
DAY_NUMBER = 'A day number ("the 26th", "twenty-sixth") is an exact day, not a vague period: follow the first calendar row with that number.'
CUT_OFF = ('A message cut off mid-phrase ("Monday the.", "what the") is not an answer or a refusal and uses no attempt or ask: '
           'say only "Go on." and read it with the next message.')
# A12 (one product): a named day is judged by check_date in code, never by the model
CHECK_DAY = ('A day ("the 26th", "twenty-sixth", "Monday the." then "26th.", "next Friday", "tomorrow"): before you answer, call %s with '
             'their words for it, joined across turns. Never judge a date yourself: say what the result says, use its date in create_promise. '
             'Their own date, even after a "no", is never a refusal.')
DATE_ONLY = ('A date with no amount: the amount is the one of your current attempt (the full amount, or the floor once it is on the table). '
             'Propose that amount with the date as a yes/no question; never ask them to name an amount.')
MENTIONED = ('Say each amount once. Asked about it again ("what is the minimum?", "how much?"): "That is the <minimum payment / amount> I '
             'mentioned. Can you pay it <date of that offer>?": no figure, no new date (keep that date unless they refused it), never a new '
             'offer, never %s again. Never restate a figure already offered.')
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
    if re.match(r"(?:a|an|any|the|no)\s", s, re.I):
        return s
    return ("an " if s[:1].lower() in "aeiou" else "a ") + s


def bare_trigger(t):
    """A trigger without its subject ("The customer asks X." -> "asks X")."""
    t = re.sub(r"^(?:the customer|the person)\s+", "", (t or "").strip().rstrip(". "), flags=re.I)
    return t[:1].lower() + t[1:]


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
    cfg = product_cfg_of(p, PRODUCTS) or dict(words=ptype.replace('_', ' '), floor_field='')
    past_due = amount_of(p, cfg)
    days_late = days_of(p, cfg)
    words = fill(cfg['words'], p, dict(past_due=fmt(past_due), days_late=str(days_late)))
    ff = cfg.get('floor_field') or ''
    floor = float(p.get(ff)) if p.get(ff) not in (None, '') else past_due
    if floor > past_due:
        floor = past_due
    band = pick_band(days_late)
    dl = deadline(today, band['deadline'], CAL)
    attempts = []
    prev = None
    for i, a in enumerate(band['attempts']):
        d = attempt_date(today, a['date'], dl, CAL)
        when = ('on the deadline, ' + speak(d)) if a['date'] == 'deadline' else ('on ' + speak(d))
        if prev is not None and a['amount'] == prev:
            txt = 'propose only the new date ' + when + ' for that same amount, without saying the figure again.'
        elif a['amount'] == 'floor':
            txt = ('propose the lower amount ' + fmt(floor) + ' ' + when + '. If it was already offered, propose only the date ' +
                   'for that amount, without saying the figure again.')
        elif a['amount'] == 'full':
            txt = 'propose the full amount ' + fmt(past_due) + ' ' + when + '.'
        else:
            txt = ('propose the full amount ' + fmt(past_due) + ' ' + when + '. If the customer already said the full amount is too much, ' +
                   'the lower amount ' + fmt(floor) + ' is already on the table: propose only the date for that amount, without saying the figure again.')
        prev = a['amount']
        attempts.append(dict(date=d, text='Attempt ' + str(i + 1) + ': ' + txt))
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


ORD_WORDS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh',
             'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth',
             'twentieth', 'twenty-first', 'twenty-second', 'twenty-third', 'twenty-fourth', 'twenty-fifth', 'twenty-sixth',
             'twenty-seventh', 'twenty-eighth', 'twenty-ninth', 'thirtieth', 'thirty-first']


def day_label(n):
    """'the 26th (twenty-sixth)': a calendar row found by the day number in digits or in words."""
    suf = 'th' if 11 <= n % 100 <= 13 or n % 10 > 3 else ['th', 'st', 'nd', 'rd'][n % 10]
    return 'the ' + str(n) + suf + ' (' + ORD_WORDS[n - 1] + ')'


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
        out.append('- ' + day_label(d.day) + ': ' + speak(d) + ': ' + tag + (' (today)' if d == today else ''))
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
    products = [p for p in find_products()
                if pfield(p, product_cfg_of(p, PRODUCTS), 'amount_field', 'past_due') not in (None, '')]
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
    vars['deadline_iso'] = f['deadline'].isoformat()
    for i, a in enumerate(f['attempts']):
        vars['attempt' + str(i + 1) + '_text'] = speak(a['date'])
    vars['attempt_plan'] = f['plan']
    vars['allowed_dates_text'] = ', '.join(x.isoformat() for x in f['allowed'])
    vars['calendar_text'] = calendar_text(f['deadline'])
    vars['weekday_text'] = weekday_text()
    vars['debt_statement_text'] = f['debt_statement']
    vars['account_state'] = ACCOUNT_OK
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
    import datetime
    MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
              'october', 'november', 'december']
    MON3 = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
    UNITS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
    TEENS = ['ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
    TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
    ORD_UNITS = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth']
    ORD_TEENS = ['tenth', 'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth',
                 'eighteenth', 'nineteenth']
    ORD_TENS = ['', '', 'twentieth', 'thirtieth']
    FIX = r'\b(?:sorry|i mean|i meant|actually|correction|no wait|wait|rather|scratch that|no)\b'

    def _month(w):
        w = w.lower().rstrip('.')
        if w in MONTHS:
            return MONTHS.index(w) + 1
        if w == 'sept':
            return 9
        if w in MON3:
            return MON3.index(w) + 1
        return 0

    def _atom(w):
        """(kind, value) of one number word: u unit, t teen, x tens, o ordinal, h hundred, k thousand, m double/triple."""
        if w in UNITS:
            return 'u', UNITS.index(w)
        if w in TEENS:
            return 't', 10 + TEENS.index(w)
        if w in TENS:
            return 'x', 10 * TENS.index(w)
        if w in ORD_UNITS:
            return 'o', ORD_UNITS.index(w)
        if w in ORD_TEENS:
            return 'o', 10 + ORD_TEENS.index(w)
        if w in ORD_TENS:
            return 'o', 10 * ORD_TENS.index(w)
        if w == 'hundred':
            return 'h', 100
        if w == 'thousand':
            return 'k', 1000
        if w in ('double', 'triple'):
            return 'm', 2 if w == 'double' else 3
        return None, 0

    def _small(at, i):
        """A number below 100 at at[i]: (value, kind, next index). kind: d one digit, 2 two digits, o ordinal."""
        k, v = at[i]
        if k == 'x':
            if i + 1 < len(at) and at[i + 1][0] == 'u' and at[i + 1][1] > 0:
                return v + at[i + 1][1], '2', i + 2
            if i + 1 < len(at) and at[i + 1][0] == 'o' and 0 < at[i + 1][1] < 10:
                return v + at[i + 1][1], 'o', i + 2
            return v, '2', i + 1
        if k == 't':
            return v, '2', i + 1
        if k == 'o':
            return v, 'o', i + 1
        return v, 'd', i + 1

    def _convert(ws):
        """Number words of one run -> list of (digit string, kind)."""
        at = [_atom(w) if w not in ('oh', 'o') else ('z', 0) for w in ws if w != 'and']
        out = []
        i = 0
        while i < len(at):
            k, v = at[i]
            if k == 'm':
                if i + 1 < len(at) and at[i + 1][0] in ('u', 'z'):
                    out += [(str(at[i + 1][1]), 'd')] * v
                    i += 2
                else:
                    i += 1
                continue
            if k == 'z':
                if out and out[-1][1] == '2' and i + 1 < len(at) and at[i + 1][0] == 'u':
                    out.append(('0' + str(at[i + 1][1]), 'z'))
                    i += 2
                else:
                    out.append(('0', 'd'))
                    i += 1
                continue
            if k in ('h', 'k'):
                i += 1
                continue
            n, kind, i = _small(at, i)
            if kind != 'o' and i < len(at) and at[i][0] in ('h', 'k'):
                total = 0
                while True:
                    if i < len(at) and at[i][0] == 'h':
                        n, i = n * 100, i + 1
                    if i < len(at) and at[i][0] == 'k':
                        total, n, i = total + n * 1000, 0, i + 1
                        if i < len(at) and at[i][0] in ('u', 't', 'x', 'o'):
                            n, kind, i = _small(at, i)
                            if kind != 'o' and i < len(at) and at[i][0] == 'h':
                                continue
                        break
                    if i < len(at) and at[i][0] in ('u', 't', 'x', 'o'):
                        m, kind, i = _small(at, i)
                        n += m
                    break
                out.append((str(total + n), 'o' if kind == 'o' else 'n'))
                continue
            out.append((str(n), kind))
        # spoken pairs, right to left: "nineteen eighty-eight" -> 1988, "forty-four ten" -> 4410, "nineteen oh four" -> 1904
        j = len(out) - 1
        while j >= 1:
            if out[j][1] in ('2', 'z') and out[j - 1][1] == '2':
                out[j - 1:j + 1] = [(out[j - 1][0] + out[j][0], 'n')]
                j -= 2
            else:
                j -= 1
        return out

    def _words(text):
        """Number words to digits ("nineteen eighty-eight" -> 1988, "fourteenth" -> 14). Never raises."""
        tk = re.findall(r'[A-Za-z]+|\d+|[^A-Za-z\d]+', text)
        low = [t.lower() for t in tk]
        isw = [t.isalpha() for t in tk]

        def nb(i, step):
            j = i + step
            while 0 <= j < len(tk) and not isw[j] and not tk[j].isdigit():
                if tk[j].strip(' -') != '':
                    return None
                j += step
            return j if 0 <= j < len(tk) and isw[j] else None

        def num(i):
            w = low[i]
            if w in ('first', 'second'):
                n1, p1 = nb(i, 1), nb(i, -1)
                if p1 is not None and low[p1] == 'the':
                    p1 = nb(p1, -1)
                return (n1 is not None and (low[n1] == 'of' or _month(low[n1]) > 0)) or (p1 is not None and _month(low[p1]) > 0)
            return _atom(w)[0] is not None

        isn = [isw[i] and num(i) for i in range(len(tk))]
        for i in range(len(tk)):
            if isw[i] and low[i] in ('oh', 'o', 'and'):
                n1, p1 = nb(i, 1), nb(i, -1)
                isn[i] = n1 is not None and p1 is not None and isn[n1] and isn[p1] if low[i] == 'and' else \
                    ((n1 is not None and isn[n1]) or (p1 is not None and isn[p1]))
        out = []
        i = 0
        while i < len(tk):
            if not isn[i]:
                out.append(tk[i])
                i += 1
                continue
            run = [low[i]]
            j = i + 1
            while True:
                n1 = nb(j - 1, 1)
                if n1 is None or not isn[n1]:
                    break
                run.append(low[n1])
                j = n1 + 1
            if run[-1] == 'and':
                run.pop()
            out.append(' '.join(s for s, k in _convert(run)))
            i = j
        return ''.join(out)

    def _dtoks(s):
        out = []
        for m in re.finditer(r'[A-Za-z]+\.?|\d+', s):
            t = m.group(0)
            if t.isdigit():
                out.append(('N', t))
            elif _month(t):
                out.append(('M', _month(t)))
        return out

    def _ymd(y, mo, d):
        try:
            return datetime.date(int(y), int(mo), int(d)).isoformat()
        except Exception:
            return None

    def _triple(ts, order):
        """ISO date, 'RAW' (a whole numeric date whose order does not fit, e.g. a swapped day and month), or None."""
        ms = [v for k, v in ts if k == 'M']
        ns = [v for k, v in ts if k == 'N']
        if len(ms) == 1 and len(ns) == 2:
            ys = [x for x in ns if len(x) == 4]
            ds = [x for x in ns if len(x) <= 2]
            if len(ys) == 1 and len(ds) == 1:
                return _ymd(ys[0], ms[0], ds[0])
            return None
        if ms or len(ns) != 3:
            return None
        x, y, z = ns
        if len(x) == 4 and len(y) <= 2 and len(z) <= 2:
            return _ymd(x, y, z)
        if len(z) != 4 or len(x) > 2 or len(y) > 2:
            return None
        r = _ymd(z, x, y) if order == 'MDY' else _ymd(z, y, x)
        if r:
            return r
        return 'RAW' if (_ymd(z, y, x) or _ymd(z, x, y)) else None

    def _date(a, order, alone):
        segs = [s for s in re.split(FIX, a, flags=re.I) if _dtoks(s)]
        if not segs or (len(segs) > 1 and not alone):
            return a, False
        ts = _dtoks(segs[-1])
        if not ts or len(ts) != 3:
            # several dates without a correction word between them: ask again, never guess which one counts
            return a, False
        r = _triple(ts, order)
        if r is None:
            return a, False
        if r == 'RAW':
            # a whole numeric date in the wrong order goes to the bank as said (it answers with a non-counting hint)
            return a, alone and len(segs) == 1 and len(ts) == 3
        return r, True

    def _digits(a, n, alone):
        segs = [s for s in re.split(FIX, a, flags=re.I) if re.search(r'\d', s)]
        if not segs or (len(segs) > 1 and not alone):
            return '', False
        # two plain values ("4410 and 5521") join to too many digits: incomplete, never the last one
        dg = ''.join(re.findall(r'\d+', segs[-1]))
        return dg, (len(dg) == n if n else bool(dg))

    def _year(a, alone):
        y, ok = _digits(a, 4, alone)
        if not ok:
            return y, False
        m = re.match(r'\d{4}', str(vars.get('now') or ''))
        cur = int(m.group(0)) if m else datetime.date.today().year
        return y, 1900 <= int(y) <= cur

    def _norm(a, q, alone):
        """(normalised answer, complete?) for one answer text."""
        fm = q.get('format')
        if fm == 'date':
            return _date(a, q.get('date_order') or 'DMY', alone)
        if fm == 'digits':
            return _digits(a, int(q.get('length') or 0), alone)
        if fm == 'year':
            return _year(a, alone)
        if fm == 'postcode':
            return a.upper().replace(' ', ''), True
        return a, True

    vars['id_incomplete'] = 'no'
    b = config.get('body')
    if isinstance(b, dict):
        fld = str(b.get('field') or '')
        q = FORMATS.get(fld)
        a = str(b.get('answer') if b.get('answer') is not None else '').strip()
        part = str(vars.get('id_partial') or '').strip()
        vars['id_partial_seen'] = part
        # combine only parts the extraction tied to THIS question and that no checked call has used yet
        if part and (part == str(vars.get('id_partial_spent') or '') or str(vars.get('id_partial_field') or '') != fld):
            part = ''
        if q and a:
            ok = False
            v = a
            try:
                conv = q.get('format') in ('date', 'digits', 'year')
                numeric = q.get('format') in ('digits', 'year')
                a2 = _words(a) if conv else a
                p2 = _words(part) if conv and part else part
                if numeric and p2 and not re.fullmatch(r'[\d\s,.\-]+', p2):
                    # a digits answer combines only with digit parts (a stale "14 March" must not become 1410)
                    p2 = ''
                cands = [(a2, True)]
                if p2:
                    if re.search(r'(?<![0-9A-Za-z])' + re.escape(a2) + r'(?![0-9A-Za-z])', p2, flags=re.I):
                        # this answer is already one of the recorded pieces: try the parts on their own
                        cands.append((p2, False))
                    elif not re.search(FIX, p2 + ' ' + a2, flags=re.I):
                        # digits join in the order said; a date's typed parts may come in either order
                        cands += [(p2 + ' ' + a2, False)] + ([] if numeric else [(a2 + ' ' + p2, False)])
                for c, alone in cands:
                    v2, ok2 = _norm(c, q, alone)
                    if ok2:
                        v, ok = v2, True
                        break
            except Exception:
                ok = False
            if ok:
                b['answer'] = v
            else:
                # a partial answer must not reach the bank as an answer (a short digit string counts as wrong)
                vars['id_incomplete'] = 'yes'
                b['field'] = 'incomplete_answer'
                b['answer'] = ''
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
        err = str(d.get('error') or result.get('error') or '')
        technical = str(result.get('status', 'success')) != 'success' or (d.get('ok') is None and d.get('verified') is None)
        incomplete = str(vars.get('id_incomplete') or '') == 'yes' or err.startswith('field must be')
        if incomplete:
            vars['id_incomplete'] = 'no'
            d.clear()
            d['ok'] = False
            d['counted'] = False
            d['what_to_do_next'] = ('The answer is INCOMPLETE and was NOT checked. This is NOT a wrong answer. Ask only for the missing part (' +
                                    UNREADABLE + '), keep the part already given, and then call verify_identity once with the whole answer.')
        elif technical:
            d['what_to_do_next'] = 'The security service had a technical problem and nothing was checked. Say so in one short sentence and call verify_identity again with the same answer.'
        if not incomplete:
            # any result but INCOMPLETE ends this answer: the parts collected so far are used up, never combine them again
            vars['id_partial_spent'] = str(vars.get('id_partial_seen') or '')
            vars['id_partial'] = ''
            vars['id_partial_field'] = ''
        if incomplete or technical:
            pass
        elif d.get('all_verified') is True:
            d['what_to_do_next'] = 'All answers are verified. Say only: Thank you.'
        elif hint and hint in HINTS:
            d['what_to_do_next'] = 'This is NOT a wrong answer (' + str(hint).replace('_', ' ') + '). ' + HINTS[hint] + ' Then call verify_identity again.'
        elif str(left) == '0' and d.get('verified') is not True:
            d['what_to_do_next'] = 'That was the last wrong answer allowed. The check has ended. Do not ask anything else.'
        elif d.get('ok') is False:
            d['what_to_do_next'] = 'The answer could not be read. This is NOT a wrong answer. Ask the customer to repeat it (' + UNREADABLE + '). Then call verify_identity again.'
        elif d.get('verified') is True:
            d['what_to_do_next'] = 'This answer is correct. ' + NEXT_Q + ' ' + NOT_YET
        elif str(left) == '0':
            d['what_to_do_next'] = 'That was the last wrong answer allowed. The check has ended. Do not ask anything else.'
        else:
            d['what_to_do_next'] = ('This answer is not correct. Say "I am sorry, that is not correct." (never "records" or "on file") and ask the SAME question again. Wrong answers left: ' +
                                    str(left) + '.')
except Exception:
    pass
'''

# A10: the support hold node's own turn counter before the customer's first reply is this value (probe, fix round 1).
SUPPORT_TURNS_SEEN = 0

# A10: identity's decline route. A message whose extracted answer parts are new (not the parts the last checked call
# used, not the parts this gate already sent back) carries an answer: back to identity to check it, once per parts.
ID_DECLINE_GATE = r'''
part = str(vars.get('id_partial') or '').strip()
if part and part != str(vars.get('id_partial_spent') or '').strip() and part != str(vars.get('id_gate_part') or ''):
    vars['id_gate_part'] = part
    vars['id_event'] = 'continue'
    vars['id_gate'] = 'answer'
else:
    vars['id_gate'] = 'close'
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
            d['what_to_do_next'] = 'The promise is recorded.' + str(vars.get('promise_date_note') or '')
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
        dt = vars.get('promise_date_used') or vars.get('create_promise_args.promise_date')
        if dt:
            b['date'] = str(dt)[:10]
        pid = vars.get('product_id')
        if pid:
            b['product_id'] = str(pid)
except Exception:
    pass
'''

# A12: one product, a day the customer names. check_date is a read-only api tool (get_account route, like set_product
# in multi.py); its pre-hook resolves the customer's words (dates.resolve_said) and judges the date by the spec rules
# (dates.date_verdict), the post-hook makes that the tool result the model reads. The model only speaks the verdict.
# Body leaves are model-filled through {{cdx_*}} defaults; no cdx_* variable is ever set.
CHECK_TOOL = "check_date"
CHECK_PRE = r'''
def _cd_arg(b, k):
    v = b.get(k)
    if v is None or (isinstance(v, str) and v.strip()[:2] == chr(123) * 2):
        return ''
    return str(v).strip()


def _cd_kind_of(d):
    if d.isoformat() in CAL['holidays']:
        return 'a bank holiday, not a business day'
    if d.weekday() >= 5:
        return 'a weekend day, not a business day'
    return 'not a business day'


try:
    b = config.get('body') if isinstance(config, dict) else None
    if not isinstance(b, dict):
        b = dict()
    said = _cd_arg(b, 'said')
    iso = _cd_arg(b, 'date')[:10]
    dl = datetime.date.fromisoformat(str(vars.get('deadline_iso') or '')[:10])
    d, why = resolve_said(said, today, WW)
    if d is None and why != 'vague' and re.match(r'^\d{4}-\d{2}-\d{2}$', iso):
        try:
            d = datetime.date.fromisoformat(iso)
        except ValueError:
            d = None
    off = None
    if d is None:
        kind = 'vague' if why == 'vague' else 'no_day'
    else:
        kind, off = date_verdict(d, today, dl, CAL, NBD, AFTER)
    cur = 'with the amount of your current attempt, as a yes/no question'
    never = ' Their own date is never a refusal.'
    if kind == 'ok':
        msg = ('OK: ' + say_date(d) + ' can be accepted. Propose it ' + cur + ' unless they already gave an amount. '
               'create_promise date: ' + d.isoformat() + '.')
    elif kind == 'not_business_day' and off is not None:
        if NBD == 'previous_business_day' and off == prev_bd(d, CAL):
            which = 'the business day before it'
        elif off == next_bd(d, CAL):
            which = 'the next business day'
        else:
            which = 'the latest date you can accept'
        msg = ('NOT A BUSINESS DAY: ' + say_date(d) + ' is ' + _cd_kind_of(d) + '. Say so and offer ' + which + ', ' + say_date(off) + ', ' + cur + '.' +
               never + ' If they accept, create_promise date: ' + off.isoformat() + '.')
    elif kind == 'not_business_day':
        msg = ('NOT A BUSINESS DAY: ' + say_date(d) + ' is ' + _cd_kind_of(d) + '. Say so and ask for a business day on or before ' + say_date(dl) + '.' + never)
    elif kind == 'after_deadline' and off is not None:
        msg = ('AFTER THE DEADLINE: ' + say_date(d) + ' is after the deadline. Say the latest date you can accept is ' + say_date(off) +
               ' and offer it ' + cur + '.' + never + ' If they accept, create_promise date: ' + off.isoformat() + '.')
    elif kind == 'after_deadline':
        msg = ('AFTER THE DEADLINE: ' + say_date(d) + ' is after the deadline. Say it cannot be accepted and ask for a date on or before ' + say_date(dl) + '.' + never)
    elif kind == 'past':
        msg = 'IN THE PAST: ' + say_date(d) + ' has already passed. Say so and ask for a date from today on.'
    elif kind == 'vague':
        msg = 'NO EXACT DAY: that is a period, not a day. Follow your rule for a vague period.'
    else:
        msg = ('NO DAY FOUND in the words given. If the customer named a day, call ' + TOOL + ' again with it as YYYY-MM-DD in date; '
               'otherwise ask for the exact day.')
    vars['cd_kind'] = kind
    vars['cd_date'] = off.isoformat() if off is not None else ''
    vars['cd_msg'] = msg
except Exception as e:
    vars['cd_kind'] = 'error'
    vars['cd_date'] = ''
    vars['cd_msg'] = 'There was a technical problem checking the date (' + str(e)[:60] + '). Call ' + TOOL + ' again with the same values.'
'''

CHECK_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        result['data'] = dict(ok=True, verdict=str(vars.get('cd_kind') or 'error'), date=str(vars.get('cd_date') or ''),
                              what_to_do_next=str(vars.get('cd_msg') or 'Call ' + TOOL + ' again with the same values.'))
except Exception:
    pass
'''

# create_promise (one product): the date sent is the one the rules allow. A date that is not acceptable becomes the date
# date_verdict offers for it (next business day, deadline); a date that is not YYYY-MM-DD becomes check_date's last date.
PROMISE_DATE_PRE = r'''
try:
    vars['promise_date_note'] = ''
    b = config.get('body') if isinstance(config, dict) else None
    if isinstance(b, dict):
        raw = str(b.get('date') or '').strip()[:10]
        dl = datetime.date.fromisoformat(str(vars.get('deadline_iso') or '')[:10])
        try:
            d = datetime.date.fromisoformat(raw)
        except ValueError:
            d = None
        use = None
        if d is None:
            use = str(vars.get('cd_date') or '') or None
        else:
            kind, off = date_verdict(d, today, dl, CAL, NBD, AFTER)
            if kind != 'ok' and off is not None:
                use = off.isoformat()
        if use and use != raw:
            b['date'] = use
            vars['promise_date_note'] = (' It is recorded for ' + say_date(datetime.date.fromisoformat(use)) +
                                         ', the date the rules allow: say that date.')
        vars['promise_date_used'] = str(b.get('date') or '')[:10]
except Exception:
    pass
'''

# spec.responses: the brief's openapi names some response fields differently; copy each back to the name the
# hooks read (RESP is {canonical: brief name}) before any other post-hook logic runs.
ALIAS_POST = r'''
try:
    if isinstance(result, dict) and isinstance(result.get('data'), dict):
        for rk, rv in RESP.items():
            if rv != rk and rv in result['data']:
                result['data'][rk] = result['data'][rv]
except Exception:
    pass
'''

# Extra request fields (spec.tool_fields / openapi-required fields the compiler has no slot for): take the value from
# its source variable when it has one, map a near-miss onto the enum, else use the spec default. EXTRA is
# {field: {enum, default, src}}.
EXTRA_PRE = r'''
import re

def _xmatch(ok, v):
    """The one enum value the customer's words name ("the mobile app" -> app), or None."""
    words = lambda x: ' '.join(re.findall(r'[a-z0-9]+', str(x).lower()))
    key = words(v)
    hit = [x for x in ok if words(x) == key]
    if not hit:
        hit = [x for x in ok if words(x) and re.search(r'\b' + re.escape(words(x)) + r'\b', key)]
    if not hit and len(key) >= 3:
        hit = [x for x in ok if re.search(r'\b' + re.escape(key) + r'\b', words(x))]
    if not hit:
        kt = set(key.split())
        hit = [x for x in ok if kt & set(t for t in words(x).split() if len(t) >= 3)]
    return hit[0] if len(hit) == 1 else None


try:
    vars['extra_note'] = ''
    eb = config.get('body')
    if isinstance(eb, dict):
        notes = []
        for ef, es in EXTRA.items():
            ev = eb.get(ef)
            if es.get('src'):
                sv = vars.get(es['src'])
                if sv not in (None, ''):
                    ev = sv
            if isinstance(ev, str):
                ev = ev.strip()
                if ev.startswith('{') or ev.lower() in ('none', 'null'):
                    ev = None
            ok = es.get('enum') or []
            said = ev
            if ok and ev not in (None, '') and ev not in ok:
                ev = _xmatch(ok, ev)
            if ev in (None, ''):
                ev = es.get('default')
            if ev is None:
                eb.pop(ef, None)
            else:
                eb[ef] = ev
                if ev != said:
                    notes.append(ef.replace('_', ' ') + ' was sent as ' + str(ev))
        if notes:
            vars['extra_note'] = ' Note: ' + '; '.join(notes) + '. Tell the customer this value and use it from now on.'
except Exception:
    pass
'''

# Model tools with extra fields: after the tool's own post-hook, tell the model which value was sent.
EXTRA_POST = r'''
try:
    if isinstance(result, dict) and isinstance(result.get('data'), dict) and vars.get('extra_note'):
        result['data']['what_to_do_next'] = str(result['data'].get('what_to_do_next') or '') + str(vars.get('extra_note'))
        vars['extra_note'] = ''
except Exception:
    pass
'''

CLOSE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict) and (b.get('outcome') in REASONLESS or not b.get('reason')):
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
        self.latched = set()  # close/wrap nodes that redirect to a support event's else-close once its offer was made
        self.multi = Multi(self) if multi_enabled(spec) else None

    # ---- helpers
    def tool(self, name, props, required, source=None, model=None, **kw):
        """An api_call tool. Request fields the call site does not fill (openapi-required, or named in
        spec.tool_fields) are added: a fixed spec value is baked; otherwise the model fills them (model tools) or
        they come from the variable `source % field` (engine nodes), else from the spec default."""
        if kw.get("path") not in (None, name):
            return api_tool(name, self.base, self.oa, props, required, **kw)
        if model is None:
            model = source is None and name in STAGE_TOOLS_ALL
        extra, req, hook = self.extra_props(name, props, source, model)
        if hook:
            kw["pre"] = "import json\n" + _const("EXTRA", hook) + EXTRA_PRE + (kw.get("pre") or "")
            if model and any(h.get("src") is None for h in hook.values()):
                kw["post"] = (kw.get("post") or "") + EXTRA_POST
        resp = {k: v for k, v in ((self.s.get("responses") or {}).get(name) or {}).items() if k != v}
        if resp:
            kw["post"] = "import json\n" + _const("RESP", resp) + ALIAS_POST + (kw.get("post") or "")
        return api_tool(name, self.base, self.oa, dict(props, **extra), list(required) + req, **kw)

    def resp_vars(self, tool, canon, prefix=None):
        """Response variables a transition tests: <prefix>_response.data.<canon>, plus the brief's alias name."""
        prefix = prefix or tool
        out = ["%s_response.data.%s" % (prefix, canon)]
        alias = ((self.s.get("responses") or {}).get(tool) or {}).get(canon)
        if alias and alias != canon:
            out.append("%s_response.data.%s" % (prefix, alias))
        return out

    def extra_fields(self, name, known=()):
        """[(field, openapi property, spec guidance)] the call site leaves out but the tool needs."""
        try:
            sc = body_schema(self.oa, name)
        except (KeyError, TypeError):
            return []
        tf = self.s.get("tool_fields") or {}
        props = sc.get("properties") or {}
        need = set(sc.get("required") or [])
        out = []
        for f, p in props.items():
            if f == "customer_id" or f in known:
                continue
            g = tf.get(name + "." + f)
            if f in need or g is not None:
                out.append((f, p, g or {}))
        return out

    def extra_desc(self, f, p, g):
        bits = [g.get("instruction") or "", p.get("description") or ""]
        said = " ".join(bits)
        if p.get("enum") and not all(re.search(r"\b%s\b" % re.escape(str(x)), said) for x in p["enum"]):
            bits.append("One of: %s." % ", ".join(str(x) for x in p["enum"]))
        if g.get("default") is not None:
            bits.append("If the customer gives none of these, use %s." % g["default"])
        return " ".join(b.strip() for b in bits if b and b.strip())

    def extra_props(self, name, known, source, model):
        """(extra body props, extra required names, EXTRA hook config or None)."""
        props, req, hook = {}, [], {}
        try:
            need = set(body_schema(self.oa, name).get("required") or [])
        except (KeyError, TypeError):
            need = set()
        for f, p, g in self.extra_fields(name, known):
            if g.get("value") is not None:
                props[f] = {"default": g["value"]}
            elif source is not None:
                var = source % f
                props[f] = {"default": "{{" + var + "}}"}
                hook[f] = {"enum": p.get("enum") or [], "default": g.get("default"), "src": var}
            elif model:
                props[f] = {"default": "{{" + f + "}}", "description": self.extra_desc(f, p, g)}
                hook[f] = {"enum": p.get("enum") or [], "default": g.get("default"), "src": None}
            elif g.get("default") is not None:
                props[f] = {"default": g["default"]}
            else:
                continue
            if f in need:
                req.append(f)
        return props, req, hook or None

    def model_extras(self, name, known):
        """Model-filled extra fields of a tool: [(field, description)] (fixed values are not the model's)."""
        return [(f, self.extra_desc(f, p, g)) for f, p, g in self.extra_fields(name, known) if g.get("value") is None]

    def extra_lines(self, name, known):
        return ["%s also needs %s: %s" % (name, f, d) for f, d in self.model_extras(name, known)]

    def has(self, ev_id):
        return ev_id in self.events

    @staticmethod
    def stages_of(ev):
        return stages_of(ev)

    @staticmethod
    def lead_in(eid):
        return LEAD_IN.get(eid, "I understand.")

    def end_target(self, stage, eid):
        """Where a closing event goes: its close node, or (several products, negotiate) its wrap node."""
        if self.multi and stage == "negotiate":
            return "wrap_" + eid
        return self.close_key(eid)

    def stage_tools(self, stage):
        if self.multi and stage == "negotiate":
            return set()
        return STAGE_TOOLS.get(stage, set())

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
            self.nodes[name] = transfer_node(name, queue, close, self.base, self.oa,
                                             tool=self.tool("transfer_to_agent", {"queue": {"default": queue}}, ["queue"], retries=1))
        return self.transfers[key]

    def stage_events(self, stage):
        skip = set(STRUCTURAL.get(stage, set()))
        if stage == "negotiate":
            skip.add(self.fail_ev)
        return [e for e in self.s["events"] if stage in stages_of(e) and e["id"] not in skip
                and not (self.multi and stage == "negotiate" and e["action"] == "note_and_continue")]

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
        parts.append(self.thanks(named))
        return " ".join(parts)

    def thanks(self, named):
        if self.s.get("closing", {}).get("thank_and_goodbye", True) is not False:
            return "Thank you for your time, " + self.s["address_as"] + ". Goodbye." if named else "Thank you for your time. Goodbye."
        return "Goodbye."

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
        self.nodes["close_account_error"] = close_node(
            "close_account_error", *self.fail_close(),
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
                trans.append({"target_node": self.latch_gate(self.end_target(stage, eid)), "is_tool_call": True,
                              "description": "Call this when the customer %s%s. The goodbye is spoken automatically." % (trig, nth),
                              "condition": {"operator": "matches_intent", "description": "The customer %s%s" % (trig, nth)}})
                continue
            extra = (" It must be the %s time; an earlier time is NOT %s." % (ORD.get(times, str(times)), eid)
                     if times > 1 and "second" not in trig and "twice" not in trig else "")
            if act == "insist_once_then_transfer":
                values += [eid + "_first_request", eid + "_insists"]
                desc.append("%s_first_request: %s, and the agent has not yet answered such a request by asking whether it can help first." % (eid, subj(who, trig)))
                desc.append("%s_insists: the agent already asked whether it can help first, and in this message %s again." % (eid, subj(who, trig)))
                t = self.transfer_to(ev["queue"], self.close_key(eid), eid)
                trans.append(go(t, "Insists: " + eid, all_of(eq(EXTRACT_VAR[stage], eid + "_insists"), eq(eid + "_asked", "yes"))))
                trans.append(go(self.ask_once_nodes(stage, ev, t), "First request: " + eid,
                                cond_any(eq(EXTRACT_VAR[stage], eid + "_first_request"), eq(EXTRACT_VAR[stage], eid + "_insists"))))
            elif act == "offer_transfer_else_close":
                team = team_words(ev["queue"]).replace("our ", "the ")
                values += [eid, eid + "_accepted", eid + "_declined"]
                desc.append("%s: %s (and does not answer an offer of %s)." % (eid, subj(who, trig), team))
                desc.append("%s_accepted: in the agent's previous message it offered to connect them with %s and they say yes to that." % (eid, team))
                desc.append("%s_declined: the agent offered to connect them with %s and in this message they refuse it. "
                            "This wins over every other value, even if the same message also asks for something else or says they cannot pay." % (eid, team))
                t = self.transfer_to(ev["queue"], self.close_key(eid) + "_transfer", eid)
                if stage == "negotiate":
                    # A10 latch: once the offer was made (by the support_nodes step, flag set in code), the customer's
                    # next refusal, decline, "cannot pay", discount request or goodbye ends with the else-close; a named
                    # date or amount is the only way back to a promise (support_nodes routes it)
                    var, done = EXTRACT_VAR[stage], eq(eid + "_offered", "yes")
                    after = [eid, eid + "_declined", "cannot_pay_or_too_much"] + (["discount_request"] if banned else []) + (["goodbye"] if self.multi else [])
                    trans.append(go(t, eid + ": offered, transfer accepted", all_of(eq(var, eid + "_accepted"), done)))
                    trans.append(go(self.end_target(stage, eid), eid + ": offered, no named payment",
                                    cond_any(*[{"logic": "and", "conditions": [eq(var, v), done]} for v in after])))
                trans.append(go(t, eid + ": transfer accepted", all_of(eq(EXTRACT_VAR[stage], eid + "_accepted"), eq(eid + "_seen", "yes"))))
                trans.append(go(self.end_target(stage, eid), eid + ": transfer declined", all_of(eq(EXTRACT_VAR[stage], eid + "_declined"), eq(eid + "_seen", "yes"))))
                if stage == "negotiate":
                    offer = self.support_nodes(ev, t)
                    trans.append(go(offer, eid + ": make the offer (once)", cond_any(
                        {"logic": "and", "conditions": [eq(EXTRACT_VAR[stage], eid), eq(eid + "_offered", "no")]},
                        {"logic": "and", "conditions": [eq(eid + "_seen", "yes"), eq(eid + "_offered", "no")]})))
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
                trans.append(go(self.end_target(stage, eid), "Close: " + eid, cond_any(eq(EXTRACT_VAR[stage], eid))))
                if self.backup_close(stage, ev):
                    # A10 audit: a missed extraction must not leave the model persuading a customer who disputes or
                    # has paid; the model can end the call itself (the extraction normally routes before it speaks)
                    trans.append({"target_node": self.end_target(stage, eid), "is_tool_call": True,
                                  "description": "Call this when the customer %s. The goodbye is spoken automatically." % trig.rstrip(". "),
                                  "condition": {"operator": "matches_intent", "description": "The customer %s" % trig.rstrip(". ")}})
        desc.append(self.not_events(stage))
        return values, desc, trans

    def not_events(self, stage):
        """Say-rule triggers are answered in place: list them as NOT an event (a robot question once fired a transfer)."""
        trig = [r["trigger"] for r in self.s.get("say_rules", [])
                if stage in (r.get("stages") or ["any"]) or ("any" in (r.get("stages") or ["any"]) and stage in ANY_STAGES)]
        if not trig:
            return ""
        default = "not_confirmed" if stage == "opening" else "continue"
        return ("These are NOT any transfer or close event (value %s): the person %s." % (default, "; or ".join(trig)))

    def ask_once_nodes(self, stage, ev, transfer):
        """insist_once_then_transfer: a code node sets <id>_asked, then a conversation node speaks the ask-once line
        and transfers only if the person insists; anything else returns to the stage."""
        eid = ev["id"]
        code_name, hold = "%s_ask_%s" % (eid, stage), "%s_hold_%s" % (eid, stage)
        if code_name in self.nodes:
            return code_name
        self.nodes[code_name] = code_node(code_name, "vars[%r] = 'yes'\nvars[%r] = 'pending'\n" % (eid + "_asked", eid + "_reply"), hold)
        back = "%s_back_%s" % (eid, stage)
        reset = "vars[%r] = %r\nvars[%r] = 'pending'\n" % (EXTRACT_VAR[stage], EXTRACT_DEFAULT[stage], eid + "_reply")
        if self.multi and stage == "negotiate":
            reset += "if not vars.get('mp_msg'):\n    vars['mp_msg'] = 'none yet'\n"
        self.nodes[back] = code_node(back, reset, stage)
        say = tpl(ev.get("say") or "Can I help you with that first?")
        who = "the customer" if stage == "negotiate" else "the person"
        self.nodes[hold] = {
            "name": hold, "node_type": "conversation",
            "on_enter": {"type": "tts", "text": say},
            "task_message": "You asked %s. If %s answers anything other than insisting, reply briefly. No tool call is allowed in this step." % (q(say), who),
            "extraction_schema": [{
                "name": eid + "_reply", "type": "string", "min_confidence": 0.6, "enum_values": ["pending", "insists", "other"],
                "description": ("The LATEST message, a reply to %s. insists: %s still %s (says yes, repeats it, or refuses help). "
                                "other: anything else (accepts help, answers a question, asks something). pending: no reply yet."
                                % (q(say), who, ev["trigger"]))}],
            "max_node_turns": 2, "max_turn_transition": back,
            "transitions": [go(transfer, "Insists: " + eid, cond_any(eq(eid + "_reply", "insists"))),
                            go(back, "Back to the conversation", cond_any(eq(eid + "_reply", "other")))]}
        return code_name

    # ---- A10: support offer latch (offer_transfer_else_close in negotiate)
    def support_events(self):
        return [e for e in self.stage_events("negotiate") if e["action"] == "offer_transfer_else_close"]

    @staticmethod
    def support_rule(ev, offered):
        """Text of the {{<id>_rule}} variable in the negotiate prompt: before and after the offer was made."""
        if not offered:
            return "Not made yet."
        return ("It was made: make no offer or persuasion of your own. Only a date or an amount the customer names "
                "themselves: check it under the rules (a date after the deadline gets the deadline, an amount below the floor "
                "gets the floor, once) and read it back. Anything else ends the call automatically.")

    def support_init_code(self):
        """Set by prepare_account: every support offer starts as not made."""
        return "".join("vars[%r] = 'no'\nvars[%r] = %r\n" % (e["id"] + "_offered", e["id"] + "_rule", self.support_rule(e, False))
                       for e in self.support_events())

    @staticmethod
    def neutral(text):
        """The brief's instruction without recording words (the hold step speaks before any promise)."""
        return re.sub(r"\brecord(?:s|ed)? (?:the|a) promise\b", "take their payment date and amount", text or "", flags=re.I)

    def support_nodes(self, ev, transfer):
        """A10 latch. <id>_offer (code) marks the offer as made; <id>_hold (conversation) speaks it and reads the reply:
        a yes transfers; a date or an amount the customer names returns to negotiate (<id>_back); a human request goes to
        that event's ask-once step; a question is answered in one sentence and the offer asked once more (first reply
        only); a decline, anything else, an unreadable reply or a second non-yes reply ends with the else-close."""
        eid = ev["id"]
        offer, hold, back = eid + "_offer", eid + "_hold", eid + "_back"
        if offer in self.nodes:
            return offer
        end = self.end_target("negotiate", eid)
        reply = eid + "_reply"
        code = "vars[%r] = 'yes'\nvars[%r] = 'pending'\nvars[%r] = %r\n" % (eid + "_offered", reply, eid + "_rule", self.support_rule(ev, True))
        if self.multi:  # reached from the read-back as well: negotiation is open again
            code += "vars['mp_phase'] = 'open'\nvars['rb_answer'] = 'pending'\n"
        self.nodes[offer] = code_node(offer, code, hold)
        reset = "vars['neg_event'] = 'continue'\nvars[%r] = 'pending'\n" % reply
        if self.multi:
            reset += ("vars['mp_msg'] = 'The customer named a date or an amount after the support offer. Make no offer of your own; "
                      "check what they named and call %s for that product.'\n" % multi_mod.SET_TOOL)
        self.nodes[back] = code_node(back, reset, "negotiate")
        team = team_words(ev["queue"]).replace("our ", "the ")
        banned = self.banned_for("negotiate")
        steps = ["Exactly one short sentence of sympathy."]
        if ev.get("say"):
            steps.append("Say %s." % q(tpl(ev["say"])))
        if banned:
            steps.append("Only if they asked for %s: say you cannot offer it." % join_or(with_article(b) for b in banned))
        steps.append("Offer to connect them with %s, as a yes/no question." % team)
        answer = ["If they then ask a question: answer it in one sentence (if what you know does not answer it, say you do "
                  "not have that information), then ask once more whether they want to be connected. Do this only once."]
        answer += ["Rule (%s): say %s" % (bare_trigger(r["trigger"]), q(tpl(r["say"])))
                   for r in self.s.get("say_rules", []) if {"any", "negotiate"} & set(r.get("stages") or ["any"])]
        # a request for a human keeps its own ask-once handling (brief rules apply after the offer too)
        values, routes, hdesc = [], [], []
        for h in self.stage_events("negotiate"):
            if h["action"] == "insist_once_then_transfer":
                v = h["id"] + "_request"
                values.append(v)
                hdesc.append("%s: they %s." % (v, h["trigger"].rstrip(". ")))
                t = self.transfer_to(h["queue"], self.close_key(h["id"]), h["id"])
                routes.append(go(self.ask_once_nodes("negotiate", h, t), eid + ": " + v, cond_any(eq(reply, v))))
        turns = hold + ".total_turns"
        self.nodes[hold] = {
            "name": hold, "node_type": "conversation",
            "on_enter": {"type": "llm"},
            "task_message": ("Situation: %s. Your first reply in this step, in this order, and nothing else:\n%s\n%s\n"
                             "Never a payment request or a persuasion attempt: no amount, no date, no plan. "
                             "The brief's rule for this situation: %s\nNo tool call is allowed in this step."
                             % ("the customer " + bare_trigger(ev["trigger"]),
                                "\n".join("%d. %s" % (i + 1, x) for i, x in enumerate(steps)),
                                "\n".join(answer), tpl(self.neutral(ev.get("instruction") or "Offer the team once.")))),
            "extraction_schema": [{
                "name": reply, "type": "string", "min_confidence": 0.6,
                "enum_values": ["pending", "accepted", "names_payment", "declined", "question"] + values + ["other"],
                "description": " ".join(["The customer's LATEST message, a reply to the offer to connect them with %s." % team,
                                         "accepted: they say yes to being connected.",
                                         "names_payment: they name a payment date or an amount themselves (\"I can pay 100 on Friday\").",
                                         "declined: they refuse the offer (even if they also ask whether there is anything else).",
                                         "question: they only ask a question (what the team does, whether this is a scam) and do not refuse."]
                                        + hdesc + ["other: anything else (they say they cannot pay, ask for a discount or a plan).",
                                                   "pending: no reply yet."])}],
            # probe (fix round 1): the cap fires on the user turn that reaches it, before any route is evaluated, so 3
            # lets the second reply route normally (a yes there still transfers); the counter is 0 before the first reply
            "max_node_turns": 3, "max_turn_transition": end,
            "transitions": [go(transfer, eid + ": transfer accepted", cond_any(eq(reply, "accepted"))),
                            go(back, eid + ": the customer names a payment", cond_any(eq(reply, "names_payment")))] + routes + [
                            go(end, eid + ": declined, anything else, unreadable, or a second non-yes reply", cond_any(
                                eq(reply, "declined"), eq(reply, "other"),
                                {"logic": "and", "conditions": [eq(reply, "pending"), gt(turns, SUPPORT_TURNS_SEEN)]},
                                gt(turns, SUPPORT_TURNS_SEEN + 1)))]}
        return offer

    def latch_gate(self, target):
        """A close the model calls (refused, a counted close) or a turn cap: once a support offer was made it ends
        with that event's else-close instead (no refused outcome after a support offer). The target keeps its name (the
        model's tool is still transition_to_<target>); build() puts the redirect first in that node's transitions,
        and the else-close node then overwrites outcome, reason and farewell (or the wrap's notes) in full."""
        if self.support_events():
            self.latched.add(target)
        return target

    def apply_latch(self):
        for t in sorted(self.latched):
            n = self.nodes.get(t)
            if not n:
                continue
            for e in reversed(self.support_events()):
                end = self.end_target("negotiate", e["id"])
                if end != t:
                    n["transitions"].insert(0, go(end, "Support offer made: " + e["id"], cond_any(eq(e["id"] + "_offered", "yes"))))

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
                lines.append("If %s: the ask-once question is spoken automatically." % (
                    subj("the customer" if stage == "negotiate" else "the person", ev["trigger"])))
            elif ev["action"] == "offer_transfer_else_close" and stage == "negotiate":
                lines.append("If %s: the offer of %s is made automatically; never make it yourself and make no persuasion attempt. "
                             "Offer status: {{%s_rule}}" % ("the customer " + bare_trigger(ev["trigger"]),
                                                            team_words(ev["queue"]).replace("our ", "the "), ev["id"]))
            elif ev["action"] == "offer_transfer_else_close":
                extra = (" This comes before persuasion: make no persuasion attempt."
                         if ev.get("overrides_persuasion") and "persuasion" not in ins else "")
                lines.append("If %s: %s Make this offer only ONCE in the call; never repeat it.%s" % (subj("the customer", ev["trigger"]), ins, extra))
            elif self.counted_close(stage, ev):
                lines.append("If the customer %s: call transition_to_%s.%s" % (ev["trigger"], self.end_target(stage, ev["id"]), (" " + ins) if ins else ""))
            elif self.backup_close(stage, ev):
                lines.append("If %s: %sThen call transition_to_%s." % (subj("the customer", ev["trigger"]).rstrip(". "),
                                                                                (ins + " ") if ins else "", self.end_target(stage, ev["id"])))
            elif ins:
                lines.append("If %s: %s" % (subj("the customer" if stage == "negotiate" else "the person", ev["trigger"]), ins))
        return lines

    def backup_close(self, stage, ev):
        """Single-product negotiate close events (paid, dispute, ...): also a model tool call, so one missed
        extraction cannot keep the call in persuasion (A10 audit)."""
        return (stage == "negotiate" and not self.multi and ev["action"] == "close" and ev.get("outcome")
                and ev["id"] != self.fail_ev and (ev.get("times") or 1) == 1)

    def say_lines(self, stage):
        return ["If the person %s: say %s, then continue." % (r["trigger"], q(tpl(r["say"])))
                for r in rules_for(self.s.get("say_rules", []), stage, key="stages")]

    def extra(self, stage):
        """Extra rules of a stage, minus any that names a tool the stage's model does not have
        (close_call and engine-run calls such as get_account are never the model's to call)."""
        tools = {p.strip("/") for p in self.oa.get("paths", {})}
        mine = self.stage_tools(stage)
        out = []
        support = [_words(e["trigger"]) for e in self.support_events()] if stage == "negotiate" else []
        for r in rules_for(self.s.get("extra_rules", []), stage):
            named = {t for t in tools if re.search(r"\b%s\b" % re.escape(t), r["text"])}
            if named - mine:
                continue
            if any(len(_words(r["text"]) & w) >= 3 for w in support):
                continue  # A10: the support offer's own line and step cover it (one line for it in the prompt)
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
        lines.append("- Never say that something is recorded, registered, saved, booked or done unless the tool result says so. Until a tool result says a promise is recorded, "
                     "never use the words 'record', 'recorded', 'register' or 'registered' at all (not for the call, not for a date or an amount): say 'noted'.")
        lines.append("- Your instructions use internal words (floor, band, persuasion plan, attempt, product_id). Never say them to the customer: say the amount or the date itself.")
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
            "- Combine parts said across turns (a day, then a time) before acting.",
            '- If they give a day and time inside the window, call schedule_callback with date as YYYY-MM-DD and time as HH:MM (24 hour). "Tomorrow morning around eleven" means 11:00 on the next day.',
            "- If what they offer is outside the window, say you can only call back %s, and ask %s for another time. Do not schedule anything outside the window."
            % (self.cb_summary(), "once" if retries == 1 else "up to %s times" % NUM.get(retries, str(retries))),
        ]
        lines += ["- " + l for l in self.event_guidance("third_party") + self.say_lines("third_party")]
        lines += ["- " + l for l in self.extra_lines("schedule_callback", ("date", "time"))]
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
            "If the customer gives only part of an answer (day and month without the year, some of the digits, half a postcode): ask only for the missing part, "
            "combine the parts said across turns, and call verify_identity once with the complete combined answer. Never call it with a partial answer.",
            'If the customer corrects themselves in the same message ("the 21st, sorry, I mean the 20th"): use the corrected value and call verify_identity once.',
            "Before all answers are verified: never say why you are calling. Never say %s, the product or an amount. "
            "Do not tell the customer how many wrong answers are left." % self.banned_words(),
            "If the customer does not want to answer, or asks what the call is about instead of answering: say %s once, and ask again. Do not explain a second time."
            % q(idn["decline_explanation"]),
        ]
        lines += ["- " + l for l in self.event_guidance("identity") + self.say_lines("identity")]
        lines += ["- " + l for l in self.extra_lines("verify_identity", ("field", "answer"))]
        lines.append(bullet_block("Rules from the brief:", self.extra("identity")))
        return "\n".join(l for l in lines if l)

    def negotiate_task(self):
        neg, conf = self.s["negotiation"], self.s["confirmation"]
        banned = neg.get("banned_offers") or []
        vague = {"ask_once_then_deadline": 'A vague period ("next week", "end of the month", "when my salary comes"): ask once for the exact day. If they still give no day, propose the deadline.',
                 "propose_deadline": 'A vague period ("next week", "end of the month", "when my salary comes"): propose the deadline.',
                 "ask_until_exact": 'A vague period ("next week", "end of the month", "when my salary comes"): ask for the exact day.'}[neg.get("vague_date", "ask_once_then_deadline")]
        below = {"offer_floor_once": "Amount from the floor up to the full amount: accept it. Below the floor: say it cannot be accepted and offer the floor once.",
                 "reject": "Amount from the floor up to the full amount: accept it. Below the floor: say it cannot be accepted.",
                 "accept": "Any amount above zero up to the full amount: accept it."}[neg.get("below_floor", "offer_floor_once")]
        fo = neg.get("floor_offer") or {}
        times = fo.get("times", 1)
        if times:
            too_much = ("The customer says the full amount is too much: offer the floor {{floor_text}} %s, in the same sentence as the date of your current attempt"
                        % ("once" if times == 1 else "up to %s times" % NUM.get(times, str(times))))
            too_much += (", and add %s once" % q(tpl(fo["sentence_suffix"]))) if fo.get("sentence_suffix") else ""
            too_much += "."
        else:
            too_much = "The customer says the full amount is too much: do not offer a lower amount; treat it as a refusal."
        if neg.get("never_ask_how_much"):
            too_much += " Never ask how much they can pay; you propose every amount."
        refuse = ("A flat refusal or a non-answer (including \"no\" to paying today with no date and no amount, refusing the floor, refusing the deadline) "
                  "uses one persuasion attempt of the plan. Make the next one. Never repeat an offer the customer already refused.")
        if self.has("refused"):
            refuse += " When all attempts in the plan are used and the customer still refuses, call transition_to_close_refused."
        first_ask = tpl(neg["first_ask"])
        t = """The customer is verified. You have already said: "{{debt_statement_text}} %s"

FACTS (computed, use them exactly)
- Today: {{today_text}}. Current time {{now_time}}.
- Product: {{product_words}}.
- Full past-due amount: {{past_due_text}}.
- Floor (lowest amount you may accept): {{floor_text}}.
- Deadline (latest date you may accept): {{deadline_text}}.
- Persuasion plan:
{{attempt_plan}}

HOW TO GET A PROMISE
1. The customer names a date or an amount (combine parts said across turns: "twelve"... "fifty" = 1250, "four hundred"... "and twenty" = 420; a short number right after a partial one is the rest of it): never a refusal. Check it:
   - %s
   - %s
   - %s
   - %s
   - %s
2. %s
3. %s
   %s
4. When you have an amount and a date the customer accepts, read back the product, the amount and the date and ask %s. A read-back is not an offer, so it states all three. If they change the date or amount, check it again and read it back again.
5. Only after a clear yes, call create_promise with amount (number) and date (YYYY-MM-DD). The result decides whether it is recorded.""" % (
            first_ask, DATE_ONLY, CHECK_DAY % CHECK_TOOL, vague, CUT_OFF, below, too_much, refuse, self.mentioned(), q(tpl(conf["question"])))
        for line in self.extra_lines("create_promise", PROMISE_FIELDS):
            t += "\n   " + line + " Read it back too."
        if conf.get("change_note"):
            t += "\n6. If the customer asks to change or cancel a recorded promise, say: %s" % q(tpl(conf["change_note"]))
        t = self.said_already(t)
        other = self.event_guidance("negotiate")
        if self.banned_for("negotiate"):
            other.append("If asked for %s: say you cannot offer it." % join_or(with_article(b) for b in self.banned_for("negotiate")))
        other += self.say_lines("negotiate")
        t += "\n" + bullet_block("OTHER SITUATIONS", other)
        t += "\n" + bullet_block("RULES FROM THE BRIEF", self.extra("negotiate"))
        return t.rstrip()

    # ---- nodes
    def fail_close(self):
        """(outcome, reason) of the service-failure close (also used when the account cannot be read)."""
        fe = self.events.get(self.fail_ev) or {}
        return fe.get("outcome") or "no_promise", fe.get("reason") or ""

    def setup_nodes(self):
        """setup computes the call's date facts and writes a plain default farewell (no outcome). A snippet that did
        not run (sets no setup_state) is run once more by setup_retry; the call goes on after that either way."""
        cb = self.s["third_party"]["callback"]
        code = (farewell_default_code("I am sorry, there was a technical problem. " + self.thanks(named=False)) +
                self.prelude() + _const("CB", cb) +
                "CB_FALLBACK = %r\n" % ("A callback may be booked only " + self.cb_summary() + ".") + SETUP_CODE)
        ran = cond_any(eq("setup_state", "ok"), eq("setup_state", "error"))
        self.nodes["setup"] = code_node("setup", code, "setup_retry", [go("opening", "Setup ran", ran)])
        self.nodes["setup_retry"] = code_node("setup_retry", code, "opening")

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
            }] + self.seen_vars("opening"),
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
                         cond_any(eq("cb_status", "booked"), *is_true(*self.resp_vars("schedule_callback", "ok"))["conditions"])))
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
            }] + self.seen_vars("third_party"),
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
        if self.has("declined"):
            # A10 (SELF-06): a decline extracted from a message that also carries an unchecked answer goes back to
            # identity once, so the answer is checked (a third wrong answer then closes as failed, not declined)
            gate = "id_decline_gate"
            self.nodes[gate] = code_node(gate, ID_DECLINE_GATE, self.close_key("declined"),
                                         [go("identity", "The message carried an answer to check", cond_any(eq("id_gate", "answer")))])
            for t in trans:
                if t["target_node"] == self.close_key("declined") and not t.get("is_tool_call"):
                    t["target_node"] = gate
        return {
            "name": "identity", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": enter},
            "on_enter_first_entry_only": True,
            "task_message": self.identity_task(),
            "tools": [self.tool("verify_identity", {
                "field": {"description": "Which question this answer is for: %s." % join_or([x["field"] for x in qs]), "default": "{{field}}"},
                "answer": {"description": answer_desc + ". The COMPLETE answer: combine parts said across turns; after a self-correction use only the corrected value.",
                           "default": "{{answer}}"},
            }, ["field", "answer"], description="Check one security answer. CALL it every time the customer answers a security question.",
                retries=1, mapping=[{"path": "data.all_verified", "variable": "id_all"}, {"path": "data.wrong_answers_left", "variable": "id_left"}],
                pre=pre, post=post)],
            "extraction_schema": [{
                "name": "id_event", "type": "string", "min_confidence": 0.6,
                "enum_values": ["continue"] + values,
                "description": " ".join(desc + ["continue: anything else, including a first refusal. A partial answer (only part of a date, "
                                                "some of the digits), \"give me a second\", a self-correction or a question back is continue, never a refusal. "
                                                "A message that gives an answer to the security question (in digits or in words, right or wrong) is continue, "
                                                "never a decline, even if it also complains or asks what the call is about."]),
            }, {
                "name": "id_partial", "type": "string", "min_confidence": 0.5,
                "description": ("The parts of the answer to the security question currently being asked, as said so far across the customer's "
                                "messages, combined in the order said (e.g. \"14 March\" then \"1988\" gives \"14 March 1988\"; \"44\" then \"10\" gives \"4410\"). "
                                "After a self-correction keep only the corrected value. Empty when a new question is asked."),
            }, {
                "name": "id_partial_field", "type": "string", "min_confidence": 0.6,
                "enum_values": [x["field"] for x in qs],
                "description": ("Which security question the parts in id_partial answer: the question the agent asked most recently "
                                "(%s)." % "; ".join("%s: %s" % (x["field"], q(x["ask"])) for x in qs)),
            }] + self.seen_vars("identity"),
            "max_node_turns": 20, "max_turn_transition": declined,
            "transitions": [
                go("load_account", "All answers verified", is_true("id_all", *self.resp_vars("verify_identity", "all_verified"))),
                go(failed, "Too many wrong answers", cond_any(eq("id_left", "0"), *[eq(x, "0") for x in self.resp_vars("verify_identity", "wrong_answers_left")])),
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
        code = (self.prelude() + self.support_init_code() + "ACCOUNT_OK = %r\n" % ("facts" if self.multi else "ok") +
                _const("PRODUCTS", self.s["products"]) + _const("BANDS", self.s["bands"]) +
                _const("NEG", {k: neg.get(k) for k in ("debt_statement", "debt_statement_per_product", "weekday_words", "non_business_day", "first_ask", "first_ask_all") if neg.get(k) is not None}) +
                _const("NUMS", {str(k): v for k, v in NUM.items()}) + ACCOUNT_CODE +
                (self.multi.account_code() if self.multi else ""))
        # negotiate only on facts the snippet confirmed (account_state is its last write); anything else, including a
        # snippet that did not run, closes as an account error rather than speaking empty amounts and dates
        self.nodes["prepare_account"] = code_node("prepare_account", code, "close_account_error",
                                                  [go("negotiate", "Account facts ready", cond_any(eq("account_state", "ok")))])

    def negotiate_node(self):
        neg, conf = self.s["negotiation"], self.s["confirmation"]
        banned = self.banned_for("negotiate")
        values, desc, trans = self.event_values("negotiate")
        structural = ["continue", "cannot_pay_or_too_much"] + (["discount_request"] if banned else [])
        sdesc = ["cannot_pay_or_too_much: they say they cannot pay (today or at all) or that the amount is too much, without asking for anything else. A message cut off mid-phrase (\"you tell me what the\", \"Monday the.\") is never cannot_pay_or_too_much: it is continue."]
        if banned:
            sdesc.append("discount_request: they ask for %s." % join_or(with_article(b) for b in banned))
        retries = conf.get("retries", 1)
        fail = self.close_key(self.fail_ev) if self.has(self.fail_ev) else "close_account_error"
        tr = []
        if self.has("promise_recorded"):
            tr.append(go(self.close_key("promise_recorded"), "Promise recorded",
                         cond_any(eq("promise_status", "recorded"), *is_true(*self.resp_vars("create_promise", "ok"))["conditions"])))
        if retries >= 1:
            tr.append(go("retry_promise", "First promise call failed with an HTTP error", all_of(
                eq("create_promise_failure_count", "1"), eq("create_promise_response.status", "error"))))
        tr.append(go(fail, "Promise failed after the retries", cond_any(
            gt("promise_fail_n", retries), gt("create_promise_failure_count", retries))))
        tr += trans
        if self.has("refused"):
            ev = self.events["refused"]
            tr.append({"target_node": self.latch_gate(self.close_key("refused")), "is_tool_call": True,
                       "description": "Call this when every persuasion attempt in the plan has been made and the customer still refuses. The goodbye is spoken automatically.",
                       "condition": {"operator": "matches_intent", "description": "Customer: " + ev["trigger"]}})
        return {
            "name": "negotiate", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": self.verified_lead() + "{{debt_statement_text}} " + tpl(neg["first_ask"])},
            "on_enter_first_entry_only": True,
            "task_message": self.negotiate_task(),
            "tools": [self.check_tool(), self.tool("create_promise", {
                "product_id": {"default": "{{product_id}}"},
                "amount": {"description": "Promised amount, a number such as 420.00. From the floor up to the full past-due amount.", "default": "{{promise_amount}}"},
                "date": {"description": "Promised payment date, YYYY-MM-DD: the date check_date gave, or the date of the plan's offer.", "default": "{{promise_date}}"},
            }, ["amount", "date"], description="Record the payment promise. CALL it only after the customer said yes to your read-back of product, amount and date.",
                mapping=[{"path": "data.promise_status", "variable": "promise_status"}], pre=self.date_rules() + PROMISE_DATE_PRE,
                post=PROMISE_POST.replace("RETRIES", str(int(retries))))],
            "extraction_schema": [{
                "name": "neg_event", "type": "string", "min_confidence": 0.6,
                "enum_values": structural[:1] + values + structural[1:],
                "description": " ".join(["Judge the customer's LATEST message."] + desc + sdesc +
                                        ["continue: anything else (a date, an amount, a question, a confirmation, an agreement)."]),
            }] + self.seen_vars("negotiate"),
            "max_node_turns": 40, "max_turn_transition": self.latch_gate(self.fallback_close("negotiate", ["refused"])),
            "transitions": tr,
        }

    def date_rules(self):
        """Prelude (dates, CAL, today) plus the spec's date policies, for the hooks that judge a named date (A12)."""
        neg = self.s["negotiation"]
        return (self.prelude() + _const("WW", neg.get("weekday_words") or {}) +
                "NBD = %r\nAFTER = %r\nTOOL = %r\n" % (neg.get("non_business_day", "next_business_day"),
                                                     neg.get("after_deadline", "offer_deadline"), CHECK_TOOL))

    def check_tool(self):
        """check_date (one product): the model passes the customer's words for a day; code returns the verdict."""
        return self.tool(CHECK_TOOL, {
            "said": {"type": "string", "default": "{{cdx_said}}",
                     "description": "The customer's own words for the day, joined across turns, e.g. \"Monday the twenty-sixth\"."},
            "date": {"type": "string", "default": "{{cdx_date}}",
                     "description": "The same day as YYYY-MM-DD if you know it; used only when the words name no day."},
        }, ["said"], path="get_account",
            description=("Check a payment day the customer names. CALL it before you answer any day they name (any form, also in pieces "
                         "or words); say what what_to_do_next says. It records nothing."),
            pre=self.date_rules() + CHECK_PRE, post="TOOL = %r\n" % CHECK_TOOL + CHECK_POST)

    def verified_lead(self):
        """Spoken by the engine as identity passes: the thanks, then negotiation.post_verify_sentence word for word."""
        pvs = self.s["negotiation"].get("post_verify_sentence")
        return "Thank you, you are verified. " + (tpl(pvs) + " " if pvs else "")

    def said_already(self, t):
        """The task's "You have already said" quote includes the post-verification sentence."""
        pvs = self.s["negotiation"].get("post_verify_sentence")
        return t.replace('You have already said: "', 'You have already said: "' + tpl(pvs) + ' ', 1) if pvs else t

    def mentioned(self):
        suffix = (self.s["negotiation"].get("floor_offer") or {}).get("sentence_suffix")
        return MENTIONED % (q(tpl(suffix)) if suffix else "the same offer")

    def negotiate_multi_task(self):
        neg, conf = self.s["negotiation"], self.s["confirmation"]
        nbd = {"next_business_day": "A weekend day or holiday: offer the NEXT business day after it.",
               "previous_business_day": "A weekend day or holiday: offer the business day BEFORE it.",
               "reject": "A weekend day or holiday: say it is not a business day and ask for a business day."}[neg.get("non_business_day", "next_business_day")]
        vague = {"ask_once_then_deadline": 'A vague period ("next week", "end of the month", "when my salary comes"): ask once for the exact day. If they still give no day, propose the product\'s deadline.',
                 "propose_deadline": 'A vague period ("next week", "end of the month", "when my salary comes"): propose the product\'s deadline.',
                 "ask_until_exact": 'A vague period ("next week", "end of the month", "when my salary comes"): ask for the exact day.'}[neg.get("vague_date", "ask_once_then_deadline")]
        fo = neg.get("floor_offer") or {}
        times = fo.get("times", 1)
        if times:
            too_much = ("The customer says the full amount of a product is too much: offer that product's floor %s, in the same sentence as the date of its current attempt"
                        % ("once" if times == 1 else "up to %s times" % NUM.get(times, str(times))))
            too_much += (", and add %s once." % q(tpl(fo["sentence_suffix"]))) if fo.get("sentence_suffix") else "."
        else:
            too_much = "The customer says the full amount is too much: do not offer a lower amount; treat it as a refusal."
        if neg.get("never_ask_how_much"):
            too_much += " Never ask how much they can pay; you propose every amount."
        mp = self.s.get("multi_product") or {}
        order = {"get_account": "in the order of PRODUCTS", "highest_past_due": "in the order of PRODUCTS (highest past-due first)",
                 "most_days_late": "in the order of PRODUCTS (most days late first)"}.get(mp.get("order", "get_account"), "in the order of PRODUCTS")
        notes = [o for o in self.multi.note_outcomes if o != "refused"]
        t = """The customer is verified. You have already said: "{{debt_statement_text}} {{first_ask_text}}"

PRODUCTS (computed, use them exactly; a product_id is for the tool only, never say it)
{{products_text}}
- Today: {{today_text}}. Current time {{now_time}}.
- Weekday words:
{{weekday_text}}
- Calendar:
{{calendar_text}}

HOW TO RUN THE CALL
1. If the customer can pay everything today: call %(tool)s with product_id ALL, outcome agreed, amount full and today's date.
   If not, go through the products one by one, %(order)s. Do not ask about today again. Start each product at attempt 1 of its own persuasion plan; attempts are counted per product. A "no" to the today question uses no attempt: never report it as refused.
2. For the product you are discussing, the customer names a date or an amount (combine parts said across turns, "four hundred... and twenty" = 420): never a refusal.
   - Date: a business day from today up to that product's deadline. %(nbd)s A date after its deadline: say the latest date you can accept for it is its deadline, and offer it.
   - %(vague)s %(day_number)s
   - %(cut_off)s
   - Amount: from the product's floor up to its full amount. %(date_only)s
   - %(too_much)s
3. A flat refusal or a non-answer for the current product (refusing the floor, refusing the deadline) uses one attempt of its plan: call %(tool)s with outcome refused at once, before you speak. Its result gives the next attempt or says to make no more offers for it; make only an offer a result or the plan gives you. Never repeat an offer the customer already refused.
   %(mentioned)s
4. As soon as a product is settled, call %(tool)s:
   - agreed, with the amount (a number, or full / floor) and the date (YYYY-MM-DD). If the customer names one date (and amount) for every product not yet settled, call it once with product_id ALL.
   - refused, after EVERY refusal of an attempt (see 3).%(notes)s
   Its result (what_to_do_next) names the next product. Say a product is noted only after a result that says "Noted" for it; after "NOT accepted", "Not counted yet" or any other result, nothing is noted: do what it says. A result that says a product is still open ("NOT every product is settled", "Still open"): nothing is noted for it and the call is not over; never say noted, recorded or goodbye, continue with that product. Never read promises back or ask for confirmation yourself. Last result: {{mp_msg}}
5. When every product is settled, the single read-back of all promises and the question %(confirm)s are spoken automatically. If the customer then changes something, call %(tool)s again for that product; if they then accept the same promises, call %(tool)s with product_id ALL and outcome review. Nothing is registered before the confirmation; never say anything is recorded or registered.""" % dict(
            tool=multi_mod.SET_TOOL, order=order, nbd=nbd, vague=vague, too_much=too_much, confirm=q(tpl(conf["question"])),
            mentioned=self.mentioned(), day_number=DAY_NUMBER, cut_off=CUT_OFF, date_only=DATE_ONLY,
            notes="".join("\n   - %s, when the customer %s; then continue with the other products." % (o, self.multi.outcome_words(o)[len("the customer "):]) for o in notes))
        for f, d in self.model_extras("create_promise", PROMISE_FIELDS):
            t += "\n   %s with outcome agreed also needs %s: %s" % (multi_mod.SET_TOOL, f, d)
        if conf.get("change_note"):
            t += "\n6. If the customer asks to change or cancel a promise that is already registered, say: %s" % q(tpl(conf["change_note"]))
        t = self.said_already(t)
        other = self.event_guidance("negotiate")
        if self.banned_for("negotiate"):
            other.append("If asked for %s: say you cannot offer it." % join_or(with_article(b) for b in self.banned_for("negotiate")))
        other += self.say_lines("negotiate")
        t += "\n" + bullet_block("OTHER SITUATIONS", other)
        t += "\n" + bullet_block("RULES FROM THE BRIEF", self.extra("negotiate"))
        return t.rstrip()

    def negotiate_multi_node(self):
        banned = self.banned_for("negotiate")
        values, desc, trans = self.event_values("negotiate")
        structural = ["continue", "cannot_pay_or_too_much"] + (["discount_request"] if banned else []) + ["goodbye"]
        sdesc = ["cannot_pay_or_too_much: they say they cannot pay (today or at all) or that an amount is too much, without asking for anything else. A message cut off mid-phrase (\"you tell me what the\", \"Monday the.\") is never cannot_pay_or_too_much: it is continue."]
        if banned:
            sdesc.append("discount_request: they ask for %s." % join_or(with_article(b) for b in banned))
        sdesc.append("goodbye: they only say goodbye or end the conversation (\"thank you, goodbye\"), with no date, amount, refusal or question.")
        tr = [go("readback", "Every product settled: read back the promises", cond_any(eq(multi_mod.PHASE, "readback"))),
              go("wrap_done", "Every product settled, nothing to confirm", cond_any(eq(multi_mod.PHASE, "done")))] + trans + [
              go(multi_mod.END_NODE, "The customer says goodbye", cond_any(eq("neg_event", "goodbye")))]
        return {
            "name": "negotiate", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": self.verified_lead() + "{{debt_statement_text}} {{first_ask_text}}"},
            "on_enter_first_entry_only": True,
            "task_message": self.negotiate_multi_task(),
            "tools": [self.multi.set_tool()],
            "extraction_schema": [{
                "name": "neg_event", "type": "string", "min_confidence": 0.6,
                "enum_values": structural[:1] + values + structural[1:],
                "description": " ".join(["Judge the customer's LATEST message."] + [d for d in desc if d] + sdesc +
                                        ["continue: anything else (a date, an amount, a question, a confirmation, an agreement, "
                                         "a refusal, a claim that a product is paid or not theirs)."]),
            }] + self.seen_vars("negotiate"),
            "max_node_turns": 30, "max_turn_transition": multi_mod.END_NODE,
            "transitions": tr,
        }

    def retry_node(self):
        fail = self.close_key(self.fail_ev) if self.has(self.fail_ev) else "close_account_error"
        ok = self.close_key("promise_recorded") if self.has("promise_recorded") else fail
        rp = self.tool("create_promise", {
            "product_id": {"default": "{{product_id}}"},
            "amount": {"default": "{{create_promise_args.promise_amount}}"},
            "date": {"default": "{{create_promise_args.promise_date}}"},
        }, ["amount", "date"], source="create_promise_args.%s", pre=RETRY_PRE)
        return {
            "name": "retry_promise", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": fail,
            "on_enter": {"type": "tts", "text": "I am sorry, there was a technical problem on our side. Let me try that once more."},
            "disable_inactivity_monitor": True,
            "pre_actions": [rp],
            "response_mapping": [{"path": "data.ok", "variable": "retry_ok"}],
            "transitions": [go(ok, "Retry worked", is_true("retry_ok", *self.resp_vars("create_promise", "ok", "retry_promise"))), always(fail)],
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
        self.setup_nodes()
        n["opening"] = self.opening_node()
        n["third_party"] = self.third_party_node()
        n["identity"] = self.identity_node()
        self.account_nodes()
        if self.multi:
            n["negotiate"] = self.negotiate_multi_node()
            self.build_closes()
            self.multi.build(n)
        else:
            n["negotiate"] = self.negotiate_node()
            n["retry_promise"] = self.retry_node()
            self.build_closes()
        n["goodbye"] = goodbye_node()
        self.apply_latch()
        prune_unreachable(n, "setup")
        test_vars = {"customer_id": "c_test"} if "customer_id" in self.s.get("call_variables", []) else {}
        # call variables beyond the usual ones (e.g. a company name): declared with an empty default, so a call
        # that does not pass one never speaks a raw placeholder
        for name in self.s.get("call_variables", []):
            if name not in STANDARD_VARS and name not in test_vars:
                test_vars[name] = ""
        return {
            "initial_node": "setup",
            "system_prompt": self.system_prompt(),
            "nodes": n,
            "global_nodes": {},
            "tools": [self.close_call_tool()],
            "test_variables": test_vars,
        }


def prune_unreachable(nodes, start):
    """Drop nodes no path from `start` reaches (e.g. a close that several products route through a wrap)."""
    seen, todo = {start}, [start]
    while todo:
        nd = nodes.get(todo.pop()) or {}
        tg = [t.get("target_node") for t in nd.get("transitions") or []]
        tg += [nd.get(k) for k in ("passthrough_default_transition", "max_turn_transition") if nd.get(k)]
        for t in tg:
            if t in nodes and t not in seen:
                seen.add(t)
                todo.append(t)
    for k in [k for k in nodes if k not in seen]:
        del nodes[k]


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
