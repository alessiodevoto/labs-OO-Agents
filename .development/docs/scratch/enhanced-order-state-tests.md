# Enhanced Order State Tests Implementation

**Date**: January 13, 2026
**Issue**: WP-5 State Validation Enhancement
**Status**: Implemented

---

## Summary

Implemented structured order state validation for fast food ordering agent tests. The system validates full order contents including items, sizes, and modifications with menu-based constraint validation.

**Key Benefits**:
- Validates actual order contents with product IDs
- Supports complex modifications (additions/removals from menu, special instructions)
- Menu-based validation ensures only valid items and modifications
- Order item IDs for unambiguous item reference
- Clean JSON output with Pydantic models

---

## What Was Implemented

### 1. Pydantic Data Models

**Files**: `experiments/capability_eval/agents/order.py`, `tests/capability/agents/order.py`

New models for structured order state:

```python
class Modifications(BaseModel):
    additions: list[str] | None = None       # ["cheese", "bacon"]
    removals: list[str] | None = None        # ["lettuce", "tomato"]
    special_instructions: list[str] | None = None  # ["extra cheese", "light onion"]

class OrderItem(BaseModel):
    product_id: int                          # 1001, 2001, 3001, etc.
    size: str | None = None                  # "small", "medium", "large"
    modifications: Modifications | None = None

class OrderState(BaseModel):
    order_submitted: bool | None             # Only in output when True
    order_canceled: bool | None              # Only in output when True
    order_items: dict[int, OrderItem] = {}   # Keyed by order_item_id

    def model_dump(self, **kwargs):
        # Serializes order_items as list for simpler test comparison
        data = super().model_dump(**kwargs)
        data["order_items"] = list(data["order_items"].values())
        return data
```

Output uses `model_dump(exclude_none=True)` for clean JSON.

### 2. Menu System with Validation

**Files**: `experiments/capability_eval/agents/order.py`, `tests/capability/agents/order.py`

Product ID scheme: `1xxx` = mains, `2xxx` = sides, `3xxx` = drinks

| Product ID | Name | Sizes | Default Ingredients | Allowed Additions |
|------------|------|-------|---------------------|-------------------|
| 1001 | burger | None | bun, patty, lettuce, tomato | cheese, bacon, onion, pickles |
| 1002 | chicken sandwich | None | bun, chicken, lettuce, tomato | cheese, bacon, pickles, mayo |
| 2001 | fries | small/medium/large | salt | cheese sauce, ranch |
| 2002 | onion rings | small/medium/large | salt | ranch, bbq sauce |
| 3001 | coke | small/medium/large | — | ice, lemon |
| 3002 | sprite | small/medium/large | — | ice, lemon |

### 3. Error Codes

```python
class ErrorCode(str, Enum):
    ITEM_NOT_IN_MENU = "ITEM_NOT_IN_MENU"
    ITEM_NOT_IN_ORDER = "ITEM_NOT_IN_ORDER"
    INVALID_SIZE = "INVALID_SIZE"
    INVALID_ADDITION = "INVALID_ADDITION"
    INVALID_REMOVAL = "INVALID_REMOVAL"
    ORDER_EMPTY = "ORDER_EMPTY"
```

### 4. Tool Interface

| Tool | Signature | Returns |
|------|-----------|---------|
| `add_item` | `(product_id, size, additions, removals, special_instructions)` | `order_item_id` or `ErrorCode` |
| `modify_item` | `(order_item_id, additions, removals, special_instructions, size)` | `None` or `ErrorCode` |
| `remove_item` | `(order_item_id)` | `None` or `ErrorCode` |
| `submit_order` | `()` | `None` or `ErrorCode.ORDER_EMPTY` |
| `cancel_order` | `()` | `None` |
| `get_order_status` | `()` | Current order summary string |
| `get_menu` | `()` | Full menu with IDs, descriptions, options |

**Clearing semantics** (empty value clears, `None` = no change):
- `size=""` clears size
- `additions=[]` clears all additions
- `removals=[]` clears all removals
- `special_instructions=[]` clears all special instructions

**Modification behavior**:
- Non-empty lists extend existing values (with deduplication)
- Empty lists clear the field entirely

### 5. Agent Docstring Guidelines

Added detailed guidelines in `process_message` docstring:

- **State Structure**: Documents `order_items` dict, `OrderItem`, `Modifications` schema
- **Menu Reference**: Agent can call `get_menu()` for product IDs and constraints
- **Submit/Cancel**: Only when customer explicitly confirms

---

## Test Data

### `tests/capability/data/fast_food_order.jsonl` (6 test cases)

| # | Scenario | Key Aspects |
|---|----------|-------------|
| 1 | Basic order | Burger + fries, removal modification |
| 2 | Complex modifications | additions + removals + special_instructions together |
| 3 | Duplicate items | Two burgers with different modifications |
| 4 | Distractor messages | Chitchat mixed with order items |
| 5 | Ambiguous reference | "Make that larger" → "The fries I mean" |
| 6 | Long conversation | 11-turn order building with multiple items |

### `tests/capability/data/fast_food_cancel.jsonl` (2 test cases)

| # | Scenario | Key Aspects |
|---|----------|-------------|
| 1 | Cancel with items | Build order then cancel |
| 2 | Cancel full order | Multiple items then cancel all |

**Total**: 8 test cases with full state validation

---

## Key Design Decisions

1. **Product IDs over names**: 4-digit integers (`1001`) provide unambiguous item identification
2. **Order item IDs**: Each added item gets unique ID (starting at 5000) for modification/removal
3. **Menu validation**: All additions must be in `allowed_additions`, removals in `default_ingredients`
4. **Size defaults**: Items with sizes default to "medium" when not specified
5. **Boolean flags omit when False**: `order_submitted` and `order_canceled` only appear when `True`
6. **List serialization**: `OrderState.model_dump()` converts order_items dict to list for test comparison
7. **ExactMatchScorer**: No custom scorer needed - Pydantic serialization is deterministic

---

## Implementation Differences

**experiments/capability_eval/** version:
- Uses `Menu` and `MenuItem` Pydantic models with `ItemSize` enum
- Menu created dynamically in `_create_menu()` method

**tests/capability/** version:
- Uses module-level `MENU` dict constant
- `MenuItem.sizes` is `list[str] | None` instead of enum
