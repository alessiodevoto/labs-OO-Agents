# Strategy Refactoring: Final Implementation Plan

**Status**: Ready for Implementation
**Date**: 2025-12-08
**Version**: Final (incorporating all feedback)

---

## Executive Summary

**Goal**: Enable natural strategy composition where complex strategies are built from simpler strategies using the `@plan` decorator.

**Strategy Hierarchy**:
```
TemplateStrategy                    ← Pure (no LLM, no composition)
    ↓ used by
PurePythonStrategy                  ← Composite (uses TemplateStrategy)
StructuredOutputStrategy            ← Composite (uses TemplateStrategy)
    ↓ used by
ReflexionStrategy                   ← Composite (uses above strategies)
PlanExecuteStrategy                 ← Composite (uses above strategies)
EnsembleStrategy                    ← Composite (uses above strategies)
```

**Key Decisions** (confirmed with user):
1. ✅ **Explicit runtime parameter** - Pass `runtime` to every `@plan` method on strategies
2. ✅ **Keep RuntimeServices interface** - Don't expose full Agent
3. ✅ **All strategies except TemplateStrategy are composite** - They all use TemplateStrategy for prompts
4. ✅ **Breaking changes accepted** - Get the design right

---

## Core Design

### 1. TemplateStrategy (The Foundation)

The only "pure" strategy - no LLM, no composition, just string templating.

```python
class TemplateStrategy(GenerationStrategy):
    """String templating strategy using runtime.expand_variables().

    This is the foundational strategy that all others build upon.
    No LLM call, just template rendering with Python expression evaluation.
    """

    @property
    def name(self) -> str:
        return "TEMPLATE"

    @property
    def requires_lock(self) -> bool:
        return False  # No LLM call

    @property
    def strategy_prompt(self) -> str:
        return ""  # No prompt needed

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> str:
        """Render template from call.docstring using call.kwargs as variables."""
        template = call.docstring or ""

        if not template:
            return ""

        # Build context
        context = {
            "self": runtime.agent,  # For {self.xxx} expressions
            "call": call,           # For {call.xxx} expressions
            **call.kwargs,          # Method parameters
        }

        # Expand using runtime's powerful expand_variables
        return runtime.expand_variables(
            template,
            extra_context=context,
            error_mode="raise",
        )
```

---

### 2. Extended @plan Decorator

Make `@plan` work on both Agent methods and Strategy methods.

