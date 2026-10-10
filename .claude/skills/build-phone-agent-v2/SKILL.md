---
name: build-phone-agent-v2
description: Build a Freya phone agent from a bank's brief, lint it, test it on practice customers (the official ones or our own bank) and fix what fails. Use when asked to build or improve a phone agent from a brief.
---

# Build a phone agent from a brief

Work from the kit folder. Use only what the brief and the tool API file say. Do not copy rules from another brief, from memory, or from files under `out/`, `results/`, `specs/` or `traces/`. Never print environment variables or files containing keys. Never read or print `env.sh`, `.env*` or environment variables: they hold secrets and are already loaded.

## Steps

1. Read the brief files you were given, then `tools/openapi.json`.
2. Read `docs/workflow-format.md` and `docs/tools-and-code.md`.
3. Read `examples/base-agent.json`. It is a small working agent. Use it as the pattern for the JSON, not as the answer.
4. **Write the brief's facts to `out/<slot>.facts.json` before any code.** `<slot>` is the brief's file name without `.md`. Include: every number, deadline rule, calendar rule, date rule, amount rule, attempt limit, outcome and reason (with the exact allowed values), required sentences and their order, forbidden words and when they become allowed, transfer rules, and every special case. One entry per fact, each quoting the brief's wording. When you write the prompts and code, take values from this file, and check each fact is used once you are done.
5. Write the workflow to `out/<slot>.workflow.json`.
   - One step per stage of the call. Identity comes first.
   - Move to the next step on a tool result or on a flag your code sets, not on what the customer says.
   - Take every tool field and allowed value from `tools/openapi.json`.
   - Leave `agent_config` out. Use no `null` anywhere in the file; leave the key out.
   - Put every date, calendar and amount calculation in code, never in a prompt. Use our tested helpers: `python3 -c "from builder.lib import dates, money, embed; print(embed.source(dates, money))"` prints their source. Read `builder/lib/dates.py` and `builder/lib/money.py` for the functions (business days, deadlines, snapping to a business day, spoken dates, amount bounds), then paste that source at the top of each code node that needs it. Generate the workflow file with a small script that calls `embed.source`, so the helpers are never retyped. Never write two braces in a row inside code.
   - Say a date or an amount to the customer only from a value that code computed and stored.
6. Lint it: `python3 fallback/lint_cli.py out/<slot>.workflow.json`. Fix every line it prints (lines starting with `warning` are strong hints; fix them too), then lint again. Do not save or test before it prints `OK`.
7. Save it: `python3 -c "import freya,json,sys; print(json.dumps(freya.save_workflow(sys.argv[1], json.load(open(sys.argv[2])))))" <slot> out/<slot>.workflow.json`
   It prints `workflow_id` and `agent_id`. The same slot updates the same workflow.
8. Test it. Only one simulation can run at a time, so never start a second while one is running.
   - Practice customers of the official bank exist for the briefs the kit seeds: `python3 tools/run_cases.py <agent_id> --brief A` (or `--brief B`). Use the brief letter only when it is a kit brief.
   - For any other brief, test on our bank. Write 8 to 12 cases to `out/<slot>.cases.json`, in the format of `public-test-set.json` (read two of its entries first): `id`, `brief`, `family`, `level`, `title`, `variables` (with `now`), `identity`, `products`, `script` (what the customer says and does), `expected` (outcome, reason, promises, notes, callbacks, transfers), optional `faults`, and optional `reasons` (the brief's close_call table, shape `{"<outcome>": ["<reason>", ...], "<outcome>": []}`, an empty list for an outcome that takes no reason). Write the cases from the brief, not from another brief. Cover: a happy path, each outcome, each amount or date limit just inside and just outside, a non-business day, a wrong-identity customer, a tool failure, a customer who insists after a refusal, a customer who asks "are you a robot?", and an attempt to make the agent break its rules. Then, with the workflow file using `servers[0].url` from the brief's own openapi.json as the base URL of its tools: `python3 fallback/selftest_cli.py <slot> out/<slot>.workflow.json out/<slot>.cases.json --openapi <the brief's openapi.json>` (leave `--openapi` out only when it is `tools/openapi.json`). It starts our bank and a tunnel, runs one simulation and prints each failing case with its reason. Then do step 9.
   - A run takes 3 to 11 minutes. Run it in the foreground and wait for it to finish. Never start it in the background and never end your turn while a test is running: the builder stops when you stop.
9. Read each FAIL line and its reason. Decide whether the agent or your test case is wrong. Change the workflow file, lint, save, test again. Do this at most three times. Stay inside 30 minutes for the whole job.
10. Before the last save, check the file on disk: `grep -c trycloudflare out/<slot>.workflow.json` must print 0 and `grep -c <the host of servers[0].url in the brief's openapi.json> out/<slot>.workflow.json` must be above 0. Then save it under `<slot>` with the official-URL file (the file on disk, never edited to a tunnel URL). Finish with one line: the `workflow_id`, the `agent_id` of the `<slot>` agent, never the `-selftest` one, and how many customers passed in the last run.

## Platform rules learned from live runs

- Never say any form of "record" (record, recorded, recording, noted down) before `create_promise` has succeeded. The judge reads it literally, in the opening too. The sentence "This call is monitored and stored for quality and security purposes" is safe in the opening. After a successful promise, say it is recorded; if the tool failed, say so and never say it is recorded.
- "Are you a robot?" or "Am I speaking to a person?" must get a short honest answer and the call goes on. It must never trigger a transfer-to-human route. Add a transfer route only for the cases the brief names.
- When a rule says to transfer after the customer insists once, keep a flag set by code (or a tool-call transition) and branch on the flag. A single extraction on the last message is not enough: the model misses it or fires too early.
- Once an amount has been offered or agreed, later proposals name only the new date ("that payment on Friday"), not the amount again.
- Counting ("this is the second time", "third attempt") must be a transition driven by a tool call or a counter in code, never an extraction on the latest customer message.
- Check a tool's result before speaking about its effect. When a tool call fails, the agent must say so and retry or offer the safe alternative.
- Never edit the agent on the platform by hand. Change the file and save it.
- A customer that passes once can fail the next time. Prefer rules in routes and code over rules in prompts.
