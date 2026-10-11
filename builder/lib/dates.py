"""Deterministic business-calendar helpers.

Embedded as source text into Freya code nodes (sandbox: json, re, datetime only).
Keep imports to `datetime` and `re` and never write the two-character brace sequences
that Freya templating would interpret.
"""
import datetime
import re

WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
       'September', 'October', 'November', 'December']


# A business-day search that meets no business day in this many days in a row stops with an error.
MAX_SCAN_DAYS = 800


def check_weekdays(cal):
    """calendar.weekdays must be a non-empty list of whole numbers 0 (Monday) to 6; else ValueError."""
    wd = cal.get("weekdays") if isinstance(cal, dict) else None
    if not isinstance(wd, list) or not wd or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6 for x in wd):
        raise ValueError("calendar.weekdays must be a non-empty list of integers 0-6, got " + str(wd))


def is_bd(d, cal):
    return d.weekday() in cal["weekdays"] and d.isoformat() not in cal["holidays"]


def _no_bd(start, cal):
    raise ValueError("no business day within " + str(MAX_SCAN_DAYS) + " days of " + start.isoformat() +
                     "; check calendar.weekdays and calendar.holidays")


def next_bd(d, cal):
    check_weekdays(cal)
    start = d
    d = d + datetime.timedelta(days=1)
    k = 1
    while not is_bd(d, cal):
        d = d + datetime.timedelta(days=1)
        k += 1
        if k > MAX_SCAN_DAYS:
            _no_bd(start, cal)
    return d


def prev_bd(d, cal):
    check_weekdays(cal)
    start = d
    d = d - datetime.timedelta(days=1)
    k = 1
    while not is_bd(d, cal):
        d = d - datetime.timedelta(days=1)
        k += 1
        if k > MAX_SCAN_DAYS:
            _no_bd(start, cal)
    return d


def nth_bd(start, n, cal):
    check_weekdays(cal)
    d = start
    c = 0
    miss = 0
    while c < n:
        d = d + datetime.timedelta(days=1)
        if is_bd(d, cal):
            c += 1
            miss = 0
        else:
            miss += 1
            if miss > MAX_SCAN_DAYS:
                _no_bd(start, cal)
    return d


def last_bd_of_week(d, cal):
    """Last business day of the Monday-based week containing d."""
    monday = d - datetime.timedelta(days=d.weekday())
    f = monday + datetime.timedelta(days=6)
    k = 0
    while not is_bd(f, cal) and k < 6:
        f = f - datetime.timedelta(days=1)
        k += 1
    return f


def snap_forward(d, cal):
    if is_bd(d, cal):
        return d
    return next_bd(d, cal)


def snap_back(d, cal):
    if is_bd(d, cal):
        return d
    return prev_bd(d, cal)


def weekday_ref(today, weekday, which):
    """The given weekday (0=Monday) in this week, or in next week."""
    monday = today - datetime.timedelta(days=today.weekday())
    d = monday + datetime.timedelta(days=weekday)
    if which == "next":
        d = d + datetime.timedelta(days=7)
    return d


