# Critical Discovery: CodeActStrategy Ellipsis Detection

**Date**: 2026-01-19
**Issue**: opt6 and opt7 both failed with Phase 7 returning None

## Root Cause

**The `@strategy(CodeActStrategy())` decorator requires the method body to contain ONLY an ellipsis (`...`) after the docstring.**

From `/Users/rcabral/nemo_oo_agents/src/nemo_oo_agents/decorators.py` lines 64-79:

```python
def is_ellipsis_body(func):
    # Parse AST
    body = func_def.body

    # Skip docstring
    if docstring_present:
        body = body[1:]

    # CHECK: Must be exactly 1 statement
    if len(body) == 1 and isinstance(body[0], ast.Expr):
        return body[0].value.value is ...  # Returns True

    return False  # ANY other code breaks this!
```

**The constraint**: `len(body) == 1` - body must have **exactly ONE statement**.

## Why opt6 and opt7 Failed

### opt6 Structure
```python
async def phase_7_compute(...):
    """Docstring"""

    def helper1(): ...     # ← Statement 1 (FunctionDef)
    def helper2(): ...     # ← Statement 2 (FunctionDef)
    def helper3(): ...     # ← Statement 3 (FunctionDef)

    # Guidance comment
    ...                    # ← Statement 4 (Expr)
```

- **Body length**: 4 statements
- **Result**: `is_ellipsis_body()` returns `False`
- **Effect**: Method marked as `_needs_generation = False`
- **Execution**: Runs directly without LLM → ellipsis is no-op → returns `None`

### opt7 Structure
```python
async def phase_7_compute(...):
    """Docstring"""

    def helper1(): ...              # ← Statement 1
    def helper2(): ...              # ← Statement 2
    def helper3(): ...              # ← Statement 3
    is_delta = check_condition()   # ← Statement 4 (Assignment)
    if is_delta: return result     # ← Statement 5 (If)
    ...                             # ← Statement 6 (Expr)
```

- **Body length**: 6 statements
- **Same issue**: Not detected as ellipsis body

## The Execution Flow

1. **Decorator applies** (`@strategy(CodeActStrategy())`)
2. **Checks** `is_ellipsis_body(func)`
3. **If True**: Sets `wrapper._needs_generation = True`
4. **If False**: Sets `wrapper._needs_generation = False`
5. **At runtime**: Actor checks `_needs_generation` flag
6. **If True**: Routes to LLM generation
7. **If False**: Executes method directly as-is

## Valid vs Invalid Patterns

### ✅ Valid (Triggers Generation)
```python
@strategy(CodeActStrategy())
async def compute(...) -> Result:
    """Docstring"""
    ...  # ← ONLY statement
```

### ❌ Invalid (Does NOT trigger generation)
```python
@strategy(CodeActStrategy())
async def compute(...) -> Result:
    """Docstring"""

    def helper(): ...  # ← Extra code!
    x = 5             # ← Extra code!
    ...               # ← Ignored as no-op
```

## Solutions

### Option 1: Helpers as Class Methods ✅
```python
class Agent:
    def _helper1(self, x): ...
    def _helper2(self, y): ...

    @strategy(CodeActStrategy())
    async def phase_7_compute(...):
        """Can use self._helper1() and self._helper2()"""
        ...  # ← Works! Only statement.
```

### Option 2: Complete Implementation (No Ellipsis) ✅
```python
@strategy(CodeActStrategy())  # Don't actually need this
async def phase_7_compute(...) -> Result:
    """Docstring"""

    def helper1(): ...
    def helper2(): ...

    # Complete implementation
    result = helper1(helper2(...))
    return Result(result=result)  # ← Explicit return
```

**Note**: If there's no ellipsis, don't use `@strategy(CodeActStrategy())` - just implement normally!

### Option 3: Hybrid - Split Method ✅
```python
@strategy(CodeActStrategy())
async def phase_7_detect_pattern(...) -> PatternInfo:
    """Detect question pattern"""
    ...

async def phase_7_compute(...) -> Result:
    """Main compute (not decorated)"""

    def helpers(): ...

    pattern = await self.phase_7_detect_pattern(...)

    if pattern == "delta":
        return self._handle_delta(...)
    else:
        # Fallback: generate code
        return await self.phase_7_generate(...)

@strategy(CodeActStrategy())
async def phase_7_generate(...) -> Result:
    """Fallback LLM generation"""
    ...
```

## Recommendation for opt8

Use **Option 1: Helpers as Class Methods**

**Why**:
- Keeps helpers (guaranteed correct logic)
- LLM can still use `...` for flexibility
- Cleanest separation of concerns
- Helpers available to all phases

**Implementation**:
```python
class RSCDABAgentHardOpt8(Agent):
    # Helper methods (not decorated)
    def _matches_criteria(self, rule, field, value):
        \"\"\"Check if rule matches value.\"\"\"
        field_value = rule.get(field)
        if field_value is None:
            return True
        if isinstance(field_value, list):
            return len(field_value) == 0 or value in field_value
        return field_value == value

    def _find_lowest_matching_fee(self, transaction, fees_list):
        \"\"\"Find lowest fee that matches transaction.\"\"\"
        matching = [f for f in fees_list
                   if all(self._matches_criteria(f, attr, transaction[attr])
                          for attr in ['card_scheme', 'is_credit', 'account_type', 'aci'])]
        if not matching:
            return None
        # Return fee with lowest amount
        amounts = [(f['fixed_amount'] + f['rate'] * transaction['transaction_value_eur'] / 10000, f)
                   for f in matching]
        return min(amounts)[1]

    def _calculate_delta(self, transactions, fees_path, fee_id, param_name, new_value):
        \"\"\"Calculate total delta with fee-switching.\"\"\"
        with open(fees_path) as f:
            original_fees = json.load(f)

        modified_fees = json.loads(json.dumps(original_fees))
        for fee in modified_fees:
            if fee['ID'] == fee_id:
                fee[param_name] = new_value

        total = 0
        for txn in transactions:
            current_fee = self._find_lowest_matching_fee(txn, original_fees)
            new_fee = self._find_lowest_matching_fee(txn, modified_fees)
            if current_fee and new_fee:
                v = txn['transaction_value_eur']
                current_amt = current_fee['fixed_amount'] + current_fee['rate'] * v / 10000
                new_amt = new_fee['fixed_amount'] + new_fee['rate'] * v / 10000
                total += new_amt - current_amt
        return total

    @strategy(CodeActStrategy(max_iterations=15))
    async def phase_7_compute(self, data_dir: str, phase6: Phase6Output, phase1: Phase1Output) -> Phase7Output:
        \"\"\"Phase 7: Compute result

        For delta/what-if fee questions, use:
        - self._calculate_delta(transactions, fees_path, fee_id, param_name, new_value)

        This helper handles the "lowest fee wins" algorithm automatically.
        \"\"\"
        ...  # ← Now this works! Only statement.
```

## Next Steps

1. Create opt8 with helpers as class methods
2. Test on dabstep_1871_hard
3. If passes, run full 10-task evaluation
4. Compare with opt3 baseline (50%)

## Files

- **Discovery document**: `docs/8phase-ellipsis-discovery.md` (this file)
- **Investigation trace**: Agent exploration findings
- **Source code**:
  - `/Users/rcabral/nemo_oo_agents/src/nemo_oo_agents/decorators.py` (lines 30-122)
  - `/Users/rcabral/nemo_oo_agents/src/nemo_oo_agents/runtime/actor.py` (lines 1322-1364)
