# TDD Plan: Class Assignment Validator

**Date**: 2026-01-27
**Priority**: Critical
**Source**: `/Volumes/dev/dev/agent006/docs/scratch/fix-plan-class-method-replacement.md`

---

## Problem Summary

When the LLM generates code that assigns a method to a **class** (not instance), it corrupts all subsequent test runs:

```python
# Bug pattern - LLM generates:
RouterTestWrapper.process = _make_process_method()  # Assigns to CLASS!
```

This causes:
1. The replaced method runs **outside** `execute_python` context
2. `_parent_agent_var` is not set when sub-agents are instantiated
3. Sub-agents run as independent roots without parent context
4. **All models fail** because they share the corrupted class definition

---

## Implementation Plan (TDD)

### Phase 1: Write Failing Tests (RED)

All tests must FAIL before implementing the fix.

#### 1.1 ClassAssignmentValidator Unit Tests

**File**: `tests/runtime/test_code_validator.py`

Add new test class `TestClassAssignmentValidator` with:

| Test Name | Pattern | Expected |
|-----------|---------|----------|
| `test_reject_direct_class_assignment` | `ParentAgent.method = value` | REJECT |
| `test_reject_factory_class_assignment` | `def _make(): ...; Class.method = _make()` | REJECT |
| `test_reject_subagent_class_assignment` | `SubAgent.work = lambda: ...` | REJECT |
| `test_allow_self_assignment` | `self.helper = lambda: 42` | ALLOW |
| `test_allow_local_variable_assignment` | `foo.bar = value` (non-class) | ALLOW |
| `test_allow_dict_key_assignment` | `data["key"] = value` | ALLOW |

#### 1.2 HelperMethodManager Guard Tests

**File**: `tests/strategies/test_helper_method_manager.py` (new file)

| Test Name | Scenario | Expected |
|-----------|----------|----------|
| `test_rejects_class_instead_of_instance` | `manager.apply(..., agent=FakeAgent)` | TypeError |
| `test_accepts_instance` | `manager.apply(..., agent=FakeAgent())` | Success |
| `test_method_does_not_leak_to_class` | Bind to inst1, check inst2 | inst2 has no method |

### Phase 2: Implement Fixes (GREEN)

#### 2.1 Add ClassAssignmentValidator

**File**: `src/agent006/runtime/code_validator.py`

```python
class ClassAssignmentValidator:
    """Detect dangerous class attribute assignments like ClassName.method = ..."""

    def __init__(self):
        pass

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        visitor = _ClassAssignmentVisitor(context)
        visitor.visit(tree)
        return visitor.issues


class _ClassAssignmentVisitor(ast.NodeVisitor):
    """AST visitor for class assignment detection."""

    def __init__(self, context: ValidationContext):
        self.context = context
        self.issues: list[ValidationIssue] = []
        self.known_class_names = self._collect_class_names()

    def _collect_class_names(self) -> set[str]:
        """Collect names that refer to classes in the execution context."""
        names = set()
        agent = self.context.agent
        if agent:
            # The agent's class name
            names.add(type(agent).__name__)
            # Class attributes that are themselves classes (sub-agent classes)
            for attr_name in dir(type(agent)):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(type(agent), attr_name, None)
                    if isinstance(attr, type):
                        names.add(attr_name)
                except Exception:
                    continue
        return names

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check for ClassName.attr = value patterns."""
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                if isinstance(target.value, ast.Name):
                    if target.value.id in self.known_class_names:
                        self.issues.append(ValidationIssue(
                            line=node.lineno,
                            col=node.col_offset,
                            message=f"Cannot assign to class attribute '{target.value.id}.{target.attr}'. "
                                    f"This would corrupt all instances. "
                                    f"Use 'self.{target.attr} = ...' to assign to the instance.",
                            code="E401",
                            severity="error",
                            fix_hint=f"self.{target.attr} = ... instead of {target.value.id}.{target.attr} = ...",
                        ))
        self.generic_visit(node)
```

**Integration**: Add to `UnifiedCodeValidator.__init__()`:

```python
# In __init__
if validators is not None:
    self.validators = validators
else:
    self.validators = [
        SecurityValidator(),
        AsyncSafetyValidator(),
        ClassAssignmentValidator(),  # NEW
    ]
```

#### 2.2 Add HelperMethodManager Guard

**File**: `src/agent006/strategies/generated_code.py`

Add guard at line ~236 in `HelperMethodManager.apply()`:

```python
def apply(self, code: str, agent: Any, session_locals: dict, *, namespace: dict, target_method_name: str) -> HelperApplyResult:
    # ... existing code ...

    for node in tree.body:
        # ... existing checks ...

        # NEW: Guard against class instead of instance
        if inspect.isclass(agent):
            raise TypeError(
                f"HelperMethodManager received a class instead of instance: {agent}. "
                f"Cannot bind helper method '{method_name}' to class."
            )

        # Bind to the *instance* (avoid cross-instance leakage).
        bound = types.MethodType(func, agent)
        setattr(agent, method_name, bound)
        # ... rest of code ...
```

### Phase 3: Verification

```bash
# Run specific tests
pytest tests/runtime/test_code_validator.py::TestClassAssignmentValidator -v
pytest tests/strategies/test_helper_method_manager.py -v

# Run full test suite
pytest tests/ -v --tb=short
```

---

## Files to Modify

| Order | File | Action |
|-------|------|--------|
| 1 | `tests/runtime/test_code_validator.py` | Add `TestClassAssignmentValidator` class |
| 2 | `tests/strategies/test_helper_method_manager.py` | Create new test file |
| 3 | `src/agent006/runtime/code_validator.py` | Add `ClassAssignmentValidator` |
| 4 | `src/agent006/strategies/generated_code.py` | Add guard to `HelperMethodManager.apply()` |

