# Issue 254 — Trace viewer: parallel agent calls no longer interleave confusingly

## Problem

The viewer rendered OTEL spans in a flat timeline sorted purely by `start_time_ns`.
nooa workflows commonly spawn **parallel sub-agent calls mid-workflow, at any
depth and nested** (e.g. an orchestrator launches several CodeAct sub-agents via
`asyncio.gather`). With a flat timestamp sort, those concurrent sibling subtrees
**interleave**, so a reader scanning top-to-bottom sees spans from different branches
adjacent and concludes a tool call was "skipped" when it actually ran in another branch.

## Final design

Two complementary changes; **no heuristics** anywhere.

### 1. Viewer: tree order (start time + parent-child nesting)

`src/nooa/viewer/frontend-react/src/components/trace/tree_order.ts`
- `annotateTreeOrder(events)` assigns each event a `_tree_rank` = DFS-preorder position,
  with each parent's children ordered by start time (span-events placed under their span).
  Sorting by `_tree_rank` keeps every subtree contiguous, so parallel branches render as
  blocks instead of interleaving.
- It is a **no-op for sequential traces**: when no sibling subtrees overlap, DFS order
  equals a flat timestamp sort. It also degrades gracefully to timestamp order for flat /
  parentless traces (all spans are roots → sorted by start time), and handles orphan/cyclic
  parenting robustly — so tree order is safe to apply unconditionally.

The model is exactly: **start time + parent-child nesting.** A short sibling that starts
after a long sibling renders after that sibling's whole subtree — the standard meaning of
ordering siblings by start time. Tree order is the *only* order — there is no "sort by time"
toggle (it was considered redundant since tree order is strictly better and degrades to
timestamp order anyway) and no color-coding/badges (an earlier draft had per-branch colours +
`∥ k/n` badges; dropped — all the complexity lived in detecting "parallel groups," and tree
order already de-interleaves without it).

### 2. Tracer: correct parenting of methods called from generated code

See [issue-254b-codeexec-method-parenting.md](issue-254b-codeexec-method-parenting.md).
A method invoked from inside `execute_python` (e.g. `self.submit()`) was parented to the
enclosing agent method, making it a sibling of the long `generation` span — so it rendered
at the *end* of the branch instead of where it ran. The tracer now nests such calls under
the active `code_execution` span (per-task contextvar; concurrency-safe; `agent.parent_call_id`
attribute and ATIF export unchanged). With correct parenting, tree order places it at its
true position automatically.

## Files

- `frontend-react/src/components/trace/tree_order.ts` — **new**: `annotateTreeOrder`.
- `frontend-react/src/api/{traces,types}.ts` — call `annotateTreeOrder`; add `_tree_rank`.
- `frontend-react/src/components/trace/TraceView.tsx` — `applyFilters` sorts by `_tree_rank`
  (eval-summary spans still floated to the top).
- `frontend-react/dist/` — rebuilt (the Python viewer serves the committed build).
- `nooa/tracing/_hooks_impl.py` — the parenting fix (see issue-254b).
- `tests/runtime/test_codeexec_method_parenting.py` — regression (sync + async submit).

## Verification

- `npm run build` clean; tree order confirmed on a real parallel trace (3 sub-agents render
  as contiguous blocks; sequential traces unchanged).
- Tracer: hook simulation, sync + async end-to-end pytest, and a real-LLM run (parallel
  sub-agents) all show `submit` nested under `code_execution`. The full tracing/runtime
  suite passes (see CI), aside from one unrelated, pre-existing sandbox process-kill failure.

## Out of scope

- Per-branch colour-coding / swimlanes — dropped in favour of plain tree order.
- Updating already-recorded traces — they keep whatever parenting they were recorded with;
  re-running with the deployed fix produces correct nesting.
