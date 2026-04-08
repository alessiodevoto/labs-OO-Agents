# Critical Review: llm-inspect Package Proposal

**Date:** December 8, 2025
**Reviewer:** Analysis of proposal against agent006 codebase

---

## Executive Summary

**Recommendation:** ✅ **Strongly Recommended with Minor Modifications**

The proposal is well-designed, feasible, and would significantly improve agent006's architecture. The extraction of introspection capabilities into a standalone package is architecturally sound and aligns with the project's goal of modularity.

**Key Strengths:**
- Excellent API design: composable, stateless, expression-compatible
- Clean separation of concerns from agent006 framework
- Reduces 595 lines to ~20 lines in agent006
- Magic method protocol is elegant and extensible
- Zero dependencies for core functionality

**Key Concerns:**
- Package name is misleading (see naming suggestions below)
- Missing some functionality currently in `doc.py` (child agents, imports)
- Need clarity on where `agent_doc()` helper lives
- Configuration defaults for agent006 need definition

**Implementation Complexity:** Medium (4-6 weeks for Phase 1-3)

---

## Naming Analysis

### Why "llm-inspect" is Problematic

You're absolutely correct that "llm-inspect" sounds like a library that **inspects LLMs** rather than **provides introspection FOR LLMs**. The name creates confusion about directionality.

### Suggested Alternative Names

**Top Recommendations:**

1. **`agentdoc`** ⭐ (BEST)
   - Clear: Documentation for agents
   - Short, memorable, pythonic
   - Parallel to `pydoc`, `docstring`
   - Package: `agentdoc`, import: `from agentdoc import doc, brief, methods`

2. **`codex-introspect`** or **`codex`**
   - "Codex" = book of knowledge/documentation
   - Implies code + documentation
   - Package: `codex`, import: `from codex import doc, brief`

3. **`llmdoc`**
   - Clear that it's documentation for LLMs
   - Simple, straightforward
   - Package: `llmdoc`, import: `from llmdoc import doc, brief`

4. **`pyinspect-ai`** or **`pyintrospect-ai`**
   - Makes directionality clear (Python introspection for AI)
   - More verbose but unambiguous
   - Package: `pyinspect_ai`, import: `from pyinspect_ai import doc`

5. **`docgen`** (Documentation Generation)
   - Short and clear
   - But might imply generating *code* documentation rather than runtime introspection

**Recommendation:** Use **`agentdoc`** - it's clear, concise, and establishes a new category.

---

## Detailed Feasibility Analysis

### 1. Core API Design ✅ Excellent

The P0 function signatures are well-thought-out:

```python
doc(obj, config=None) -> str
brief(obj) -> str
methods(obj, detail="summary") -> str
variables(obj) -> str
imports(module) -> str
schema(obj) -> str
```

**Strengths:**
- Stateless (no object to maintain)
- Composable (same functions work on any object)
- Expression-compatible (works in context-blocks)
- Returns strings (prompt-ready)

**Comparison to current `doc.py`:**
The current `Doc` class is **stateful** and **agent-specific**:
- `Doc.__init__(agent)` - tied to agent instance
- `self._expanded: set[str]` - maintains state
- `self._methods: Literal["summary", "full"]` - persistent settings

The proposal is **simpler and more powerful** because:
- No state to manage across calls
- Works on any Python object, not just agents
- Agents learn composable patterns: `doc(self.database)` instead of `self.doc.expand(self.database)`

### 2. Magic Method Protocol ✅ Solid

The protocol design is excellent:

```python
def __doc_brief__(self) -> str: ...
def __doc_full__(self) -> str: ...
def __doc_schema__(self) -> str: ...
```

**Strengths:**
- Follows Python conventions (dunder methods)
- Opt-in customization
- Falls back to introspection automatically

**Implementation note:** Need to decide precedence clearly:
1. Check for magic method → use if present
2. Else: apply automatic introspection

### 3. Configuration System ✅ Well-designed

The `DocConfig` dataclass is good:

```python
DocConfig(
    max_value_length=50,
    max_list_items=10,
    hidden_prefixes=["_"],
    hidden_names={"runtime", "history"},
    include_inherited=False,
    include_types=True,
    include_docstrings=True,
    include_hints=True,
)
```

**Current implementation comparison:**
`doc.py` has hardcoded filtering in `_SYSTEM_INTERNALS`:
```python
_SYSTEM_INTERNALS = frozenset({
    "context", "runtime", "history", "prompts", "events",
    "history_manager", "render_format", "prompt_stats", "doc",
})
```

