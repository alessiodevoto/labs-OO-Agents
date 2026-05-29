# Improve Harbor import

Issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/224

## Problem

`nemo oo import-harbor` works on Harbor trace directories but has two gaps that make it
unusable on large, current Harbor runs:

1. **Score field mismatch.** The importer only reads `verifier/reward.json["score"]`,
   but current Harbor/BinPool trials write the scalar under `"reward"` (and embed it in
   `result.json["verifier_result"]["rewards"]`). Result: imported traces show `score=n/a`
   even though every trial has a reward.
2. **Per-line HTTP posting.** Each OTLP JSONL line is posted independently to `/v1/traces`.
   A recent run had 115 files / 1,033,271 lines / ~3.0 GB. One POST per line takes hours; a
   batched importer uploaded the same run in ~137 s (1296 POSTs, 115/115 imported, 0 errors).

## Files

- `packages/nemo-oo-agents-cli/src/nemo_oo_agents_cli/commands/import_harbor.py` — main changes
- `packages/nemo-oo-agents-cli/src/nemo_oo_agents_cli/commands/_otlp_helpers.py` — add batch POST helper
- `packages/nemo-oo-agents-cli/tests/cli/test_import_commands.py` — tests

## Change 1: Score field fallback

Add a helper `_read_score(trial_dir, trial_result)` and call it from *inside* `_trial_meta`
(replacing the block at lines 84-93), reusing the `trial_result` dict already loaded at
line 76. `_trial_meta`'s signature is unchanged (still `_trial_meta(jsonl_path)`), so all its
callers and `TestTrialMeta` are unaffected.

Fallback order (each tried in turn; first coercible float wins):

1. `verifier/reward.json["score"]`
2. `verifier/reward.json["reward"]`
3. `result.json["verifier_result"]["rewards"]["score"]`
4. `result.json["verifier_result"]["rewards"]["reward"]`
5. `verifier/reward.txt` float fallback

Behavioural notes:
- `reward.txt` is now tried **regardless** of whether `reward.json` exists (it is the last
  resort if every JSON path yields nothing). This is a superset of the old
  `if reward.json … elif reward.txt …`; the existing `test_reward_txt_fallback` (reward.txt
  only, no reward.json) stays green.
- `result.json["verifier_result"]["rewards"]` is **assumed to be a dict**. Defensively handle
  the non-dict case (e.g. a list, or missing) by skipping steps 3-4 — never raise.
- Coerce each candidate to `float` defensively (reward may be int/str); ignore non-coercible
  values (e.g. `None`, `"n/a"`) and fall through to the next candidate.
- Use explicit presence checks, not truthiness, so a valid `0.0` is returned (not skipped).
- Return `None` when nothing is found.

Helper sketch:

```python
def _coerce_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _read_score(trial_dir: Path, trial_result: dict) -> float | None:
    reward_json = _read_json(trial_dir / "verifier" / "reward.json")
    for key in ("score", "reward"):
        s = _coerce_float(reward_json.get(key))
        if s is not None:
            return s
    rewards = (trial_result.get("verifier_result") or {}).get("rewards")
    if isinstance(rewards, dict):
        for key in ("score", "reward"):
            s = _coerce_float(rewards.get(key))
            if s is not None:
                return s
    reward_txt = trial_dir / "verifier" / "reward.txt"
    if reward_txt.exists():
        return _coerce_float(reward_txt.read_text().strip())
    return None
```

## Change 2: Batched OTLP posting

The viewer's `/v1/traces` accepts an OTLP body with a `resourceSpans` array. Batching =
concatenating the `resourceSpans` lists from many JSONL lines into one body and posting once.

### New helper in `_otlp_helpers.py`

```python
def post_traces_batch(endpoint: str, bodies: list[dict]) -> bool:
    """POST multiple OTLP bodies as one request by merging their resourceSpans."""
    merged = {"resourceSpans": []}
    for b in bodies:
        merged["resourceSpans"].extend(b.get("resourceSpans", []))
    if not merged["resourceSpans"]:
        return True
    return post_trace(endpoint, merged)
```

