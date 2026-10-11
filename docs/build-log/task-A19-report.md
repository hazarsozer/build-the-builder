# Task A19 report: dashboard layout and call-variable samples

Base 567eb9a. Branch `worktree-agent-a1208e115536a1fe2`. Commits d8e0ee3 (freya client), 8315887 (compile + layout + tests), plus this report.

## Where the editor reads positions (not what the kit doc says)

`docs/workflow-format.md` lists `nodePositions` as a key inside `workflow`. The dashboard does not store it there. I read the
dashboard's JS bundle (chunks from the cached Playwright profile, fetched read-only from the CDN):

- The workflow **record** has `nodePositions: record<string, {x:number, y:number}>` next to `workflow` and `canvasViewport`
  (DB column `node_positions jsonb`, default `{}`). Create and update bodies accept it as an optional sibling of `workflow`.
- Each **node definition** may carry `position: {x, y}` (optional field in WorkflowDefinitionSchema, nodes and global nodes).
- The editor's graph class keeps both and reads the map first, then the node's own `position` (`i[e] || s.position || {x:0,y:0}`).
- The agent export/import format has `nodePositions` at record level too.

Every one of our ~25 workflows had `nodePositions: {}`, hence the pile. So the builder now writes both:

1. `compile` puts `position` on every node (it travels with the workflow JSON, so a workflow-only import still lays out).
2. `freya.create_workflow` / `update_workflow` send `nodePositions` (copied from the node positions) in the request body;
   `export_workflow` adds the record's `nodePositions` to the exported file. A workflow without positions sends nothing new.

Dry-run validation accepted it (`valid: true`), and the read-back of the `layout-preview` save kept both (63/63, identical).

## Layout (builder/layout.py)

- Columns by BFS depth from `initial_node` over transitions, `passthrough_default_transition`, `max_turn_transition`.
- Close nodes (`close_*`, or any non-end node whose every target is an end-call node) in the second-to-last column,
  `end_call` nodes in the last one. Global nodes in a column left of the start. Unreached nodes get a column (prune
  normally drops them).
- Siblings: discovery order, then one left-to-right pass orders each column by the mean height of its parents (fewer
  crossings). Columns centred against the tallest.
- Spacing: 560 px between columns, 460 px between stacked cards (cards ~300 x 250-400). Integer coordinates.
- spec-b: 13 columns, close column 17 cards tall (~7.8k px); spec-a: 11 columns.

## test_variables

`sample_call_variables(spec, wf)`: one sample per call variable = `spec.call_variables` plus placeholders of
`address_as`, minus anything the workflow writes (extraction, response mappings). Samples by name: `*_id` -> `c_test`
(as before), title -> `Ms`, first -> `Dana`, last -> `Reyes`, `now` -> first `examples[].today` + `T10:00`
(spec-a/b: `2026-10-14T10:00`), other `*name*` -> `Example Ltd` / `Alex Morgan`, else the name's words.

Not declared: model-filled tool placeholders (`field`, `answer`, `cb_date`, `cb_time`, `sp_*`, `rec*_*`, `note*_*`,
`outcome`, `reason`, `farewell`, ...). A set variable overrides the model's tool input; the docstring says why. Those
"will not resolve" warnings stay and are expected.

Simulation safety: a value passed with the call wins over a test variable. Test checks every builder-generated case
(seeds 0 and 1) and every public case passes each declared variable (customer_id comes from the practice bank session).

Changed behaviour from the previous rule: extra call variables (e.g. `company_name`) used to be declared as `""` so a
call that omitted one never spoke a placeholder; they now get a sample. Two tests in test_generalise.py updated to the
new contract.

## Behaviour unchanged

Compiled the reference specs (with and without base URL) at base and at HEAD: identical after removing `position` and
`test_variables`. Lint clean on both reference specs.

## Node names

All 63 node ids are readable snake_case and match `name`. The numbered ones are deliberate per-product slots in the
multi-product path: `record_1..3`, `retry_1..3`, `note_1..3` (product 1..3 of get_account). No renames made.

## layout-preview

One save, new slot `layout-preview` (spec-b, default base URL from openapi, no tunnel):
workflow `068d6cce-0768-40b2-af6a-e0fa3a52b71b`, agent `f2c34c3d-2b48-4025-b259-dad85d86c84d`.

## Tests

tests/test_layout.py (23): positions on every node, no overlap with a 300x400 card, deterministic, start-left /
close / end column order, nothing else added, globals left, unreached nodes placed, test_variables == call variables,
never a model-filled placeholder, every case passes every declared variable, client sends record-level map.
Full suite: 791 passed, 6 skipped (base 768).

## Concerns

- `now` sample: if a grader call ever omitted `now`, the agent would use the sample date instead of the real clock
  (setup's fallback). Every brief and case passes `now`, so this is theoretical. Same for extra call variables.
- `layout-preview` was created, not updated, so freya.py's agent_config model fix (done only on update) did not run;
  the dashboard "Call Agent" / chat on that slot may refuse to start. Simulations are unaffected. A second save would fix it.
- `docs/workflow-format.md` line 60 is wrong about where `nodePositions` lives; I did not edit the kit doc.
- `fallback/` builder not touched (no layout there).
- The editor's load path for positions was inferred from the graph class and schemas, not seen executing; the
  controller's dashboard check is the real verification.
