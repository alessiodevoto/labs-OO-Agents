# AST Validator Enhancements Design

**Date**: 2025-01-26
**Status**: Proposal

## Executive Summary

The current Agent006 AST validation system is split across three modules with overlapping concerns:
1. `src/nemo_oo_agents/runtime/validator.py` - Security/safety validation (291 lines)
2. `src/nemo_oo_agents/runtime/async_safety.py` - Async deadlock prevention (492 lines)
3. `src/nemo_oo_agents/strategies/generated_code.py` - REPL policy validation (689 lines, partial)

This proposal outlines enhancements to consolidate validation logic, improve error messages, add missing validations, and optimize performance.

---

## Current Architecture Analysis

### Module 1: `validator.py` - Planning Language Validator

**Purpose**: Prevents security risks and operational hazards in LLM-generated code.

**Current Validations**:
| Rule | Implementation | Error Quality |
|------|----------------|---------------|
| Forbidden builtins (`exec`, `eval`, `compile`, `__import__`, `input`, `breakpoint`) | `visit_Call` | Good - IPython-style |
| Import restrictions (only whitelisted modules) | `visit_Import`, `visit_ImportFrom` | Good - shows available modules |
| Recursive self-call prevention | `visit_Call` with `forbidden_self_calls` | Good |
| `from X import *` prohibition | `visit_ImportFrom` | Good |
| Aliased builtin tracking | `visit_ImportFrom` + `visit_Call` | Good |

**Strengths**:
- Clean IPython-style error formatting with source context and caret
- Execution count tracking (`Cell In[N]`)
- Comprehensive forbidden builtin list

**Weaknesses**:
- No validation for dangerous attribute access (e.g., `__class__.__bases__`)
- No detection of `globals()` / `locals()` abuse
- Single-error reporting (stops at first error)

---

### Module 2: `async_safety.py` - Async Safety Validator

**Purpose**: Prevents deadlocks when code runs in async context.

**Current Validations**:
| Pattern | Detection Method | Runtime Backup |
|---------|-----------------|----------------|
| `asyncio.run()` | AST - `NestedLoopDetector` | None |
| `loop.run_until_complete()` | AST - `NestedLoopDetector` | None |
| `loop.run_forever()` | AST - `NestedLoopDetector` | None |
| `run_coroutine_threadsafe()` | AST - `ThreadsafePatternDetector` | None |
| `Future.result()` | AST - `FutureResultDetector` | Context var patches |

**Strengths**:
- Multiple specialized detectors for different patterns
- Runtime patches as backup for missed AST patterns
- Tracks import aliases (`import asyncio as aio`)
- Tracks variable assignments (`loop = get_event_loop()`)

**Weaknesses**:
- Runtime patches applied at module import time (global side effects)
- Separate from main validator (two validation passes)
- No detection of `ThreadPoolExecutor` blocking patterns

---

### Module 3: `generated_code.py` - REPL Policy Validator

**Purpose**: Enforces REPL-style coding conventions.

**Current Validations**:
| Rule | Implementation | Error Quality |
|------|----------------|---------------|
| No class definitions | `_contains_class_def` | Basic |
| Missing `await` on async calls | `_missing_await_errors` | Good - with example |

**Strengths**:
- Detects missing await on `self.method()` calls
- Handles list/generator comprehensions for `asyncio.gather()` pattern
- Collects async methods from both class and instance

**Weaknesses**:
- Only checks `self.method()` calls, not tool calls
- Doesn't detect `await` on sync functions (wastes tokens)
- Error messages lack source context

---

## Proposed Enhancements

### Enhancement 1: Unified Validator Architecture

**Problem**: Three separate validation passes with inconsistent error handling.

**Current Flow**:
```
execute_code()
    ├── validate_planning_code()      # validator.py
    ├── validate_async_safety()       # async_safety.py
    └── GeneratedCodeValidator()      # generated_code.py (in strategy)
```

**Proposed Flow**:
```
execute_code()
    └── UnifiedCodeValidator.validate()
            ├── SecurityValidator      # forbidden builtins, imports
            ├── AsyncSafetyValidator   # deadlock prevention
            ├── REPLPolicyValidator    # await, class defs
            └── (future validators)
```

