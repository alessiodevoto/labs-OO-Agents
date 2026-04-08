# Blocking access to hidden agent members (exploration)

**Context:** We have `DEFAULT_BLOCKED_CALLS` (and `BlockingCallValidator`) that prevent the LLM from calling specific functions/methods from certain modules (e.g. `time.sleep`, `Thread.join`). Visibility is already expressed with `@hidden` and `Annotated[T, hidden]` for agent methods and fields. Today, those only affect **documentation** (e.g. `doc(self)`) and **module-level** filtering; they do **not** block runtime access. If the LLM writes `self._my_private_method()` or `self._my_private_attr`, that code runs.

**Goal:** Explore generalizing “blocked” semantics so that **user-defined** hidden members (methods and attributes) can also be **blocked** at validation or runtime, using the same `@hidden` / `Annotated[T, hidden]` markers.

---

## Is it possible?

**Yes.** We already have:

- **Visibility:** `is_hidden_method(func)` and `is_hidden_field(cls, name)` in `visibility.py` — so we can derive the set of “blocked” names from the agent class.
- **Validation pipeline:** `ValidationContext` has `agent`, `agent_class`, and `exec_globals`; validators get the AST and can resolve `self` to the agent instance/type.
- **Blocking precedent:** `BlockingCallValidator` already blocks specific calls by resolving names and checking against a config (module + call name). We can add a similar validator that blocks `self.<hidden_method>()` and `self.<hidden_attr>` (read/write).

So we can **block** calls to hidden methods and access to hidden attributes in agent code without changing the visibility API.

---

## Approaches

### 1. AST validator (recommended)

**Idea:** Add a validator (e.g. `HiddenMemberValidator`) that runs with the existing `UnifiedCodeValidator`. It visits:

- **Calls:** `node.func` is `ast.Attribute`, value is a name that resolves to the agent (e.g. `self`). If the attribute name is a hidden **method** on the agent class → emit a validation error.
- **Attribute load/store:** `node` is `ast.Attribute`, value resolves to the agent. If the attribute name is a hidden **field** → emit error (read and/or write, depending on policy).

**How we get “hidden” sets:**

- From `context.agent_class` (or `context.agent`): walk the class (and optionally MRO), collect:
  - **Hidden methods:** names where `is_hidden_method(getattr(cls, name))` is True (only on callables).
  - **Hidden fields:** names where `is_hidden_field(cls, name)` is True (from `__annotations__` + visibility helpers).

**Resolving `self`:** `exec_globals["self"]` is the agent instance. So for a node `ast.Attribute(value=ast.Name(id="self"), attr="x")`, we check `type(context.exec_globals["self"])` and the hidden sets for that type. Optional: track simple aliases (e.g. `me = self`) so `me._private()` is also blocked; not strictly necessary for v1.

**Complexity:** **Low–medium.**

- Reuse `ValidationContext` and existing visibility helpers.
- One new visitor class (~50–80 lines), one new validator class that builds hidden sets and runs the visitor.
- Wire into `UnifiedCodeValidator` and ensure `ValidationContext` always has `agent_class`/`agent` where code is executed (already the case in `execute_code`).
- Edge cases: only direct `self.*` is blocked; aliases like `x = self; x._private()` could be allowed in v1 or handled with optional alias tracking.

---

### 2. Proxy around `self`

**Idea:** Replace `exec_globals["self"]` with a **proxy** that forwards attribute access and method calls to the real agent, but raises (or blocks) when the member is hidden.

**Implementation sketch:** A wrapper class that implements `__getattr__` (and optionally `__setattr__`): if the name is in the “hidden” set for the agent type, raise a clear error; otherwise forward to the real agent. For methods, the returned bound method is the real one; you could wrap it again so that calling it still checks “hidden” (so `getattr(self, "_private")()` is also blocked if `_private` is hidden).

**Complexity:** **Medium–high.**

- Must be transparent for normal use (equality, `isinstance`, serialization, etc.). So either a thin proxy that only overrides attribute access or a more complex one that preserves type and behavior.
- Subclasses, `__getattribute__`, descriptors, and properties can make “hidden” checks subtle (e.g. property that exposes internal state).
- Need to decide: block only direct attribute access, or also `getattr(self, "_private")` (proxy can do both).
- Risk of breaking code that relies on `self` identity (e.g. `self is agent`) unless the proxy is very careful.

**Verdict:** Possible but heavier and more fragile than an AST check. Better as a future hardening layer if we ever want runtime enforcement without running the validator.

---

### 3. Doc-only (current behavior)

**Idea:** Rely only on visibility: hidden members are omitted from `doc(self)` and from module-level globals. No explicit “block” on access.

**Pros:** No new code; no risk of false positives.
**Cons:** LLM can still call `self._private()` or read `self._private_attr` if it guesses or introspects. So “hidden” is advisory, not enforced.

