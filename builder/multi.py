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
Verified 11 Oct (chat-API probe, call 26dc5ce1): SET_POST's rewrite of result['data'] is the tool result the model
reads (it spoke a token that existed only in what_to_do_next). A code_exec tool returns data null instead, so the
model never sees a rejection: api is the default. A non-2xx response is a function_call_error and skips the post-hook.
Body leaves need a {{sp_<field>}} default to be model-filled: without one only customer_id was sent (regate-a9-B).
Verified 11 Oct (chat-API probe, call b17bfc2f, seeded and verified PUB-13 customer): the model's product_id, outcome,
amount and date reach config['body']; the pre-hook rejected a date after the deadline, then stored the product as agreed
on its deadline in mp_state; the model spoke both answers (the deadline, then the next product's attempt 1).
"""
from builder.lib import dates, embed, money
from builder.nodes import all_of, always, assert_no_braces, close_node, code_node, cond_any, eq, go, py_json

SLOTS = 3          # products per call the engine can record / note (the brief allows up to three)
SET_TOOL = "set_product"
SET_MODE = "api"   # "api" (get_account route + pre/post hooks; the model reads what_to_do_next) or "code" (code_exec, data null)
PHASE = "mp_phase"
ARG_VAR = "sp_"    # set_product body field f defaults to {{sp_f}}; no sp_* variable is ever set, so the model fills it
PROMISE_FIELDS = ("product_id", "amount", "date")


def _const(name, value):
    return "%s = %s\n" % (name, py_json(value))


# ---------------------------------------------------------------- prepare_account addition

MP_ACCOUNT_CODE = r'''
try:
    if vars.get('account_state') != 'facts':
        raise ValueError(str(vars.get('account_error') or 'no account facts'))
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
                     NUMS.get(str(n), str(n)).capitalize() + (' persuasion attempt' if n == 1 else ' persuasion attempts') + ' for this product.' +
                     (' After one refusal make no other offer for it (no lower amount, no other date).' if n == 1 else '') +
                     (' Make no offer for it.' if n == 0 else ''))
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
    vars['mp_msg'] = 'none yet'
    vars['mp_readback'] = ''
    vars['rb_answer'] = 'pending'
    vars['mp_ended'] = 'no'
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


# ---------------------------------------------------------------- per-product state
# mp_state holds what prepare_account computed (order, amounts, plans, calendar) and is never rewritten by set_product.
# What a set_product call changes for a product lives in that product's own variable mp_p<num> (and the "offer made"
# flag a result sets in mp_o<num>). Verified 11 Oct (chat-API probe f58d6ed0, three parallel calls): parallel calls'
# pre-hooks all read the same snapshot of vars; their writes merge per variable, so a shared JSON rewritten by each
# call keeps only the last writer's change (PUB-12 call 922f2517 lost an agreed product that way), while one variable
# per product keeps every change. The post-hooks ran after the batch's pre-hooks and saw the merged variables.
# A14: a refusal counts only for the product the code most recently told the model to offer, and only when the customer
# has spoken since that instruction. "told" is the clock (the customer's turn number in negotiate, NODE.total_turns,
# readable in hooks: chat-API probe 4c48412b) at which the product's current attempt was named; "no" remembers a
# customer's no given before any offer for the product, so its first offer is made with an acknowledgement.
DYN = ("status", "amount", "date", "capped", "extra", "asked", "told", "no", "tried", "ack", "seq", "own")
NODE = "negotiate"   # the node whose turn counter is the clock (the multi negotiate node's name)

LOAD = r'''
def _load(vars):
    """mp_state with each product's own variable mp_p<num> laid over it."""
    st = json.loads(str(vars.get('mp_state') or '{}'))
    for q in st.get('order') or []:
        P = st['p'][q]
        k = str(P.get('num'))
        try:
            d = json.loads(str(vars.get('mp_p' + k) or '{}'))
        except Exception:
            d = dict()
        if isinstance(d, dict):
            P.update(d)
        try:
            o = int(str(vars.get('mp_o' + k) or '').strip())
        except Exception:
            o = None
        if o is not None and (P.get('told') is None or o > int(P['told'])):
            P['told'] = o
    return st
'''


def load_state(v):
    """The merged product state of a variable table (tests, offline tools): the same code the hooks run."""
    import json
    g = {"json": json}
    exec(LOAD, g)
    return g["_load"](v)


# ---------------------------------------------------------------- set_product hooks

SET_LIB = r'''
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


def _xval(es, v):
    """An extra create_promise field: the enum value the words name, else the spec default."""
    if isinstance(v, str):
        v = v.strip()
    ok = es.get('enum') or []
    if ok and v not in (None, '') and v not in ok:
        v = _xmatch(ok, v)
    if v in (None, ''):
        v = es.get('default')
    return v


def _readback(st):
    parts = []
    for q in st['order']:
        P = st['p'][q]
        if P['status'] != 'agreed':
            continue
        s = fmt(P['amount']) + ' for your ' + P['words'] + ' on ' + _say(P['date'])
        for xf, xv in sorted((P.get('extra') or dict()).items()):
            if xv not in (None, ''):
                s += ', ' + xf.replace('_', ' ') + ' ' + str(xv).replace('_', ' ')
        if P.get('capped'):
            s += ', because that is the latest date I can accept for it'
        parts.append(s)
    if len(parts) > 1:
        body = '; '.join(parts[:-1]) + '; and ' + parts[-1]
    else:
        body = parts[0]
    return 'To confirm, you will pay ' + body + '. ' + CFG['confirm']


def _clock(vars):
    """The customer's turn number in negotiate; without it (offline, code mode) the set_product batch counter."""
    try:
        t = int(str(vars.get(TURNS)).strip())
        if t >= 1:
            return t
    except Exception:
        pass
    try:
        return int(str(vars.get('mp_seq') or '0').strip() or 0)
    except Exception:
        return 0


def _told(st, q):
    """When the current attempt of q was named to the model. Product 1's attempt 1 is named by the prompt for the
    customer's first answer (the today question), so a refusal of it counts from the next turn on."""
    t = st['p'][q].get('told')
    if t is None and st['order'] and q == st['order'][0]:
        return 1
    return None if t is None else int(t)


def _next(st, cur=None):
    pend = [q for q in st['order'] if st['p'][q]['status'] == 'pending']
    if cur is not None and cur in pend:
        return 'open', ''
    if pend:
        P = st['p'][pend[0]]
        if not P['att']:
            return 'open', ('Next is the ' + P['words'] + ' (product_id ' + pend[0] + '). Its plan has no offers: make no offer for it and call ' +
                            TOOL + ' with outcome refused for it now.')
        st['_offer'] = pend[0]
        k = min(int(P.get('tried') or 0), len(P['att']) - 1)
        head = 'Next is the ' + P['words'] + ' (product_id ' + pend[0] + '). '
        if P.get('no') and not P.get('tried'):
            return 'open', (head + 'The customer already said no to it before any offer; that used no attempt. Acknowledge it once, in the same '
                            'sentence as the offer ("I understand you said no to the ' + P['words'] + '; ..."), and make attempt 1 of its plan now: ' +
                            P['att'][0] + ' Call ' + TOOL + ' refused for it only after the customer answers this offer. Do not ask about today again.')
        return 'open', (head + 'If the customer already agreed to an amount and a date for it' +
                        (' or settled it (' + ', '.join(CFG['notes']) + ')' if CFG['notes'] else '') + ', call ' + TOOL + ' for it now. '
                        'Otherwise make attempt ' + str(k + 1) + ' of its plan: ' + P['att'][k] + ' A no said before this offer uses no attempt: call ' +
                        TOOL + ' refused for it only after the customer answers this offer. Do not ask about today again.')
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
    # one date for several products, also when they are reported one at a time: another product already agreed on it
    shared = (every and len(targets) > 1) or any(
        st['p'][o]['status'] == 'agreed' and st['p'][o].get('asked') == d for o in st['order'] if o not in targets)
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
    extra = dict()
    said = []
    for xf, es in (CFG.get('extra') or dict()).items():
        extra[xf] = _xval(es, b.get(xf))
        if extra[xf] is not None and extra[xf] != b.get(xf):
            said.append(xf.replace('_', ' ') + ' noted as ' + str(extra[xf]))
    for q, v in new.items():
        st['p'][q].update(status='agreed', amount=v[0], date=v[1], capped=v[2], extra=extra, asked=d)
    note = (' Note: ' + '; '.join(said) + '. Tell the customer this value.') if said else ''
    return st, 'Noted for now (nothing is registered before the confirmation).' + note


def _refuse(st, b, clock):
    """A refusal counts (uses an attempt) only for the product the code named last (the first open one, A14) and only
    when the customer spoke after that instruction; an agreed product withdrawn counts as before. Any other refusal is
    not counted: a no before any offer is remembered (P['no']) so the product's attempt 1 is made with an acknowledgement.
    Returns (st, ack) or, for ALL, (st, {product: ack})."""
    targets, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
    if every:
        targets = [q for q in targets if st['p'][q]['status'] == 'pending']
    if not targets:
        if every:
            return None, 'No product is open: nothing to refuse.'
        return None, 'Unknown product_id. Use one of ' + ', '.join(st['order']) + '.'
    pend = [q for q in st['order'] if st['p'][q]['status'] == 'pending']
    cur = pend[0] if pend else None
    acks = dict()
    for q in targets:
        P = st['p'][q]
        P['counted'] = False
        n = len(P['att'])
        if n == 0:
            P.update(status='refused', amount=None, date=None, capped=False)
            acks[q] = 'Noted: the plan of the ' + P['words'] + ' has no offers. Make no offer for it.'
            continue
        told = _told(st, q)
        if P['status'] != 'agreed' and not (q == cur and told is not None and clock > told):
            if q == cur:
                why = 'no offer for it was made before this answer; a no before an offer uses no attempt.'
            else:
                why = 'its offer is not made yet (it comes later in the order); this no uses no attempt.'
            if not P.get('tried') and not every:
                P['no'] = True
            acks[q] = 'Not counted: ' + why
            continue
        P['counted'] = True
        k = int(P.get('tried') or 0) + 1
        P['tried'] = k
        if k < n:
            P.update(status='pending', amount=None, date=None, capped=False, told=clock)
            acks[q] = ('Refusal noted (attempt ' + str(k) + ' of ' + str(n) + ' for the ' + P['words'] + '). Make attempt ' + str(k + 1) +
                       ' now: ' + P['att'][k] + ' Never repeat an offer the customer already refused.')
            continue
        P.update(status='refused', amount=None, date=None, capped=False)
        acks[q] = ('Noted: that was the last attempt for the ' + P['words'] + '. Make no more offers for it: no other amount, no other date.')
    return st, (acks if every else acks[targets[0]])


def _note(st, b, out):
    targets, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
    if not targets:
        return None, 'Unknown product_id. Use one of ' + ', '.join(st['order']) + ', or ALL.'
    for q in targets:
        st['p'][q].update(status=out, amount=None, date=None, capped=False)
    return st, 'Noted. ' + CFG['say'].get(out, '')
'''

SET_PRE = r'''
_seq = 0
try:
    import json, re
    _seq = int(str(vars.get('mp_seq') or '0').strip() or 0) + 1
    # parallel set_product calls each read the same snapshot of vars and their writes merge per variable (A11 probe):
    # a call writes only its own products' mp_p<num> and the shared counter, never a shared JSON
    vars['mp_seq'] = str(_seq)
    b = ARGS()
    if not isinstance(b, dict):
        b = dict()
    st = _load(vars)
    today = st['today']
    DAYS = st.get('days') or dict()
    out = str(b.get('outcome') or '').strip().lower()
    touched, every = _targets(st, b.get('product_id'), ('pending', 'agreed', 'refused') + tuple(CFG['notes']))
    if every:
        touched, every = _targets(st, b.get('product_id'), ('pending', 'agreed'))
        if out == 'refused':
            touched = [q for q in touched if st['p'][q]['status'] == 'pending']
    if out == 'review':
        new, ack, touched = None, '', []
    elif out == 'agreed':
        new, ack = _agree(st, b, today)
    elif out == 'refused':
        new, ack = _refuse(st, b, _clock(vars))
    elif out in CFG['notes']:
        new, ack = _note(st, b, out)
    else:
        new, ack, touched = None, 'Unknown outcome. Use one of: agreed, refused, ' + ', '.join(CFG['notes']) + ', review.', []
    if new is not None:
        st = new
    if new is None and len(touched) != 1:
        touched = []
    if touched:
        ws = [st['p'][q]['words'] for q in touched]
        label = (', the '.join(ws[:-1]) + ' and the ' + ws[-1]) if len(ws) > 1 else ws[0]
        for q in touched:
            P = st['p'][q]
            # the ack itself says what to do next for a product that is still open (refusal counted, NOT accepted);
            # a refusal that was not counted leaves the instruction to the code's next step (the current product)
            own = new is None or (out == 'refused' and P['status'] == 'pending' and bool(P.get('counted')))
            if isinstance(ack, dict):
                P.update(ack='The ' + P['words'] + ': ' + ack.get(q, ''), seq=_seq, own=own)
            else:
                P.update(ack='The ' + label + ': ' + ack, seq=_seq, own=own)
            vars['mp_p' + str(P['num'])] = json.dumps(dict((k, P.get(k)) for k in DYN))
    else:
        vars['mp_ax'] = json.dumps(dict(seq=_seq, msg=ack, review=out == 'review'))
except Exception as e:
    vars['mp_ax'] = json.dumps(dict(seq=_seq, msg='There was a technical problem with that step (' + str(e)[:60] + '). Call ' + TOOL +
                                    ' again with the same values.', review=False))
'''

# the answer, from the merged state: in api mode this runs in the post-hook, which reads vars after the parallel calls'
# pre-hook writes have merged (A11 probe f58d6ed0); every result of a batch carries every ack of that batch
SET_FIN = r'''
try:
    import json
    st = _load(vars)
    DAYS = st.get('days') or dict()
    seq = int(str(vars.get('mp_seq') or '0').strip() or 0)
    acks = []
    cur = None
    own = False
    for q in st['order']:
        P = st['p'][q]
        if P.get('seq') == seq and P.get('ack'):
            if P['ack'] not in acks:
                acks.append(P['ack'])
            own = own or bool(P.get('own'))
            if cur is None and P.get('own') and P['status'] == 'pending':
                cur = q
    review = False
    try:
        ax = json.loads(str(vars.get('mp_ax') or '{}'))
    except Exception:
        ax = dict()
    if isinstance(ax, dict) and ax.get('seq') == seq:
        review = bool(ax.get('review'))
        if ax.get('msg'):
            acks.append(str(ax['msg']))
    phase, nxt = _next(st, cur)
    if own and not review:
        # a result of this batch already says what to do (attempt k+1, not counted yet, NOT accepted)
        nxt = ''
        st.pop('_offer', None)
    if st.get('_offer'):
        # the clock at which the code named this product's offer: a refusal of it counts from the next turn on (A14)
        vars['mp_o' + str(st['p'][st['_offer']]['num'])] = str(_clock(vars))
    open_ = [st['p'][q]['words'] for q in st['order'] if st['p'][q]['status'] == 'pending']
    if phase == 'open':
        names = (', the '.join(open_[:-1]) + ' and the ' + open_[-1]) if len(open_) > 1 else open_[0]
        head = ('NOT every product is settled: the ' + names + (' is' if len(open_) == 1 else ' are') + ' still open. Nothing is noted '
                'or recorded for ' + ('it' if len(open_) == 1 else 'them') + ' and the call is not over: never say noted, recorded or goodbye now.')
        if review:
            msg = ' '.join([head] + acks + [nxt])
        elif len(acks) > 1:
            # several answers in one turn (parallel calls): every result of the batch lists every answer and what is open
            msg = ' '.join(acks + [nxt, 'Still open: the ' + names + '.'])
        else:
            msg = ' '.join(acks + [nxt])
    else:
        msg = ' '.join(acks + [nxt])
    vars['mp_readback'] = _readback(st) if [q for q in st['order'] if st['p'][q]['status'] == 'agreed'] else ''
    if phase == 'readback':
        vars['rb_answer'] = 'pending'
    vars['mp_phase'] = phase
    vars['mp_msg'] = re.sub(r'\s+', ' ', msg).strip()
except Exception as e:
    vars['mp_msg'] = 'There was a technical problem with that step (' + str(e)[:60] + '). Call ' + TOOL + ' again with the same values.'
'''

API_ARGS = r'''
def ARGS():
    b = config.get('body') if isinstance(config, dict) else None
    if isinstance(b, dict):
        return dict((k, v) for k, v in b.items() if not (isinstance(v, str) and v.strip()[:2] == chr(123) * 2))
    return b
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
try:
    st = _load(vars)
    ags = [q for q in st['order'] if st['p'][q]['status'] == 'agreed']
except Exception:
    st, ags = dict(order=[], p=dict()), []
# the customer confirmed these at the read-back: no wrap ever notes them as refused (A11)
vars['mp_confirmed'] = json.dumps(ags)
for i in range(1, SLOTS + 1):
    if i <= len(ags):
        P = st['p'][ags[i - 1]]
        vars['rec' + str(i) + '_pid'] = ags[i - 1]
        vars['rec' + str(i) + '_amount'] = P['amount']
        vars['rec' + str(i) + '_date'] = P['date']
        for xf in EXTRA_F:
            xv = (P.get('extra') or dict()).get(xf)
            vars['rec' + str(i) + '_' + xf] = '' if xv is None else xv
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

END_NODE = "negotiate_end"
NEG_END_CODE = r'''
try:
    st = _load(vars)
    DAYS = st.get('days') or dict()
    ags = [q for q in st.get('order', []) if st['p'][q]['status'] == 'agreed']
    if ags:
        # from the merged state: a result spoken while parallel calls were still landing may have built an older read-back
        vars['mp_readback'] = _readback(st)
except Exception:
    ags = []
if ags and str(vars.get('mp_readback') or '') and str(vars.get('mp_ended') or '') != 'yes':
    vars['mp_phase'] = 'readback'
    vars['rb_answer'] = 'pending'
    vars['mp_end'] = 'readback'
else:
    vars['mp_end'] = 'wrap'
vars['mp_ended'] = 'yes'
'''

WRAP_CODE = r'''
def _true(k):
    return str(vars.get(k)).lower() == 'true'
try:
    st = _load(vars)
    order = st.get('order') or []
except Exception:
    st, order = dict(p=dict()), []
try:
    confirmed = json.loads(str(vars.get('mp_confirmed') or '[]'))
except Exception:
    confirmed = []
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
        # every product without a promise gets a note; negotiate_end reads agreed products back before any wrap.
        # Confirmed at the read-back but not recorded: the service-failure note, never the refused note (A11)
        r = FAIL_NOTE if (RECORDING or q in confirmed) else END_NOTE
        if RECORDING or q in confirmed:
            failed.append(P['words'])
    elif stt == 'pending':
        r = END_NOTE
    else:
        r = stt
    if NOTES_ON and r in REASONS:
        notes.append((q, r))
outcome, reason = close_reason(notes, len(recorded), None, RULE, ALL_PAID, FALLBACK, REASON_MAP, PAID_NOTE)
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
        self.reason_map = dict(notes.get("close_reason_map") or {})
        self.paid_note = notes.get("paid_note_reason") or "paid"
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
        keys += ["no_promise|" + self.reason_map.get(r, r) for r in self.reasons]
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
               "notes": [o for o in outcomes if o not in ("agreed", "refused", "review")], "say": say, "extra": {}}
        xprops = {}
        for f, p, g in self.c.extra_fields("create_promise", PROMISE_FIELDS):
            if g.get("value") is not None:
                continue
            cfg["extra"][f] = {"enum": p.get("enum") or [], "default": g.get("default")}
            xprops[f] = {"type": "string", "description": "For agreed: " + self.c.extra_desc(f, p, g)}
            if p.get("enum"):
                xprops[f]["enum"] = list(p["enum"])
        head = "import json, re\n" + embed.source(money) + "\nDAYS = dict()\n" + _const("CFG", cfg) + "TOOL = %r\nTURNS = %r\n" % (SET_TOOL, NODE + ".total_turns") + _const("DYN", list(DYN))
        props = {
            "product_id": {"type": "string", "description": "The product_id from PRODUCTS (for example p1), or ALL when the customer gives one answer for every product not yet settled."},
            "outcome": {"type": "string", "enum": outcomes, "description": "agreed: the customer accepted an amount and a date for it. refused: the customer refused the current persuasion attempt for it (call it after EVERY refusal; the result says whether another attempt is allowed). "
                        + " ".join("%s: %s." % (o, self.outcome_words(o)) for o in cfg["notes"])
                        + " review: every product is settled and the promises must be read back again (with product_id ALL)."},
            "amount": {"type": "string", "description": "For agreed: the amount as a number such as 220.00, or full, or floor."},
            "date": {"type": "string", "description": "For agreed: the payment date, YYYY-MM-DD."},
        }
        props.update(xprops)
        desc = ("Report a product's answer. CALL it after EVERY refusal of an offer (refused, before you speak), and when a product "
                "is agreed or settled (agreed, " + ", ".join(cfg["notes"]) + "). Then follow what_to_do_next.")
        if mode == "api":
            # api_call body leaves are model-filled only through a {{var}} default naming a variable that is never set
            # (docs/tools-and-code.md "One key decides"); a leaf without default never reached the request (regate-a9-B)
            body = {k: dict(v, default="{{%s%s}}" % (ARG_VAR, k)) for k, v in props.items()}
            return self.c.tool(SET_TOOL, body, ["product_id", "outcome"], path="get_account", description=desc,
                               mapping=[{"path": "data.phase", "variable": PHASE}], pre=head + API_ARGS + LOAD + SET_LIB + SET_PRE,
                               post=head + LOAD + SET_LIB + SET_FIN + SET_POST)
        code = head + CODE_ARGS + LOAD + SET_LIB + SET_PRE + SET_FIN + CODE_RESULT
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
        fb = fallback or ("no_promise", self.reason_map.get(self.refused_note, self.refused_note))
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
        code = ("import json\n" + LOAD + embed.source(dates, money) + "\n" + "SLOTS = %d\n" % SLOTS +
                "RECORDING = %s\n" % bool(recording) + "END_NOTE = %r\nFAIL_NOTE = %r\n" % (end_note, self.fail_note) +
                "NOTES_ON = %s\n" % self.notes_on + _const("REASONS", self.reasons) +
                "RULE = %r\nALL_PAID = %r\n" % (self.mp.get("no_promise_reason", "first_non_paid_note"), self.mp.get("all_paid_outcome") or "") +
                _const("REASON_MAP", self.reason_map) + "PAID_NOTE = %r\n" % self.paid_note +
                _const("FALLBACK", list(fb)) + _const("CLOSE_BY", by_used) + _const("LEAD", lead) +
                "SUCCESS = %r\nFAIL_SAY = %r\n" % (self.s["confirmation"].get("success_sentence") or "", fe.get("say") or "") + WRAP_CODE)
        first = "note_1" if self.notes_on else "finish"
        tr = [go(first, "Notes to write", cond_any(eq("note_has_1", "yes")))] if self.notes_on else []
        return code_node(name, code, "finish", tr)

    def readback_events(self):
        """L3: transfer and support events of negotiate are also heard at the read-back and routed like in negotiate."""
        c = self.c
        values, desc, trans = [], [], []
        for ev in c.stage_events("negotiate"):
            eid, act, trig = ev["id"], ev["action"], ev["trigger"]
            if act == "transfer":
                values.append(eid)
                desc.append("%s: the customer %s." % (eid, trig))
                t = c.transfer_to(ev["queue"], c.close_key(eid), eid)
                trans.append(go(t, "Transfer: " + eid, cond_any(eq("rb_answer", eid))))
            elif act == "insist_once_then_transfer":
                values += [eid + "_first_request", eid + "_insists"]
                desc.append("%s_first_request: the customer %s (the first time)." % (eid, trig))
                desc.append("%s_insists: the agent already asked whether it can help first and the customer %s again." % (eid, trig))
                t = c.transfer_to(ev["queue"], c.close_key(eid), eid)
                trans.append(go(t, "Insists: " + eid, all_of(eq("rb_answer", eid + "_insists"), eq(eid + "_asked", "yes"))))
                trans.append(go(c.ask_once_nodes("readback", ev, t), "First request: " + eid,
                                cond_any(eq("rb_answer", eid + "_first_request"), eq("rb_answer", eid + "_insists"))))
            elif act == "offer_transfer_else_close":
                values.append(eid)
                desc.append("%s: the customer %s." % (eid, trig))
                # A10: the support offer step (spoken once, reply routed in code), as from negotiate
                t = c.transfer_to(ev["queue"], c.close_key(eid) + "_transfer", eid)
                trans.append(go(c.support_nodes(ev, t), "Support offer: " + eid, cond_any(eq("rb_answer", eid))))
        return values, desc, trans

    def build(self, nodes):
        c = self.c
        # records
        extra_f = [f for f, _ in c.model_extras("create_promise", PROMISE_FIELDS)]
        nodes["record_plan"] = code_node("record_plan", "import json\n" + LOAD + _const("EXTRA_F", extra_f) + "SLOTS = %d\n" % SLOTS + RECORD_PLAN,
                                         "wrap_recorded",
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
                }, ["product_id", "amount", "date"], source="rec" + s + "_%s", pre=pre, post=post)
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
        ev_values, ev_desc, ev_trans = self.readback_events()
        nodes["readback"] = {
            "name": "readback", "node_type": "conversation",
            "on_enter": {"type": "tts", "text": "{{mp_readback}}"},
            "task_message": ("You just read back every promise and asked %s. If the customer asks a question, answer it briefly "
                             "from the read-back and ask %s again. Nothing is registered yet: never say anything is recorded or registered. "
                             "No tool call is allowed in this step." % (q, q)),
            "extraction_schema": [{
                "name": "rb_answer", "type": "string", "min_confidence": 0.6,
                "enum_values": ["pending", "yes", "change", "other"] + ev_values,
                "description": ("The customer's LATEST message, an answer to the read-back of the promises. yes: a clear yes, they confirm. "
                                + " ".join(ev_desc) +
                                " change: no, or they want a different amount or date, or refuse, or raise anything else to settle "
                                "(a paid or disputed product). other: only a question about the read-back. "
                                "pending: no answer yet.")}],
            "max_node_turns": 4, "max_turn_transition": "readback_reset",
            "transitions": [go("record_plan", "Confirmed", cond_any(eq("rb_answer", "yes")))] + ev_trans +
                           [go("readback_reset", "Not confirmed", cond_any(eq("rb_answer", "change")))]}
        nodes["readback_reset"] = code_node(
            "readback_reset", "vars['mp_phase'] = 'open'\nvars['rb_answer'] = 'pending'\n"
            "vars['mp_msg'] = 'The customer did not confirm the read-back. Handle what they said; call %s again for any product that changes, "
            "or with product_id ALL and outcome review if the same promises stand.'\n" % SET_TOOL, "negotiate",
            [go("wrap_done", "The call is already ending: no second negotiation", cond_any(eq("mp_ended", "yes")))])
        # negotiate's turn cap and the customer's goodbye: agreed products are read back once (and recorded on a yes),
        # never noted as refused; nothing agreed -> wrap_done
        rb_head = ("import json, re\n" + embed.source(money) + "\nDAYS = dict()\n" + _const("CFG", {"confirm": self.s["confirmation"]["question"]}) +
                   LOAD + SET_LIB)
        nodes[END_NODE] = code_node(END_NODE, rb_head + NEG_END_CODE, "wrap_done",
                                    [go("readback", "Agreed products: read them back", cond_any(eq("mp_end", "readback")))])
        # wraps; a turn cap, goodbye or "nothing to confirm" after a support offer ends with that event's wrap (A10)
        c.latch_gate("wrap_done")
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
        # no close chosen (a wrap that did not run): the service-failure close when the wraps can reach it
        fail = c.close_key(self.fail_ev)
        default = fail if fail in targets else (targets[-1] if targets else "close_account_error")
        nodes["finish"] = code_node("finish", "vars['mp_finished'] = 'yes'\n", default,
                                    [go(t, "Close: " + t, cond_any(eq("mp_close", t))) for t in targets])
        # close nodes reached from a wrap speak the computed summary
        for t in targets:
            eid = t[len("close_"):]
            ev = c.events.get(eid) or {}
            outcome, reason = ev.get("outcome"), ev.get("reason")
            if ev.get("action") == "offer_transfer_else_close":
                outcome, reason = ev.get("else_outcome") or "no_promise", ev.get("else_reason")
            nodes[t] = close_node(t, outcome, reason or "", "{mp_said} " + c.thanks(named=True))
