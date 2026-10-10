"""Generate out/brief-a.workflow.json from Brief A. Run: python3 out/gen_brief_a.py"""
import json, pathlib

BANK = "https://freya-bank-mock.freyavoice.workers.dev/v1"
HOLIDAYS = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"]
OUT = pathlib.Path(__file__).resolve().parent / "brief-a.workflow.json"


def api_tool(name, body_props, required, description="", retries=0, mapping=None, pre=None, post=None):
    props = {"customer_id": {"type": "string", "default": "{{customer_id}}"}}
    props.update(body_props)
    t = {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description or name,
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": f"{BANK}/{name}"},
                "method": {"type": "string", "default": "POST"},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": required},
            }},
        },
    }
    if mapping: t["response_mapping"] = mapping
    if pre: t["pre_process_code"] = pre
    if post: t["post_process_code"] = post
    return t


def code_node(name, code, target, extra_transitions=()):
    assert "{{" not in code and "}}" not in code, name
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": list(extra_transitions) + [{"target_node": target, "description": "always", "condition": {"operator": "always"}}],
    }


def cond_any(*conds):
    return {"logic": "or", "conditions": list(conds)}


def eq(var, val):
    return {"variable": var, "operator": "equals", "operand": val}


def is_true(*vars_):
    return cond_any(*[eq(v, s) for v in vars_ for s in ("true", "True")])


def go(target, desc, condition):
    return {"target_node": target, "description": desc, "condition": condition}


# ---------------------------------------------------------------- code snippets
DATE_LIB = r'''
import datetime, json
HOL = %s
WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
def isbd(d):
    return d.weekday() < 5 and d.isoformat() not in HOL
def speak(d):
    return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MON[d.month - 1] + ' ' + str(d.year) + ' (' + d.isoformat() + ')'
def nextbd(d):
    d = d + datetime.timedelta(days=1)
    while not isbd(d):
        d = d + datetime.timedelta(days=1)
    return d
def nth_bd(start, n):
    d = start
    c = 0
    while c < n:
        d = d + datetime.timedelta(days=1)
        if isbd(d):
            c += 1
    return d
def last_bd_of_week(d):
    f = d - datetime.timedelta(days=d.weekday()) + datetime.timedelta(days=4)
    k = 0
    while not isbd(f) and k < 4:
        f = f - datetime.timedelta(days=1)
        k += 1
    return f
now_s = str(vars.get('now') or '')
try:
    nowdt = datetime.datetime.fromisoformat(now_s[:16])
except Exception:
    nowdt = datetime.datetime(2026, 10, 14, 11, 0)
today = nowdt.date()
''' % json.dumps(HOLIDAYS)

SETUP_CODE = DATE_LIB + r'''
try:
    nb = nextbd(today)
    hm = nowdt.strftime('%H:%M')
    vars['today_text'] = speak(today)
    vars['now_time'] = hm
    if nowdt.hour < 19:
        tpart = 'today, ' + speak(today) + ', at a time later than ' + hm + ' and between 10:00 and 19:00'
    else:
        tpart = 'not today (it is already too late today)'
    vars['callback_rules'] = ('A callback may only be booked for ' + tpart + ', or on the next business day, ' + speak(nb) +
        ', between 10:00 and 19:00 (both included). Any other day or time is outside the window. Next business day date: ' + nb.isoformat() + '.')
    vars['setup_state'] = 'ok'
except Exception as e:
    vars['today_text'] = now_s
    vars['callback_rules'] = 'A callback may be booked only for today or the next business day, between 10:00 and 19:00, later than now (' + now_s + ').'
    vars['setup_state'] = 'error'
'''

