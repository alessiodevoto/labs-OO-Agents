# PurePythonStrategy Refactoring Proposal

**Date**: 2025-12-10
**Current File**: `src/nemo_oo_agents/strategies/pure_python.py` (709 lines)

## Executive Summary

The current `PurePythonStrategy` implementation suffers from several code smells:
1. **Mode parameter anti-pattern**: `task_message_mode` creates two different behaviors in one class
2. **Prompt proliferation**: 6 different `@plan` template methods for various messages
3. **God method**: 220-line `execute()` method doing too many things
4. **Convoluted logic**: Complex task completion checks, nested conditions, mixed concerns
5. **Debugging artifacts**: Hardcoded prompt fragments (line 446)

## Detailed Analysis

### Issue 1: Mode Parameter Anti-Pattern

**Current Code** (lines 67-83):
```python
def __init__(
    self,
    *,
    max_iterations: int = 10,
    max_retries: int = 3,
    task_message_mode: bool = False,  # <-- BAD
):
```

**Problem**: The `task_message_mode` parameter creates branching behavior throughout the class:
- Lines 99-110: Different block overrides
- Lines 228-231: Different task building
- Lines 373-377: Different task condensation

**Impact**: Violates Single Responsibility Principle, makes testing harder, increases cognitive complexity.

**Recommendation**: Create two separate strategy classes:
```python
class PurePythonStrategy(CompositeStrategy):
    """Default: Strategy prompt in system message."""
    ...

class TaskMessagePurePythonStrategy(PurePythonStrategy):
    """Everything in task message (unified prompt)."""
    ...
```

---

### Issue 2: Prompt Redundancy

**Current Template Methods** (6 total):
1. `error_empty()` - "Output Python code. Use `return` to complete..."
2. `error_syntax()` - "**Syntax error** - ensure valid Python..."
3. `feedback_not_done()` - "Use `return` to complete the task..."
4. `strategy_instructions()` - Full REPL instructions (18 lines)
5. `initial_task_template()` - Initial task with anti-patterns
6. `condensed_task_template()` - Minimal post-success task

**Problems**:
- **Redundancy**: `error_empty` and `feedback_not_done` say nearly the same thing
- **Duplication**: `initial_task_template` and `condensed_task_template` share 90% of code
- **Complexity**: Too many similar prompts with subtle differences

**Note**: We MUST keep the `@plan(strategy=TemplateStrategy())` pattern for all prompts - this is how Agent006 discovers and tracks prompts systematically. Don't convert to simple strings.

**Recommendation**: Consolidate redundant prompts:
1. Merge `error_empty` + `feedback_not_done` → single `continuation_prompt`
2. Merge `initial_task_template` + `condensed_task_template` → single `format_task` with optional expansion
3. Keep `error_syntax` and `strategy_instructions` as-is

Result: 6 prompts → 4 prompts, clearer roles, maintains discoverability.

---

### Issue 3: God Method - `execute()`

**Current Structure** (lines 204-423, 220 lines):

```python
async def execute(self, runtime, call):
    # 1. Setup (15 lines)
    self._session_locals = {}
    target_method_name = call.method_name
    builtins = self._build_builtins(runtime, call)
    task_content = await self._build_task_message(...)  # or initial
    task_event_id = runtime.history.add(...)

    # 2. Main loop (180 lines!)
    while iteration < max_iterations and error_count < max_retries:
        # 2a. Generate
        response, event_id = await runtime.generate(tools=[])
        code = response.content.strip()
        clean_code = self._strip_reasoning_calls(code)

        # 2b. Handle empty
        if not code:
            error_count += 1
            # ... error handling ...
            continue

        # 2c. Execute
        result = await runtime.execute_code(...)

        # 2d. Handle error
        if result.error:
            error_count += 1
            # ... error formatting, guidance ...
            continue

        # 2e. Success - install methods
        iteration += 1
        for method_name, bound_method in result.defined_methods.items():
            if method_name == target_method_name:
                logger.warning(...)
                continue
            setattr(runtime.agent, method_name, bound_method)
            self._session_locals[method_name] = bound_method

        # 2f. Check completion (30 lines!)
        method = getattr(runtime.agent, target_method_name, None)
        return_type = None
        method_exists = False
        if method:
            try:
                sig = inspect.signature(method)
                return_type = sig.return_annotation
                method_exists = True
            except (ValueError, TypeError):
                pass

        task_complete = result.has_return or (
            method_exists
            and (
                return_type is inspect.Signature.empty
                or return_type is type(None)
                or return_type is None
            )
        )

        if task_complete:
            # 2g. Validate and return (20 lines)
            if result.has_return:
                result_to_validate = result.returned_value
            else:
                result_to_validate = None

            try:
                validated_result = self.validate_return_type(...)
            except TypeError as e:
                error_count += 1
                runtime.history.add(ErrorEvent(...))
                continue

            if self.task_message_mode:
                runtime.history.update(...)

            return validated_result

        # 2h. Not done - provide feedback (15 lines)
        feedback_parts = []
        if result.stdout:
            feedback_parts.append(...)
        if result.defined_methods:
            feedback_parts.append(...)
        feedback_msg = await self.feedback_not_done(...)
        feedback_parts.append(feedback_msg)
        runtime.history.add(FeedbackEvent(...))

    # 3. Exhausted limits (20 lines)
    if error_count >= self.max_retries:
        raise GenerationError(...)
    else:
        raise GenerationError(...)
```

