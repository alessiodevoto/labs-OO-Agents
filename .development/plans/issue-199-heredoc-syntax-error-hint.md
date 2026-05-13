# Issue 199 — Better SyntaxError message for bash heredocs in shell.run()

Issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/199

## Problem

Agents frequently write code like:

```python
await shell.run("cat <<EOF
content
EOF")
```

Python's parser rejects the unterminated single/double-quoted string with one of three messages, none of which mentions heredocs or the `<<` token:

- `unterminated string literal (detected at line 2)`
- `unexpected character after line continuation character`
- `invalid syntax. Perhaps you forgot a comma?`

Telemetry (per issue): of 11 agents that hit this pattern, **4 (~36%) hit max iterations** because the error gives them nothing actionable.

Heredocs already work fine through a triple-quoted Python string — the only failure mode is the *quoting choice*, not the heredoc itself. So the fix is purely a better error message; no runtime behavior change.

## Intervention point

`src/nemo_oo_agents/errors/formatting.py:135-157` — `IPythonErrorFormatter._format_syntax_error`. This is where every SyntaxError surfaced to the LLM is built. Append a hint *after* the existing formatted message when we detect a heredoc-shaped source line combined with one of the three diagnostic messages above.

Why here and not at `ast.parse()` in `actor.py:1151-1155`: the formatter is the single funnel for *all* SyntaxErrors shown to the LLM (compile errors during `ast.parse`, the `compile()` step later, validator parse errors, etc.). Adding the check at the parse site would duplicate the logic across each call site and skip errors surfaced from other compile points.

## Detection logic

1. After the existing formatted text is built, look at `error.msg` and compare against a fixed set:
   - `"unterminated string literal"` (substring match — Python appends the line number)
   - `"unexpected character after line continuation character"`
   - `"invalid syntax. Perhaps you forgot a comma?"`

2. If `error.msg` matches one of those, check `error.text` (the offending line that Python attaches) for a heredoc marker. The regex used is `r"<<-?\s*['\"]?\w+['\"]?"`, which covers `<<EOF`, `<< EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`, etc. Note that `\w+` also matches numeric shifts like `<< 2` — the false-positive guard is the quote check in step 3, not the regex.

3. Require a `"` or `'` to appear *before* the `<<` on the same line — this is the tighter check that suppresses real bit-shift false positives like `func(a << foo b)` (no quote before `<<`). For the LLM-embedded-heredoc pattern we're targeting, both tokens are necessarily on the same source line as `error.text` (the LLM wrote them inside one broken string literal), so a broader source-window scan is unnecessary.

4. If matched, append (separated by a blank line) a single hint block:

   ```text
   Hint: this looks like a bash heredoc (`<<…`) embedded in a single- or double-quoted
   Python string. Python rejects multi-line single/double-quoted strings, which is
   why the parser errored before reaching the heredoc body.

   Fix 1 (preferred) — use a triple-quoted Python string so the newlines are legal:
       await shell.run("""cat <<EOF
       content
       EOF""")

   Fix 2 — write the script to a file first, then run it:
       await shell.write("/tmp/script.sh", script_text)
       await shell.run("bash /tmp/script.sh")
   ```

   Hint text lives as a module-level constant so it is testable.

5. Do nothing otherwise — every other SyntaxError flows through unchanged.

## Files to touch

1. `src/nemo_oo_agents/errors/formatting.py`
   - Add two module-level constants near the top: the trigger-message tuple and the heredoc regex.
   - Add a module-level constant `_HEREDOC_HINT` with the hint text.
   - Add a small private helper `_maybe_append_heredoc_hint(error, code) -> str | None` that returns the hint text if the heuristic matches, else `None`.
   - In `_format_syntax_error`, after the existing line-number adjustment, append the hint if `_maybe_append_heredoc_hint` returns one.

2. `src/nemo_oo_agents/tools/shell_tools.py` — **deferred** to a follow-up MR.
   The plan originally included a one-example docstring tweak here. At commit time the file had a pre-existing pyright failure (`StreamEvent(kind=stream_name, ...)` where `stream_name: str` but `kind: Literal['stdout', 'stderr']`) that fires whenever the file is touched. The docstring is explicitly listed as an optional follow-up in the issue — deferring keeps this MR focused on the actual error-message change.

