# Issue 232 — PredictStrategy emits invalid Responses API schema for top-level `list` return types

## Problem

`PredictStrategy._create_response_model` special-cases `list` / `list[T]` return types by
producing a `pydantic.RootModel[list[...]]`. The JSON schema for a `RootModel[list]` has a
top-level `{"type": "array"}`. The OpenAI / Azure **Responses API** rejects `response_format`
(`text.format.schema`) whose root is not `type: "object"`:

```text
Invalid schema for response_format 'ProposeClustersResponse':
schema must be a JSON Schema of 'type: "object"', got 'type: "array"'.
```

So any structured-output generation method annotated `-> list[T]` fails at request time, even
though container return types are documented as supported.

## Root-cause detail

In `src/nemo_oo_agents/strategies/predict.py::_create_response_model`:

- `dict` / `dict[...]`  → `RootModel[dict[str, Any]]`  → root schema `type: object`  ✅ valid
- `list` / `list[...]`  → `RootModel[list[inner]]`     → root schema `type: array`   ❌ invalid
- `tuple`, `set`, scalars, `Union[...]` → fall through to the generic wrapper
  `create_model(name, value=(return_type, ...))` → root schema `type: object`  ✅ valid

Only the `list` branch produces an array-rooted schema. `tuple[...]` and `set[...]` are already
handled correctly by the generic `value`-wrapper path (their array lives under the `value`
property, so the root stays an object).

## Fix

Remove the dedicated `list` `RootModel` branch and let `list` / `list[T]` fall through to the
**same generic `value`-wrapper** used for `tuple`/`set`/scalars:

```python
response_model = create_model(model_name, value=(return_type, ...))
```

This yields a root schema `{"type": "object", "properties": {"value": {"type": "array", ...}}}`,
which the Responses API accepts. After validation, the existing `_validate_response` unwrap path
(`hasattr(validated, "value") → return validated.value`) returns the bare list to the caller, so
the public contract (`agent.method() -> list[...]`) is unchanged.

This reuses machinery already present and exercised for `tuple`/`set`, so it is low-risk.

### Why `value` (not `result`)

The issue text suggests a `{"result": [...]}` wrapper, but the codebase already wraps
scalars/tuple/set under `value` and `_validate_response` already unwraps `value`. Reusing `value`:
- requires no new unwrap code path,
- keeps list consistent with the other wrapped container/scalar types,
- the wrapper is an internal implementation detail (unwrapped before returning to the caller), so
  the field name is not part of any public contract.

### Robustness bonus

`_parse_llm_response` already wraps a bare-array JSON response into `{"value": parsed_data}`. With
the generic `value`-wrapper model, a non-compliant model that returns a bare JSON array still
validates (previously it relied on the `RootModel` accepting the list positionally). Behavior for
compliant `{"value": [...]}` responses is identical.

## Scope / non-goals

- `dict` return types are **left unchanged** (their `RootModel` root is already `type: object`,
  which the API accepts). Touching them would be unnecessary churn and risk.
- No change to `CodeActStrategy` or other strategies.
- No change to public API or return contracts.

## Files to touch

1. `src/nemo_oo_agents/strategies/predict.py`
   - Delete the `if return_type is list or origin is list:` block in `_create_response_model`
     (lines ~783–796) so `list` falls through to the generic `value`-wrapper.
   - Update the method docstring bullet that claims `list` uses `RootModel`.

2. `tests/strategies/test_strategies_coverage.py`
   - `TestPredictStrategyDictListReturn::test_returns_list` and `test_returns_list_str` currently
     simulate the LLM by passing a hand-built `RootModel[list]` instance as `content`. Update them
     to send `{"value": [...]}` JSON content (matching the new wrapper schema) and still assert the
     unwrapped list is returned. Keep `test_returns_dict` as-is.

3. New regression test (bug-first): assert the generated schema is object-rooted.
   - Add `tests/strategies/test_predict_list_root_schema.py` (or extend the existing coverage file)
     with:
     - `_create_response_model(list[Cluster], "propose_clusters").model_json_schema()["type"] == "object"`
       for bare `list`, `list[str]`, and `list[<dataclass>]`.
     - An end-to-end `FakeLLMClient` test: method `-> list[Cluster]`, scripted content
       `{"value": [{"theme": "a"}, {"theme": "b"}]}`, asserting the agent returns the unwrapped
       `list[Cluster]`.