def deadline(today, rule, cal):
    kind = rule["rule"]
    if kind == "nth_business_day":
        d = nth_bd(today, rule["n"], cal)
    elif kind == "calendar_days":
        d = today + datetime.timedelta(days=rule["n"])
    elif kind == "end_of_month":
        first_next = datetime.date(today.year + today.month // 12, today.month % 12 + 1, 1)
        d = first_next - datetime.timedelta(days=1)
    elif kind == "end_of_next_month":
        m = today.month % 12 + 1
        y = today.year + today.month // 12
        first_next = datetime.date(y + m // 12, m % 12 + 1, 1)
        d = first_next - datetime.timedelta(days=1)
    elif kind == "fixed_weekday_after_n_weeks":
        d = weekday_ref(today, rule["weekday"], "this") + datetime.timedelta(days=7 * rule["weeks"])
    else:
        raise ValueError("unknown deadline rule: " + str(kind))
    snap = rule.get("snap")
    if snap == "back":
        d = snap_back(d, cal)
    elif snap == "forward":
        d = snap_forward(d, cal)
    return d


def attempt_date(today, kind, deadline_d, cal):
    if kind == "today":
        d = today
    elif kind == "last_business_day_of_week":
        d = last_bd_of_week(today, cal)
        if d <= today:
            d = last_bd_of_week(today + datetime.timedelta(days=7), cal)
    elif kind == "deadline":
        d = deadline_d
    elif kind == "next_business_day":
        d = next_bd(today, cal)
    else:
        raise ValueError("unknown attempt kind: " + str(kind))
    if d > deadline_d:
        d = deadline_d
    return d


def say_date(d):
    return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MON[d.month - 1] + ' ' + str(d.year)


def speak(d):
    return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MON[d.month - 1] + ' ' + str(d.year) + ' (' + d.isoformat() + ')'


def pfield(p, cfg, key, default):
    """A product's value for a mapped field (products.<type>.amount_field / days_late_field), else the default name."""
    name = (cfg or dict()).get(key) or default
    v = p.get(name)
    if v in (None, '') and name != default:
        v = p.get(default)
    return v


def product_cfg_of(p, product_cfg):
    """A product's products.<type> config: by its `type` field; else by any of its string values that is a config key;
    else the first config whose amount_field holds a value in the product; else an empty dict."""
    pc = product_cfg or dict()
    cfg = pc.get(str(p.get('type') or ''))
    if cfg:
        return cfg
    for v in p.values():
        if isinstance(v, str) and isinstance(pc.get(v), dict):
            return pc[v]
    for c in pc.values():
        f = (c or dict()).get('amount_field') if isinstance(c, dict) else None
        if f and p.get(f) not in (None, ''):
            return c
    return dict()


def amount_of(p, cfg):
    return float(pfield(p, cfg, 'amount_field', 'past_due'))


def days_of(p, cfg):
    return int(float(pfield(p, cfg, 'days_late_field', 'days_late') or 0))


def band_for(days_late, bands):
    for b in bands:
        if b['days_late'][0] <= days_late <= b['days_late'][1]:
            return b
    if days_late < min(b['days_late'][0] for b in bands):
        return min(bands, key=lambda b: b['days_late'][0])
    return max(bands, key=lambda b: b['days_late'][1])


def plan_products(products, today, bands, cal, product_cfg=None):
    """Per-product facts: product_id, band, deadline, floor, full, attempts [(amount_kind, date)]."""
    out = []
    for i, p in enumerate(products):
        cfg = product_cfg_of(p, product_cfg)
        full = amount_of(p, cfg)
        ff = cfg.get('floor_field') or 'floor'
        floor = float(p.get(ff)) if p.get(ff) not in (None, '') else full
        if floor > full:
            floor = full
        band = band_for(days_of(p, cfg), bands)
        dl = deadline(today, band['deadline'], cal)
        attempts = [(a['amount'], attempt_date(today, a['date'], dl, cal)) for a in band['attempts']]
        out.append(dict(product_id=str(p.get('product_id') or ('p' + str(i + 1))), band=band['name'],
                        deadline=dl, floor=floor, full=full, attempts=attempts))
    return out


def apply_shared_date(plans, d):
    """One date named for every product: a product whose deadline is earlier keeps its own deadline."""
    out = []
    for p in plans:
        if d > p['deadline']:
            out.append((p['product_id'], p['deadline'], True))
        else:
            out.append((p['product_id'], d, False))
    return out


def close_reason(notes, promises, transferred, rule, all_paid='paid', fallback=None, reason_map=None, paid='paid'):
    """(outcome, reason) of a several-product call.

    notes: [(product_id, reason)] in product order; promises: number recorded; transferred: the
    transfer's reason or None. A transfer wins, then any recorded promise, then the notes.
    reason_map: note reason -> close reason (multi_product.notes.close_reason_map); paid: the note
    reason that means "already paid" (multi_product.notes.paid_note_reason)."""
    m = reason_map or dict()
    paid = paid or 'paid'
    if transferred:
        return ('transferred', transferred)
    if promises and promises > 0:
        return ('promise', None)
    reasons = [r for _, r in notes]
    if reasons and all(r == paid for r in reasons) and all_paid:
        return (all_paid, None)
    if rule == 'last_note' and reasons:
        return ('no_promise', m.get(reasons[-1], reasons[-1]))
    for r in reasons:
        if r != paid:
            return ('no_promise', m.get(r, r))
    if fallback:
        return (fallback[0], fallback[1])
    return ('no_promise', m.get(reasons[0], reasons[0]) if reasons else None)



# ---------------------------------------------------------------- a date the customer names (A12)
# The customer's words for a day are resolved and judged here, in code: the agent only speaks the verdict.

_UNIT_W = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
           'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
_ORD_W = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh',
          'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth']
_TENS_W = dict(twenty=20, thirty=30)
_TENS_O = dict(twentieth=20, thirtieth=30)
_MON3 = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
def _mon_of(w):
    w = w.rstrip('.')
    for i, m in enumerate(MON):
        if w == m.lower():
            return i + 1
    if w == 'sept':
        return 9
    if len(w) == 3 and w in _MON3 and w != 'may':
        return _MON3.index(w) + 1
    return 0


def _wd_of(w):
    for i, n in enumerate(WD):
        if w in (n.lower(), n.lower() + 's'):
            return i
    return -1


def _tokens(text):
    """[(kind, value)]: kind ord (a day ordinal), card (a cardinal number), mon, wd, iso, w (any other word)."""
    t = str(text or '').lower().replace('-', ' ').replace(',', ' ')
    raw = re.findall(r'\d{4} \d{2} \d{2}|\d+(?:st|nd|rd|th)?|[a-z]+\.?', t)
    out = []
    i = 0
    while i < len(raw):
        w = raw[i].rstrip('.')
        nx = raw[i + 1].rstrip('.') if i + 1 < len(raw) else ''
        m = re.match(r'^(\d{4}) (\d{2}) (\d{2})$', w)
        if m:
            out.append(('iso', (int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        elif re.match(r'^\d+(st|nd|rd|th)$', w):
            out.append(('ord', int(re.match(r'^\d+', w).group(0))))
        elif w.isdigit():
            out.append(('card', int(w)))
        elif w in _TENS_W and nx in _ORD_W[1:10]:
            out.append(('ord', _TENS_W[w] + _ORD_W.index(nx)))
            i += 1
        elif w in _TENS_W and nx in _UNIT_W[1:10]:
            out.append(('card', _TENS_W[w] + _UNIT_W.index(nx)))
            i += 1
        elif w in _TENS_O:
            out.append(('ord', _TENS_O[w]))
        elif w in _TENS_W:
            out.append(('card', _TENS_W[w]))
        elif w in _ORD_W[1:]:
            out.append(('ord', _ORD_W.index(w)))
        elif w in _UNIT_W:
            out.append(('card', _UNIT_W.index(w)))
        elif _mon_of(w):
            out.append(('mon', _mon_of(w)))
        elif _wd_of(w) >= 0:
            out.append(('wd', _wd_of(w)))
        else:
            out.append(('w', w))
        i += 1
    # "may" and "march" are months only next to a day number ("May 26th", "the 2nd of March")
    for i, (k, v) in enumerate(out):
        if k == 'mon' and v in (3, 5):
            near = [out[j][0] for j in (i - 1, i + 1) if 0 <= j < len(out)]
            if i >= 2 and out[i - 1] == ('w', 'of'):
                near.append(out[i - 2][0])
            if not ('ord' in near or 'card' in near):
                out[i] = ('w', MON[v - 1].lower())
    return out


def _first_with_day(today, n):
    """The first date from today on whose day of the month is n (the first calendar row with that number)."""
    y, m = today.year, today.month
    for _ in range(14):
        try:
            d = datetime.date(y, m, n)
        except ValueError:
            d = None
        if d is not None and d >= today:
            return d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def _weekday_date(today, wd, mod, ww):
    """A weekday name with its modifier (next / next_week / this / after_next / '') under negotiation.weekday_words."""
    ww = ww or dict()
    if mod == 'next':
        if ww.get('next') == 'next_occurrence':
            return today + datetime.timedelta(days=(wd - today.weekday() - 1) % 7 + 1)
        return weekday_ref(today, wd, 'next')
    if mod == 'next_week':
        return weekday_ref(today, wd, 'next')
    if mod == 'this_week':
        return weekday_ref(today, wd, 'this')
    if mod == 'after_next':
        return weekday_ref(today, wd, 'next') + datetime.timedelta(days=7)
    if mod == 'this' and ww.get('this') != 'next_occurrence':
        return weekday_ref(today, wd, 'this')
    # "this" as next occurrence, or a bare weekday: its next occurrence from today on
    return today + datetime.timedelta(days=(wd - today.weekday()) % 7)


# a word after an ordinal that makes it a period, not a day ("the second week", "the first payment")
_PERIODISH = ('week', 'weeks', 'weekend', 'month', 'months', 'half', 'quarter', 'time', 'payment', 'instalment',
              'installment', 'salary', 'paycheck', 'one')
# words that name a period, not a day
_VAGUE_W = ('week', 'weeks', 'weekend', 'month', 'months', 'salary', 'payday', 'paycheck', 'paid', 'soon', 'later',
            'sometime', 'end', 'beginning', 'early', 'mid', 'couple', 'few')
# period words that do not override a day said before them ("the 23rd or later")
_WEAK = ('later', 'soon', 'early')
# words skipped when looking back from a mention for "not"
_NEG_SKIP = ('on', 'the', 'this', 'next', 'a', 'that', 'for', 'by')


def _mentions(tk):
    """Every day or period the words mention, in order: [(position, kind, value)] with kind iso | day (value
    (number, month or 0, adjacent weekday or -1)) | wd (value (weekday, modifier)) | rel (days from today) | vague |
    weak (a period word that a day said before it overrides).
    A mention right after "not" is dropped ("not Friday, Thursday")."""
    n = len(tk)
    used = set()
    out = []

    def W(j):
        return tk[j][1] if 0 <= j < n and tk[j][0] == 'w' else None

    def K(j):
        return tk[j][0] if 0 <= j < n else None

    for i, (k, v) in enumerate(tk):
        if i in used:
            continue
        if k == 'iso':
            out.append((i, 'iso', v))
        elif k in ('ord', 'card') and 1 <= v <= 31:
            if W(i + 1) in _PERIODISH:
                continue
            mon, mi = 0, None
            if K(i - 1) == 'mon':
                mon, mi = tk[i - 1][1], i - 1
            elif W(i - 1) == 'the' and K(i - 2) == 'mon':
                mon, mi = tk[i - 2][1], i - 2
            elif K(i + 1) == 'mon':
                mon, mi = tk[i + 1][1], i + 1
            elif W(i + 1) == 'of' and K(i + 2) == 'mon':
                mon, mi = tk[i + 2][1], i + 2
            wd, wi = -1, None
            if K(i - 1) == 'wd':
                wd, wi = tk[i - 1][1], i - 1
            elif W(i - 1) == 'the' and K(i - 2) == 'wd':
                wd, wi = tk[i - 2][1], i - 2
            if k == 'ord':
                if v in (1, 2) and not (mon or wd >= 0 or W(i - 1) in ('the', 'on') or W(i + 1) == 'of'):
                    continue
            elif not (mon or wd >= 0 or W(i - 1) in ('the', 'on')):
                continue
            for j in (mi, wi):
                if j is not None:
                    used.add(j)
            out.append((i, 'day', (v, mon, wd)))
        elif k == 'wd':
            mod = ''
            if W(i + 1) == 'next' and W(i + 2) == 'week':
                mod = 'next_week'
                used.update((i + 1, i + 2))
            elif W(i + 1) == 'this' and W(i + 2) == 'week':
                mod = 'this_week'
                used.update((i + 1, i + 2))
            elif W(i + 1) == 'after' and W(i + 2) == 'next':
                mod = 'after_next'
                used.update((i + 1, i + 2))
            elif W(i + 1) == 'the' and W(i + 2) == 'week' and W(i + 3) == 'after' and W(i + 4) == 'next':
                mod = 'after_next'
                used.update((i + 1, i + 2, i + 3, i + 4))
            elif W(i + 1) in ('week', 'fortnight') or (W(i - 1) in ('on', 'from') and W(i - 2) in ('week', 'fortnight') and
                                                        W(i - 3) not in ('next', 'this')):
                # "Friday week", "a week on Friday": counted from this Friday or from next Friday depending on the
                # speaker; no reliable day, so it is a period (the agent asks for the exact day)
                for j in (i - 3, i - 2, i - 1, i + 1):
                    used.add(j)
                out.append((i, 'vague', None))
                continue
            elif W(i - 1) == 'week' and W(i - 2) in ('next', 'this'):
                mod = 'next_week' if W(i - 2) == 'next' else 'this_week'
                used.update((i - 1, i - 2))
            elif W(i - 1) == 'on' and W(i - 2) == 'week' and W(i - 3) in ('next', 'this'):
                mod = 'next_week' if W(i - 3) == 'next' else 'this_week'
                used.update((i - 1, i - 2, i - 3))
            elif W(i - 1) == 'next':
                mod = 'next'
                used.add(i - 1)
            elif W(i - 1) in ('this', 'coming'):
                mod = 'this'
                used.add(i - 1)
            out.append((i, 'wd', (v, mod)))
        elif k == 'mon':
            out.append((i, 'vague', None))
        elif k == 'w':
            if v == 'today':
                out.append((i, 'rel', 0))
            elif v == 'tomorrow':
                if W(i - 1) == 'after' and W(i - 2) == 'day':
                    out.append((i, 'rel', 2))
                else:
                    out.append((i, 'rel', 1))
            elif v in _VAGUE_W:
                out.append((i, 'weak' if v in _WEAK else 'vague', None))
    keep = []
    for m in out:
        j = m[0] - 1
        while j >= 0 and W(j) in _NEG_SKIP:
            j -= 1
        if W(j) == 'not' or m[0] in used:
            continue
        keep.append(m)
    return keep


def resolve_said(text, today, ww=None, prefer=None):
    """The exact day the customer's words name: (date, '') or (None, why) with why 'vague' (a period, no day),
    'invalid' (no such date) or 'none' (no day in the words). The LAST day or period mentioned wins (a correction:
    "the 20th, no, the 23rd"); one right after "not" never counts. A day number wins over a weekday next to it that
    does not fit, except when that weekday fits the latest past date with that number (then that past date).
    prefer: dates the agent proposed; a bare weekday ("Tuesday is fine") that one of them falls on means that date."""
    tk = _tokens(text)
    ms = _mentions(tk)
    if not ms:
        return None, 'none'
    years = [v for k, v in tk if k == 'card' and 2000 <= v <= 2100]
    strong = [m for m in ms if m[1] != 'weak']
    pos, kind, val = strong[-1] if strong else (ms[-1][0], 'vague', None)
    if kind == 'iso':
        try:
            return datetime.date(val[0], val[1], val[2]), ''
        except ValueError:
            return None, 'invalid'
    if kind == 'vague':
        return None, 'vague'
    if kind == 'rel':
        return today + datetime.timedelta(days=val), ''
    if kind == 'wd':
        if not val[1]:
            fit = sorted(p for p in (prefer or []) if p >= today and p.weekday() == val[0])
            if fit:
                return fit[0], ''
        return _weekday_date(today, val[0], val[1], ww), ''
    n, mon, wd = val
    if mon:
        y = years[-1] if years else today.year
        if not years and mon < today.month:
            y += 1
        try:
            return datetime.date(y, mon, n), ''
        except ValueError:
            return None, 'invalid'
    d = _first_with_day(today, n)
    if d is None:
        return None, 'invalid'
    if wd >= 0 and wd != d.weekday() and n < today.day:
        # "Monday the 12th" said on the 14th: the latest date with that number, if it is that weekday (then past)
        try:
            c = datetime.date(today.year, today.month, n)
        except ValueError:
            c = None
        if c is not None and c.weekday() == wd:
            return c, ''
    return d, ''


def date_verdict(d, today, dl, cal, nbd='next_business_day', after='offer_deadline', today_open=True):
    """(kind, date to offer or None) for a date the customer named, by the spec's rules.
    kind: ok (accept d) | past | after_deadline | not_business_day | today_closed (today, after the spec's same-day
    cut-off: today_open False; the next business day is offered). The offered date is d itself for ok; the deadline
    after the deadline (after=offer_deadline); the next / previous business day (or the deadline when the next one is
    past it, the last business day on or before the deadline) for a weekend day or holiday; None when the rule is to reject and ask again."""
    if d < today:
        return 'past', None
    if d > dl:
        return 'after_deadline', (dl if after != 'reject' else None)
    if d == today and not today_open:
        # after the spec's same-day cut-off: today is no longer possible; the next business day (or ask again)
        n = next_bd(d, cal)
        return 'today_closed', (n if n <= dl else None)
    if not is_bd(d, cal):
        if nbd == 'reject':
            return 'not_business_day', None
        if nbd == 'previous_business_day':
            p = prev_bd(d, cal)
            if p >= today:
                return 'not_business_day', p
        n = next_bd(d, cal)
        if n <= dl:
            return 'not_business_day', n
        last = snap_back(dl, cal)
        return 'not_business_day', (last if last >= today else None)
    return 'ok', d