**Implementation**:

```python
# src/nemo_oo_agents/runtime/code_validator.py (new unified module)

from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class ValidationIssue:
    """Single validation issue with location and severity."""
    line: int
    col: int
    message: str
    severity: Literal["error", "warning"] = "error"
    code: str = ""  # e.g., "E001", "W001"
    fix_hint: str | None = None

class Validator(Protocol):
    """Protocol for individual validators."""
    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]: ...

@dataclass
class ValidationContext:
    """Shared context for all validators."""
    code: str
    agent_class: type | None = None
    available_names: set[str] = field(default_factory=set)
    importable_modules: set[str] = field(default_factory=set)
    forbidden_self_calls: set[str] = field(default_factory=set)
    execution_count: int = 1
    # Agent instance for method introspection
    agent: Any = None

class UnifiedCodeValidator:
    """Orchestrates multiple validators with consistent error handling."""

    def __init__(self, validators: list[Validator] | None = None):
        self.validators = validators or [
            SecurityValidator(),
            AsyncSafetyValidator(),
            REPLPolicyValidator(),
        ]

    def validate(
        self,
        code: str,
        context: ValidationContext,
        *,
        stop_on_first_error: bool = True,
    ) -> None:
        """Validate code against all registered validators.

        Args:
            code: Python source code
            context: Shared validation context
            stop_on_first_error: If True, raise on first error (current behavior)

        Raises:
            ValidationError: With IPython-style formatting
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ValidationError(f"Syntax error: {e}", original_exception=e) from e

        all_issues: list[ValidationIssue] = []

        for validator in self.validators:
            issues = validator.validate(tree, context)
            all_issues.extend(issues)

            if stop_on_first_error and any(i.severity == "error" for i in issues):
                break

        # Format and raise errors
        errors = [i for i in all_issues if i.severity == "error"]
        if errors:
            raise ValidationError(self._format_error(code, errors[0], context))

        # Log warnings
        warnings = [i for i in all_issues if i.severity == "warning"]
        for warning in warnings:
            logger.warning(f"Validation warning: {warning.message}")
```

**Benefits**:
- Single entry point for all validation
- Consistent error formatting
- Easy to add new validators
- Supports both errors and warnings
- Future: configurable validator chains per strategy

---

### Enhancement 2: Security Hardening

**Problem**: Current validator misses some security-sensitive patterns.

**New Detections**:

```python
class SecurityValidator:
    """Enhanced security validation."""

    # Dangerous attribute access patterns
    DANGEROUS_ATTRS = frozenset({
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__import__",
    })

    # Dangerous function calls
    DANGEROUS_CALLS = frozenset({
        "globals",
        "locals",
        "vars",
        "dir",  # Warning only - useful for debugging
        "getattr",  # Warning only when used with __dunder__
        "setattr",
        "delattr",
        "open",  # Warning - should use tools for file access
        "exit",
        "quit",
    })

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        """Detect dangerous attribute access patterns."""
        # Block: obj.__class__.__bases__[0].__subclasses__()
        if node.attr in self.DANGEROUS_ATTRS:
            self.errors.append(ValidationIssue(
                line=node.lineno,
                col=node.col_offset,
                message=f"Access to '{node.attr}' is forbidden - "
                        "this could bypass security restrictions",
                code="E101",
            ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        """Detect dangerous function calls."""
        if isinstance(node.func, ast.Name):
            name = node.func.id

            if name in {"globals", "locals"}:
                self.errors.append(ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"{name}() is forbidden - "
                            "use explicit variable access instead",
                    code="E102",
                ))

            elif name == "open":
                self.errors.append(ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="open() is forbidden - use file tools for file access",
                    code="E103",
                    severity="warning",  # May be intentional for tools
                ))
```

**New Tests**:

```python
def test_validator_forbids_class_jailbreak():
    """Test that __class__.__bases__ escape is blocked."""
    code = """
().__class__.__bases__[0].__subclasses__()
"""
    with pytest.raises(ValidationError, match="__class__.*forbidden"):
        validate_planning_code(code)

def test_validator_forbids_globals_call():
    """Test that globals() is blocked."""
    code = "x = globals()['__builtins__']"
    with pytest.raises(ValidationError, match="globals.*forbidden"):
        validate_planning_code(code)
```