ACCOUNT_CODE = DATE_LIB + r'''
import re
def money(x):
    return '%.2f' % float(x)
def find_product():
    for k in ('acct_first_json',):
        v = vars.get(k)
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.strip().startswith('{'):
            try:
                return json.loads(v)
            except Exception:
                pass
    for k in ('acct_products', 'load_account_response.data.products', 'get_account_response.data.products'):
        p = vars.get(k)
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = None
        if isinstance(p, list) and p and isinstance(p[0], dict):
            return p[0]
    found = dict()
    for k in list(vars.keys()):
        m = re.search(r'products(?:\.|\[)0\]?\.(\w+)$', str(k))
        if m:
            found[m.group(1)] = vars.get(k)
    if found:
        return found
    return None
try:
    p = find_product()
    if not p or p.get('past_due') in (None, ''):
        raise ValueError('no product')
    ptype = str(p.get('type') or '')
    past_due = float(p.get('past_due'))
    days_late = int(float(p.get('days_late') or 0))
    if ptype == 'credit_card':
        words = 'credit card ending ' + str(p.get('card_last4') or '')
        floor = float(p.get('minimum_payment') or 0)
    elif ptype == 'car_loan':
        words = 'car loan'
        floor = float(p.get('instalment_amount') or 0)
    else:
        words = 'personal loan'
        floor = float(p.get('instalment_amount') or 0)
    if days_late <= 7:
        band, n, attempts = 'early', 12, 1
    else:
        band, n, attempts = 'late', 7, 2
    deadline = nth_bd(today, n)
    lb = last_bd_of_week(today)
    if lb <= today:
        lb = last_bd_of_week(today + datetime.timedelta(days=7))
    if lb > deadline:
        lb = deadline
    allowed = []
    d = today
    while d <= deadline:
        if isbd(d):
            allowed.append(d)
        d = d + datetime.timedelta(days=1)
    cal = []
    d = today
    end = deadline + datetime.timedelta(days=21)
    while d <= end:
        tag = 'business day' if isbd(d) else ('BANK HOLIDAY, not a business day' if d.isoformat() in HOL else 'weekend, not a business day')
        if not isbd(d) and d <= deadline:
            tag += '. If the customer names this day, offer the next business day: ' + speak(nextbd(d))
        if d > deadline:
            tag += ', AFTER THE DEADLINE'
        cal.append('- ' + speak(d) + ': ' + tag + (' (today)' if d == today else ''))
        d = d + datetime.timedelta(days=1)
    mon = today - datetime.timedelta(days=today.weekday())
    wk = []
    for i in range(7):
        t = mon + datetime.timedelta(days=i)
        wk.append('- "this ' + WD[i] + '" = ' + speak(t) + '; "next ' + WD[i] + '" = ' + speak(t + datetime.timedelta(days=7)))
    vars['product_id'] = str(p.get('product_id') or 'p1')
    vars['product_words'] = words
    vars['past_due_text'] = money(past_due)
    vars['past_due_num'] = past_due
    vars['floor_text'] = money(floor)
    vars['floor_num'] = floor
    vars['days_late_text'] = str(days_late)
    vars['band'] = band
    vars['attempts_allowed'] = str(attempts)
    vars['deadline_text'] = speak(deadline)
    vars['attempt1_text'] = speak(lb)
    vars['allowed_dates_text'] = ', '.join(x.isoformat() for x in allowed)
    vars['calendar_text'] = '\n'.join(cal)
    vars['weekday_text'] = '\n'.join(wk)
    if attempts == 2:
        vars['attempt_plan'] = ('Attempt 1: propose the full amount ' + money(past_due) + ' (or the floor ' + money(floor) +
            ' if the customer already said the full amount is too much) on ' + speak(lb) + '.\nAttempt 2: propose the floor amount ' +
            money(floor) + ' on the deadline, ' + speak(deadline) + '.\nThis customer is in the LATE band: two persuasion attempts in total.')
    else:
        vars['attempt_plan'] = ('Attempt 1: propose the full amount ' + money(past_due) + ' (or the floor ' + money(floor) +
            ' if the customer already said the full amount is too much) on ' + speak(lb) + '.\nThis customer is in the EARLY band: only ONE persuasion attempt. There is no attempt 2.')
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
except Exception:
    pass
'''