The proposal correctly moves this to **configuration**.

### 4. Integration with agent006 ⚠️ Needs Clarification

**The proposal says:**

> Files to modify:
> - Remove `self.doc = Doc(self)` from `__init__`
> - Change `DEFAULT_CONTEXT_BLOCKS["python_tools"].expr` to `agent_doc(self)`
> - Create `src/agent006/util/inspect_config.py` (~20 lines)

**Issues to address:**

#### 4.1 Where does `agent_doc()` function live?

Proposal shows:
```python
# In agent006/util/inspect_config.py
from agentdoc import DocConfig, doc

AGENT_DOC_CONFIG = DocConfig(...)

def agent_doc(agent, **kwargs):
    return doc(agent, config=AGENT_DOC_CONFIG, **kwargs)
```

**Question:** How does this get into the expression evaluation namespace?

**Current implementation:** The runtime builds a namespace for expression evaluation. Need to ensure `agent_doc` (or `doc` + `AGENT_DOC_CONFIG`) is available.

**Options:**
1. Make `doc` available directly: `doc(self, config=AGENT_DOC_CONFIG)`
2. Make `agent_doc` helper available: `agent_doc(self)`
3. Use context-blocks' eval namespace injection

**Recommendation:** Import `agentdoc` functions into `agent006/util/__init__.py` for easy access:

```python
# src/agent006/util/__init__.py
from agentdoc import doc, brief, methods, variables, schema, imports
from .inspect_config import AGENT_DOC_CONFIG

# Make doc functions available in generated code
__all__ = ["doc", "brief", "methods", "variables", "schema", "imports", "AGENT_DOC_CONFIG", ...]
```

Then in expressions: `doc(self, config=AGENT_DOC_CONFIG)` or define a helper.

#### 4.2 Backwards compatibility during migration

The proposal doesn't address migration path for:
- Existing agents using `self.doc.expand()`
- Tests that reference `agent.doc._expanded`

**Recommendation:** Add migration guide with examples of translating old patterns to new.

### 5. Missing Functionality Analysis ⚠️ Gaps to Address

#### 5.1 Child Agent Rendering (Currently in doc.py)

**Current code** (lines 321-412 in `doc.py`):
- `_render_child_agents()` - separate section for Agent subclasses
- `_format_child_agent_summary()` - shows docstring + method list
- `_format_child_agent_full()` - shows full details including `__init__` signature

**In proposal:** Not mentioned in P0 or P1 functions.

**Recommendation:** Add to P1:
```python
def child_agents(obj) -> str:
    """List Agent subclass attributes (for agent006 integration)."""
```

Or: Make `methods()` and `variables()` smart enough to detect and group Agent subclasses.

#### 5.2 Imports Rendering (Currently in doc.py)

**Current code** (lines 557-594 in `doc.py`):
- `_render_imports()` - categorizes module imports into Modules/Classes/Functions
- Filters out `agent006` internals

**In proposal:** Listed as P0 function ✅
```python
imports(module) -> str
```

**Status:** Covered in proposal, but needs implementation detail:
- How to get agent's module?
- Same filtering logic?

**Recommendation:** Implementation should match current behavior:
```python
def imports(module_or_object) -> str:
    """Render available imports from a module or object's module."""
    if not inspect.ismodule(module_or_object):
        module = inspect.getmodule(type(module_or_object))
    else:
        module = module_or_object
    # ... current _render_imports logic
```

#### 5.3 Tool Formatting (Currently in doc.py)

**Current code** (lines 510-554):
- `_format_tool_summary()` - show tool class + method list
- `_format_tool_full()` - expand to show all methods with signatures

**In proposal:** Handled by `doc()` and `methods()` on the tool object.

**Analysis:** The proposal's approach is **better** - instead of special "tool" handling:
```python
# Old way
self.doc.expand(self.calculator)  # Special case in doc.py

# New way (proposal)
methods(self.calculator)  # Works for any object
doc(self.calculator)      # Works for any object
```

**Status:** ✅ No gap - proposal handles this more elegantly.

### 6. Inline Hints and Drill-Down Teaching ✅ Excellent Idea

**Proposal says:**
```markdown
## Variables
- self.items = list[5 items]  # doc(self.items)
- self.database = DatabaseClient  # methods(self.database)
```

**Current implementation:** Shows expand/collapse hints:
```markdown
- self.items = list[3 items]  [self.doc.expand(self.items)]
```