---

### Enhancement 3: Better Async Validation

**Problem**: Current async validation misses some patterns and creates global side effects.

**New Detections**:

```python
class AsyncSafetyValidator:
    """Enhanced async safety validation."""

    # Additional blocking patterns
    BLOCKING_METHODS = frozenset({
        "result",      # Future.result()
        "exception",   # Future.exception()
        "join",        # Thread.join(), Process.join()
        "wait",        # Process.wait()
        "acquire",     # Lock.acquire() without timeout
        "get",         # Queue.get() without timeout
        "put",         # Queue.put() without timeout
    })

    def visit_Call(self, node: ast.Call) -> Any:
        """Detect blocking calls in async context."""
        if not isinstance(node.func, ast.Attribute):
            return self.generic_visit(node)

        method = node.func.attr

        # ThreadPoolExecutor patterns
        if method == "submit" and self._is_executor_call(node):
            # Track for later .result() detection
            pass

        # Thread/Process.join() - usually wrong in async
        if method == "join":
            self.errors.append(ValidationIssue(
                line=node.lineno,
                col=node.col_offset,
                message="Thread/Process.join() blocks in async context - "
                        "use asyncio.to_thread() for CPU-bound work",
                code="E201",
                severity="warning",  # May be intentional
            ))

        # time.sleep() - should use asyncio.sleep()
        if self._is_time_sleep(node):
            self.errors.append(ValidationIssue(
                line=node.lineno,
                col=node.col_offset,
                message="time.sleep() blocks the event loop - "
                        "use 'await asyncio.sleep()' instead",
                code="E202",
                fix_hint="await asyncio.sleep(duration)",
            ))

        self.generic_visit(node)

    def _is_time_sleep(self, node: ast.Call) -> bool:
        """Check if this is time.sleep() call."""
        if not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr != "sleep":
            return False
        if isinstance(node.func.value, ast.Name):
            return node.func.value.id in self.time_aliases
        return False
```

**Remove Global Patches**:

The current `async_safety.py` patches `concurrent.futures.Future.result` at import time. This should be moved to a scoped context manager:

```python
# Before (global side effect at import):
concurrent.futures.Future.result = _safe_future_result

# After (scoped to execution):
class AsyncSafetyValidator:
    @contextmanager
    def runtime_safety_context(self):
        """Apply runtime patches only during agent code execution."""
        token = _in_agent_context.set(True)
        try:
            yield
        finally:
            _in_agent_context.reset(token)
```

---

### Enhancement 4: REPL Policy Improvements

**Problem**: Missing await detection only works for `self.method()`, not tools.

**Enhanced Detection**:

```python
class REPLPolicyValidator:
    """Enhanced REPL policy validation."""

    def __init__(self, agent: Any = None):
        self.agent = agent
        self.async_names: set[str] = set()
        self.sync_names: set[str] = set()
        self._collect_method_types()

    def _collect_method_types(self):
        """Collect async/sync status of all accessible methods and tools."""
        if not self.agent:
            return

        # Collect from agent class
        for name in dir(self.agent):
            if name.startswith("_"):
                continue
            try:
                attr = getattr(self.agent, name)
                if callable(attr):
                    if inspect.iscoroutinefunction(attr):
                        self.async_names.add(name)
                    else:
                        self.sync_names.add(name)
            except Exception:
                continue

    def _check_await_correctness(self, tree: ast.AST) -> list[ValidationIssue]:
        """Check for missing/unnecessary await."""
        errors = []
        parent_map = self._build_parent_map(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            method_name = self._get_call_name(node)
            if not method_name:
                continue

            is_awaited = self._is_awaited(node, parent_map)

            # Missing await on async method
            if method_name in self.async_names and not is_awaited:
                errors.append(ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Method `{method_name}` is async - add 'await'",
                    code="E301",
                    fix_hint=f"await self.{method_name}(...)",
                ))

            # Unnecessary await on sync method (warning)
            if method_name in self.sync_names and is_awaited:
                errors.append(ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Method `{method_name}` is sync - 'await' not needed",
                    code="W301",
                    severity="warning",
                ))

        return errors

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Extract method name from call node."""
        # self.method() -> "method"
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                return node.func.attr
        # tool_name() -> "tool_name" (for tools in scope)
        if isinstance(node.func, ast.Name):
            return node.func.id
        return None
```

