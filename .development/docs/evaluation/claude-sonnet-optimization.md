# Claude Sonnet Code Generation Optimization for Agent006

**Date**: 2026-01-17
**Model**: Claude Sonnet 4.5 (aws/anthropic/bedrock-claude-sonnet-4-5-v1)
**Baseline**: nemo_oo_agents with PurePythonStrategy - 10% pass rate (1/10 tasks)

## Problem Analysis

Analyzed 88 code execution errors across 11 DABStep trace files. Claude Sonnet was failing to generate valid Python code for nemo_oo_agents's PurePythonStrategy.

### Top 5 Failure Patterns

1. **Conversational Text Instead of Pure Python (87.5% of errors)**
   - Claude outputs "I'll solve this...", "Let me check...", etc.
   - Entire text submitted to Python interpreter as-is
   - Causes: `SyntaxError: invalid syntax`

2. **Using Undefined `reasoning()` Function (39.8% of errors)**
   - Claude calls `reasoning("explanation...")` which doesn't exist
   - Appears to confuse internal thinking with executable code
   - Causes: `NameError` or syntax errors

3. **Unterminated String Literals (45.5% of errors)**
   - Multi-line `reasoning()` calls with unbalanced quotes
   - String starts with `reasoning("` but closing quote missing/malformed
   - Causes: `SyntaxError: unterminated string literal`

4. **Using Invalid FileTools Methods (62.5% of errors)**
   - Calls `self.files.read()` instead of `self.files.read_file()`
   - Guesses API based on conventions rather than actual methods
   - Causes: `AttributeError: 'FileTools' object has no attribute 'read'`

5. **Wrapping Code in Markdown Blocks (10.2% of errors)**
   - Outputs code inside ` ```python ... ``` ` markdown blocks
   - Markdown syntax submitted to interpreter
   - Causes: `SyntaxError: invalid syntax`

### Example Error

```
I'll solve this task by analyzing the payments data.

reasoning("This is a straightforward data analysis task...")

```python
df = pd.read_csv("/path/to/data.csv")
```
```

**Result**: `SyntaxError: invalid syntax` on line 1

## Solution Implemented

Created `nemo_oo_agents_claude_optimized.py` with:

### 1. Code Cleaning Function

```python
def _clean_claude_output(code: str) -> str:
    """Clean Claude's output to extract pure Python code."""
    # Remove markdown code blocks
    code = re.sub(r'```python\n', '', code)
    code = re.sub(r'```\n?', '', code)

    # Remove reasoning() calls
    code = re.sub(r'reasoning\s*\([^)]*\)\s*\n?', '', code, flags=re.DOTALL)

    # Remove conversational prefixes
    patterns = [r'^I\'ll .*$', r'^Let me .*$', r'^I need to .*$']
    for pattern in patterns:
        code = re.sub(pattern, '', code, flags=re.MULTILINE)

    return textwrap.dedent(code)
```

### 2. Enhanced System Prompt

Added explicit instructions in `solve_task()` docstring:

```python
CRITICAL CODE GENERATION RULES:
- OUTPUT ONLY EXECUTABLE PYTHON CODE - no explanations, no markdown
- DO NOT use conversational phrases like "I'll...", "Let me..."
- DO NOT wrap code in markdown blocks (```)
- DO NOT call reasoning() - it doesn't exist
- DO NOT generate explanatory text before or after code

Available Tools:
- self.files.read_file(path: str) -> str : Read file contents
- self.files.write_file(path: str, content: str) : Write file
- pd.read_csv(path) : Load CSV data
- json.load(open(path)) : Load JSON data
```

### 3. Validation Pipeline

```python
def _validate_python_syntax(code: str) -> str:
    # Clean Claude-specific artifacts
    code = _clean_claude_output(code)

    # Validate syntax
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Invalid Python syntax at line {e.lineno}: {e.msg}")
    return code
```

### 4. Correct API Examples

Provided working examples showing:
- Correct FileTools API (`self.files.read_file()` not `self.files.read()`)
- Pure Python output format (no markdown, no explanations)
- Proper imports and data loading patterns

## Expected Improvements

Based on the failure analysis:

1. **Eliminates 87.5% of conversational text errors** via regex cleaning
2. **Eliminates 39.8% of reasoning() errors** via removal
3. **Fixes 45.5% of unterminated string errors** via cleaning
4. **Reduces 62.5% of API errors** via explicit documentation
5. **Eliminates 10.2% of markdown errors** via stripping

**Conservative Estimate**: 50-70% error reduction
**Target**: 30-40% pass rate (3-4 tasks passing instead of 1)

## Testing

### Baseline (nemo_oo_agents)
- Config: `nemo_oo_agents` with PurePythonStrategy
- Model: Claude Sonnet 4.5
- Result: **10% pass rate (1/10)**
- Primary failure: 8/10 tasks failed with "Unable to generate valid code"

### Optimized (nemo_oo_agents_claude_opt)
- Config: `nemo_oo_agents_claude_opt` with cleaned code
- Model: Claude Sonnet 4.5
- Status: **Running...**

## Usage

```bash
python run_ablation.py \\
  --provider nvidia_internal \\
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 \\
  --benchmark dabstep \\
  --config nemo_oo_agents_claude_opt \\
  --limit 10
```

## Future Improvements

If this approach works:

1. **Apply to other models**: Test if Qwen/GPT also benefit from cleaning
2. **Fine-tune regex patterns**: Adjust based on new failure modes
3. **Add retry with stricter prompt**: If cleaned code still fails, retry with "ONLY OUTPUT PYTHON CODE" prefix
4. **Model-specific strategies**: Create cleaning functions per model family
5. **Integration into PurePythonStrategy**: Add cleaning as a built-in preprocessing step

## References

- Trace analysis agent ID: `a826280`
- Baseline results: `/Users/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20260116_104108_bedrock-claude-sonnet-4-5-v1_2d807c/`
- Implementation: `/Users/rcabral/nemo_oo_agents/experiments/evaluation-ablations/agents/nemo_oo_agents_claude_optimized.py`
