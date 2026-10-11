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
_VAGUE = ('week', 'month', 'salary', 'payday', 'paid', 'soon', 'later', 'weekend', 'end of', 'beginning', 'early', 'mid',
          'sometime', 'some time', 'couple', 'few')


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


def _day_number(tk):
    """The day number the words name, or None. An ordinal counts ("the 26th", "twenty-sixth"; "first"/"second" only next
    to the, of, a month or a weekday); a cardinal 1-31 only next to a month or after "the"/"on"."""
    for i, (k, v) in enumerate(tk):
        if k != 'ord' or not 1 <= v <= 31:
            continue
        if v in (1, 2):
            near = [tk[j] for j in (i - 1, i + 1) if 0 <= j < len(tk)]
            if not any(x[0] in ('mon', 'wd') or x == ('w', 'the') or x == ('w', 'of') for x in near):
                continue
        return v
    for i, (k, v) in enumerate(tk):
        if k != 'card' or not 1 <= v <= 31:
            continue
        prev = tk[i - 1] if i > 0 else ('', '')
        nxt = tk[i + 1] if i + 1 < len(tk) else ('', '')
        nx2 = tk[i + 2] if i + 2 < len(tk) else ('', '')
        if prev[0] == 'mon' or nxt[0] == 'mon' or (nxt == ('w', 'of') and nx2[0] == 'mon') or prev in (('w', 'the'), ('w', 'on')):
            return v
    return None


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
    """A weekday name with its modifier (next / this / after_next / '' ) under negotiation.weekday_words."""
    ww = ww or dict()
    if mod == 'next':
        if ww.get('next') == 'next_occurrence':
            return today + datetime.timedelta(days=(wd - today.weekday() - 1) % 7 + 1)
        return weekday_ref(today, wd, 'next')
    if mod == 'next_week':
        return weekday_ref(today, wd, 'next')
    if mod == 'after_next':
        return weekday_ref(today, wd, 'next') + datetime.timedelta(days=7)
    if mod == 'this' and ww.get('this') != 'next_occurrence':
        return weekday_ref(today, wd, 'this')
    # "this" as next occurrence, or a bare weekday: its next occurrence from today on
    return today + datetime.timedelta(days=(wd - today.weekday()) % 7)


def resolve_said(text, today, ww=None):
    """The exact day the customer's words name: (date, '') or (None, why) with why 'vague' (a period, no day),
    'invalid' (no such date) or 'none' (no day in the words). A day number wins over a weekday that does not fit it."""
    t = str(text or '').lower()
    tk = _tokens(t)
    for k, v in tk:
        if k == 'iso':
            try:
                return datetime.date(v[0], v[1], v[2]), ''
            except ValueError:
                return None, 'invalid'
    words = [v for k, v in tk if k == 'w']
    n = _day_number(tk)
    mons = [v for k, v in tk if k == 'mon']
    years = [v for k, v in tk if k == 'card' and 2000 <= v <= 2100]
    wds = [(i, v) for i, (k, v) in enumerate(tk) if k == 'wd']
    if n is not None:
        if mons:
            y = years[0] if years else today.year
            if not years and mons[0] < today.month:
                y += 1
            try:
                d = datetime.date(y, mons[0], n)
            except ValueError:
                return None, 'invalid'
        else:
            d = _first_with_day(today, n)
            if d is None:
                return None, 'invalid'
            if wds and wds[0][1] != d.weekday():
                # "Monday the 12th" said on the 14th: the latest date with that number, if it is that weekday (the
                # verdict then calls it past); otherwise the day number decides
                c = datetime.date(today.year, today.month, n) if n < today.day else None
                if c is not None and c.weekday() == wds[0][1]:
                    return c, ''
        return d, ''
    if re.search(r'\bday after tomorrow\b', t):
        return today + datetime.timedelta(days=2), ''
    if re.search(r'\btomorrow\b', t):
        return today + datetime.timedelta(days=1), ''
    if wds:
        i, wd = wds[0]
        prev = tk[i - 1][1] if i > 0 and tk[i - 1][0] == 'w' else ''
        rest = ' '.join(str(v) for _, v in tk[i + 1:i + 3])
        if rest.startswith('after next') or (prev == 'next' and i > 1 and tk[i - 2] == ('w', 'the') and 'after' in words):
            mod = 'after_next'
        elif rest.startswith('next week') or re.search(r'\bnext week\b', t):
            mod = 'next_week'
        elif prev == 'next':
            mod = 'next'
        elif prev in ('this', 'coming'):
            mod = 'this'
        else:
            mod = ''
        return _weekday_date(today, wd, mod, ww), ''
    if re.search(r'\btoday\b', t):
        return today, ''
    if any(re.search(r'\b' + x, t) for x in _VAGUE):
        return None, 'vague'
    return None, 'none'


def date_verdict(d, today, dl, cal, nbd='next_business_day', after='offer_deadline'):
    """(kind, date to offer or None) for a date the customer named, by the spec's rules.
    kind: ok (accept d) | past | after_deadline | not_business_day. The offered date is d itself for ok; the deadline
    after the deadline (after=offer_deadline); the next / previous business day (or the deadline when the next one is
    past it) for a weekend day or holiday; None when the rule is to reject and ask again."""
    if d < today:
        return 'past', None
    if d > dl:
        return 'after_deadline', (dl if after != 'reject' else None)
    if not is_bd(d, cal):
        if nbd == 'reject':
            return 'not_business_day', None
        if nbd == 'previous_business_day':
            p = prev_bd(d, cal)
            if p >= today:
                return 'not_business_day', p
        n = next_bd(d, cal)
        return 'not_business_day', (n if n <= dl else dl)
    return 'ok', d