**Analysis:** The proposal's approach is **better** because:
1. Teaches composable functions (works on any object)
2. Doesn't require understanding stateful expand/collapse
3. More discoverable (shows the actual function to call)

**Recommendation:** Keep this as specified. Make hints configurable via `DocConfig.include_hints`.

### 7. Package Structure ✅ Good

```
packages/agentdoc/  # (renamed from llm-inspect)
├── pyproject.toml
├── README.md
├── src/
│   └── agentdoc/
│       ├── __init__.py      # Public API exports
│       ├── core.py          # doc(), brief(), methods(), variables()
│       ├── schema.py        # schema(), example()
│       ├── source.py        # source(), params()
│       ├── errors.py        # explain()
│       ├── scope.py         # imports(), available(), hierarchy()
│       ├── config.py        # DocConfig
│       ├── protocols.py     # Magic method protocols
│       └── format.py        # Internal formatting utilities
└── tests/
```

**Recommendation:** Good structure. Consider:
- `core.py` might get large - consider splitting into `inspect_object.py`, `inspect_methods.py`, etc.
- Add `py.typed` marker for type checking support

### 8. Zero Dependencies Claim ⚠️ Mostly True

**Proposal claims:** "Zero dependencies (except stdlib)"

**Analysis:** True for basic functionality, but:
- `schema()` function needs to handle Pydantic models, dataclasses, TypedDict
- Pydantic has good introspection: `model.model_fields`, `model_json_schema()`

**Recommendation:**
- Core package: zero dependencies ✅
- Optional extras: `pip install agentdoc[pydantic]` for enhanced Pydantic support

### 9. Testing Strategy ✅ Clear Path

**Current tests:** `tests/utils/test_doc_utility.py` (389 lines)
- Tests for stateful expand/collapse
- Tests for method/variable formatting
- Tests for child agents
- Tests for hiding internals

**Migration:** Most test logic translates directly:
```python
# Old
agent.doc.expand(agent.items)
assert "items" in agent.doc._expanded

# New (test the output instead of internal state)
output = doc(agent)
assert "['a', 'b', 'c']" in output
```

**Recommendation:**
1. Port existing tests to new API
2. Add tests for magic method protocol
3. Add tests for DocConfig options
4. Add tests for edge cases (circular refs, large objects)

---

## Implementation Complexity Assessment (REVISED)

### Phase 1: Core Package (MVP - Zero Dependencies)

**Effort:** 1-2 weeks

**Scope:** Core introspection functions only (defer `imports()`, `schema()`)

**Tasks:**
1. Create package structure in `packages/agentdoc/` ✅ 1 day
2. Implement `doc()`, `brief()`, `methods()`, `variables()` - extract from `doc.py` ✅ 3-4 days
3. Implement magic method protocol (`__doc_brief__`, `__doc_full__`) ✅ 1-2 days
4. Implement `DocConfig` with filtering (hidden_names, hidden_prefixes) ✅ 1-2 days
5. Add drill-down hints (configurable) ✅ 1 day
6. Write comprehensive tests ✅ 2-3 days
7. Add `py.typed` and full type annotations ✅ 1 day
8. Basic README with examples ✅ 1 day

**Risk factors:**
- Need to extract and refactor formatting logic from `doc.py`
- Protocol precedence needs clear implementation

### Phase 2: agent006 Integration

**Effort:** 3-5 days

**Tasks:**
1. Add `agentdoc` to agent006 dependencies (pyproject.toml) ✅ 5 minutes
2. Import `doc`, `brief`, `methods`, `variables` in agent006 namespace ✅ 30 minutes
3. Make functions available in executor's eval namespace ⚠️ 1 day
4. Implement `Agent.__doc_full__()` with child agent rendering ⚠️ 1-2 days
5. Create `AGENT_DOC_CONFIG` constant with hidden names ✅ 30 minutes
6. Update `DEFAULT_CONTEXT_BLOCKS["python_tools"]` to use `doc(self)` ✅ 5 minutes
7. Update strategy templates (pure_python.py) ✅ 15 minutes
8. Delete `src/agent006/util/doc.py` (595 lines removed!) ✅ 5 minutes
9. Rewrite tests in `tests/utils/test_doc_utility.py` ⚠️ 1-2 days
10. End-to-end testing with existing agents ⚠️ 1 day

**Risk factors:**
- Namespace integration needs testing
- Agent protocol implementation needs child agent detection logic

### Phase 3: Extended Functions (Future - Optional)