Reuses `post_trace` for the actual HTTP work (single source of truth for URL/headers/timeout).
The new symbol must be added to the `from ._otlp_helpers import (...)` block in
`import_harbor.py` (lines 22-28) so `patch.multiple` on `_HARBOR_HELPERS_PATH` can find it.

### CLI options

Add to `import_harbor.command`:

- `--batch-lines` (int, default 1000): max JSONL lines (== resourceSpans envelopes) per POST.
- `--batch-bytes` (int, default 4_000_000 ≈ 4 MB): max raw input bytes accumulated per POST.

Either limit reached → flush the batch.

### Loop changes

Per trace file:
- accumulate `(body, raw_len)` into a buffer after `inject_resource_attrs`
- flush when `len(buffer) >= batch_lines` or `accumulated_bytes >= batch_bytes`
- flush remaining buffer at end of file (sessions are per-file; don't batch across files so
  a failure is attributable to one file and resource attrs stay per-trial)
- **Success semantics (preserve existing any-success behaviour):** `file_imported = True` if
  *any* flush succeeds (matches the current per-line code where one good post marks the file
  imported). For *each* failed flush, append an error to `errors`. So a partially-failed file
  is counted as imported AND surfaces per-flush errors. This keeps `test_post_trace_failure`
  semantics intact: when every flush fails, `file_imported` stays False → `0 imported` plus
  error lines.

Note: batching is per file. Since the largest single file in the cited run is bounded and the
default batch sizes apply within a file, cross-file batching is intentionally avoided to keep
session attribution and error reporting simple. Files themselves are the unit of "imported".

`--batch-bytes` accounts the **raw JSONL input line length** (an approximation — the
re-serialized merged POST body is slightly larger after `inject_resource_attrs`). This is an
intentional, documented approximation to avoid re-serializing each line just to measure it.

### Timeout

`post_trace` uses a 10 s timeout. A 4 MB body may need more. Bump `post_trace` to accept an
optional `timeout` param (default 30 s) so large batches don't spuriously fail. Keep default
generous; per-line callers unaffected.

## Edge cases

- Empty file / all-empty lines → no flush, file counted as skipped (unchanged semantics).
- Invalid JSON / non-`resourceSpans` lines → skipped, not added to batch (unchanged).
- `batch-lines=1` → effectively per-line (regression-equivalent path); keep working.
- Partial batch failure: a flush is all-or-nothing per POST; record one error per failed flush.
- Score `0.0` is falsy but valid → use explicit `is not None` checks, never truthiness.

## Tests (pytest, offline via mocks)

Score (test `_read_score` directly and/or via `_trial_meta`):
- `reward.json` with `{"reward": 0.5}` → score 0.5
- `result.json` `verifier_result.rewards.reward` fallback (no reward.json) → score read
- `result.json` `verifier_result.rewards` as a non-dict (e.g. list) → no crash, falls through
- `score` key still takes precedence over `reward` within reward.json
- score `0.0` preserved (not treated as missing)
- existing reward.json/reward.txt/none tests stay green

Batch:
- `post_traces_batch` merges resourceSpans from multiple bodies (unit test, mock `post_trace`,
  assert merged body has the combined resourceSpans count)
- `post_traces_batch` with empty input returns True without posting
- CLI `--batch-lines` / `--batch-bytes` options accepted; import succeeds
- multi-line trace file imported with batching: add a fixture writing several `_OTLP_TRACE`
  lines to one `run.jsonl` (since `_make_harbor_job` writes a single line); assert
  `post_traces_batch` call count < number of lines for batch-lines>1
- failed batch flush → recorded as error; all-flushes-failed file → not imported

**Important — existing harbor command tests now flow through `post_traces_batch`.** The command
loop calls `post_traces_batch` (not `post_trace`) per flush. So:
- Update `_mock_harbor_helpers` to patch `post_traces_batch` (add a `post_traces_batch_val`
  kwarg defaulting to True). Keep patching `post_trace` (still imported/used elsewhere/unit).
- Update `test_post_trace_failure` to drive `post_traces_batch_val=False` (rename if helpful).

## Verification

`uv run pytest packages/nemo-oo-agents-cli/tests/cli/test_import_commands.py` and
`uv run ruff check` on changed files.