```python
def plan(
    func: Callable[P, R] | None = None,
    *,
    llm: "UnifiedLLM | None" = None,
    strategy: GenerationStrategyABC | None = None,
    blocks: dict[str, Block | None] | None = None,
    event_blocks: dict[str, Block | None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]] | Callable[P, R]:
    """Decorator for planning methods.

    Works on both Agent methods and Strategy methods.

    On Agent:
        @plan(strategy=PurePythonStrategy())
        async def method(self, arg1, arg2):
            '''Docstring'''
            ...

    On Strategy:
        @plan(strategy=TemplateStrategy())
        async def method(self, runtime: RuntimeServices, arg1, arg2):
            '''Docstring'''
            ...

    Note: Strategy methods must have `runtime: RuntimeServices` as first parameter.
    """

    def decorator(f: Callable[P, R]) -> Callable[P, R]:
        # Validate is async
        if not inspect.iscoroutinefunction(f):
            raise TypeError(f"@plan method '{f.__name__}' must be async")

        # Prevent stacking
        if hasattr(f, "_agent_decorator"):
            raise ValueError(f"Cannot stack agent decorators on {f.__name__}")

        # Check if body is ellipsis
        needs_gen = is_ellipsis_body(f)

        # Determine strategy
        strat = None
        if needs_gen:
            strat = strategy if strategy is not None else PurePythonStrategy()
        else:
            if strategy is not None:
                raise ValueError(
                    f"@plan method {f.__name__} has implemented body; do not pass strategy."
                )

        @functools.wraps(f)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            """Wrapper for @plan - works on Agent and Strategy."""

            # Case 1: Called on Agent (self has .runtime)
            if hasattr(self, 'runtime'):
                runtime = self.runtime
                call_args = args
                call_kwargs = kwargs

            # Case 2: Called on Strategy (first arg must be RuntimeServices)
            elif args and hasattr(args[0], 'agent') and hasattr(args[0], 'history'):
                # Duck-type check for RuntimeServices
                runtime = args[0]
                call_args = args[1:]
                call_kwargs = kwargs

            else:
                raise ValueError(
                    f"@plan method {f.__name__} on {type(self).__name__} requires "
                    f"RuntimeServices as first argument. "
                    f"Expected: async def {f.__name__}(self, runtime: RuntimeServices, ...)"
                )

            # For Agent methods: use existing flow
            if hasattr(self, 'runtime'):
                from nemo_oo_agents.runtime.actor import _in_generation_session

                call_id = str(uuid4())
                parent_call_id = runtime._agent_call_id

                hook_context = call_before_hook(
                    "before_agent_call",
                    agent=self,
                    method_name=f.__name__,
                    args=args,
                    kwargs=kwargs,
                    call_id=call_id,
                    parent_call_id=parent_call_id,
                )

                result = None
                exception_caught = None

                try:
                    if _in_generation_session.get():
                        result = await runtime._execute_task(f, args, kwargs)
                    else:
                        result = await runtime._call_plan(f, args, kwargs)
                    return result
                except Exception as e:
                    exception_caught = e
                    raise
                finally:
                    call_after_hook(
                        "after_agent_call",
                        hook_context,
                        agent=self,
                        method_name=f.__name__,
                        result=result,
                        exception=exception_caught,
                    )

            # For Strategy methods: direct execution with nested strategy
            else:
                if not strat:
                    raise ValueError(f"@plan method {f.__name__} requires strategy parameter")

                # Build CurrentCall
                call = CurrentCall.from_method(f, call_args, call_kwargs)

                # Execute nested strategy
                return await runtime.execute_nested(strat, call)

        # Attach metadata
        wrapper._agent_decorator = "plan"
        wrapper._needs_generation = needs_gen
        wrapper._plan_llm = llm
        wrapper._plan_blocks = blocks
        wrapper._plan_event_blocks = event_blocks
        if strat:
            wrapper._plan_strategy = strat

        # Also attach to original function
        f._agent_decorator = "plan"
        f._needs_generation = needs_gen
        f._plan_llm = llm
        f._plan_blocks = blocks
        f._plan_event_blocks = event_blocks
        if strat:
            f._plan_strategy = strat

        return wrapper

    # Support both @plan and @plan() patterns
    if func is None:
        return decorator
    else:
        return decorator(func)
```

---

### 3. CompositeStrategy Base Class

```python
class CompositeStrategy(GenerationStrategy):
    """Base class for strategies that compose other strategies.

    All strategies except TemplateStrategy are composite strategies.
    They use @plan methods to delegate to simpler strategies.

    Note: Not all composite strategies have a "base" strategy.
    - PurePythonStrategy and StructuredOutputStrategy compose only TemplateStrategy
    - ReflexionStrategy, PlanExecuteStrategy, etc. have a base strategy for generation
    """

    # No required attributes - subclasses decide what they need
    pass
```

Simple base class - just marks strategies as composite. Each subclass implements what it needs.

---

### 4. PurePythonStrategy (Composite)