**Verdict:** Keeps current semantics. Can be combined with approach 1 so that “hidden” means both “not documented” and “forbidden in generated code.”

---

## Recommendation

- **Implement approach 1 (AST validator)** so that:
  - **Calls** to `self.<hidden_method>()` are blocked (same spirit as `DEFAULT_BLOCKED_CALLS`).
  - **Read/write** of `self.<hidden_attr>` are blocked so that hidden fields are not accessible in generated code.
- Reuse the existing visibility API: no new decorator or annotation; `@hidden` and `Annotated[T, hidden]` become the single source of truth for “not in doc” and “not allowed in code.”
- Optionally extend `RestrictionsConfig` later with a flag like `block_hidden_members: bool = True` so users can turn this off if they want to allow access to hidden members in generated code (e.g. for debugging or special flows).

---

## Implementation sketch (approach 1)

1. **Helpers (e.g. in `visibility.py` or a small helper used by the validator):**
   - `get_hidden_method_names(cls: type) -> set[str]`
   - `get_hidden_field_names(cls: type) -> set[str]`
   Both walk the agent class (and MRO if desired) and use `is_hidden_method` / `is_hidden_field`.

2. **New validator:** `HiddenMemberValidator`:
   - In `validate(tree, context)`:
     - If `context.agent_class` is None, return no issues.
     - Compute `hidden_methods = get_hidden_method_names(context.agent_class)` and `hidden_fields = get_hidden_field_names(context.agent_class)`.
     - Run an AST visitor that:
       - On `visit_Call`: if call is `self.<attr>(...)` and `attr in hidden_methods`, add error.
       - On `visit_Attribute` (load): if `self.<attr>` and `attr in hidden_fields`, add error.
       - On `visit_Assign` (targets): if any target is `self.<attr>` and `attr in hidden_fields`, add error.
     - Resolve “self” by checking `ast.Name(id="self")` and confirming it’s the agent in `exec_globals` (or track aliases for v2).

3. **Integration:** Add `HiddenMemberValidator()` to `UnifiedCodeValidator`’s default list (after security and blocking-call validators).

4. **Tests:** Add tests that:
   - Agent with `@hidden` method and `Annotated[T, hidden]` field; code that references them fails validation with a clear message.
   - Code that uses only public members passes.

5. **Docs:** Update AGENTS.md (or visibility doc) to state that hidden members are not only omitted from `doc(self)` but also **forbidden** in generated code when this validator is enabled.

---

---

## Hidden fields on arbitrary types (e.g. Pydantic models)

**Scenario:** User defines a type (e.g. Pydantic model) with both visible and hidden fields:

```python
class MyType(BaseModel):
    x: int                          # LLM sees this
    secret: Annotated[str, hidden]   # LLM doesn't see this

class MyAgent(Agent):
    def get_result(self) -> MyType:
        ...
```

**Requirements:**

1. **doc(MyType)** (and doc(self) when it shows return types) should **not** show `secret` — only `x`.
2. **Structured output** when the method return type is `MyType`: the schema sent to the LLM must not include `secret`; the LLM must not be able to set or see it.
3. **Generated code** that has `MyType` in scope should not be able to access `obj.secret` (block at validation or runtime).

**Approaches:**

| Concern | Approach | Notes |
|--------|----------|--------|
| **doc()** | Optional **field filter** in agentdoc: when extracting type info, exclude fields where a predicate returns True (e.g. `is_hidden_field(cls, name)`). Thread via `DocConfig.field_visibility(cls, name) -> bool` or `extract_type_info(..., field_filter=...)`. Agent006 injects `doc` with a config that uses `visibility.is_hidden_field` so `doc(MyType)` shows only public fields. | Agentdoc stays generic (no dependency on agent006); agent006 supplies the filter when calling doc. |
| **Structured output** | When `_create_response_model` receives a Pydantic model that has hidden fields: build a **public-only** model (e.g. `create_model` with only non-hidden fields), use that as `output_model` for the LLM. After validation, instantiate the original type with validated data; hidden fields get defaults. | Implement in agent006 `structured_output.py`. |
| **Code access** | Extend the hidden-member validator: block `obj.attr` when the static type of `obj` has `attr` as a hidden field. Requires type tracking (e.g. names assigned from `MyType(...)` or from functions returning `MyType`). Conservative alternative: block any `.attr` where `attr` is a hidden field on any type in `exec_globals`. | Same validator as agent `self.*`; extend to tracked types or conservative global set. |

**Summary:** Use the same `Annotated[T, hidden]` marker for types and agents. doc() and structured output can be implemented with a small agentdoc extension (field filter) and changes in agent006; code blocking for non-agent types is a follow-up (type tracking or conservative name set).