**Effort:** 1-2 weeks (when needed)

**Tasks:**
1. Implement `params()` ✅ 1 day
2. Implement `source()` ✅ 1-2 days
3. Implement `imports()` (deferred from Phase 1) ✅ 1-2 days
4. Implement `schema()` (add optional Pydantic dependency) ⚠️ 2-3 days
5. Implement `explain()` (error context) ⚠️ 3-4 days
6. Implement `example()` (type → instance generation) ⚠️ 2-3 days
7. Tests and docs ✅ 2-3 days

**Risk factors:**
- `explain()` needs exception context (traceback, locals)
- `example()` needs smart defaults for complex types
- `schema()` adds dependency on Pydantic

**Total implementation time (Phase 1-2):** 2-3 weeks (MVP + integration)

---

## Comparison: Current vs Proposed

| Aspect | Current (`doc.py`) | Proposed (`agentdoc`) |
|--------|-------------------|----------------------|
| **Lines of code in agent006** | 595 lines | ~20 lines (config) |
| **API complexity** | Stateful (expand/collapse) | Stateless (composable) |
| **Scope** | Agent-specific | Works on any object |
| **Dependencies** | None (internal) | External package |
| **Reusability** | Agent006 only | Any Python project |
| **Configurability** | Hardcoded filtering | `DocConfig` dataclass |
| **Expression usage** | `self.doc.show()` | `doc(self)` |
| **Drill-down** | `self.doc.expand(self.db)` | `methods(self.db)` |
| **Teaching** | Expand/collapse hints | Function call hints |
| **Testing** | Coupled to Agent | Unit testable |

**Winner:** Proposed approach is significantly better.

---

## Recommendations and Action Items

### APPROVED DECISIONS (from user feedback)

1. **✅ Package name:** `agentdoc`
2. **✅ No `agent_doc()` wrapper** - use direct `doc(self)` with functions in namespace
3. **✅ No backwards compatibility** - pre-alpha, breaking changes OK for clean design
4. **✅ Child agents via `__doc_full__`** - Agent base class implements protocol
5. **✅ Defer `imports()`** - not in first iteration
6. **✅ Defer `schema()`** - maintain zero dependencies in v1

### Must Have (Phase 1 - MVP)

1. **Core functions:** `doc()`, `brief()`, `methods()`, `variables()`
2. **Magic method protocol:** `__doc_brief__`, `__doc_full__`, `__doc_schema__`
3. **DocConfig system** with filtering (hidden_names, hidden_prefixes, etc.)
4. **Make functions available in agent006 namespace** - import in executor
5. **Agent base class implements `__doc_full__`** - handles child agents rendering
6. **Comprehensive tests** for all core functions
7. **Typing support** - `py.typed` marker, full type annotations

### Should Have (Phase 2)

8. **Extended functions:** `params()`, `source()` (defer `explain()`, `example()`)
9. **Agent006-specific config** - `AGENT_DOC_CONFIG` with complete hidden names list
10. **Documentation and examples** - README, API reference, usage patterns
11. **Drill-down hints** - `# doc(self.items)` style hints (configurable)

### Nice to Have (Future)

12. **Circular reference handling** - detect and truncate circular refs
13. **Large object truncation** - smart handling of huge lists/dicts
14. **Later:** `imports()`, `schema()`, `explain()`, `example()` functions

---

## Alternative Approaches Considered

### Alternative 1: Keep stateful Doc class but improve API

**Pros:**
- Less migration effort
- Backwards compatible

**Cons:**
- Still agent-specific
- Doesn't simplify agent006 architecture
- Can't be reused by other projects

**Verdict:** ❌ Don't do this - proposal is better

### Alternative 2: Hybrid approach (stateless + optional state)

Keep stateless functions but add optional state tracking:

```python
tracker = DocTracker()  # Optional
doc(self, tracker=tracker)  # Remembers expansions
```

**Pros:**
- Best of both worlds
- Backwards compatible pattern

**Cons:**
- Added complexity
- Unclear benefit over pure stateless

**Verdict:** ⚠️ Consider for Phase 4 if users request it

### Alternative 3: Use existing libraries (rich.inspect, pydoc)

**Pros:**
- No need to maintain code

**Cons:**
- Output not LLM-optimized (too verbose)
- Not token-efficient
- Can't customize for agent needs

**Verdict:** ❌ Doesn't meet requirements

---

## Risk Assessment

### High Risk
- **Expression namespace integration** - needs careful design and testing
- **Breaking existing agents** - migration may be painful for users