3. `tests/test_error_formatting.py`
   - Add a `TestHeredocHint` class with the following tests:
     - **3 positive**: one per trigger message. Construct real `SyntaxError` instances by `compile()`-ing source that produces each message and contains a heredoc marker (verified live in this branch — all three triggers reproduce reliably with `shell.run("cat <<EOF...")`, `shell.run("cat <<EOF" b)`, `shell.run("cat <<EOF" \xyz)`). Assert the result contains `"heredoc"` and both `"""` (Fix 1) and `shell.write` (Fix 2).
     - **1 negative — same trigger msg, no heredoc**: same trigger message but the source line is plain (e.g. `'x = "hello'` produces `unterminated string literal` without `<<`). Assert the hint is *not* appended (no `"heredoc"` substring; no Fix 1/Fix 2 markers).
     - **1 negative — heredoc-looking source, unrelated SyntaxError**: e.g. `'x = << 2'` parsed in a context that produces bare `invalid syntax`. Assert no hint appended.
     - **1 negative — bit-shift with identifier RHS** (per reviewer): construct a `SyntaxError` (or `compile()` source) with msg in the trigger set but where the `<<` is actually a legitimate bit-shift, e.g. `'func(a << foo b)'` (real call → `Perhaps you forgot a comma?`). Assert no hint appended. *(Note: with the proposed regex, `<<\s*foo` would match and the hint would be appended. This is an acknowledged false positive. The plan's mitigation is to keep the hint short and additive (no diagnostic replacement). If the test reveals this is loud, we tighten the regex by requiring `"` or `'` to appear on the same line before the `<<` — i.e. the heredoc must look like it's embedded in a string. Decision: try the looser regex first and tighten only if this negative test fails meaningfully.)*
     - **1 negative — wholly unrelated SyntaxError with `<<EOF` in source**: e.g. an `'await' outside function` error in source that happens to contain `<<EOF`. Assert no hint appended. This proves the gate is `msg ∈ TRIGGERS`, not just `source contains <<`.

## Broader coverage note

`_format_syntax_error` is also reached from validator parse errors and the late `compile()` call in `actor.py`. This is a feature, not a bug — any place a SyntaxError surfaces to the LLM benefits from the same hint. No changes required at those call sites.

## Subtleties verified up front

- **Which of the three messages does Python actually produce for the canonical bad case?** On 3.12 `"cat <<EOF\ncontent\nEOF"` produces `unterminated string literal (detected at line 1)`. The other two messages occur with slight variations (e.g. backslash continuations, no closing quote on a one-liner). All three are in the issue's telemetry, so the test must cover each — we construct them by:
  - `unterminated string literal`: `'await shell.run("cat <<EOF\ncontent\nEOF")'`
  - `unexpected character after line continuation character`: `'x = "cat <<EOF\\\n"'` (backslash before newline inside the literal)
  - `invalid syntax. Perhaps you forgot a comma?`: `'await shell.run("cat <<EOF" "content" "EOF")'` — this is the shape Python suggests a missing comma for. If we can't reliably produce this message via `compile()`, we construct a `SyntaxError` instance manually with `msg=` set and `text=` set, which is what the formatter already accepts (see existing `test_syntax_error_from_code_string`).
- **Negative test for the same SyntaxError without heredoc**: must produce *exactly* the same `error.msg` but with `error.text` that does not contain `<<\w+`. This proves the hint is gated on the source-line check, not just the message.
- **`error.text` vs `code`**: `error.text` is only the single offending line. Some heredoc patterns will have `<<` on that line (the bad case in the issue) but for the "missing comma" shape it might be on a later line. We therefore also scan a window of `code` around `error.lineno` when `code` is provided.
- **Line offset / Cell formatting**: the hint is appended *after* `_adjust_line_numbers`. No need to re-adjust — the hint text contains no line references.
- **Existing tests**: nothing in the file currently asserts on the exact end of `_format_syntax_error` output (they use `in` checks), so appending text won't break them. Verify by running the file before and after.

## Test plan

```bash
uv run pytest tests/test_error_formatting.py -x -q
uv run pytest tests/test_execute_code_error_recovery.py tests/test_error_line_numbers.py -x -q
uv run pytest -x -q
uv run ruff check src/nemo_oo_agents/errors/formatting.py src/nemo_oo_agents/tools/shell_tools.py tests/test_error_formatting.py
```

## Acceptance criteria mapping (from the issue)

- [x] Heredoc inside a single-quoted string → error mentions heredoc + both fix patterns.
  Covered by the 3 positive tests.
- [x] Unrelated SyntaxErrors unchanged.
  Covered by the two negative tests; existing tests in the file continue to pass.
- [x] Unit test per SyntaxError variant + heredoc + a negative.
  Five tests; meets the requirement.

## Out of scope (matching the issue's "optional follow-ups")

- **`heredoc_syntax_error` counter in `harness_metrics`**. The issue lists it as optional; deferring keeps this MR small and focused on the user-visible error fix. Worth a follow-up issue.
- Anything that auto-rewrites the LLM's code. The issue explicitly notes this is fragile (distinguishing intentional heredocs from genuinely broken strings) and not in scope.