---

## Test Implementation Details

### Test 1: Reject Direct Class Assignment

```python
def test_reject_direct_class_assignment(self, validator, default_context):
    """Validator blocks: ClassName.method = value"""
    from agent006.agent import Agent
    from unifiedllm import FakeLLMClient

    class SubAgent(Agent, llm=FakeLLMClient()):
        async def work(self) -> str:
            ...

    class ParentAgent(Agent, llm=FakeLLMClient()):
        SubAgent = SubAgent
        async def process(self) -> dict:
            ...

    agent = ParentAgent()
    context = ValidationContext(code="", agent=agent)

    code = "ParentAgent.process = lambda self: None"

    with pytest.raises(ValidationError, match="Cannot assign to class"):
        validator.validate(code, context)
```

### Test 2: Allow Self Assignment (False Positive Check)

```python
def test_allow_self_assignment(self, validator, default_context):
    """self.attr = value is allowed (instance assignment)."""
    from agent006.agent import Agent
    from unifiedllm import FakeLLMClient

    class TestAgent(Agent, llm=FakeLLMClient()):
        async def process(self) -> dict:
            ...

    agent = TestAgent()
    context = ValidationContext(code="", agent=agent)

    code = "self._helper = lambda: 42"
    validator.validate(code, context)  # Should NOT raise
```

### Test 3: Allow Non-Class Attribute Assignment

```python
def test_allow_non_class_attribute_assignment(self, validator, default_context):
    """obj.attr = value is allowed when obj is not a known class."""
    from agent006.agent import Agent
    from unifiedllm import FakeLLMClient

    class TestAgent(Agent, llm=FakeLLMClient()):
        async def process(self) -> dict:
            ...

    agent = TestAgent()
    context = ValidationContext(code="", agent=agent)

    # 'config' is not a known class name
    code = "config.value = 42"
    validator.validate(code, context)  # Should NOT raise
```

---

## Success Criteria

### Phase 1 Checklist (RED) - COMPLETE
- [x] `test_reject_direct_class_assignment` written and FAILING
- [x] `test_reject_factory_class_assignment` written and FAILING
- [x] `test_reject_subagent_class_assignment` written and FAILING
- [x] `test_allow_self_assignment` written and PASSING (false positive check)
- [x] `test_allow_non_class_attribute_assignment` written and PASSING
- [x] `test_rejects_class_instead_of_instance` written and FAILING
- [x] `test_accepts_instance` written and PASSING
- [x] `test_method_does_not_leak_to_class` written and PASSING

### Phase 2 Checklist (GREEN) - COMPLETE
- [x] `ClassAssignmentValidator` implemented
- [x] All rejection tests PASSING
- [x] `HelperMethodManager` guard implemented
- [x] All HelperMethodManager tests PASSING

### Phase 3 Checklist - COMPLETE
- [x] Full test suite passes: `pytest tests/ -v`
- [x] No regressions in existing tests

---

## Additional Improvements (2026-01-27)

Based on code review, the following enhancements were added:

### MRO Parent Class Detection
- Added parent class detection via `type(agent).__mro__`
- Prevents LLM from assigning to `BaseAgent.method` when agent extends `BaseAgent`
- New test: `test_reject_parent_class_assignment_via_mro`
- New test: `test_reject_agent_base_class_assignment`

### Annotated Assignment Support
- Added `visit_AnnAssign` handler for patterns like `ClassName.attr: Type = value`
- New test: `test_reject_annotated_class_assignment`
- New test: `test_allow_annotation_only` (annotation without assignment is allowed)

### Test Refactoring
- Added `agent_context` fixture to reduce test boilerplate
- Refactored 7 tests to use the new fixture

---

## Gap Fixes (2026-01-27)

Critical review identified and fixed the following gaps:

### setattr() Detection
- Added `visit_Call` handler to detect `setattr(ClassName, 'attr', value)` patterns
- Also detects `setattr(type(self), ...)` patterns
- New tests:
  - `test_reject_setattr_to_class`
  - `test_reject_setattr_to_subagent_class`

### Dynamic Class Reference Detection via type(self)
- Track variables assigned from `type(self)` in `class_ref_vars` set
- Detect assignments to those variables: `cls = type(self); cls.attr = value`
- Detect inline pattern: `type(self).attr = value`
- New tests:
  - `test_reject_dynamic_class_via_type_self`
  - `test_reject_inline_type_self_assignment`

### Defense in Depth
- `self.__class__` patterns are blocked by SecurityValidator (dunder check)
- Verified with test: `test_reject_dynamic_class_via_dunder_class`

### Nested Self Reference Detection (2026-01-27)
- Track variables assigned from `self` in `self_ref_vars` set
- `_is_type_self_call()` now detects `type(var)` where var was assigned from self
- Catches patterns like: `agent = self; type(agent).attr = value`
- New tests:
  - `test_reject_nested_self_reference_type_call`
  - `test_reject_setattr_with_nested_self_reference`

### False Positive Prevention Tests
- Added comprehensive tests to ensure legitimate patterns are NOT blocked:
  - `test_allow_method_call_that_looks_like_assignment`
  - `test_allow_class_attribute_read`
  - `test_allow_instantiation`
  - `test_allow_isinstance_check`
  - `test_allow_class_in_type_annotation`
  - `test_allow_class_as_dict_value`
  - `test_allow_class_in_list`