### Medium Risk
- **`schema()` implementation** - complex type system handling
- **Testing coverage** - need comprehensive tests for all object types

### Low Risk
- Core extraction from `doc.py` - straightforward refactoring
- Package structure - standard Python packaging

---

## Conclusion

**Overall Assessment:** ✅ **Strongly Approve**

The proposal is excellent and should be implemented with the following modifications:

1. **Rename to `agentdoc`** (or similar non-confusing name)
2. **Add child agent support** explicitly
3. **Document expression namespace integration** clearly
4. **Create detailed migration guide**

**Benefits:**
- Reduces agent006 complexity by 575 lines
- Creates reusable introspection library
- Improves composability and teachability
- Better separation of concerns

**Timeline:** 4-6 weeks for full implementation (P1-P3)

**Next Steps:**
1. Choose final package name
2. Create implementation plan with milestones
3. Set up package structure in `packages/agentdoc/`
4. Begin Phase 1 implementation

---

## REVISED IMPLEMENTATION PLAN (Based on User Feedback)

**Status:** ✅ Approved to proceed

**Package Name:** `agentdoc`

### Scope Changes

**IN SCOPE (Phase 1 - MVP):**
- Core functions: `doc()`, `brief()`, `methods()`, `variables()`
- Magic method protocol: `__doc_brief__`, `__doc_full__`
- `DocConfig` with filtering
- Zero external dependencies
- Direct usage: `doc(self)` (no wrapper functions)
- Agent base class implements `__doc_full__()` for child agent rendering

**DEFERRED (Future phases):**
- `imports()` - defer
- `schema()` - defer to maintain zero dependencies
- `explain()`, `example()` - defer
- Backwards compatibility - not needed (pre-alpha)

### Child Agent Handling Strategy

The elegant solution: Let the `Agent` base class implement the magic method protocol.

```python
# In agent006/agent.py
from agentdoc import doc, methods, variables

class Agent:
    def __doc_full__(self) -> str:
        """Custom documentation for agents including child agents."""
        parts = ["# Agent Documentation\n"]

        # Methods
        parts.append("## Methods")
        parts.append(methods(self))
        parts.append("")

        # Child Agents (detect from class attributes)
        child_agents = []
        for name in dir(self.__class__):
            attr = getattr(self.__class__, name)
            if self._is_agent_subclass(attr):
                child_agents.append((name, attr))

        if child_agents:
            parts.append("## Child Agents")
            for name, cls in child_agents:
                doc_line = cls.__doc__.split('\n')[0] if cls.__doc__ else ""
                parts.append(f"- self.{name}: {cls.__name__} - {doc_line}")
            parts.append("")

        # Variables
        parts.append("## Variables")
        parts.append(variables(self))

        return "\n".join(parts)

    @staticmethod
    def _is_agent_subclass(obj) -> bool:
        """Check if obj is an Agent subclass."""
        # Implementation here
        ...
```

This keeps agent-specific logic in agent006 while using agentdoc for generic introspection.

### Namespace Integration

Make agentdoc functions available in the execution namespace:

```python
# In agent006/runtime/executor.py or similar
from agentdoc import doc, brief, methods, variables

# Add to namespace dict used for expression evaluation
namespace = {
    "doc": doc,
    "brief": brief,
    "methods": methods,
    "variables": variables,
    # ... other utilities (message, logger, context, etc.)
}
```

Then expressions work naturally:
- `doc(self)` - full documentation
- `methods(self.database)` - just methods
- `brief(self.items)` - one-line summary

### Timeline (Revised)

- **Phase 1 (agentdoc MVP):** 1-2 weeks
- **Phase 2 (agent006 integration):** 3-5 days
- **Total:** 2-3 weeks to production

### Success Criteria

1. ✅ Remove 595 lines from agent006 (`doc.py` deleted)
2. ✅ Zero external dependencies in agentdoc
3. ✅ All existing tests pass (rewritten for new API)
4. ✅ Child agents render correctly via `Agent.__doc_full__()`
5. ✅ Expression evaluation works: `doc(self)`, `methods(self.tool)`
6. ✅ Drill-down hints appear: `# doc(self.items)`

### Next Steps

1. Create `packages/agentdoc/` structure
2. Implement core functions (extract from `doc.py`)
3. Add magic method protocol support
4. Implement `DocConfig`
5. Write tests
6. Integrate into agent006
7. Delete old `doc.py`

**Ready to proceed!** 🚀