```python
class PurePythonStrategy(CompositeStrategy):
    """Pure Python code generation via multi-turn loop.

    Composes TemplateStrategy for prompt rendering.
    """

    # ========== Configuration ==========

    def __init__(
        self,
        *,
        max_iterations: int = 10,
        max_retries: int = 3,
    ):
        self.max_iterations = max_iterations
        self.max_retries = max_retries

    # ========== Prompt Templates (using TemplateStrategy) ==========

    @plan(strategy=TemplateStrategy())
    async def instructions(self, runtime: RuntimeServices) -> str:
        """## PURE_PYTHON Mode (Code Execution Loop)

        **Output Format**: Your entire response must be valid Python code.
        No markdown, no fences, no explanations.

        **Interaction Pattern**: This is a code execution loop:
        1. You output Python code (your entire response is Python)
        2. The SYSTEM executes your code and returns the output
        3. Messages labeled "[SYSTEM]" are execution results
        4. The session ends when you define the target method

        **Available in Your Code**:
        - `reasoning("...")` - Your thinking (not shown to user)
        - `message("...")` - Send message to user
        - `print(...)` - Debug output
        - `self` - Agent instance

        Available tools:
        {self.doc.show()}
        """
        ...

    @plan(strategy=TemplateStrategy())
    async def error_empty(self, runtime: RuntimeServices, method: str) -> str:
        """Empty response. Output Python code directly. Define `{method}` to complete."""
        ...

    @plan(strategy=TemplateStrategy())
    async def error_syntax(
        self,
        runtime: RuntimeServices,
        method: str,
        error: str,
    ) -> str:
        """**Syntax error - ensure complete method definition.**

        Error: {error}

        You MUST define: `async def {method}(...):`
        """
        ...

    @plan(strategy=TemplateStrategy())
    async def feedback_not_done(
        self,
        runtime: RuntimeServices,
        method: str,
        stdout: str,
        defined_methods: list[str],
    ) -> str:
        """Output:
        ```
        {stdout}
        ```

        Methods defined: {", ".join(defined_methods) if defined_methods else "none"}

        Define `{method}` to complete the task."""
        ...

    # ========== Strategy Implementation ==========

    @property
    def strategy_prompt(self) -> str:
        """System prompt for pure Python mode.

        Note: This is a static version. The full instructions with tools
        are rendered dynamically via the instructions() method.
        """
        return """## PURE_PYTHON Mode

**Output Format**: Your entire response must be valid Python code.
No markdown, no fences."""

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Execute pure Python generation loop."""
        target_method = call.method_name

        for iteration in range(self.max_iterations):
            # Generate code
            response, _ = await runtime.generate(tools=[])
            code = response.content.strip()

            # Handle empty response
            if not code:
                error_msg = await self.error_empty(runtime, method=target_method)
                runtime.history.add(ErrorEvent(data=ContentData(content=error_msg)))
                continue

            # Execute code
            result = await runtime.execute_code(code)

            # Handle execution error
            if result.error:
                if "'return' outside function" in str(result.error):
                    error_msg = await self.error_syntax(
                        runtime,
                        method=target_method,
                        error=str(result.error),
                    )
                else:
                    error_msg = f"Error: {result.error}"
                runtime.history.add(ErrorEvent(data=ContentData(content=error_msg)))
                continue

            # Check if target method defined
            if target_method in result.defined_methods:
                return await result.defined_methods[target_method](*call.args, **call.kwargs)

            # Not done - provide feedback
            feedback = await self.feedback_not_done(
                runtime,
                method=target_method,
                stdout=result.stdout,
                defined_methods=list(result.defined_methods.keys()),
            )
            runtime.history.add(FeedbackEvent(data=ContentData(content=feedback)))

        raise GenerationError(
            f"Failed to generate {target_method} after {self.max_iterations} iterations"
        )
```

---

### 5. StructuredOutputStrategy (Composite)

```python
class StructuredOutputStrategy(CompositeStrategy):
    """Single LLM call with structured output validation.

    Composes TemplateStrategy for prompt rendering.
    """

    # ========== Configuration ==========

    def __init__(self, output_model: type):
        """Initialize with Pydantic model for output validation.

        Args:
            output_model: Pydantic model class defining output structure.
        """
        self.output_model = output_model

    # ========== Prompt Templates (using TemplateStrategy) ==========

    @plan(strategy=TemplateStrategy())
    async def structured_output_instructions(
        self,
        runtime: RuntimeServices,
        schema: dict,
    ) -> str:
        """You are generating structured data matching the specified schema.

        **Output Schema**:
        ```json
        {schema}
        ```

        **Requirements**:
        1. Your response must match this schema exactly
        2. All required fields must be present
        3. Field types must be correct
        4. Follow any field constraints (min/max, enum values, etc.)

        Generate the output now.
        """
        ...

    # ========== Strategy Implementation ==========

    @property
    def strategy_prompt(self) -> str:
        """System prompt for structured output."""
        return "You are generating structured data matching a specified schema."

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Execute single LLM call with structured output."""

        # Build schema description
        schema = self.output_model.model_json_schema()

        # Build instructions
        instructions = await self.structured_output_instructions(
            runtime,
            schema=schema,
        )

        # Add instructions to history if needed (or just use as context)
        # For now, rely on task docstring + output_model

        # Generate with structured output
        response, _ = await runtime.generate(
            tools=[],
            output_model=self.output_model,
        )

        return response.parsed
```

---

### 6. ReflexionStrategy (Composite)

