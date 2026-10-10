"""Deterministic business-calendar helpers.

Embedded as source text into Freya code nodes (sandbox: json, re, datetime only).
Keep imports to `datetime` and never write the two-character brace sequences
that Freya templating would interpret.
"""
import datetime

WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
       'September', 'October', 'November', 'December']


def is_bd(d, cal):
    return d.weekday() in cal["weekdays"] and d.isoformat() not in cal["holidays"]


def next_bd(d, cal):
    d = d + datetime.timedelta(days=1)
    while not is_bd(d, cal):
        d = d + datetime.timedelta(days=1)
    return d


def prev_bd(d, cal):
    d = d - datetime.timedelta(days=1)
    while not is_bd(d, cal):
        d = d - datetime.timedelta(days=1)
    return d


def nth_bd(start, n, cal):
    d = start
    c = 0
    while c < n:
        d = d + datetime.timedelta(days=1)
        if is_bd(d, cal):
            c += 1
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
        cfg = (product_cfg or dict()).get(str(p.get('type') or '')) or dict()
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
