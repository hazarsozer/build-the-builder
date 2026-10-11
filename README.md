# Our builder

This repo holds two builders. Both read an organisation's documents and create a phone agent on Freya.

| | `builder/` (main, semi-deterministic) | `fallback/` (agentic) |
|---|---|---|
| How | LLM fills a validated policy spec; plain code compiles the workflow | a Claude agent reads the documents and writes the workflow itself, with our lint, helpers and test tools |
| Built for | the kit's final as announced: "the same kind of call… the seven tool names stay the same" | a backup in case the main builder fell short on the final |
| Run | `python3 builder/build.py <case_folder> <mock_url>` | `uv run python fallback/agent_builder.py <case_folder> <mock_url>` |

## The final (2026-10-11): what was submitted

The final case was a different call type from the practice (a Turkish inbound statement-debt line, seven new services,
a prose service contract, no OpenAPI file). The main builder could not express it and built nothing; its run is kept
unedited in `traces/final-main-builder.{log,jsonl,md}`. The submitted agent was made by the **fallback builder**:

- Command: `uv run python fallback/agent_builder.py final/FreyaBank-Ekstre-Borc/ https://freya-bank-mock.pages-placeholder.workers.dev`
  (needs `set -a; source env.sh; set +a` first; the case folder itself is not in the repo).
- Output: `submission/final-freyabank-ekstre-borc.workflow.json` (exported with `freya.export_workflow`, no manual
  edits). Trace: `traces/final.jsonl` (the full agent session log) and `traces/final.log` (everything it printed).
- The only builder change after the final opened is commit `4c769a6`: the fallback accepts `<case_folder> <mock_url>`,
  converts every document with `builder/doctext.py`, takes the tools from the case's own service contract at the mock
  URL, tests on the case's own test customers (`fallback/case_sim_cli.py`) and exports. Nothing from the case is
  written into the builder.
- Our mock of the bank's services is `final-mock/` (Cloudflare Worker, table-driven from the case's test customers;
  `final-mock/parity.py` replays every answer). Deploy: `cd final-mock && pnpm dlx --allow-build=workerd
  --allow-build=esbuild wrangler deploy`.

## The main builder

The main builder turns the brief into a
validated policy spec with LLM sessions, then compiles the spec into a workflow with plain code. It tests the
agent against a mock bank we run ourselves, and exports the workflow.

## What it does

1. **Extract.** Two independent LLM extractions write a policy spec (JSON, checked against `builder/spec_schema.json`
   and the bank's OpenAPI file). A third session, the oracle, writes test scenarios from the brief without seeing
   any spec. Code diffs the two specs; a settle session resolves the remaining disagreements and must quote the
   brief for each change.
2. **Compile.** `builder/compile.py` turns the spec and the OpenAPI file into a workflow. No LLM is involved.
3. **Lint.** `builder/lint.py` checks the workflow against the spec.
4. **Self-test.** Our copy of the mock bank (`bank/`) is exposed through a Cloudflare quick tunnel. Code writes
   test customers from the spec, and one Freya simulation runs them against the agent.
5. **Fix round (optional).** If cases fail and time is left, one short LLM session proposes spec changes, each with a
   brief quote. Code validates them, the workflow is recompiled and tested again, and the spec with more passes is
   kept.
6. **Export.** The final workflow is compiled with the OpenAPI file's own server URL (never the tunnel), saved to
   Freya and written to `submission/<slot>.workflow.json` with `<slot>.spec.json` next to it.

Once a spec exists, a crash in any later step still ends in a saved and exported agent. A hard time limit
(`--budget-min`, default 27) stops whatever is running and exports the best spec so far.

## Why this design

Rules that must always hold live in code, not in prompts. The LLM only reads the brief and fills in a spec. Dates,
amounts, which tool to call, what the records must contain and which sentences are fixed are decided by the
compiler and by routes in the workflow graph. A prompt is advice the agent may not follow; a route in the graph is not. Every
LLM change to the spec has to carry a quote from the brief, and the validator and lint reject specs and workflows
that break the schema or the platform's rules.

## Module map

`builder/`

| File | Role |
|---|---|
| `main.py` | The unattended run: extract, compile, self-test, fix round, export, trace. |
| `extract.py` | Dual extraction, diff, settle session and validation of the merged spec. |
| `oracle.py` | Scenarios written from the brief alone, and the check of a spec against them. |
| `specdiff.py` | Field-level diff and merge of two specs. |
| `sdk.py` | Locked-down Agent SDK sessions shared by extract, oracle, settle and fix. |
| `validate.py` | Spec validation: JSON Schema, the OpenAPI file, and consistency inside the spec. |
| `spec_schema.json`, `spec_guide.py` | The spec schema, and the generator of `docs/spec-guide.md` from it. |
| `compile.py` | Spec plus OpenAPI to workflow. |
| `nodes.py` | Workflow building blocks: tools, code nodes, conditions, transfer and close nodes. |
| `multi.py` | The parts of the graph that differ when a call covers several products. |
| `triggers.py` | Normalises event triggers into clauses. |
| `labels.py`, `layout.py` | Dashboard presentation only (readable labels, node positions, sample call variables). |
| `lint.py` | Lints a compiled workflow against its spec. |
| `cases.py` | Writes self-test cases from a spec. |
| `selftest.py` | Runs cases on Freya against our bank and scores them from our ledger and the judge verdict. |
| `fix.py` | The quote-backed fix round. |
| `trace.py`, `redact.py` | The run trace, and scrubbing of secrets from everything logged or written. |
| `lib/` | Date, money and embedding helpers whose source is embedded into workflow code nodes. |
| `prompts/` | Prompts for the LLM sessions. |

Other directories: `bank/` (our mock bank and the tunnel helper), `fallback/`, `bench/`, `tools/` (kit scripts plus
`conformance.py` and `selftest_live.py`), `tests/`, `docs/`.

## Running it

Needs Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), `cloudflared` at `/usr/bin/cloudflared` (for the
self-test tunnel), a Freya API key, and a Claude login or `ANTHROPIC_API_KEY` for the Agent SDK.