```python
class Reflection(BaseModel):
    """Reflection on task result."""
    is_satisfactory: bool
    issues: list[str]
    suggestions: list[str]


class ReflexionStrategy(CompositeStrategy):
    """Generate → reflect → improve loop.

    Composes:
    - Base strategy (usually PurePythonStrategy) for generation
    - TemplateStrategy for prompt building
    - StructuredOutputStrategy for reflection
    """

    # ========== Configuration ==========

    def __init__(
        self,
        base: GenerationStrategy | None = None,
        max_reflections: int = 3,
    ):
        # Lazy import to avoid circular dependency
        if base is None:
            from nemo_oo_agents.strategies.pure_python import PurePythonStrategy
            base = PurePythonStrategy()

        self.base = base
        self.max_reflections = max_reflections

    # ========== Helper: Delegate to Base Strategy ==========

    async def run(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Execute call with base strategy."""
        return await runtime.execute_nested(self.base, call)

    # ========== Prompt Templates (using TemplateStrategy) ==========

    @plan(strategy=TemplateStrategy())
    async def build_reflection_prompt(
        self,
        runtime: RuntimeServices,
        task: str,
        result: str,
    ) -> str:
        """Critically evaluate if the result meets task requirements.

        **Original task:**
        {task}

        **Result produced:**
        ```
        {result}
        ```

        **Evaluate:**
        1. Does this result fully address the task requirements?
        2. Are there any errors, inaccuracies, or inconsistencies?
        3. What specific improvements could be made?

        Be strict but fair. Only mark as satisfactory if truly meets all requirements.
        """
        ...

    @plan(strategy=TemplateStrategy())
    async def build_improvement_feedback(
        self,
        runtime: RuntimeServices,
        issues: list[str],
        suggestions: list[str],
    ) -> str:
        """Your implementation needs improvement.

        **Issues identified:**
        {chr(10).join(f"- {issue}" for issue in issues)}

        **Suggestions:**
        {chr(10).join(f"- {sug}" for sug in suggestions)}

        Please create an improved implementation that addresses these issues.
        """
        ...

    # ========== Sub-Components (using StructuredOutputStrategy) ==========

    @plan(strategy=StructuredOutputStrategy(output_model=Reflection))
    async def reflect(
        self,
        runtime: RuntimeServices,
        prompt: str,
    ) -> Reflection:
        """{prompt}"""
        ...

    # ========== Main Logic ==========

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Execute reflexion loop."""
        result = None

        for iteration in range(self.max_reflections):
            # Generate with base strategy
            result = await self.run(runtime, call)

            # Build reflection prompt
            prompt = await self.build_reflection_prompt(
                runtime,
                task=call.docstring or "",
                result=str(result),
            )

            # Reflect on result
            reflection = await self.reflect(runtime, prompt=prompt)

            if reflection.is_satisfactory:
                return result

            # Build improvement feedback
            feedback = await self.build_improvement_feedback(
                runtime,
                issues=reflection.issues,
                suggestions=reflection.suggestions,
            )

            # Add to history for next iteration
            runtime.history.add(FeedbackEvent(data=ContentData(content=feedback)))

        return result
```

---

## Implementation Plan

### Phase 1: Foundation (1-2 days)

**Goal**: Add TemplateStrategy and extend @plan decorator.

**Tasks**:

1. **Implement TemplateStrategy**
   - Create `src/nemo_oo_agents/strategies/template.py`
   - Use `runtime.expand_variables()` for rendering
   - Set `requires_lock = False`
   - Add comprehensive docstring with examples

2. **Extend @plan Decorator**
   - Update `src/nemo_oo_agents/decorators.py`
   - Add Agent vs Strategy detection logic
   - Handle RuntimeServices as first parameter
   - Preserve all existing behavior for Agents

3. **Add Tests**
   ```python
   # Test TemplateStrategy
   async def test_template_strategy_simple():
       call = CurrentCall(
           docstring="Hello {name}",
           kwargs={"name": "world"}
       )
       result = await TemplateStrategy().execute(mock_runtime, call)
       assert result == "Hello world"

   async def test_template_strategy_expressions():
       call = CurrentCall(
           docstring="Found {len(self.tools)} tools",
           kwargs={}
       )
       result = await TemplateStrategy().execute(mock_runtime, call)
       assert "Found 3 tools" in result

   # Test @plan on strategies
   async def test_plan_on_strategy():
       class TestStrategy(CompositeStrategy):
           @plan(strategy=TemplateStrategy())
           async def build_prompt(self, runtime, x: int) -> str:
               """Value: {x}"""
               ...

       strategy = TestStrategy()
       result = await strategy.build_prompt(mock_runtime, x=42)
       assert result == "Value: 42"
   ```