**New Validations**:

```python
def _check_forbidden_patterns(self, tree: ast.AST) -> list[ValidationIssue]:
    """Check for patterns forbidden in REPL-style code."""
    errors = []

    for node in ast.walk(tree):
        # No class definitions
        if isinstance(node, ast.ClassDef):
            errors.append(ValidationIssue(
                line=node.lineno,
                col=node.col_offset,
                message="Class definitions not allowed - "
                        "define classes in the agent module instead",
                code="E302",
            ))

        # No top-level async function definitions (use @strategy instead)
        if isinstance(node, ast.AsyncFunctionDef):
            if node.args.args and node.args.args[0].arg == "self":
                # This is a helper method, allowed
                continue
            errors.append(ValidationIssue(
                line=node.lineno,
                col=node.col_offset,
                message="Top-level async functions not recommended - "
                        "define as agent method with @strategy",
                code="W302",
                severity="warning",
            ))

        # No infinite loops without break
        if isinstance(node, ast.While):
            if self._is_infinite_loop(node) and not self._has_break(node):
                errors.append(ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="Potential infinite loop detected - "
                            "add a break condition or use iteration limit",
                    code="W303",
                    severity="warning",
                ))

    return errors
```

---

### Enhancement 5: Error Message Improvements

**Problem**: Error messages don't consistently provide actionable guidance.

**Enhanced Error Format**:

```python
@dataclass
class ValidationIssue:
    line: int
    col: int
    message: str
    severity: Literal["error", "warning"] = "error"
    code: str = ""
    fix_hint: str | None = None
    doc_link: str | None = None  # NEW: Link to documentation

def _format_error(self, code: str, issue: ValidationIssue, context: ValidationContext) -> str:
    """Format error in enhanced IPython style with fix hints."""
    lines = code.split("\n")
    source_line = lines[issue.line - 1] if 1 <= issue.line <= len(lines) else ""

    cell_name = f"Cell In[{context.execution_count}]"
    indent = "    "
    caret = " " * issue.col + "^"

    parts = [
        f"{cell_name}, line {issue.line}",
        f"{indent}{source_line}",
        f"{indent}{caret}",
        f"[{issue.code}] {issue.message}",
    ]

    if issue.fix_hint:
        parts.append(f"\nFix: {issue.fix_hint}")

    if issue.doc_link:
        parts.append(f"\nSee: {issue.doc_link}")

    return "\n".join(parts)
```

**Example Output**:

```
Cell In[3], line 5
    result = asyncio.run(fetch_data())
             ^
[E201] Don't use asyncio.run() in async context - use 'await' directly

Fix: result = await fetch_data()
See: https://docs.nemo_oo_agents.dev/async-patterns
```

---

### Enhancement 6: Performance Optimization

**Problem**: Three separate AST parsing passes for the same code.

**Optimization**: Single-pass AST traversal.

```python
class CombinedASTVisitor(ast.NodeVisitor):
    """Single-pass visitor that runs all validators."""

    def __init__(self, validators: list[Validator]):
        self.validators = validators
        self.issues: list[ValidationIssue] = []

    def visit(self, node: ast.AST):
        """Visit node with all validators."""
        for validator in self.validators:
            if hasattr(validator, f"visit_{type(node).__name__}"):
                method = getattr(validator, f"visit_{type(node).__name__}")
                issues = method(node)
                if issues:
                    self.issues.extend(issues)
        self.generic_visit(node)
```

**Benchmark Expectation**:
- Before: 3 `ast.parse()` + 3 `ast.walk()` per code block
- After: 1 `ast.parse()` + 1 `ast.walk()` per code block
- Expected speedup: ~2-3x for validation phase

---

## Implementation Plan

### Phase 1: Consolidation (High Priority)