VERIFY_PRE = r'''
try:
    import re
    b = config.get('body')
    if isinstance(b, dict):
        f = str(b.get('field') or '')
        a = str(b.get('answer') or '').strip()
        if f == 'phone_last4':
            dg = ''.join(ch for ch in a if ch.isdigit())
            if len(dg) >= 4:
                b['answer'] = dg[-4:]
        elif f == 'date_of_birth':
            m = re.match(r'^(\d{1,2})[./ -](\d{1,2})[./ -](\d{4})$', a)
            if m:
                b['answer'] = '%s-%02d-%02d' % (m.group(3), int(m.group(2)), int(m.group(1)))
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
        if str(result.get('status', 'success')) != 'success' or (d.get('ok') is None and d.get('verified') is None):
            d['what_to_do_next'] = 'The security service had a technical problem and nothing was checked. Say so in one short sentence and call verify_identity again with the same answer.'
        elif d.get('all_verified') is True:
            d['what_to_do_next'] = 'Both answers are verified. Say only: Thank you.'
        elif d.get('hint') == 'day_month_swapped':
            d['what_to_do_next'] = 'Day and month were probably swapped. This is NOT a wrong answer. Ask the customer to say the date of birth again with the month as a word, for example 14 March 1988. Then call verify_identity again.'
        elif d.get('ok') is False:
            d['what_to_do_next'] = 'The answer could not be read. This is NOT a wrong answer. Ask the customer to repeat it (a date of birth as day, month as a word, year; or the four digits of the phone number). Then call verify_identity again.'
        elif d.get('verified') is True:
            d['what_to_do_next'] = 'This answer is correct. If it was the date of birth, now ask for the last four digits of the phone number on file. Do not mention any account, debt or payment yet.'
        elif str(left) == '0':
            d['what_to_do_next'] = 'That was the third wrong answer. The check has ended. Do not ask anything else.'
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
            if n < 2:
                d['what_to_do_next'] = 'NOT RECORDED: the service failed. Do NOT say the promise is recorded. Tell the customer in one short sentence that there was a technical problem and you are trying again, and call create_promise again right away with exactly the same amount and date.'
            else:
                d['what_to_do_next'] = 'NOT RECORDED: the service failed again. Do NOT say the promise is recorded. Do not try again.'
except Exception:
    pass
'''

SIMPLE_POST = r'''
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
    if isinstance(b, dict) and b.get('outcome') in ('promise', 'paid'):
        b.pop('reason', None)
except Exception:
    pass
'''

# ---------------------------------------------------------------- closings
CLOSINGS = {
    # node: (outcome, reason, farewell)
    "close_promise": ("promise", "", "Your payment promise is recorded. You will receive a text message confirming this promise. Thank you for your time, {{title}} {{last_name}}. Goodbye."),
    "close_paid": ("paid", "", "Thank you for letting us know. Payments can take one business day to appear, so there is nothing more you need to do today. Thank you for your time, goodbye."),
    "close_refused": ("no_promise", "refused", "I understand. Thank you for your time, {{title}} {{last_name}}. Goodbye."),
    "close_hardship": ("no_promise", "hardship", "I understand. Thank you for your time, and take care. Goodbye."),
    "close_dispute": ("no_promise", "dispute", "Understood, thank you for telling us. Thank you for your time, goodbye."),
    "close_service_error": ("no_promise", "service_error", "I am sorry, because of a technical problem I could not record your promise. Thank you for your time, goodbye."),
    "close_account_error": ("no_promise", "service_error", "I am sorry, because of a technical problem I cannot see the account details right now. Thank you for your time, goodbye."),
    "close_failed": ("not_verified", "failed", "I am sorry, I could not verify your identity, so I cannot continue this call. Please use the Freya Bank mobile app or visit a branch. Thank you, goodbye."),
    "close_declined": ("not_verified", "declined", "I understand. Without the security questions I cannot continue this call. Thank you for your time, goodbye."),
    "close_callback": ("not_reached", "callback", "Thank you, we will call back then. Goodbye."),
    "close_no_callback": ("not_reached", "no_callback", "Thank you for your time. Goodbye."),
    "close_wrong_number": ("not_reached", "wrong_number", "I apologise for the inconvenience. Thank you, goodbye."),
    "close_deceased": ("not_reached", "deceased", "I am sorry for your loss. Thank you, goodbye."),
    "close_unclear": ("not_reached", "unclear", "I could not confirm who I am speaking with, so I will end the call here. Thank you, goodbye."),
    "close_restructuring": ("transferred", "restructuring", "I am transferring you to our restructuring team now. Thank you, goodbye."),
    "close_support": ("transferred", "support", "I am transferring you to our customer support team now. Thank you, goodbye."),
    "close_human": ("transferred", "human", "I am transferring you to a colleague now. Thank you, goodbye."),
}