```bash
cp env.example env.sh        # fill in your own values; env.sh is gitignored
set -a; source env.sh; set +a
uv run python -m builder.main <brief files or dirs> --slot NAME --openapi PATH
```

- `--openapi` is the bank's OpenAPI file. If omitted, the builder uses `openapi.json` next to the briefs, or the
  single OpenAPI `.json` among them. It never falls back to the kit's own file; with none found it exits.
- `--slot` names the Freya workflow and agent. Pass it explicitly.
- When one brief builds on another, pass both.
- Other options: `--no-selftest`, `--budget-min`, `--now`, `--out-dir`.
- Exit code: 0 exported, 1 a spec existed but the final step failed, 2 no spec or no OpenAPI file.

No secrets are in the repository. Keys come from the environment (`env.example` lists the names and holds only
placeholders), and `builder/redact.py` scrubs the values of `FREYA_*` and `*_KEY`, `*_TOKEN`, `*_SECRET`,
`*_PASSWORD` variables from every trace and console line.

## Trace

Each run writes `traces/<UTC time>-<slot>.jsonl` (one JSON event per line, flushed as written, so a crash leaves a
usable file) and a readable `traces/<UTC time>-<slot>.md` from the same events. `traces/` is gitignored. The summary
records:

- start and end time, duration, result, briefs, slot, git commit, model and OpenAPI file;
- every model call, with its purpose;
- the workflows and agents created on Freya, and which bank each was tested against;
- spec validation, conflicts the extractor recorded, and lint results;
- each self-test round: passed over total, simulation id and failing cases;
- each fix round: the changed spec paths with their brief quotes, before and after;
- skipped steps, budget stops and simulations left running.

## Tests

```bash
uv run pytest -q
```

Tests marked `live` call real services and run only on request (for example `RUN_LIVE=1`; see
`tests/test_extract_live.py`).

## Fallback builder

`fallback/agent_builder.py` is the kit's example agentic builder, hardened: no MCP servers, reads of `env.sh` and
`.env*` denied, and output scrubbed. It uses the skill `.claude/skills/build-phone-agent-v2` and our tested parts.
`fallback/lint_cli.py` lints a workflow file without a spec, and `fallback/selftest_cli.py` runs a case file against a workflow file on our bank.
`fallback/case_sim_cli.py` runs the agent's own test cases (call variables + customer script + checks) in one Freya
simulation whose tools reach the case's mock.

```bash
uv run python fallback/agent_builder.py <brief files> [--dry]          # a practice brief + the kit's tool API
uv run python fallback/agent_builder.py <case_folder> <mock_url>       # a case folder with its own service contract
```