**Problems**:
- **Single Responsibility Violation**: Doing setup, generation, execution, validation, error handling, feedback, completion checking
- **Cognitive Load**: 220 lines is too much to hold in working memory
- **Testing Difficulty**: Can't unit test individual pieces
- **Debugging Nightmare**: When something fails, hard to identify which piece

**Metrics**:
- **Cyclomatic Complexity**: ~15+ (should be < 10)
- **Lines of Code**: 220 (should be < 50 for a method)
- **Depth of Nesting**: 4 levels deep in places

---

### Issue 4: Convoluted Task Completion Logic

**Current Code** (lines 319-342):

```python
# Get method signature
method = getattr(runtime.agent, target_method_name, None)
return_type = None
method_exists = False
if method:
    try:
        sig = inspect.signature(method)
        return_type = sig.return_annotation
        method_exists = True
    except (ValueError, TypeError):
        pass

# Check if complete
task_complete = result.has_return or (
    method_exists
    and (
        return_type is inspect.Signature.empty
        or return_type is type(None)
        or return_type is None
    )
)
```

**Problems**:
- **Unclear Intent**: What exactly determines completion?
- **Brittle Logic**: Relying on multiple conditions with subtle edge cases
- **Poor Naming**: `method_exists` doesn't capture the real condition
- **Hidden Complexity**: Why do we need to check all these return type conditions?

**Recommendation**: Extract to a clear method with explicit logic:
```python
def _is_task_complete(self, result: ExecutionResult, method: callable) -> bool:
    """Check if generation task is complete.

    Task is complete if:
    1. Code executed a return statement, OR
    2. Method has no return type (returns None implicitly)

    Returns:
        True if task is complete, False if more iterations needed.
    """
    # Explicit return always means done
    if result.has_return:
        return True

    # Check if method expects no return value
    try:
        sig = inspect.signature(method)
        return_type = sig.return_annotation

        # Method returns None (explicitly or implicitly)
        if return_type in (inspect.Signature.empty, type(None), None):
            return True
    except (ValueError, TypeError):
        pass

    return False
```

---

### Issue 5: Debugging Artifacts

**Line 446**:
```python
What in my prompt so far makes you think that you cannot just return a hardcoded result using `return` directly. What would I need to change in the prompt to make you feel otherwise. Use reasoning('') for this.
```

**Problem**: This looks like a debugging/experimentation fragment that leaked into production code.

**Recommendation**: Remove or move to proper documentation.

---

## Proposed Refactoring

### Phase 1: Split Strategy Classes

**Before**:
```python
class PurePythonStrategy(CompositeStrategy):
    def __init__(self, task_message_mode: bool = False):
        self.task_message_mode = task_message_mode

    def get_block_overrides(self):
        if self.task_message_mode:
            return {}
        return {"strategy_prompt": ...}
```