def close_node(name, outcome, reason, farewell):
    code = "vars['outcome'] = %r\n" % outcome
    if reason:
        code += "vars['reason'] = %r\n" % reason
    # farewell may contain {{title}} placeholders: assemble them in code instead
    parts = farewell.replace("{{title}}", "\x00t").replace("{{last_name}}", "\x00l")
    expr = " + ".join(
        "str(vars.get('title') or '')" if seg == "\x00t" else "str(vars.get('last_name') or '')" if seg == "\x00l" else repr(seg)
        for seg in _split(parts))
    code += "vars['farewell'] = " + expr + "\n"
    return code_node(name, code, "goodbye")


def _split(s):
    out, buf, i = [], "", 0
    while i < len(s):
        if s[i] == "\x00":
            if buf: out.append(buf); buf = ""
            out.append(s[i:i + 2]); i += 2
        else:
            buf += s[i]; i += 1
    if buf: out.append(buf)
    return out


def transfer_node(name, queue, close):
    return {
        "name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": close,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [api_tool("transfer_to_agent", {"queue": {"type": "string", "enum": ["restructuring", "general", "support"], "default": queue}}, ["queue"], retries=1)],
        "transitions": [{"target_node": close, "description": "always", "condition": {"operator": "always"}}],
    }


# ---------------------------------------------------------------- prompts
SYSTEM = """You are the digital assistant of Freya Bank, on an outbound phone call. Speak English. One or two short sentences per turn. Be polite and calm.
Address the customer as {{title}} {{last_name}}.
Hard rules for every step:
- Never invent figures, dates, reasons or terms. If you do not have some information, say you do not have that information.
- Never offer a discount, a waiver, an instalment plan or any term not given in your instructions.
- Never say that something is recorded, booked or done unless the tool result says so.
- If asked whether you are a robot or an AI: say you are Freya Bank's digital assistant, then continue.
- If asked whether this is a scam: say they can verify this call by phoning Freya Bank's customer service number, then continue.
- After every tool result, speak to the customer or call the next tool. Never end a turn with nothing. Call one tool per message.
- When a tool result contains what_to_do_next, follow it.
- Never ask "how much can you pay?"."""

OPENING_TASK = """You opened the call and asked "Am I speaking with {{title}} {{last_name}}?". The identity of the person on the line is not confirmed.
Do NOT say why you are calling. Never say "collections", "debt", "payment", "loan", "card", an amount or the product.
- Only "yes", "that's me", "speaking" or the customer's own name confirm it. "Hello", "go ahead", "who is this" do not.
- If the person asks a question (who is calling, is this a robot, is this a scam), answer it briefly (you are Freya Bank's digital assistant; they can verify by phoning Freya Bank's customer service number), then ask again: "Am I speaking with {{title}} {{last_name}}?"
- If they do not confirm, ask "Am I speaking with {{title}} {{last_name}}?" once more.
- If they ask for a human, ask once whether you can help first.
No tool call is allowed in this step."""

THIRD_PARTY_TASK = """The person on the line is NOT {{title}} {{last_name}}. Do not say why you are calling: no account, debt, payment, product or amount, even if they ask or offer to pay. They may not answer any security question. If they ask what it is about, say you can only discuss it with {{title}} {{last_name}} personally.
Ask when {{title}} {{last_name}} can be reached.
Callback window: {{callback_rules}}
- If they give a day and time inside the window, call schedule_callback with date as YYYY-MM-DD and time as HH:MM (24 hour). "Tomorrow morning around eleven" means 11:00 on the next day.
- If what they offer is outside the window, say you can only call back today or on the next business day between 10:00 and 19:00, and ask once for another time. Do not schedule anything outside the window.
Never call schedule_callback with a time outside the window."""

