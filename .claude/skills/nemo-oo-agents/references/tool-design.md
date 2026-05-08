# Tool Design

Rules for writing helper methods and tools that the LLM will call well. Applies to:
- Deterministic helper methods on an agent
- External tools attached as class attributes (`BashTool`, `FileTool`, `TodoManager`, etc.)
- Methods exposed to the LLM in CodeAct

## What makes a good tool

1. **Single responsibility.** One tool, one concern. Don't mix reasoning with I/O, or computation with state mutation.

2. **Return typed values.** Prefer Pydantic models, dataclasses, or primitive types. Mutating methods return the affected object (or `None` on missing id) -- never a status string like `"OK"` or `"Task #3 completed"`.

3. **No side effects beyond own state.** A stock checker returns the stock; it does not print, log, or write files unless that is its purpose. Document any side effects explicitly in the docstring.

4. **Idempotent reads.** `status()`, `list_todos()`, `get_var()`, `check_stock()` -- anything that reads should be safe to call repeatedly with no side effects.

5. **Expose state via methods, not attributes.** Keep internal state private (`self._todos`, `self._stock`) and let the LLM call `add()`, `check_stock()`. Raw attribute access from generated code is brittle.

6. **Fail gracefully.** Return `None` on missing id or not-found. Do not raise exceptions for control flow -- raised exceptions in CodeAct cost an extra iteration for the LLM to recover from.

7. **Discoverable API.** Short verb-noun names: `add`, `done`, `comment`, `check_stock`, `find_alternatives`. The LLM should be able to infer usage from the name plus a one-line docstring.

## Example: well-designed tool

```python
from pydantic import BaseModel

class Order(BaseModel):
    ingredients: dict[str, int]
    substitutions: dict[str, str]

class InventoryAgent(Agent, llm=llm):
    def __init__(self):
        super().__init__()
        self._stock = {"butter": 100, "sugar": 100}
        self._alternatives = {"butter": ["margarine", "coconut oil"]}

    def check_stock(self, ingredient: str) -> int:
        """Return stock for ingredient, or 0 if not in inventory."""
        return self._stock.get(ingredient, 0)

    def find_alternatives(self, ingredient: str) -> list[str]:
        """Return available substitutes for ingredient, or empty list."""
        return [alt for alt in self._alternatives.get(ingredient, [])
                if self._stock.get(alt, 0) > 0]

    def place_order(self, items: dict[str, int]) -> Order:
        """Deduct items from stock and return the finalized Order."""
        substitutions = {}
        ...
        return Order(ingredients=items, substitutions=substitutions)
```

- Private state `_stock`, `_alternatives` not exposed directly
- Each helper does one thing
- Returns primitive (`int`, `list[str]`) or typed Pydantic model (`Order`)
- Missing ingredient returns `0` / `[]`, not raises
- Names describe the action

## Example: poorly-designed tool

```python
# BAD
class InventoryAgent(Agent, llm=llm):
    stock = {"butter": 100}  # public attribute -- LLM may mutate directly

    def process_order(self, items: dict) -> str:
        """Process an order and return a status message."""
        # Mixes validation, mutation, formatting, and logging
        print(f"Processing {items}")
        for item, qty in items.items():
            if item not in self.stock:
                raise KeyError(item)  # raises instead of returning None
            self.stock[item] -= qty
        return f"Processed {len(items)} items successfully"  # status string
```

- Public mutable attribute
- One method doing four jobs (validate, mutate, log, format)
- Raises on missing item
- Returns unparseable status string

## When the LLM is a poor tool user

If the agent keeps getting tool calls wrong, the fix is usually in the tool API, not the prompt:

- **LLM tries to work around the tool** → the tool is too narrow. Widen it or add a sibling.
- **LLM calls the tool with wrong shapes** → the signature is ambiguous. Tighten types.
- **LLM ignores the tool and reimplements it inline** → the tool is not discoverable. Rename it or improve the docstring.

Ask `print_prompt(agent.method, ...)` to see exactly what the LLM sees about your tools.