4. **Update Exports**
   - Add to `src/nemo_oo_agents/strategies/__init__.py`

**Validation**:
- ✅ All existing tests pass (no regression)
- ✅ New template tests pass
- ✅ @plan works on strategy methods

---

### Phase 2: Refactor PurePythonStrategy (2-3 days)

**Goal**: Demonstrate prompt-as-@plan-method pattern.

**Tasks**:

1. **Convert Prompts to @plan Methods**
   - Move `PurePythonConfig` prompt strings to `@plan` methods
   - Use `@plan(strategy=TemplateStrategy())`
   - Keep `max_iterations`, `max_retries` in `__init__`

2. **Update Execution Logic**
   - Replace `self.config.error_empty` with `await self.error_empty(runtime, ...)`
   - Pass `runtime` explicitly to all prompt methods

3. **Deprecate Config Prompts**
   - Mark `PurePythonConfig` prompt fields as deprecated
   - Add migration guide in docstring
   - Keep config-only fields (`max_iterations`, `max_retries`)

4. **Add Override Tests**
   ```python
   class CustomPurePython(PurePythonStrategy):
       @plan(strategy=TemplateStrategy())
       async def error_empty(self, runtime, method: str) -> str:
           """CUSTOM: Please define {method}!"""
           ...

   async def test_override_prompts():
       strategy = CustomPurePython()
       msg = await strategy.error_empty(mock_runtime, method="test")
       assert "CUSTOM" in msg
   ```

5. **Benchmark Performance**
   - Ensure no significant regression
   - Template calls should be fast (no LLM)

**Validation**:
- ✅ All PurePython tests pass
- ✅ Prompts appear in traces
- ✅ Override via subclass works
- ✅ Performance acceptable

---

### Phase 3: Refactor StructuredOutputStrategy (1-2 days)

**Goal**: Add prompt templates to StructuredOutputStrategy.

**Tasks**:

1. **Add Prompt Methods**
   - `structured_output_instructions()` for schema description
   - Use `@plan(strategy=TemplateStrategy())`

2. **Update Execution Logic**
   - Call prompt methods to build instructions
   - Pass to LLM as context

3. **Add Tests**
   - Test prompt rendering
   - Test override via subclass

**Validation**:
- ✅ StructuredOutput tests pass
- ✅ Prompts appear in traces
- ✅ Override works

---

### Phase 4: Refactor ReflexionStrategy (2-3 days)

**Goal**: Demonstrate full composite pattern.

**Tasks**:

1. **Make CompositeStrategy**
   - Inherit from `CompositeStrategy`
   - Add `run()` helper method

2. **Convert to @plan Methods**
   - `build_reflection_prompt()` - uses TemplateStrategy
   - `reflect()` - uses StructuredOutputStrategy
   - `build_improvement_feedback()` - uses TemplateStrategy

3. **Update Execution Logic**
   - Replace manual prompt building with method calls
   - Pass `runtime` explicitly

4. **Measure LOC Reduction**
   - Before: ~346 LOC
   - After: ~90 LOC (target: >50% reduction)

5. **Add Comprehensive Tests**
   - Test reflection loop
   - Test sub-task tracing
   - Test override via subclass

**Validation**:
- ✅ LOC reduced by >50%
- ✅ Full trace visibility
- ✅ All tests pass
- ✅ Behavior unchanged

---

### Phase 5: New Composite Strategies (3-4 days)

**Goal**: Validate pattern with new strategies.

**Tasks**:

1. **Implement PlanExecuteStrategy**
   ```python
   class PlanExecuteStrategy(CompositeStrategy):
       @plan(strategy=TemplateStrategy())
       async def build_planning_prompt(self, runtime, task: str) -> str:
           """Break down this task into concrete steps: {task}"""
           ...

       @plan(strategy=StructuredOutputStrategy(output_model=Plan))
       async def create_plan(self, runtime, prompt: str) -> Plan:
           """{prompt}"""
           ...

       @plan(strategy=TemplateStrategy())
       async def build_step_prompt(self, runtime, step: str, context: str) -> str:
           """Execute: {step}\nContext: {context}"""
           ...

       async def execute(self, runtime, call):
           # Plan
           planning_prompt = await self.build_planning_prompt(runtime, task=call.docstring)
           plan = await self.create_plan(runtime, prompt=planning_prompt)

           # Execute steps
           context = ""
           for step in plan.steps:
               step_prompt = await self.build_step_prompt(runtime, step=step.description, context=context)
               # Create call for step
               step_call = CurrentCall(method_name="execute_step", docstring=step_prompt, kwargs={})
               result = await runtime.execute_nested(self.base, step_call)
               context += f"\n{step.description}: {result}"

           # Synthesize final answer
           return context
   ```

2. **Implement EnsembleStrategy**
   ```python
   class EnsembleStrategy(CompositeStrategy):
       @plan(strategy=StructuredOutputStrategy(output_model=Vote))
       async def vote_on_candidates(
           self,
           runtime,
           task: str,
           candidates: list[str],
       ) -> Vote:
           """Select the best candidate for task: {task}

           Candidates:
           {chr(10).join(f"{i+1}. {c}" for i, c in enumerate(candidates))}
           """
           ...

       async def execute(self, runtime, call):
           # Generate multiple candidates
           candidates = []
           for _ in range(self.num_candidates):
               result = await self.run(runtime, call)
               candidates.append(str(result))

           # Vote on best
           vote = await self.vote_on_candidates(
               runtime,
               task=call.docstring or "",
               candidates=candidates,
           )

           return candidates[vote.best_index]
   ```

3. **Document Patterns**
   - Create strategy development guide
   - Show examples of each pattern
   - Explain when to use each strategy

**Validation**:
- ✅ New strategies work correctly
- ✅ Easy to implement (<100 LOC each)
- ✅ Full trace visibility
- ✅ Documentation clear

---

### Phase 6: Documentation & Cleanup (1-2 days)

**Goal**: Polish and document.

**Tasks**:

1. **Update Documentation**
   - Strategy development guide
   - Migration guide for existing code
   - API reference updates
   - Examples in docs

2. **Deprecation Notices**
   - Mark old patterns as deprecated
   - Add warnings for config-based prompts
   - Document migration path

3. **Code Review**
   - Review all changes for consistency
   - Check error messages are helpful
   - Ensure type hints are complete

4. **Performance Testing**
   - Benchmark all strategies
   - Ensure no significant regressions
   - Document any trade-offs

**Validation**:
- ✅ Documentation complete
- ✅ Deprecation warnings in place
- ✅ Code reviewed
- ✅ Performance acceptable

---

## Testing Strategy

### Unit Tests

1. **TemplateStrategy**
   - Simple variable substitution
   - Expression evaluation (`{len(self.tools)}`)
   - Nested expressions (`{self.doc.show()}`)
   - Error handling (missing variables)

2. **@plan Decorator**
   - Works on Agent methods
   - Works on Strategy methods
   - Validates runtime parameter
   - Handles both sync and async properly

3. **Each Strategy**
   - Core functionality
   - Prompt rendering
   - Override via subclass
   - Error handling

### Integration Tests

1. **End-to-End Generation**
   ```python
   async def test_pure_python_generates_code():
       @agent(llm=test_llm)
       class TestAgent(Agent):
           @plan(strategy=PurePythonStrategy())
           async def add(self, a: int, b: int) -> int:
               """Add two numbers."""
               ...

       agent = TestAgent()
       result = await agent.add(2, 3)
       assert result == 5
   ```

2. **Strategy Composition**
   ```python
   async def test_reflexion_improves_result():
       @agent(llm=test_llm)
       class TestAgent(Agent):
           @plan(strategy=ReflexionStrategy(max_reflections=2))
           async def analyze(self, data: str) -> dict:
               """Analyze data."""
               ...

       agent = TestAgent()
       result = await agent.analyze("test")
       # Verify reflection happened
       # Verify improvement occurred
   ```

3. **Trace Verification**
   ```python
   async def test_trace_shows_substeps():
       # Execute with tracing
       result = await agent.method()

       # Verify trace shows:
       # - Template rendering steps
       # - Reflection sub-task
       # - All prompts visible
   ```

### Performance Tests