In case-folder mode it converts every document to text under `out/<slot>/docs/`, tells the agent the slot and the mock
base URL (the skill's "Case folder" section applies), and after the agent finishes exports the slot's workflow to
`submission/<slot>.workflow.json`, records the workflow id, agent id, steps and tool URLs in the trace, and exits 1
unless every tool URL is the mock. Slot = `final-<folder name>`.

## Bench

`bench/` holds a corpus of briefs, each with its own OpenAPI file and, for most, hand-checked cases.
`bench/run.py` builds every brief with one builder (`ours` or `fallback`) and then runs the cases on our bank:

```bash
uv run python bench/run.py <ours|fallback> [names...] [--no-sim] [--reps N]
```

Results go to `results/` (gitignored).

## Runbook

`RUNBOOK.md` is the checklist for the timed final run: preparation, the command per brief, the secret scan of
traces and exports, and what to do when something breaks.

## More documents

`docs/spec-guide.md` is the spec field guide.

---

# Kit README (unchanged)

# Build the Builder

**Kit version 3** (10 October 2026). The file `VERSION` holds the number. When we update the kit, the number goes up and the changes are listed here.

- v3: the example builder waits for its own test run (up to 20 minutes per command) instead of stopping before the results arrive. On Brief A it built an agent that passed 10 of the 10 practice customers in one run.
- v2: `examples/base-agent.json` (an example phone agent) and `examples/agent_builder.py` with the skill `.claude/skills/build-phone-agent` (an example builder) replace `examples/minimal_builder.py`.
- v1: first release.

You write a **builder**: a program that reads a bank's brief and creates a phone agent on Freya.
We put that phone agent in front of simulated customers. The share it handles correctly is your score.

One simulated customer is one case. A step of a phone agent is called a node in the platform's documents.

## The ten-minute start

You need Python 3.10 or newer.

```bash
cp env.example env.sh        # put your Freya API key in it, and a team key you make up
source env.sh

# 1. Put the example agent on Freya and test it. It prints {"workflow_id": ..., "agent_id": ...}
python3 -c "import freya,json; print(freya.save_workflow('base', json.load(open('examples/base-agent.json'))))"
python3 tools/run_cases.py <agent_id> --brief A            # ten practice customers, 3 to 11 minutes

# 2. Let the example builder build one from the brief
pip install claude-agent-sdk
python3 examples/agent_builder.py briefs/brief-a.md
python3 examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md   # Brief B builds on Brief A, so pass both
```

`run_cases.py` prints one line per customer: pass or fail, the tool calls the agent made, and the reason for a failure.

`examples/base-agent.json` is an agent, not a builder. It is the same two-step workflow that is already in your workspace: check identity, then take a payment promise. It uses four of the seven tools, so it handles only the simplest customers: in one run it passed 2 of the 13 practice customers. Read it to see what a valid workflow looks like.

`examples/agent_builder.py` is a builder. It runs Claude through the Claude Agent SDK with one skill, `.claude/skills/build-phone-agent/SKILL.md`. Claude reads the brief and the tool API file, writes the workflow, runs the practice customers and fixes what fails. The skill is short on purpose. Your job is a better builder: a better skill, more skills, your own code, or another agent altogether. It needs a Claude login or `ANTHROPIC_API_KEY`.

## What is in this kit

| Path | What it is |
|---|---|
| `briefs/brief-a.md` | Freya Bank's specification for customers late on one product. 47 rules. Read all of it. |
| `briefs/brief-b.md` | The extra rules for customers with up to three products. It builds on Brief A. |
| `tools/openapi.json` | The bank's tool API that the phone agent calls during a call. |
| `public-test-set.json` | 13 practice customers: what each one does and what the bank's records must show afterwards. |
| `examples/base-agent.json` | An example phone agent: two steps, four tools. The same one that is in your workspace. |
| `examples/agent_builder.py` | An example builder on the Claude Agent SDK. Claude builds, tests and fixes the agent. |
| `.claude/skills/build-phone-agent/SKILL.md` | The one skill the example builder uses. |
| `freya.py` | A small client for the Freya API. Standard library only. |
| `tools/run_cases.py`, `tools/score.py` | Run the practice customers against a phone agent and score it. |
| `docs/workflow-format.md` | What a phone agent is made of: steps, prompts, variables, routes. |
| `docs/tools-and-code.md` | Tools that call an API, code steps, hooks, where a tool's response lands. |
| `docs/api.md` | The API calls a builder needs. |

## How you are scored

A customer counts as handled when all of these hold:

1. **Outcome: the bank's records are right.** The outcome, every promise (product, amount, date), every note, callback and transfer match what the brief requires. The scorer reads them from the bank, not from what the agent said.
2. **Policy: the conversation stayed inside the brief.**
3. **Honesty: the phone agent told the customer the truth**, for example it never says a promise is recorded when the tool call failed.

A judge model reads the transcript for 2 and 3, against the checks listed for that customer.

There is no partial credit. Your score is the share of customers handled.

The same phone agent can pass a customer in one run and fail it in the next. Treat one green run as a hint, not as proof.

## The final

- On Sunday we publish briefs you have not seen. They are the same kind of call with different rules: other numbers and limits, other identity questions, another outcome table, another calendar. Their form can differ too: one final brief is a clean specification, the other is raw bank material (a mail, a policy extract, a memo that changes it, a code list).
- Each final brief comes with its own tool API file. The seven tool names stay the same. The fields and the allowed values can change.
- After the final briefs open you may fix a bug in your builder. You may not write anything from a final brief into it: no rule, no value, no name. Fixing a crash on a table your parser did not expect is allowed. Adding the new brief's holiday dates or outcome names to your code or prompts is not.
- You run your builder once on each, with no manual step, and submit the phone agents it created, your code and the builder's trace.
- You export each phone agent to a file with `freya.export_workflow(workflow_id, "<brief>.workflow.json")` and submit those files. We load them into our own workspace to test them, so what you export is what we score.
- We test those phone agents on customers nobody has seen, on several calendar days, three times each. A customer counts only when all three runs pass. A phone agent that is right most of the time is not right.
- Final customers can have up to three products, as in Brief B.
- Final customers talk like people. They give a number in two parts, correct themselves, talk about other things, ask a question back, or say amounts and dates in words. The facts stay the same. Practise with `--style pieces`, `corrects`, `talkative`, `asks_back` or `in_words` on `run_cases.py`.

So build something that reads a brief, not something that knows this one. Whatever your builder can take from the brief, it should take from the brief.

## Rules

- Your builder takes brief files as input and creates the phone agent with no manual step. Do not edit a phone agent by hand after it is created.
- The phone agent runs on the model set in `freya.py` (`AGENT_LLM`). Do not change it. Your builder may use any model or coding agent.
- The phone agent may call only the bank's tool API. Code steps and hooks inside the workflow are allowed and expected.
- Use `freya.save_workflow(...)` or update your workflow in place. A workspace holds 200 workflows. A builder that creates a new one on every run fills it up and leaves you with a list nobody can read.
- One simulation runs at a time per workspace. A ten-customer run takes 3 to 11 minutes. A second `run_cases.py` started meanwhile waits for the first.
- A builder run on one brief may take 30 minutes at most, including any test it runs on its own phone agent. You may run it more than once and submit the phone agent of one run, with that run's trace.
- Equal scores are ranked by the total promised amount on the customers that passed, then by shorter build time, then by earlier submission.
- Your builder must leave a trace of each run: start and end time, each model call it made (model and a one-line purpose), what it created on Freya, and any test it ran on its own agent. Plain text, no fixed format. You submit one trace per final brief.
- All data here is synthetic. Do not bring real customer data.

## When something does not work

- `error code: 1010` in an HTML page: your request has no browser-like `User-Agent`. `freya.py` sets one.
- `Workspace plan limit reached`: you created too many workflows. Update one in place.
- You changed your builder and the score did not move at all: an updated workflow must be published before a simulation uses it. `freya.save_workflow` publishes. A plain `PUT` does not. See "Update a workflow" in `docs/api.md`.
- A customer fails and you do not see why: `freya.get_call(call_id)["messages"]` is the full transcript with the tool calls. `run_cases.py --out results/run.json` saves the call id of every customer.
- Calls end as `customer-ended-call` before the phone agent is done: it was silent too long and the customer hung up, as a person would. `run_cases.py` prints the slowest agent turn per call after every run, and a warning when the median is above 10 seconds. If the same phone agent is fast in the next run, the model was busy and the low score was not yours.
- You want to see what your phone agent says without waiting for a simulation: `POST /api/v2/chat/message` runs one turn at a time. See `docs/api.md`.
- The bank answers `unknown product_id`: the model made an id up. Product ids come from `get_account` (`p1`, `p2`, ...). Give them to the model in the tool result, or fill them in with code.
- A simulation stays at "running" and no call ever starts: the workflow cannot start a call. Try one chat turn. Leave `agent_config` out of the workflow you send.
- The agent says nothing after a tool call and the customer hangs up: see "Tool results and the next turn" in `docs/tools-and-code.md`.
