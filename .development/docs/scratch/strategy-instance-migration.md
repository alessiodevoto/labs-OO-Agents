# Migration to Instance-Based Strategies

## Current State

Agent006 currently supports **two** ways to specify strategies in the `@plan` decorator:

1. **String-based** (legacy): `@plan(strategy="PURE_PYTHON")`
2. **Instance-based** (current): `@plan(strategy=PurePythonStrategy())`

The string-based approach is a backwards-compatibility layer that converts strings to strategy instances via the `_resolve_strategy()` function in [decorators.py:240](../src/agent006/decorators.py#L240).

### Usage Statistics

- **String-based usage**: ~67 occurrences across tests, examples, tools, and agents
- **Instance-based usage**: ~1 occurrence
- **README documentation**: Still shows outdated `STRUCTURED_OUTPUT` constant

### How It Works Today

```python
# Both of these work:
@plan(strategy="PURE_PYTHON")              # String → converted to instance
@plan(strategy=PurePythonStrategy())       # Instance → used directly

# Conversion happens in decorators.py:
def _resolve_strategy(strategy: GenerationStrategyABC | str | None) -> GenerationStrategyABC:
    if strategy is None:
        return PurePythonStrategy()

    if isinstance(strategy, GenerationStrategyABC):
        return strategy  # Already an instance

    # String lookup
    strategy_map = {
        "PURE_PYTHON": PurePythonStrategy,
    }
    return strategy_map[strategy]()
```

## Why Move to Instance-Based Only?

### Benefits

1. **Configuration at Call Site**
   ```python
   # With instances, you can configure strategies:
   @plan(strategy=PurePythonStrategy(max_iterations=5, max_retries=2))
   async def analyze(self, data: str) -> dict:
       ...

   # With strings, you can't - always uses defaults:
   @plan(strategy="PURE_PYTHON")  # No way to set max_iterations!
   async def analyze(self, data: str) -> dict:
       ...
   ```

2. **Type Safety**
   - IDEs can autocomplete `PurePythonStrategy()` and show parameters
   - Type checkers can validate strategy types
   - Import errors caught at import time, not runtime

3. **Simpler Architecture**
   - Removes the string→instance conversion layer
   - Removes the `strategy_map` registry
   - One less thing to document and maintain

4. **Future-Proofing**
   - Easy to add new strategies with custom parameters
   - No need to register new strategies in a central map
   - Strategy instances can have complex initialization logic

### Tradeoffs

1. **Slightly More Verbose**
   ```python
   # Before: 24 characters
   strategy="PURE_PYTHON"

   # After: 25 characters
   strategy=PurePythonStrategy()
   ```

2. **Requires Import**
   ```python
   # Must import the strategy class
   from agent006.strategies import PurePythonStrategy
   ```

3. **Breaking Change for External Code**
   - Any external code using `strategy="PURE_PYTHON"` will break
   - Deprecation warning period recommended

## Migration Plan

### Phase 1: Prepare (No Breaking Changes)

**Goal**: Make instance-based the documented default while maintaining backwards compatibility.

1. **Update Documentation**
   - [x] Update README.md to show instance-based examples
   - [x] Update docstrings in decorators.py
   - [ ] Update strategy docstrings to show instance examples
   - [ ] Add migration guide

2. **Add Deprecation Warnings**
   ```python
   def _resolve_strategy(strategy: GenerationStrategyABC | str | None) -> GenerationStrategyABC:
       if isinstance(strategy, str):
           import warnings
           warnings.warn(
               f"String-based strategy '{strategy}' is deprecated. "
               f"Use {strategy_map[strategy].__name__}() instead.",
               DeprecationWarning,
               stacklevel=3
           )
       # ... rest of logic
   ```

3. **Update Examples First**
   - Migrate all examples/ to instance-based
   - Shows best practices to users
   - ~5 files to update

### Phase 2: Internal Migration (No Breaking Changes)

**Goal**: Update all internal code to use instance-based.

1. **Migrate Tests** (~67 files)
   ```python
   # Before
   @plan(strategy="PURE_PYTHON")
   async def task(self) -> str: ...

   # After
   @plan(strategy=PurePythonStrategy())
   async def task(self) -> str: ...
   ```

2. **Migrate Utilities** (~10 files)
   - util/prompt-optimization/
   - agents/

3. **Add Import Statements**
   ```python
   # Add to top of each migrated file
   from agent006.strategies import PurePythonStrategy
   ```

### Phase 3: Remove String Support (BREAKING CHANGE)

**Goal**: Simplify codebase by removing backwards compatibility layer.

1. **Update `@plan` Signature**
   ```python
   # Before
   def plan(
       func: Callable[P, R] | None = None,
       *,
       strategy: GenerationStrategyABC | str | None = None,
   ) -> ...

   # After
   def plan(
       func: Callable[P, R] | None = None,
       *,
       strategy: GenerationStrategyABC | None = None,  # Remove | str
   ) -> ...
   ```

2. **Remove `_resolve_strategy()` Complexity**
   ```python
   # Simplified version
   def _resolve_strategy(strategy: GenerationStrategyABC | None) -> GenerationStrategyABC:
       return strategy if strategy is not None else PurePythonStrategy()
   ```

3. **Remove `strategy_map` Registry**
   - No longer needed
   - Reduces maintenance burden

4. **Update Tests**
   - Remove backwards compatibility tests in test_decorator_strategy.py

## Implementation Checklist

### Required Changes

- [ ] Update decorators.py
  - [ ] Change type hint from `str | GenerationStrategyABC` to `GenerationStrategyABC`
  - [ ] Simplify `_resolve_strategy()`
  - [ ] Remove `strategy_map`

- [ ] Update tests (~67 files)
  - [ ] Replace `strategy="PURE_PYTHON"` with `strategy=PurePythonStrategy()`
  - [ ] Add imports

- [ ] Update examples (~5 files)
  - [ ] Same as tests

- [ ] Update tools (~10 files)
  - [ ] Same as tests

- [ ] Update documentation
  - [ ] README.md
  - [ ] Docstrings
  - [ ] Migration guide

### Testing Strategy

1. **Pre-Migration Validation**
   ```bash
   # Ensure all tests pass before starting
   pytest tests/
   ```

2. **Incremental Migration**
   - Migrate one directory at a time
   - Run tests after each directory
   - Commit after each successful directory

3. **Post-Migration Validation**
   ```bash
   # All tests should still pass
   pytest tests/

   # No deprecation warnings
   pytest tests/ -W error::DeprecationWarning
   ```

## Automation Opportunities

### Find & Replace Script

```bash
#!/bin/bash
# migrate-strategies.sh

# Find all Python files with string-based strategies
FILES=$(grep -rl 'strategy="PURE_PYTHON"' tests/ examples/ util/ agents/)

for file in $FILES; do
    echo "Migrating $file..."

    # Add import if not present
    if ! grep -q "from agent006.strategies import PurePythonStrategy" "$file"; then
        # Add after other agent006 imports or at top
        sed -i '' '/from agent006/a\
from agent006.strategies import PurePythonStrategy
' "$file"
    fi

    # Replace string with instance
    sed -i '' 's/strategy="PURE_PYTHON"/strategy=PurePythonStrategy()/g' "$file"

    echo "  ✓ Migrated"
done

echo "Migration complete. Run: pytest tests/"
```

### Validation Script

```python
#!/usr/bin/env python3
# validate-migration.py

import ast
import sys
from pathlib import Path

def check_file(path: Path) -> list[str]:
    """Check if file has any string-based strategies."""
    issues = []
    try:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if hasattr(node.func, 'id') and node.func.id == 'plan':
                    for keyword in node.keywords:
                        if keyword.arg == 'strategy':
                            if isinstance(keyword.value, ast.Constant):
                                if isinstance(keyword.value.value, str):
                                    issues.append(f"{path}:{node.lineno}: Found string strategy")
    except SyntaxError:
        pass
    return issues

# Check all Python files
issues = []
for path in Path('tests').rglob('*.py'):
    issues.extend(check_file(path))

if issues:
    print("Found string-based strategies:")
    for issue in issues:
        print(f"  ❌ {issue}")
    sys.exit(1)
else:
    print("✓ All strategies are instance-based")
```

## Timeline Estimate

Assuming automated migration with manual review:

- **Phase 1** (Deprecation): 2-4 hours
  - Add warnings
  - Update docs
  - Update examples

- **Phase 2** (Migration): 4-6 hours
  - Run automation script
  - Manual review and fixes
  - Test suite validation

- **Phase 3** (Cleanup): 1-2 hours
  - Remove compatibility layer
  - Final testing

**Total**: 7-12 hours of focused work

## Recommendation

**YES** - Move to instance-based only.

The benefits (configurability, type safety, simplicity) far outweigh the costs (slightly more verbose, requires imports). The migration is straightforward and can be largely automated.

### Suggested Approach

1. **Now**: Add deprecation warnings (non-breaking)
2. **Milestone 1.0**: Complete internal migration
3. **Milestone 2.0**: Remove string support (breaking change)

This gives external users one major version to migrate their code.

## References

- Current implementation: [decorators.py:240](../src/agent006/decorators.py#L240)
- Strategy base class: [strategies/base.py](../src/agent006/strategies/base.py)
- PurePython implementation: [strategies/pure_python.py](../src/agent006/strategies/pure_python.py)
- Backwards compat tests: [test_decorator_strategy.py](../tests/test_decorator_strategy.py)