- [ ] Create `src/nemo_oo_agents/runtime/code_validator.py` with unified architecture
- [ ] Migrate `SecurityValidator` from `validator.py`
- [ ] Migrate `AsyncSafetyValidator` from `async_safety.py`
- [ ] Migrate `REPLPolicyValidator` from `generated_code.py`
- [ ] Update `execute_code()` to use `UnifiedCodeValidator`
- [ ] Remove global patches from `async_safety.py`
- [ ] Update tests

### Phase 2: Security Enhancements (High Priority)

- [ ] Add `__class__.__bases__` escape detection
- [ ] Add `globals()`/`locals()` detection
- [ ] Add `open()` warning
- [ ] Add dangerous attribute blocklist
- [ ] Add comprehensive security tests

### Phase 3: Async Improvements (Medium Priority)

- [ ] Add `time.sleep()` detection
- [ ] Add `Thread.join()` detection
- [ ] Improve `Future.result()` tracking
- [ ] Scope runtime patches to execution context
- [ ] Add async pattern tests

### Phase 4: REPL Policy Improvements (Medium Priority)

- [ ] Extend await checking to tools
- [ ] Add unnecessary await warning
- [ ] Add infinite loop detection
- [ ] Improve error messages with fix hints
- [ ] Add policy tests

### Phase 5: Performance & Polish (Low Priority)

- [ ] Implement single-pass AST traversal
- [ ] Add benchmarks
- [ ] Add documentation links to errors
- [ ] Create error code reference document

---

## File Changes Summary

| File | Change | Lines |
|------|--------|-------|
| `src/nemo_oo_agents/runtime/code_validator.py` | NEW | ~500 |
| `src/nemo_oo_agents/runtime/validator.py` | DEPRECATE | -291 |
| `src/nemo_oo_agents/runtime/async_safety.py` | DEPRECATE | -492 |
| `src/nemo_oo_agents/strategies/generated_code.py` | MODIFY | -100 |
| `src/nemo_oo_agents/runtime/actor.py` | MODIFY | -20 |
| `tests/test_code_validator.py` | NEW | ~400 |
| `tests/test_sandbox.py` | MODIFY | ~50 |

**Net Change**: ~250 lines added (after removing deprecated code)

---

## Risk Assessment

### Low Risk:
- Adding new validations (additive)
- Improving error messages
- Performance optimizations

### Medium Risk:
- Consolidating validator modules (behavior changes possible)
- Removing global async patches (need careful scoping)

### High Risk:
- Changing error types/formats (may break existing error handling)

### Mitigation:
- Keep deprecated modules as aliases during transition
- Feature flag for new validator (`AGENT006_USE_UNIFIED_VALIDATOR=1`)
- Comprehensive test coverage before migration
- Gradual rollout with monitoring

---

## Success Metrics

1. **Correctness**: All existing tests pass
2. **Coverage**: New security patterns detected in test suite
3. **Performance**: Validation < 10ms for typical code blocks
4. **Usability**: Error messages include actionable fix hints
5. **Maintainability**: Single file for all validation logic

---

## Open Questions

1. **Should warnings be surfaced to LLM?** Currently only errors block execution. Warnings could be added as feedback to guide better code generation.

2. **Configurable validation levels?** Different strategies might want different validation strictness (e.g., sandbox mode vs production mode).

3. **Validation caching?** For helper methods that don't change, could cache validation results.

---

## Appendix: Error Code Reference

| Code | Category | Message |
|------|----------|---------|
| E001 | Security | Forbidden builtin call |
| E002 | Security | Forbidden import |
| E003 | Security | Import * forbidden |
| E101 | Security | Dangerous attribute access |
| E102 | Security | globals()/locals() forbidden |
| E103 | Security | open() forbidden (warning) |
| E201 | Async | asyncio.run() in async context |
| E202 | Async | time.sleep() blocks event loop |
| E203 | Async | Future.result() deadlock |
| E301 | REPL | Missing await on async method |
| E302 | REPL | Class definition not allowed |
| W301 | REPL | Unnecessary await on sync method |
| W302 | REPL | Top-level async function |
| W303 | REPL | Potential infinite loop |