IDENTITY_TASK = """The person confirmed they are {{title}} {{last_name}}. Now verify their identity with two security questions, one at a time:
1. date of birth, then 2. the last four digits of the phone number on file.
Call verify_identity once for every answer: field date_of_birth with the answer as YYYY-MM-DD, or field phone_last4 with four digits. Convert spoken dates yourself (e.g. "14 March 1988" -> 1988-03-14). A date given only as numbers is day, then month, then year ("02/11/1979" -> 1979-11-02).
Follow the what_to_do_next of each result exactly. A wrong answer may be asked again.
Before both answers are verified: never say why you are calling. Never say "collections", "debt", "payment", the product or an amount. Do not tell the customer how many wrong answers are left.
If the customer does not want to answer, or asks what the call is about instead of answering: explain once that the questions protect their account, and ask again. Do not explain a second time.
If they ask for a human, ask once whether you can help first."""

NEGOTIATE_TASK = """The customer is verified. You have already told them: you are calling from the collections team about a late payment on their {{product_words}}, past due {{past_due_text}}, {{days_late_text}} days late, and asked "Can you make this payment today?".

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
   - Date must be one of the dates you may record. A weekend day or holiday that is not after the deadline: offer the NEXT business day after it (the calendar names it), not the deadline. A date after the deadline: say the latest date you can record is the deadline ({{deadline_text}}) and offer it.
   - A vague period ("next week", "end of the month", "when my salary comes", "the week after next"): ask once for the exact day. If they still give no day, propose the deadline.
   - Amount from the floor up to the full amount: accept it. Below the floor: say it cannot be recorded and offer the floor once.
2. The customer says the full amount is too much: offer the floor {{floor_text}} once, and say that the rest stays overdue. Never ask how much they can pay; you propose every amount.
3. A flat refusal or a non-answer (including "no" to paying today with no date and no amount, refusing the floor, refusing the deadline) uses one persuasion attempt from the persuasion plan. Make the next attempt in the plan. Never repeat an offer the customer already refused. When all attempts in the plan are used and the customer still refuses, call transition_to_close_refused.
4. When you have an amount and a date the customer accepts, read back the product, the amount and the date and ask "Do you confirm?". If they change the date or amount, check it again and read it back again.
5. Only after a clear yes, call create_promise with amount (number) and date (YYYY-MM-DD). Do not say it is recorded yourself; the result decides.

OTHER SITUATIONS
- Illness, a death in the family or job loss: say one short sentence of sympathy, make NO persuasion attempt, and offer to connect them with the customer support team. Make this offer only ONCE in the call; never repeat it. Do not offer discounts or plans; if asked for a discount, say you cannot offer one. If they still name a date or amount themselves, handle it as a promise.
- A request for a new payment plan or lower instalments: do not describe any plan (you will be transferring them).
- A request for a human: ask once whether you can help first.
- "This debt is not mine", or the product was closed / paid off in the past: do not argue, take no promise.
- "I already paid" this amount: say payments can take one business day to appear.
- If the customer says a second time that they do not want to talk, call transition_to_close_refused.
- A question you cannot answer from these facts: say you do not have that information."""


