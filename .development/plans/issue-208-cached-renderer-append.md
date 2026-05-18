# Issue 208 — CachedRenderer must not mutate the trailing event message

Issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/208

## Problem (recap)

`CachedBlockFormatter.format` in `src/nemo_oo_agents/context_blocks/renderers/cached.py:125-147` has two emission branches for the dynamic `<context>...</context>` envelope:

- **fast path** — when the trailing message is a plain user-event (no tool_call, no tool_call_id), merge the envelope **into that event's `content` and `parts`** (lines 125-147).
- **fallback** — otherwise (assistant tool_call message, tool result, or no trailing event at all), append the envelope as a **new** `RenderedMessage(role=USER)` (lines 148-151).

The historical user-event byte content therefore differs between consecutive LLM calls in a long agent loop: at call *N* the merge fires, the envelope is appended to msg[k]; at call *N+1* msg[k] is no longer the last event (a fresh tool_result is), so the merge fires elsewhere — and msg[k] reverts to its pre-merge bytes. OpenAI's automatic prompt cache hashes from the divergence point onward and re-bills every following token. The issue measured this at ~62.7% cache-hit vs ~90.8% on a comparable codex baseline (~$1.2K cost delta on a 301-trial gpt-5.5 run).

Anthropic auto-caching is impacted by the same divergence (Anthropic places `cache_control` breakpoints and any byte change downstream of the breakpoint invalidates the segment).

## Fix

Drop the merge branch entirely. The dynamic `<context>` envelope is **always** appended as its own trailing `RenderedMessage(role=USER, content=suffix, parts=envelope_parts)`. This is the existing fallback path; we just always take it.

Effect on the rendered message sequence:

| Previous trailing event | Before | After |
|---|---|---|
| (none, e.g. only system blocks + dynamic) | `[system, user(<context>)]` | unchanged |
| `assistant` (tool_call or plain) | `[..., assistant, user(<context>)]` | unchanged |
| `tool` (tool_result) | `[..., tool, user(<context>)]` | unchanged |
| `user` (plain UserEvent) | `[..., user(event+<context>)]` (merge) | `[..., user(event), user(<context>)]` (extra msg) |

Only the bottom row changes shape: the trailing user-event is now followed by a separate `<context>` user message instead of having the envelope concatenated into it.

### Consumer impact of the extra message

- **OpenAI Chat Completions** (`OpenAIProviderFormatter`, formatter.py:412-449): emits two consecutive `{"role": "user", ...}` dicts. The Chat Completions API explicitly allows this; it does not enforce strict alternation. **Safe.**
- **Anthropic Messages** (`AnthropicProviderFormatter`, formatter.py:452-503): emits two consecutive `{"role": "user", ...}` dicts. The Anthropic Messages API documents that "the first message must use the user role" and "if the final message uses the assistant role, the response content will continue immediately from the content in that message" — it does **not** forbid consecutive same-role messages. Models treat them as two turns of user content. **Safe.**
- **Responses API** (`ResponsesProviderFormatter`, formatter.py:506+): plain user messages emit as `{"role": "user", "content": ...}` dicts; consecutive entries are allowed. **Safe.**
- **Journal publisher / trace viewer**: walks `parts` per message. The block-key (`BlockPart.key`) of the user-event and of each context block is unchanged; the trace viewer now sees two messages instead of one, but each with the same per-block content hashes as before. The reconstructed structure remains content-addressable.
- **Tests**: `tests/context_blocks/test_cached_renderer.py:113` (`test_volatile_merges_into_trailing_user_event`) encodes the **buggy** behavior. It must be replaced by a test that asserts the new shape: trailing user-event is preserved verbatim, with a separate USER message containing the `<context>` envelope appended.

### Alternatives considered (and rejected)

- **Keep the merge, gate it on whether the trailing event is "new vs historical"**: requires per-call state tracking that the renderer is intentionally stateless about. Still has edge cases at summarizer firings (the "last user event" identity can change without any tool turn).
- **Render `<context>` into a synthetic `context`-role message**: not supported by either provider's chat schema.
- **Coalesce in the provider formatter**: would need formatter-side state and is more invasive than dropping a 20-line branch in the renderer.

## Implementation steps

1. In `src/nemo_oo_agents/context_blocks/renderers/cached.py`:
   - Delete lines 125-147 (the merge branch).
   - Replace the surrounding `if/else` with the unconditional `messages.append(RenderedMessage(role=Role.USER, content=suffix, parts=envelope_parts))`.
   - Update the module docstring (lines 7-11) and the class docstring (lines 70-72) to drop the "merged into the last event message if that is already user-role" claim. Replace with: "emitted as a trailing USER message — appended unconditionally to keep byte content of historical messages stable across consecutive renders, which is what enables provider-side prompt caching to hit on the full event tail."

2. In `tests/context_blocks/test_cached_renderer.py`:
   - Replace `test_volatile_merges_into_trailing_user_event` (lines 113-137) with `test_volatile_appended_after_trailing_user_event`:
     - Same input blocks (static `sys`, dynamic `plan`, trailing UserEvent "hi").
     - Expected output: `roles == ["system", "user", "user"]`.
     - `result[1]["content"]` equals exactly the rendered UserEvent — no `<context>`, no `\n\n` suffix.
     - `result[2]["content"]` contains `<context>` and `<plan>` and nothing of the original user-event text.
   - Add a regression test `test_trailing_user_event_byte_stable_across_renders`:
     - Render the same `[static_sys, dynamic_plan, user_event_A]` blocks twice with the same `CachedBlockFormatter` instance and two different `dynamic_plan` values (simulating dynamic-context churn between turns). Assert the bytes of the user_event_A message in the output are identical across both renders.
     - Then render `[static_sys, dynamic_plan_v2, user_event_A, tool_call+tool_result]` (simulating: same historical event, fresh tool turn appended, different dynamic value). Assert user_event_A's bytes in the output are still identical to the first render. This directly captures the bug: under the old code, the merge fires on render 1+2 and not on render 3, producing two different byte representations of the same logical user_event_A.

3. Verification:
   - `uv run pytest tests/context_blocks/test_cached_renderer.py -x -q`
   - `uv run pytest -x -q`
   - `uv run ruff check src/nemo_oo_agents/context_blocks/renderers/cached.py tests/context_blocks/test_cached_renderer.py`

## Acceptance criteria mapping

- [x] Dynamic `<context>` is no longer concatenated into a historical event message — guaranteed because the merge branch is removed.
- [x] Trailing-event byte content is stable across consecutive renders — covered by the new `test_trailing_user_event_byte_stable_across_renders` regression test.
- [x] Static-prefix caching still works — the static SYSTEM message construction is untouched.
- [x] Existing public behavior (when there is no trailing user-event, e.g. trailing assistant or trailing tool result) is unchanged — already used the same `messages.append(...)` path.

## Out of scope

- A formatter-level pass that coalesces consecutive same-role messages into one (useful if providers grow stricter alternation requirements). Not needed today.
- Changing where the `<context>` envelope is positioned (head vs tail) — keep it trailing so that the dynamic content is what the model attends to most recently.
- Issue #193 (NemoFlow-emitted usage stats) — independent.