## Test / verification plan

- New regression test fails against unmodified code (schema root is `array`) and passes after the fix.
- `uv run pytest tests/strategies/test_strategies_coverage.py tests/strategies/test_predict_list_root_schema.py -q`
- `uv run ruff check src/nemo_oo_agents/strategies/predict.py tests/strategies/`
- Broader smoke: `uv run pytest tests/strategies -q`.

## Follow-up: strict-mode schema fallback for dict/tuple/set/bare-list (cross-model audit)

After the list fix, a live cross-model audit (PredictStrategy × 18 return types ×
{gpt-5-mini (OpenAI/Azure), claude-haiku (Bedrock), nemotron-super-49b}) surfaced a
broader, **related** class of bugs: several supported return types produce JSON schemas
that strict structured-output mode cannot express, so the request is rejected:

| Return type | Schema feature | strict-mode problem |
|-------------|----------------|---------------------|
| `dict`, `dict[str,V]` | free-form object (`additionalProperties`) | strict requires `additionalProperties: false` |
| bare `list` | array with untyped `items: {}` | strict requires typed `items` |
| `tuple[A,B]` | `prefixItems` | unsupported |
| `set[T]` | `uniqueItems` | unsupported |

### Fix (in `unifiedllm.py`)

- New `_schema_strict_compatible(schema)` detects the four shapes above (recursively).
- New `_loose_response_schema(schema)` reduces a schema to the OpenAI-supported keyword
  subset (drops `uniqueItems`, `prefixItems`, numeric/length bounds, Pydantic noise).
- `_maybe_sanitize_response_format` (CompletionClient path):
  - **Bedrock**: unchanged strict json_schema, but `_BEDROCK_STRIP_KEYWORDS` now also strips
    `prefixItems` and `uniqueItems` (Bedrock reports them "not supported").
  - **OpenAI/Azure/NIM**: return the Pydantic model as-is (litellm builds strict) when the
    schema is strict-compatible; otherwise send a **non-strict** json_schema built from
    `_loose_response_schema`. PredictStrategy still validates the parsed output against the
    real Pydantic model client-side (with retries), so exact typing is preserved.

Strict-compatible types (scalars, `list[str]`/`list[Model]`, `Literal`, `Optional`, `Union`,
`Enum`, Pydantic models, dataclasses) keep the unchanged strict path — no regression.

### Result
Live matrix improved from **46/54 → 54/54** across all three completion-path providers.

## Follow-up 2: same fix for the Responses API path (`client_type: responses`)

The above fixes the `CompletionClient` path. Models configured with `client_type: responses`
(e.g. `gpt-5.5-responses`, `gpt-5.5-direct`) use `ResponsesClient`, which passes
`text_format=output_model` straight to `litellm.responses` — bypassing
`_maybe_sanitize_response_format`. A live audit confirmed the **same** failures there
(`gpt-5.5-responses`: 13/18).

Fix: new `_responses_output_params(output_model)` mirrors the completion logic for the
Responses API. litellm's `convert_text_format_to_text_param` ignores `text_format` when an
explicit `text` is supplied, so for strict-incompatible schemas we pass
`text={"format": {"type": "json_schema", "name", "schema": <loose>, "strict": False}}`
instead of `text_format`. Both `ResponsesClient.call` and `.acall` use it.

The **Azure** Responses endpoint is stricter than OpenAI-direct even in non-strict mode —
it rejects "object schema missing properties" and "array schema missing items". So
`_loose_response_schema` now always supplies empty `properties: {}` / `items: {}` defaults
(meaning "any"), which satisfies Azure while remaining valid everywhere else.

### Result
- `gpt-5.5-direct` (OpenAI-direct Responses): 18/18.
- `gpt-5.5-responses` (Azure Responses): 18/18 after the `properties`/`items` defaults.

### Guarded by
- fast unit tests `tests/unifiedllm/test_response_format_strict_fallback.py` (no network) —
  cover both `_maybe_sanitize_response_format` (completion) and `_responses_output_params`
  (Responses) routing + keyword stripping;
