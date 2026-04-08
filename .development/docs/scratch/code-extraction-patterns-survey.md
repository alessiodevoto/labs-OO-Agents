# Code Extraction Patterns Survey

**Date:** 2026-02-18
**Purpose:** Document all code extraction patterns across the codebase to understand implementation differences and identify issues.

## Summary Table

| File | Pattern | Handles Nested? | Languages | Special Logic | Status |
|------|---------|----------------|-----------|---------------|--------|
| **reflector.py** | `r"```python:(\S+)\n(.*?)```"` + `r"```python\n(.*?)```"` | ❌ No | Python only | Multiple passes: named blocks first, then generic blocks, fallback to first match for single-file | ⚠️ **BROKEN** - non-greedy won't capture nested blocks |
| **livecodebench.py** | `r"```(?:python)?\s*\n(.*?)\n```"` | ❌ No | Python only | **Takes last match** (critical for self-repair scenarios) | ✅ Working |
| **bigcodebench.py** | `r"```(?:python)?\s*\n(.*?)\n```"` | ❌ No | Python only | Takes last match | ✅ Working |
| **metaclass.py** | N/A - No extraction | N/A | N/A | Uses `inspect.getsource()` for source code | ✅ N/A |
| **scoring.py** | Trace-based extraction (JSON parsing) | N/A | Python (via trace) | Extracts from OpenTelemetry trace spans, supports prefill filtering | ✅ Working |
| **capability_tests.py** | Concatenates event history | N/A | N/A | Joins all `llm_output` events with separator | ✅ Working |
| **terminal_bench.py** | `r"```(?:bash\|shell\|sh)?\n(.*?)```"` | ❌ No | Bash/Shell | Extracts bash commands, filters out comments, also matches `$` prefix | ✅ Working |
| **optimizer.py** | `r"```python\n(.*?)\n```"` | ❌ No | Python only | Takes longest match if multiple blocks found | ✅ Working |

## Detailed Analysis

### 1. reflector.py (E2E Optimization)
**Location:** `util/e2e_optimization/src/e2e_optimization/reflector.py`
**Lines:** 288-329

#### Patterns Used
```python
# Pattern 1: Named blocks with filename
pattern_with_file = r"```python:(\S+)\n(.*?)```"

# Pattern 2: Generic python blocks (fallback)
pattern_generic = r"```python\n(.*?)```"
```

#### Extraction Logic
```python
def _extract_code_blocks(self, content: str, expected_files: list[str]) -> dict[str, str]:
    result = {}

    # Try named blocks first
    pattern_with_file = r"```python:(\S+)\n(.*?)```"
    matches = re.findall(pattern_with_file, content, re.DOTALL)

    for filename, code in matches:
        if not filename.endswith(".py"):
            filename = filename + ".py"
        result[filename] = code.strip()

    # Fallback to generic blocks
    if not result:
        pattern_generic = r"```python\n(.*?)```"
        matches = re.findall(pattern_generic, content, re.DOTALL)

        if matches and len(matches) == len(expected_files):
            # Assume order matches expected files
            for filename, code in zip(expected_files, matches, strict=True):
                result[filename] = code.strip()
        elif matches and len(expected_files) == 1:
            # Single file case - use first match
            result[expected_files[0]] = matches[0].strip()

    return result
```

#### Issues
- **Non-greedy `(.*?)` won't capture nested code blocks** - this is the reported bug
- Uses `re.DOTALL` to match across newlines, but non-greedy will stop at first `\`\`\``
- Only works for single-level code blocks, not nested ones

#### Example Failure Case
```python
# LLM generates:
"""
Here's the code:
```python
def foo():
    '''
    This docstring mentions:
    ```
    some example
    ```
    '''
    pass
```
"""

# Pattern matches up to first ``` and fails to capture the rest
```

---

### 2. livecodebench.py (LiveCodeBench Adapter)
**Location:** `evaluation/adapters/livecodebench.py`
**Lines:** 463-494

#### Pattern Used
```python
code_block_pattern = r"```(?:python)?\s*\n(.*?)\n```"
```

#### Extraction Logic
```python
def _extract_code(self, output: Any) -> str | None:
    # Handle various output formats (dict, str, etc.)
    # ...get text...

    # Try to extract code from markdown code blocks
    import re

    code_block_pattern = r"```(?:python)?\s*\n(.*?)\n```"
    matches = re.findall(code_block_pattern, text, re.DOTALL)
    if matches:
        return matches[-1].strip()  # Return last code block

    # If no code blocks, return the whole text (might be just code)
    return text.strip()