def build():
    nodes = {}
    nodes["setup"] = code_node("setup", SETUP_CODE, "opening")

    nodes["opening"] = {
        "name": "opening", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": "Hello, this is a digital assistant calling from Freya Bank, and this call is recorded. Am I speaking with {{title}} {{last_name}}?"},
        "on_enter_first_entry_only": True,
        "task_message": OPENING_TASK,
        "extraction_schema": [{
            "name": "line_status", "type": "string", "min_confidence": 0.6,
            "enum_values": ["pending", "confirmed", "third_party", "wrong_number", "deceased", "not_confirmed", "declined_twice", "human_insists"],
            "description": ("Who is on the line, judged from the person's LATEST message. "
                            "confirmed: they said yes / that's me / speaking / gave their own name, confirming they are the customer. "
                            "third_party: someone else answered (spouse, relative, colleague) and the customer is a person they know. "
                            "wrong_number: they say the number does not belong to the customer or they do not know the customer. "
                            "deceased: they say the customer has died. "
                            "declined_twice: for the second time they say they do not want to talk. "
                            "human_insists: they asked for a human, the agent already asked whether it can help, and they insist again. "
                            "not_confirmed: anything else that does not confirm (hello, go ahead, who is this, a question, an unclear answer). "
                            "pending: no message yet."),
        }],
        "max_node_turns": 3, "max_turn_transition": "close_unclear",
        "transitions": [
            go("identity", "Confirmed", cond_any(eq("line_status", "confirmed"))),
            go("third_party", "Someone else answered", cond_any(eq("line_status", "third_party"))),
            go("close_wrong_number", "Wrong number", cond_any(eq("line_status", "wrong_number"))),
            go("close_deceased", "Customer died", cond_any(eq("line_status", "deceased"))),
            go("close_declined", "Declined twice", cond_any(eq("line_status", "declined_twice"))),
            go("transfer_human", "Insists on a human", cond_any(eq("line_status", "human_insists"))),
            go("close_unclear", "Second non-confirmation", cond_any({"logic": "and", "conditions": [
                eq("line_status", "not_confirmed"), {"variable": "opening.total_turns", "operator": "greater_than", "operand": 1}]})),
        ],
    }

    nodes["third_party"] = {
        "name": "third_party", "node_type": "conversation",
        "task_message": THIRD_PARTY_TASK,
        "tools": [api_tool("schedule_callback", {
            "date": {"type": "string", "description": "Callback date, YYYY-MM-DD, today or the next business day only.", "default": "{{cb_date}}"},
            "time": {"type": "string", "description": "Callback time HH:MM, 24 hour, between 10:00 and 19:00.", "default": "{{cb_time}}"},
        }, ["date", "time"], "Book a callback. CALL it as soon as the person gives a day and time inside the callback window.",
            mapping=[{"path": "data.cb_status", "variable": "cb_status"}], post=SIMPLE_POST)],
        "extraction_schema": [{
            "name": "tp_status", "type": "string", "min_confidence": 0.6,
            "enum_values": ["continue", "wrong_number", "deceased", "no_valid_time"],
            "description": ("wrong_number: the person says the number does not belong to the customer. deceased: the customer has died. "
                            "no_valid_time: the agent already asked once for another time because the first offer was outside the callback window, and the person again offers a time outside it or will not give one. "
                            "continue: anything else."),
        }],
        "max_node_turns": 12, "max_turn_transition": "close_no_callback",
        "transitions": [
            go("close_callback", "Callback booked", cond_any(eq("cb_status", "booked"), *is_true("schedule_callback_response.data.ok")["conditions"])),
            go("close_deceased", "Deceased", cond_any(eq("tp_status", "deceased"))),
            go("close_wrong_number", "Wrong number", cond_any(eq("tp_status", "wrong_number"))),
            go("close_no_callback", "No valid time", cond_any(eq("tp_status", "no_valid_time"))),
        ],
    }

    nodes["identity"] = {
        "name": "identity", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": "Thank you. For your security I need to ask you two questions. First, what is your date of birth?"},
        "on_enter_first_entry_only": True,
        "task_message": IDENTITY_TASK,
        "tools": [api_tool("verify_identity", {
            "field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "description": "Which question this answer is for: date_of_birth or phone_last4.", "default": "{{field}}"},
            "answer": {"type": "string", "description": "date_of_birth as YYYY-MM-DD; phone_last4 as four digits.", "default": "{{answer}}"},
        }, ["field", "answer"], "Check one security answer. CALL it every time the customer answers a security question.",
            retries=1, mapping=[{"path": "data.all_verified", "variable": "id_all"}, {"path": "data.wrong_answers_left", "variable": "id_left"}],
            pre=VERIFY_PRE, post=VERIFY_POST)],
        "extraction_schema": [{
            "name": "id_event", "type": "string", "min_confidence": 0.6,
            "enum_values": ["continue", "declined_twice", "human_insists"],
            "description": ("declined_twice: the agent ALREADY explained once that the questions protect the account, and the customer again refuses to answer, or again asks what the call is about instead of answering, or says again they do not want to talk. "
                            "human_insists: the customer asked for a human, the agent already asked whether it can help, and they insist. continue: anything else, including a first refusal."),
        }],
        "max_node_turns": 20, "max_turn_transition": "close_declined",
        "transitions": [
            go("load_account", "Both answers verified", is_true("id_all", "verify_identity_response.data.all_verified")),
            go("close_failed", "Three wrong answers", cond_any(eq("id_left", "0"), eq("verify_identity_response.data.wrong_answers_left", "0"))),
            go("close_declined", "Declined", cond_any(eq("id_event", "declined_twice"))),
            go("transfer_human", "Human", cond_any(eq("id_event", "human_insists"))),
        ],
    }

    ga = api_tool("get_account", {}, [], retries=2, post=GET_ACCOUNT_POST)
    nodes["load_account"] = {
        "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "prepare_account",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
        "pre_actions": [{k: v for k, v in ga.items() if k != "response_mapping"}],
        "response_mapping": [{"path": "data.products", "variable": "acct_products"}, {"path": "data.first_json", "variable": "acct_first_json"}],
        "transitions": [{"target_node": "prepare_account", "description": "always", "condition": {"operator": "always"}}],
    }
    nodes["prepare_account"] = code_node("prepare_account", ACCOUNT_CODE, "negotiate",
        [go("close_account_error", "No account data", cond_any(eq("account_state", "error")))])

    nodes["negotiate"] = {
        "name": "negotiate", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": "Thank you, you are verified. I am calling from the Freya Bank collections team about a late payment on your {{product_words}}. The past-due amount is {{past_due_text}}, and it is {{days_late_text}} days late. Can you make this payment today?"},
        "on_enter_first_entry_only": True,
        "task_message": NEGOTIATE_TASK,
        "tools": [api_tool("create_promise", {
            "product_id": {"type": "string", "default": "{{product_id}}"},
            "amount": {"type": "number", "description": "Promised amount, a number such as 420.00. From the floor up to the full past-due amount.", "default": "{{promise_amount}}"},
            "date": {"type": "string", "description": "Promised payment date, YYYY-MM-DD, one of the dates you may record.", "default": "{{promise_date}}"},
        }, ["amount", "date"], "Record the payment promise. CALL it only after the customer said yes to your read-back of product, amount and date.",
            mapping=[{"path": "data.promise_status", "variable": "promise_status"}], post=PROMISE_POST)],
        "extraction_schema": [{
            "name": "neg_event", "type": "string", "min_confidence": 0.6,
            "enum_values": ["continue", "cannot_pay_or_too_much", "hardship_mentioned", "discount_request", "already_paid", "dispute", "plan_request", "support_accepted", "support_declined", "human_insists"],
            "description": ("Judge the customer's LATEST message. "
                            "already_paid: they say they have already paid this past-due amount (e.g. paid it today in the app). "
                            "dispute: they say the debt is not theirs, they never had this product, or the product was closed or paid off in the past. "
                            "cannot_pay_or_too_much: they say they cannot pay (today or at all) or that the amount is too much, without asking for a new plan. "
                            "hardship_mentioned: they mention illness, a death in the family or job loss (and do not answer an offer of the support team). "
                            "discount_request: they ask for a discount or waiver. "
                            "plan_request: they EXPLICITLY ask for a new payment plan, restructuring, or lower / smaller instalments. Saying they cannot pay, that it is too much, or asking for a discount is NOT plan_request. "
                            "support_accepted: in the agent's previous message it offered to connect them with the customer support team and they say yes to that. "
                            "support_declined: the agent offered to connect them with the customer support team and in this message they refuse it (e.g. 'I don't want to be transferred'). This wins over every other value, even if the same message also asks for a discount or says they cannot pay. "
                            "human_insists: they asked for a human, the agent already asked once whether it can help, and they insist again. "
                            "continue: anything else (a date, an amount, a question, a confirmation, an agreement)."),
        }, {
            "name": "hardship_seen", "type": "string", "min_confidence": 0.6, "enum_values": ["yes", "no"],
            "description": "yes if anywhere in the conversation the customer said they are ill, had a death in the family, or lost their job. Otherwise no.",
        }],
        "max_node_turns": 40, "max_turn_transition": "close_refused",
        "transitions": [
            go("close_promise", "Promise recorded", cond_any(eq("promise_status", "recorded"), *is_true("create_promise_response.data.ok")["conditions"])),
            go("retry_promise", "First promise call failed with an HTTP error", cond_any({"logic": "and", "conditions": [
                eq("create_promise_failure_count", "1"), eq("create_promise_response.status", "error")]})),
            go("close_service_error", "Promise failed twice", cond_any(
                {"variable": "promise_fail_n", "operator": "greater_than", "operand": 1},
                {"variable": "create_promise_failure_count", "operator": "greater_than", "operand": 1})),
            go("close_paid", "Already paid", cond_any(eq("neg_event", "already_paid"))),
            go("close_dispute", "Dispute", cond_any(eq("neg_event", "dispute"))),
            go("transfer_restructuring", "Plan request", cond_any(eq("neg_event", "plan_request"))),
            go("transfer_support", "Support accepted", cond_any({"logic": "and", "conditions": [eq("neg_event", "support_accepted"), eq("hardship_seen", "yes")]})),
            go("close_hardship", "Support declined", cond_any({"logic": "and", "conditions": [eq("neg_event", "support_declined"), eq("hardship_seen", "yes")]})),
            go("transfer_human", "Human", cond_any(eq("neg_event", "human_insists"))),
            {"target_node": "close_refused", "is_tool_call": True,
             "description": "Call this when every persuasion attempt in the plan has been made and the customer still refuses, or when the customer says a second time that they do not want to talk. The goodbye is spoken automatically.",
             "condition": {"operator": "matches_intent", "description": "All persuasion attempts are used and the customer still refuses, or the customer said twice they do not want to talk"}},
        ],
    }

    rp = api_tool("create_promise", {
        "product_id": {"type": "string", "default": "{{product_id}}"},
        "amount": {"type": "number", "default": "{{create_promise_args.promise_amount}}"},
        "date": {"type": "string", "default": "{{create_promise_args.promise_date}}"},
    }, ["amount", "date"], pre=RETRY_PRE)
    nodes["retry_promise"] = {
        "name": "retry_promise", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "close_service_error",
        "on_enter": {"type": "tts", "text": "I am sorry, there was a technical problem on our side. Let me try that once more."},
        "disable_inactivity_monitor": True,
        "pre_actions": [rp],
        "response_mapping": [{"path": "data.ok", "variable": "retry_ok"}],
        "transitions": [
            go("close_promise", "Retry worked", is_true("retry_ok", "retry_promise_response.data.ok")),
            {"target_node": "close_service_error", "description": "always", "condition": {"operator": "always"}},
        ],
    }

    nodes["transfer_restructuring"] = transfer_node("transfer_restructuring", "restructuring", "close_restructuring")
    nodes["transfer_support"] = transfer_node("transfer_support", "support", "close_support")
    nodes["transfer_human"] = transfer_node("transfer_human", "general", "close_human")

    for name, (o, r, f) in CLOSINGS.items():
        nodes[name] = close_node(name, o, r, f)

    nodes["goodbye"] = {
        "name": "goodbye", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "transitions": [],
        "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
            "parameters": {"type": "object", "properties": {
                "message_mode": {"type": "string", "default": "custom"},
                "spoken_message": {"type": "string", "default": "{{farewell}}"}}}}},
    }

    close_call = api_tool("close_call", {
        "outcome": {"type": "string", "enum": ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"], "default": "{{outcome}}",
                    "description": "promise: a promise was recorded. paid: customer already paid. transferred: handed to a person. no_promise: verified, no promise. not_verified: identity check did not pass. not_reached: the customer was not on the line."},
        "reason": {"type": "string", "default": "{{reason}}",
                   "description": "Leave out for promise and paid. transferred: restructuring, human, support. no_promise: refused, hardship, dispute, service_error. not_verified: failed, declined. not_reached: callback, no_callback, wrong_number, deceased, unclear."},
    }, ["outcome"], "Record how the call ended.", pre=CLOSE_PRE)
    close_call["config"]["alwaysRunsAt"] = ["end_of_call"]

    return {
        "initial_node": "setup",
        "system_prompt": SYSTEM,
        "nodes": nodes,
        "global_nodes": {},
        "tools": [close_call],
        "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
    }


if __name__ == "__main__":
    wf = build()
    OUT.write_text(json.dumps(wf, indent=1))
    print("wrote", OUT, len(wf["nodes"]), "nodes")