**After**:
```python
class PurePythonStrategy(CompositeStrategy):
    """Default mode: Strategy instructions in system message."""

    def get_block_overrides(self):
        return {
            "strategy_prompt": Block(
                key="strategy_prompt",
                expr="strategy.get_instructions()"
            )
        }

class TaskMessagePurePythonStrategy(PurePythonStrategy):
    """Unified mode: All instructions in task message."""

    def get_block_overrides(self):
        # No system prompt - everything in task
        return {}

    async def _build_task_message(self, runtime, call):
        # Include strategy instructions + task
        instructions = await self.get_instructions()
        task = await super()._build_task_message(runtime, call)
        return f"{instructions}\n\n{task}"
```

**Benefits**:
- Clear separation of concerns
- Each class has single responsibility
- No conditional logic based on mode
- Easier to test independently

---

### Phase 2: Consolidate Redundant Prompts

**IMPORTANT**: Keep `@plan(strategy=TemplateStrategy())` pattern for ALL prompts - this enables prompt discovery/tracking.

**Merge these redundant `@plan` methods**:
- `error_empty` + `feedback_not_done` → `continuation_prompt()`
  ```python
  @plan(strategy=TemplateStrategy())
  async def continuation_prompt(self, runtime: RuntimeServices, method: str) -> str:
      """No return statement yet. Use `return` to complete {method}."""
      ...
  ```

- `initial_task_template` + `condensed_task_template` → `format_task(expanded: bool)`
  ```python
  @plan(strategy=TemplateStrategy())
  async def format_task(
      self,
      runtime: RuntimeServices,
      task: str,
      method_info: str,
      current_call: str,
      expanded: bool = True
  ) -> str:
      """Format task with anti-patterns and instructions (if expanded)."""
      ...
  ```

**Keep these unchanged**:
- `error_syntax` → One-line syntax error message
- `strategy_instructions` → Core REPL rules

**Result**: 6 templates → 4 templates, eliminated redundancy, maintained discoverability

---

### Phase 3: Extract Methods from `execute()`

Break the 220-line god method into focused pieces:

```python
class PurePythonStrategy(CompositeStrategy):
    async def execute(self, runtime, call):
        """Main execution loop - orchestrates generation."""
        session = self._initialize_session(runtime, call)

        while not session.is_exhausted():
            code = await self._generate_code(runtime)

            if not code:
                session.record_error(ErrorReason.EMPTY_RESPONSE)
                continue

            result = await self._execute_code(runtime, code, session.builtins)

            if result.error:
                session.record_error(ErrorReason.EXECUTION_FAILED, result.error)
                self._send_error_feedback(runtime, result.error)
                continue

            # Success
            session.record_iteration()
            self._install_helper_methods(runtime, result, call.method_name)

            if self._is_task_complete(result, runtime, call):
                return await self._finalize_success(runtime, result, call)

            # Not done yet
            self._send_continuation_feedback(runtime, result)

        # Exhausted limits
        raise session.build_failure_error(call.method_name)

    def _initialize_session(self, runtime, call) -> SessionState:
        """Initialize session state for generation."""
        ...

    async def _generate_code(self, runtime) -> str:
        """Generate and clean code from LLM."""
        ...

    async def _execute_code(self, runtime, code, builtins) -> ExecutionResult:
        """Execute code with proper builtins."""
        ...

    def _is_task_complete(self, result, runtime, call) -> bool:
        """Check if generation task is complete."""
        ...

    async def _finalize_success(self, runtime, result, call) -> Any:
        """Validate and return final result."""
        ...

    def _install_helper_methods(self, runtime, result, target_method):
        """Install helper methods on agent (but not target method)."""
        ...

    def _send_error_feedback(self, runtime, error):
        """Send error feedback to LLM."""
        ...

    def _send_continuation_feedback(self, runtime, result):
        """Send continuation feedback when not done."""
        ...
```

**Benefits**:
- Each method < 30 lines
- Clear single responsibility
- Easy to test individually
- Easy to understand flow
- Better error messages (know which step failed)

---

### Phase 4: Introduce SessionState Class

**Problem**: Session state is scattered:
- `self._session_locals` (instance variable)
- `iteration`, `error_count` (local variables)
- `task_event_id` (local variable)
- `target_method_name` (local variable)