```

#### Special Features
- **Takes last match** - critical for self-repair scenarios where LLM may include both old and new code
- Optional `python` language tag: `(?:python)?`
- Whitespace flexibility: `\s*` after language tag
- Falls back to entire text if no code blocks found

#### Why It Works
- Non-greedy `(.*?)` is acceptable because it's not designed to handle nested blocks
- Last-match behavior is explicitly desired for the use case
- Simple and predictable for single-level markdown blocks

---

### 3. bigcodebench.py (BigCodeBench Adapter)
**Location:** `evaluation/adapters/bigcodebench.py`
**Lines:** 305-327

#### Pattern Used
```python
code_block_pattern = r"```(?:python)?\s*\n(.*?)\n```"
```

#### Extraction Logic
```python
def _extract_code(self, output: Any) -> str | None:
    # Handle dict/str conversions
    # ...

    # Try to extract from markdown code blocks
    import re

    code_block_pattern = r"```(?:python)?\s*\n(.*?)\n```"
    matches = re.findall(code_block_pattern, text, re.DOTALL)
    if matches:
        return matches[-1].strip()

    return text.strip()
```

#### Features
- Identical pattern to livecodebench.py
- Takes last match
- Simple fallback to text

#### Status
✅ Working as intended for single-level code blocks

---

### 4. metaclass.py (AgentMeta)
**Location:** `src/agent006/metaclass.py`
**Lines:** 143-149

#### Extraction Method
```python
@staticmethod
def _extract_source_code(func: Callable) -> str | None:
    """Extract source code from function, returning None if unavailable."""
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return None
```

#### Features
- Not a regex-based extraction
- Uses Python's `inspect` module to get actual source code
- Used for tracing, not for parsing LLM responses
- Different use case than the others

#### Status
✅ N/A - not comparable to markdown extraction patterns

---

### 5. scoring.py (Eval Pipeline)
**Location:** `util/eval_pipeline/src/eval_pipeline/scoring.py`
**Lines:** 85-204

#### Extraction Method
Trace-based, not regex-based. Parses OpenTelemetry JSONL trace files.

```python
def extract_code_from_trace(trace_file: Path, *, skip_prefill: bool = False) -> str | None:
    """Extract generated code from trace file.

    Works with both strategies:
    - PurePythonStrategy: Extracts direct LLM output (generated code)
    - CodeAct: Extracts code_execution spans only (NOT return_result)
    """
    # Parse JSONL trace file
    for line in trace_file.read_text().splitlines():
        span = json.loads(line)
        span_name = span.get("name", "")
        attrs = span.get("attributes", {})

        # Look for code_execution spans
        if span_name == "code_execution" and "code" in attrs:
            code = attrs["code"]
            # Optional prefill filtering
            if skip_prefill and code.strip().startswith('reasoning("""Let me inspect'):
                continue
            code_blocks.append(code)

        # Look for direct LLM output (PurePythonStrategy)
        for key in ["llm.output", "gen_ai.completion", "output.value"]:
            if key in attrs and attrs[key]:
                direct_output = attrs[key]
```

#### Features
- Completely different approach - parses trace files, not markdown
- Handles two different strategies (PurePythonStrategy, CodeAct)
- Supports `skip_prefill` to filter out prefill code
- Deduplicates code blocks while preserving order
- Distinguishes between "generated code" and "executed code"

#### Status
✅ Working - different use case, no nested block issues

---

### 6. capability_tests.py (Prompt Optimization)
**Location:** `util/prompt-optimization/evaluators/capability_tests.py`
**Lines:** 28-35

#### Extraction Method
```python
def _extract_code(self, agent: Any) -> str:
    """Extract generated code from agent history."""
    events = agent.event_manager.values()
    turns = []
    for event in events:
        if hasattr(event, "event_type") and event.event_type == "llm_output":
            turns.append(event.content)
    return "\n\n-----\n\n".join(turns)
```

#### Features
- Event-based extraction from agent history
- Not a regex pattern
- Joins all LLM output events with separator
- Used for method judgment, not final code extraction

#### Status
✅ Working - different approach, no nested block issues

---

### 7. terminal_bench.py (TerminalBench Adapter)
**Location:** `evaluation/adapters/terminal_bench.py`
**Lines:** 488-502

#### Pattern Used
```python
# Pattern for bash/shell blocks
bash_blocks = re.findall(r"```(?:bash|shell|sh)?\n(.*?)```", text, re.DOTALL)