1. **Template Rendering Overhead**
   - Should be <1ms per template
   - No significant impact on total time

2. **Strategy Execution**
   - Compare to baseline (before refactor)
   - Ensure <10% overhead from @plan

---

## Open Questions & Design Decisions

### 1. strategy_prompt Property ✅ RESOLVED

**Issue**: `strategy_prompt` is a sync property, but we want dynamic prompts from `@plan` methods.

**Solution**: Keep property for static/default prompt. Strategies can override to provide basic prompt, while `@plan` methods provide dynamic prompts during execution.

```python
@property
def strategy_prompt(self) -> str:
    """Static system prompt."""
    return "Basic instructions..."

# Dynamic prompt during execution
instructions = await self.instructions(runtime)  # Full dynamic prompt
```

---

### 2. CompositeStrategy.base ✅ RESOLVED

**Issue**: Not all composite strategies have a "base" strategy.

**Solution**: `CompositeStrategy` is just a marker. Strategies that need a base can add it themselves:

```python
class ReflexionStrategy(CompositeStrategy):
    def __init__(self, base=None, ...):
        self.base = base or PurePythonStrategy()

    async def run(self, runtime, call):
        return await runtime.execute_nested(self.base, call)
```

---

### 3. Circular Imports ✅ RESOLVED

**Issue**: Decorator imports PurePythonStrategy, which uses TemplateStrategy, etc.

**Solution**: Lazy imports in decorator:

```python
def plan(...):
    def decorator(f):
        strat = None
        if needs_gen:
            if strategy is not None:
                strat = strategy
            else:
                # Lazy import
                from nemo_oo_agents.strategies import PurePythonStrategy
                strat = PurePythonStrategy()
        ...
```

---

### 4. CurrentCall Construction ✅ RESOLVED

**Issue**: When first arg is `runtime`, need to skip it for `CurrentCall.from_method()`.

**Solution**: Handled in decorator - `call_args = args[1:]` when runtime detected.

---

## Success Metrics

Track these to validate the refactoring:

### Code Quality
- 📉 LOC reduction for composites: >50% (target: ~74%)
- 📊 Test coverage: >90%
- 🔍 Type hint coverage: 100%

### Developer Experience
- ⏱️ Time to implement new composite: <2 hours
- 📚 Lines of code for typical composite: <100 LOC
- 🐛 Bug rate: 50% reduction (from clearer abstractions)

### Performance
- ⚡ Template rendering: <1ms per call
- 🔒 No additional lock contention
- 💾 Memory usage: <10% increase

### Trace Quality
- 🔍 Sub-task visibility: 100%
- 📝 Prompt visibility: 100%
- 🎯 Strategy attribution: Clear in all traces

---

## Migration Guide

### Before (Config-Based Prompts)

```python
from nemo_oo_agents.strategies import PurePythonStrategy, PurePythonConfig

config = PurePythonConfig(
    max_iterations=5,
    error_empty="Please define `{method}`!"
)

@agent(llm=llm)
class MyAgent(Agent):
    @plan(strategy=PurePythonStrategy(config=config))
    async def analyze(self, data: str) -> dict:
        """Analyze data."""
        ...
```

### After (Method-Based Prompts)

```python
from nemo_oo_agents.strategies import PurePythonStrategy, TemplateStrategy

class CustomPurePython(PurePythonStrategy):
    @plan(strategy=TemplateStrategy())
    async def error_empty(self, runtime, method: str) -> str:
        """Please define `{method}`!"""
        ...

@agent(llm=llm)
class MyAgent(Agent):
    @plan(strategy=CustomPurePython(max_iterations=5))
    async def analyze(self, data: str) -> dict:
        """Analyze data."""
        ...
```

---

## Conclusion

This final implementation plan incorporates all feedback:

1. ✅ **Explicit runtime parameter** - Clear and type-safe
2. ✅ **StructuredOutputStrategy is composite** - Uses TemplateStrategy for prompts
3. ✅ **All strategies except TemplateStrategy are composite** - Natural hierarchy
4. ✅ **Breaking changes accepted** - Clean design prioritized

**Key achievements**:
- Natural strategy composition via `@plan`
- Massive LOC reduction (>50%)
- Full trace visibility
- Overridable prompts via subclass
- Type-safe and explicit
- Full template expression power

**Ready to begin implementation with Phase 1!**