**Solution**:
```python
@dataclass
class GenerationSession:
    """Tracks state for a single generation session."""
    max_iterations: int
    max_retries: int
    iteration: int = 0
    error_count: int = 0
    session_locals: dict = field(default_factory=dict)
    builtins: dict = field(default_factory=dict)
    task_event_id: str = ""

    def is_exhausted(self) -> bool:
        return self.iteration >= self.max_iterations or self.error_count >= self.max_retries

    def record_iteration(self):
        self.iteration += 1

    def record_error(self):
        self.error_count += 1

    def build_failure_error(self, method_name: str) -> GenerationError:
        if self.error_count >= self.max_retries:
            return GenerationError(
                f"Failed after {self.error_count} errors (max_retries={self.max_retries})"
            )
        else:
            return GenerationError(
                f"Failed after {self.iteration} iterations (max_iterations={self.max_iterations})"
            )
```

**Benefits**:
- Centralized state management
- Clear lifecycle
- No scattered variables
- Easy to extend with new state

---

## Implementation Plan

### Step 1: Split Classes (High Priority)
- [ ] Create `TaskMessagePurePythonStrategy` subclass
- [ ] Remove `task_message_mode` parameter from base class
- [ ] Update tests to use both strategy classes
- [ ] Update examples and documentation

### Step 2: Consolidate Redundant Prompts (High Priority)
- [ ] Merge `error_empty` + `feedback_not_done` → `continuation_prompt` (keep `@plan`)
- [ ] Merge `initial_task_template` + `condensed_task_template` → `format_task(expanded: bool)` (keep `@plan`)
- [ ] Keep `error_syntax` and `strategy_instructions` as-is (both stay `@plan`)
- [ ] Verify all prompts are still discoverable via `@plan(strategy=TemplateStrategy())`

### Step 3: Extract Methods (Medium Priority)
- [ ] Create `GenerationSession` dataclass
- [ ] Extract `_initialize_session` from `execute()`
- [ ] Extract `_generate_code` from `execute()`
- [ ] Extract `_execute_code` from `execute()`
- [ ] Extract `_is_task_complete` from `execute()`
- [ ] Extract `_finalize_success` from `execute()`
- [ ] Extract `_install_helper_methods` from `execute()`
- [ ] Extract `_send_error_feedback` from `execute()`
- [ ] Extract `_send_continuation_feedback` from `execute()`
- [ ] Refactor `execute()` to orchestrate extracted methods

### Step 4: Cleanup (Low Priority)
- [ ] Remove debugging prompt from line 446
- [ ] Consolidate similar logic in `_build_initial_task` and `_build_condensed_task`
- [ ] Add comprehensive docstrings to extracted methods
- [ ] Add type hints to all methods

### Step 5: Testing
- [ ] Add unit tests for each extracted method
- [ ] Add integration tests for both strategy classes
- [ ] Verify all existing tests still pass
- [ ] Add edge case tests (empty response, syntax errors, etc.)

---

## Metrics Comparison

### Before Refactoring:
- **Total Lines**: 709
- **`execute()` Lines**: 220
- **Cyclomatic Complexity**: ~15
- **Number of Classes**: 1
- **Number of Template Methods**: 6
- **Session State Variables**: 5 (scattered)

### After Refactoring:
- **Total Lines**: ~600 (estimated)
- **Longest Method**: ~40 lines
- **Cyclomatic Complexity**: ~5 per method
- **Number of Classes**: 2 (clear separation)
- **Number of Template Methods**: 4 (all using `@plan` for discoverability)
- **Session State**: 1 centralized class

---

## Risk Assessment

### Low Risk:
- Splitting classes (preserves all behavior)
- Removing unused prompts
- Extracting pure functions

### Medium Risk:
- Changing task completion logic (well-tested area)
- Refactoring execute() (many interdependencies)

### Mitigation:
- Comprehensive test coverage before refactoring
- Incremental changes with testing at each step
- Keep old code commented out during transition

---

## Conclusion

The current `PurePythonStrategy` suffers from:
1. **Design issues**: Mode parameter anti-pattern
2. **Complexity issues**: God method, convoluted logic
3. **Maintainability issues**: Prompt proliferation, scattered state

The proposed refactoring addresses all issues while maintaining backward compatibility through:
- Class hierarchy for mode separation
- Method extraction for complexity reduction
- Centralized state management
- Simplified prompts

**Recommendation**: Implement in phases, starting with high-priority items (split classes, simplify prompts).