- live integration matrix `tests/integration/test_predict_return_types_live.py`
  (`@pytest.mark.integration`, key-gated, skipped by CI's `-m 'not integration'`) — now also
  includes `gpt-5.5-responses` to exercise the Responses path.

## Follow-up 3: CodeAct strategy + subsuming issue 148

CodeAct emits structured output through the `return_result` **tool** schema
(`_convert_tool_to_schema`), not `response_format`/`text.format` — the surface of **issue
148** (Responses-API strict-mode tool-schema sanitization for `dict`/`Optional`/`Any`).
Since the root cause is identical (strict mode can't express these types), this branch
subsumes 148 with the same non-strict-fallback strategy.

A live CodeAct audit found two real problems (everything else already worked because the
model usually returns the value from inside `execute_python`, bypassing the tool JSON):

1. **`tuple` on the Responses API tool path** — strict cleaning drops `prefixItems`, leaving
   an array with no `items`; `_strict_schema_valid` didn't require `items`, so the broken
   strict schema was sent and Azure rejected "array schema missing items".
2. **`Any` on the response_format path** — `Any` → empty `{}` schema; `_schema_strict_compatible`
   didn't treat untyped nodes as incompatible, so it stayed strict and OpenAI rejected
   "missing type" (148's `Any`/`json_extract` case).

### Fix
- `_strict_schema_valid` now requires arrays to declare typed `items` (a genuine strict
  requirement) → tuple/untyped arrays fall back to non-strict.
- `ResponsesClient._convert_tool_to_schema` routes its non-strict fallback through
  `_loose_response_schema` (strips `prefixItems`/`uniqueItems`, adds `items`/`properties`
  defaults) so the fallback tool schema is Responses-API-safe.
- `_schema_strict_compatible` now treats untyped (`{}`) nodes as strict-incompatible →
  `Any` routes to non-strict on the response_format path too.

148 notes that are now stale: its `_sanitize_strict_schema`/`_strip_schema` functions no
longer exist (pipeline rewritten); and its "BaseModel with Optional field" row already
passes on this branch (litellm handles optional fields — verified live).

### Result
- CodeAct live matrix across **all 5 model configs** (gpt-5-mini, claude-haiku,
  nemotron-super-49b, gpt-5.5-responses, gpt-5.5-direct) × all return types: clean
  (one transient rate-limit on gpt-5-mini.as_optional, not a schema error;
  nemotron + gpt-5.5-direct were 48/48).
- 148's flagged patterns (`Any`, `dict[str,int|bool]`, `list[dict]`) produce valid requests
  and correct Python types on both the response_format and tool-schema surfaces. (Free-form
  `dict` content can be sparse under a loose schema — inherent to the type, and strictly
  better than coerce-to-strict, where `additionalProperties:false` forces `{}`.)

### Guarded by
- unit tests extended with: untyped-node incompatibility, `_strict_schema_valid` array-items
  requirement, `_loose_response_schema` normalization, and the `ResponsesClient` tool-schema
  fallback (all no-network).
- integration test adds `SchemaStressCodeActAgent` (CodeAct) + the 148 return-type patterns.

### Strategy note (why non-strict fallback, not 148's coerce-to-strict)
True strict guided-decoding is only a hard guarantee on OpenAI/Azure; Anthropic/Bedrock and
NIM treat schemas as best-effort. Non-strict + client-side validation/retry therefore works
uniformly across every endpoint and can represent types strict literally cannot (free-form
`dict`, heterogeneous `tuple`, `set`). Coerce-to-strict is neither sufficient (can't encode
those) nor necessary (Optional already works), so the foundation is non-strict fallback,
keeping strict only where it's both supported and faithful.

## Follow-up 4: complex / nested / non-serializable return types (KDD-cup feedback)

Audited nested models, subtype reuse, `dict[str, Model]`, and non-JSON-serializable
types (`pandas.DataFrame`, `numpy.ndarray`) across Predict + CodeAct on gpt-5-mini /
gpt-5.5-responses / claude-haiku. Findings:

- **Nested models, subtype reuse, deeply-nested `list[Team]`** already work on both
  strategies (the resolved `$defs` schema + visible classes suffice) — no change needed.
- **`dict[str, Person]` in Predict returned plain dicts, not `Person`** — the dict branch
  hardcoded `RootModel[dict[str, Any]]`, discarding the value type. Fixed to preserve the
  declared key/value types so Pydantic validates/constructs the values.
- **CodeAct crashed for a Pydantic model with a non-serializable field** (e.g. a model with
  a `pd.DataFrame` field): `_is_pydantic_compatible` only probed `create_model`, which
  succeeds, then the tool-schema build blew up at `model_json_schema()`. Fixed to also probe
  `model_json_schema()` → such types fall back to the `Any` tool schema (model builds the
  value in `execute_python`).
- **Predict + non-serializable return type** (`-> pd.DataFrame`, or a model containing one)
  now fails fast with a clear, actionable `GenerationError` (points to CodeActStrategy or a
  serializable proxy like `{columns, rows}`), via a `model_json_schema()` probe in
  `_execute_inner`. Previously a cryptic Pydantic error surfaced from the request layer.

numpy/pandas (and models containing them) work in **CodeAct** via the `Any` fallback;
`spec.define_doc(<type>)` is the intended way to teach the model how to construct opaque
types (numpy/plotly ship adapters; pandas does not — a possible future addition).

### Guarded by
- `tests/strategies/test_complex_return_types.py` (no-network): dict value-typing, nested
  schema routing, `_is_pydantic_compatible` for DataFrame/ndarray/model-with-df-field, and
  the clear Predict error via FakeLLMClient.
- `tests/integration/test_predict_return_types_live.py`: `ComplexPredictAgent` +
  `ComplexCodeActAgent` (nested / reused / dict-of-models / numpy / pandas / nested-DataFrame).

## Follow-up 5: construction guidance for opaque return types (CodeAct)

Schema correctness gets a valid *request*, but for opaque types (no JSON schema →
`Any` tool fallback) the model also needs to know *how to construct* the value.
Previously the `return_result` tool for an `Any`-fallback type carried only the type
name; structure for Pydantic models lives in the tool's JSON schema, but opaque types
had nothing. Two complementary additions (the "twofold" design):

1. **Robust auto-doc fallback** (`CodeActStrategy._render_return_type_doc`): on the
   `Any` fallback path, fold the return type's `doc()` rendering into the `return_result`
   description (truncated, best-effort). Works for *any* opaque type — pandas, numpy,
   custom classes — without a hand-written adapter. Pydantic-schemable types are
   unchanged (their structure is already in the tool schema).
2. **Curated `pandas` adapter** (`agentdoc/adapters/pandas.py`): a `spec.define_doc`
   adapter for `DataFrame`/`Series` that turns pandas' ~50-line constructor docstring
   into a few construction-focused lines. Registered via `adapters.register_all()`,
   which the fallback calls lazily (installed-gated, idempotent) so the concise view
   applies out of the box. (Previously only `plotly` shipped an adapter; numpy/pandas
   rendered their verbose defaults — this is the gap the KDD team hit and hand-patched
   with their own `@spec.define_doc(pd.DataFrame)`.)

`Predict` deliberately gets no such fallback: it has no construction step (the model
emits JSON, the framework reconstructs), and opaque types have no JSON round-trip — so
the correct behavior remains the clear fail-fast error (Follow-up 4).

### Guarded by
- `tests/agentdoc/test_pandas_adapter.py`: adapter is concise + shows construction.
- `tests/strategies/test_complex_return_types.py::TestCodeActReturnTypeDocFallback`:
  opaque-type `return_result` description includes the doc reference; Pydantic types
  don't; truncation is bounded.
- **Capability test** (`tests/capability/agents/construction.py`, config
  `construction_dataframe` + `construction_widget`): a CodeAct agent that *constructs*
  and returns (a) a `pd.DataFrame` (curated adapter path) and (b) a **novel non-Pydantic
  `Widget`** with a non-guessable factory constructor and **no** adapter (pure auto-doc
  fallback). Unlike schema-validity (a binary the unit tests cover), construction is a
  quality gradient, so it belongs in the scored eval_pipeline suite. The Widget case has
  no training prior, so a pass proves the auto-doc fallback actually conveys construction
  info. Scored by a custom `ConstructionScorer` on the live returned object.

## Edge cases considered

- Bare `list` (no type arg) → `value=(list, ...)` → object root with `value: {type: array}`. ✅
- `list[PydanticModel]`, `list[dataclass]` → nested defs under `value`. ✅
- `Optional[list[T]]` → Union unwrap recurses to `list[T]`, then generic wrapper. ✅
- Non-compliant bare-array model output → `_parse_llm_response` wraps to `{"value": ...}`,
  validates. ✅
