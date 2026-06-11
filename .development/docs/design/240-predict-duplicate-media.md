# Issue 240 — PredictStrategy attaches positional Image/Audio/File inputs twice

## Problem

`CurrentCall.from_method()` stores positional arguments in **both** `call.args`
(verbatim tuple) and `call.kwargs` (mapped to parameter name for template
expansion). `PredictStrategy._execute_inner` then collects media from the union
of the two:

```python
all_values = list(call.args) + list(call.kwargs.values())
media_blocks = [media_to_content_block(v) for v in all_values if isinstance(v, Media)]
```

So `agent.analyze(img)` (positional) attaches the same media object twice and
sends it twice in the LLM request, while `agent.analyze(image=img)` (keyword)
attaches it once. Expected: each effective method input is attached exactly once.

The same double-count exists, more benignly, in
`PredictStrategy._assert_param_sizes`, which builds a `named` list from both
positional args and kwargs and so size-checks positional params twice (redundant
work, not a correctness bug).

CodeAct's `InspectInputsPrefill` already iterates `call.kwargs` only, so it is
**not** affected.

## Root cause

There is no single source of "effective parameter values, each once" on
`CurrentCall`. Call sites concatenate `args + kwargs.values()`, which overlaps
because `from_method` deliberately mirrors positionals into `kwargs`.

## Fix

### Prerequisite: align `_parse_param_names` with `inspect` naming

`from_method` maps positionals to names via `inspect.signature().parameters`
(which yields `files` for `*files`), but `_parse_param_names` currently keeps the
marker (`*files`) and also emits bare `*` (keyword-only separator) and `/`
(positional-only separator) as if they were parameters. This mismatch would
re-introduce the duplicate for `*args` media (a positional mapped to `files` in
kwargs but `*files` from the parser would not collapse on `update`).

Harden `_parse_param_names` to mirror inspect: strip leading `*`/`**` from each
name and skip the bare `*` and `/` separators. This makes the index→name mapping
in `bound_parameters` and `format_parameters_as_code` consistent with
`from_method`, and also fixes pre-existing latent mislabeling in
`format_parameters_as_code` (it would otherwise emit `*files = ...` / `/ = ...`).

(Out of scope: the parser's naive comma-split mishandles comma-containing generic
annotations like `dict[str, int]`; this is pre-existing in all three consumers
and unrelated to issue 240.)

### Add `bound_parameters()` helper to `CurrentCall`

Returns an ordered
`dict[str, Any]` mapping each effective parameter name to its value exactly once,
mirroring the de-dup logic already used by `format_parameters_as_code`:

```python
def bound_parameters(self) -> dict[str, Any]:
    """Effective parameter name -> value, each input represented exactly once.

    Positional args are mapped to parameter names parsed from the signature;
    kwargs override/extend them. Positional args beyond the named parameters
    (e.g. *args, or when no signature is available) are included under synthetic
    ``arg_<i>`` keys. This de-duplicates the overlap created by from_method(),
    which stores positional args in both ``args`` and (mapped by name) ``kwargs``.
    """
    result: dict[str, Any] = {}
    param_names = _parse_param_names(self.signature) if self.signature else []
    for i, value in enumerate(self.args):
        if i < len(param_names):
            result[param_names[i]] = value
        else:
            result[f"arg_{i}"] = value
    result.update(self.kwargs)
    return result
```

Why this is correct for the overlap case (the repro): `args=(img,)`,
`kwargs={"image": img}`, `param_names=["image"]`. The loop sets
`result["image"]=img`; `result.update(kwargs)` re-sets the same key to the same
value. `len(result.values()) == 1`.

Why it is robust without `from_method`'s mapping: if a `CurrentCall` is built
directly with non-overlapping `args`/`kwargs` (no name mirroring), positionals
get `arg_<i>` keys and kwargs keep their names — every value still appears once.

Why `*args` works after the parser fix: for `def m(self, *files)` called
`m(a, b)`, `from_method` maps `files→a` (index 0) and leaves `b` only in `args`.
With the hardened parser, `param_names == ["files"]`, so the loop sets
`result["files"]=a` (index 0) and `result["arg_1"]=b` (index 1 beyond named),
then `update({"files": a})` collapses the first. Values `== [a, b]` — each once.

Note: when a kwarg key collides with a synthetic `arg_<i>` key, the kwarg wins
(intentional — kwargs are authoritative). This is a contrived case (a real
parameter literally named `arg_1`) and is covered by a test asserting the
documented precedence.

### Call-site changes

1. `predict.py` media collection:
   ```python
   media_blocks = [
       media_to_content_block(v)
       for v in call.bound_parameters().values()
       if isinstance(v, Media)
   ]
   ```

2. `predict.py::_assert_param_sizes`: replace the hand-rolled `named` list
   construction with `named = list(call.bound_parameters().items())`, removing
   the local `_parse_param_names` import and the duplicate-counting branch. This
   also removes the redundant double size-check for positional params.

## Tests

Add to `tests/strategies/test_current_call.py`:
- `bound_parameters()` de-dups a positional arg that `from_method` also mirrored
  into kwargs (the issue's core case): one entry, correct value.
- positional + keyword mix maps to the right names with no duplicates.
- `*args` media de-dup: `def m(self, *imgs)` called positionally with two
  `Image`s → `bound_parameters().values()` has length 2, no duplicate object.
- keyword-only param: `def m(self, *, image)` called `m(image=img)` →
  `{"image": img}`, no stray `*` key.
- positional-only separator `/` does not leak as a parameter name.
- no-signature case: all args become `arg_<i>`, kwargs keep names.
- kwarg colliding with synthetic `arg_<i>` key: kwarg wins (documented).

Add a focused regression test (new file
`tests/strategies/test_predict_media_dedup.py`) reproducing the issue at the
strategy level: build a `CurrentCall.from_method` for a method with a single
positional `Image` parameter and assert that the media list derived from
`bound_parameters()` has length 1 (and that the buggy
`list(args) + list(kwargs.values())` expression would have produced 2 — guarding
against regression). Mirrors the issue's minimal repro using
`media_to_content_block`.

## Scope / non-goals

- No change to `from_method`'s args/kwargs mirroring (other code — prefill,
  template expansion — relies on positionals being present in `kwargs`).
- No change to CodeAct strategy (not affected).
- No public API removal; `bound_parameters()` is additive.

## Verification

- `uv run pytest tests/strategies/test_current_call.py tests/strategies/test_predict_media_dedup.py -q`
- `uv run pytest src/nemo_oo_agents/strategies/tests/test_predict_param_guard.py -q`
  (existing `_assert_param_sizes` regression suite — must stay green after the
  refactor; error-message `name` values must remain sensible)
- `uv run pytest tests/strategies tests/test_pure_functions_gl105.py -q`
  (regression sweep covering `format_parameters_as_code` / parser consumers)
- `uv run ruff check src/nemo_oo_agents/strategies/predict.py src/nemo_oo_agents/strategies/current_call.py`