# Pattern for $ prefix
dollar_lines = re.findall(r"^\s*\$\s*(.+)$", text, re.MULTILINE)
```

#### Extraction Logic
```python
def _extract_commands(self, output: Any) -> list[str]:
    text = str(output)

    commands = []

    # Match ```bash or ```shell blocks
    bash_blocks = re.findall(r"```(?:bash|shell|sh)?\n(.*?)```", text, re.DOTALL)
    for block in bash_blocks:
        for line in block.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)

    # Match $ prefixed lines
    dollar_lines = re.findall(r"^\s*\$\s*(.+)$", text, re.MULTILINE)
    commands.extend(dollar_lines)

    return commands
```

#### Features
- Multi-language support: bash, shell, sh
- Filters out comment lines (starting with `#`)
- Also extracts `$`-prefixed command lines
- Returns list of individual commands, not block

#### Status
✅ Working - different use case (bash commands, not nested Python blocks)

---

### 8. optimizer.py (E2E Optimization)
**Location:** `util/e2e_optimization/src/e2e_optimization/optimizer.py`
**Lines:** 2130-2143

#### Pattern Used
```python
blocks = re.findall(r"```python\n(.*?)\n```", response, re.DOTALL)
```

#### Extraction Logic
```python
# Fallback: look for any ```python blocks with regex (for simpler cases)
blocks = re.findall(r"```python\n(.*?)\n```", response, re.DOTALL)
if blocks:
    longest = max(blocks, key=len)
    return {"__single__": longest.strip()}

# Last resort: treat entire response as code
return {"__single__": response.strip()}
```

#### Features
- Takes **longest match** if multiple blocks found
- Used as fallback after more sophisticated extraction attempts
- Includes last-resort fallback to entire response

#### Status
✅ Working - takes longest, handles most real-world cases

---

## Key Findings

### Problem Pattern: Non-Greedy with Nested Blocks
The fundamental issue with `reflector.py` is:

```python
r"```python\n(.*?)```"  # Non-greedy - stops at first ```
```

When the LLM generates code with nested backticks (docstrings, comments, examples), the non-greedy `(.*?)` stops at the first closing `\`\`\``, missing the rest of the code block.

### Working Patterns

Most adapters use the same pattern but **don't need to handle nested blocks**:
- They extract simple, single-level code blocks
- They process external benchmark data, not multi-turn reflections
- The "last match" or "longest match" heuristics work fine

### Successful Alternatives

1. **Trace-based extraction** (scoring.py)
   - Parse structured trace data instead of markdown
   - No regex pattern matching needed
   - Most reliable for runtime code capture

2. **Event-based extraction** (capability_tests.py)
   - Extract from agent event history
   - Captures exact LLM outputs
   - Good for debugging and analysis

3. **Source code inspection** (metaclass.py)
   - Use Python's `inspect.getsource()`
   - Only works for actual Python functions
   - Perfect for static code analysis

## Recommendations

### For reflector.py
The current pattern cannot handle nested code blocks. Options:

1. **Use a proper parser** - Parse markdown structure or use balanced parentheses matching
2. **Switch to greedy with end anchor** - `r"```python\n(.*)\n```"` (but risky)
3. **Manual parsing** - Track backtick pairs and balance them
4. **Trace-based extraction** - Like scoring.py, extract from execution traces instead

### Pattern Standardization
Consider creating a shared utility module:

```python
# util/code_extraction.py

def extract_code_blocks(text: str, language: str = "python") -> list[str]:
    """Extract code blocks with support for nested backticks.

    Args:
        text: Text containing markdown code blocks
        language: Language identifier (python, bash, etc.)

    Returns:
        List of code blocks in order of appearance
    """
    # Implement robust extraction with proper nesting support
    pass

def extract_last_code_block(text: str, language: str = "python") -> str | None:
    """Extract last code block (common pattern in self-repair scenarios)."""
    blocks = extract_code_blocks(text, language)
    return blocks[-1] if blocks else None

def extract_named_code_blocks(text: str) -> dict[str, str]:
    """Extract code blocks with filenames (```python:filename.py)."""
    pass
```

This would:
- Centralize the logic
- Make it easier to fix issues once
- Provide consistent behavior across adapters
- Support proper nested block handling where needed

## References

### Related Files
- `util/e2e_optimization/src/e2e_optimization/reflector.py` - Broken extraction
- `evaluation/adapters/livecodebench.py` - Working, takes last
- `evaluation/adapters/bigcodebench.py` - Working, takes last
- `util/eval_pipeline/src/eval_pipeline/scoring.py` - Trace-based (best)
- `util/prompt-optimization/evaluators/capability_tests.py` - Event-based

### Test Cases to Add
```python
# Test nested docstrings
code_with_nested_docstring = '''
```python
def foo():
    """
    Example:
    ```
    some code
    ```
    """
    return 42
```
'''

# Test nested comments
code_with_nested_comment = '''
```python
# Example usage:
# ```python
# result = foo()
# ```
def foo():
    pass
```
'''

# Test multiple blocks
code_with_multiple_blocks = '''
Old code:
```python
def old():
    pass
```

New code:
```python
def new():
    pass
```
'''
```

These test cases should be used to validate any new extraction implementation.

---

## Resolution (2026-02-18)

**Status:** ✅ Fixed

**Solution:** Created shared `util/code_extraction` utility implementing "last valid match + AST validation" strategy.

### What Was Built

A production-ready utility that:
- Extracts Python code blocks from markdown
- **Handles nested backticks** using AST validation
- Uses "try greediest to least greedy, fallback to greediest" strategy
- Supports named blocks (````python:filename.py`)
- Zero external dependencies (Python stdlib only)

### Strategy Details

**Core Approach:**
1. Find ALL possible closing `\`\`\`` markers
2. Try each match from greediest (last) to least greedy (first)
3. Validate extracted code with `ast.parse()`
4. Use first valid match
5. If no valid match, fallback to greediest anyway

**Why This Works:**
- Valid Python → Gets the complete code block (not cut off at nested backticks)
- Invalid Python → Still extracts using greediest match (transparent to LLM errors)
- No false positives from partial matches

### Changes Made

**Created:**
- `util/code_extraction/` - Full module with package structure
- `src/code_extraction/extractor.py` - Core extraction logic (171 lines)
- `tests/test_extractor.py` - Comprehensive test suite (536 lines, 28 tests)
- `util/code_extraction/README.md` - Complete documentation

**Fixed:**
- `util/e2e_optimization/src/e2e_optimization/reflector.py` - Now uses utility (1 line replacement!)

**Test Results:**
```bash
cd util/code_extraction
pytest tests/ -v
# Result: ========================= 28 passed in 0.05s =========================
```

All tests pass, including:
- Nested backticks in docstrings ✅
- Multiple nesting levels ✅
- Real-world DABStep failure case ✅
- Named blocks ✅
- Self-repair scenarios (last block) ✅

### Migration Status

| Component | Status | Notes |
|-----------|--------|-------|
| **reflector.py** | ✅ Migrated | MR !361 - Works perfectly |
| **livecodebench.py** | Optional | Current pattern works, migration not urgent |
| **bigcodebench.py** | Optional | Current pattern works, migration not urgent |
| **optimizer.py** | Optional | Uses "longest match" heuristic, works fine |
| **terminal_bench.py** | N/A | Different use case (bash commands) |

### Verification

**Run the tests:**
```bash
cd util/code_extraction
pytest tests/ -v
```

**Use in your code:**
```python
from code_extraction import extract_named_code_blocks

# One line replaces 20+ lines of regex logic!
files = extract_named_code_blocks(text, expected_files=["agent.py"])
```

### Documentation

See `util/code_extraction/README.md` for:
- Installation instructions
- Usage examples
- API reference
- Migration guide
- Design decisions
- Testing instructions

### Key Takeaways

1. **Non-greedy regex `(.*?)` cannot handle nested markers** - It's not a bug in usage, it's a fundamental limitation
2. **AST validation is the key** - It tells us when we have complete, valid code
3. **Fallback strategy matters** - We don't want to hide invalid LLM outputs
4. **Shared utilities prevent duplication** - 8+ locations in codebase had similar patterns
5. **Real-world testing is critical** - The DABStep failure case was the perfect test

### Future Work

**Optional Migrations:**
- Migrate other adapters to use shared utility (for consistency)
- Add support for other languages (JavaScript, Bash, etc.)
- Consider streaming API for very large responses

**Not Needed:**
- The core problem is solved
- reflector.py (the broken component) is fixed
- All tests pass, including real-world failure cases
