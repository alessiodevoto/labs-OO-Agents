# Context-Blocks Architecture Analysis: Lessons from POML

**Status**: Exploratory Analysis
**Last Updated**: 2025-12-08
**Recommendation**: Phase 0 Validation Required Before Any Implementation

---

## Executive Summary

This document analyzes our context-blocks architecture against Microsoft's POML (Prompt Orchestration Markup Language) to evaluate whether our current form/content separation could be improved. The analysis reveals an **architectural asymmetry**: context blocks pass through BlockFormatter while history events bypass it entirely.

**Key Question**: Is this asymmetry a design flaw that needs fixing, or an intentional separation of concerns?

**Status**: This is an architectural exploration, not a problem report. No user pain points have been identified yet. The document proposes potential improvements but recommends validating the need before implementation.

## Motivation: Why Examine This?

### Potential Scenarios Where Asymmetry Could Matter

1. **Structured Tool Results**: If we want to wrap tool outputs in XML tags for consistency with context blocks, there's currently no hook point to do this uniformly.

2. **Format Experimentation**: A/B testing different prompt formats (XML vs Markdown) requires changing both BlockFormatter AND ProviderFormatter separately.

3. **Custom Event Rendering**: Users extending the system can't inject custom formatting for events without modifying ProviderFormatter.

### Current Evidence Level: **Theoretical**

⚠️ **Important**: These are architectural observations, not validated user problems. Before implementing any changes, we should:
- Check if any users have requested event formatting capabilities
- Test whether consistent formatting across context/history improves LLM performance
- Profile whether additional formatting layers impact runtime performance

### What Would Constitute Evidence?

- User reports: "I need to format tool results with XML tags"
- A/B test results: XML-wrapped history improves task completion by X%
- Code review finding: Duplicated formatting logic between BlockFormatter and ProviderFormatter
- Extension request: "How do I customize event rendering?"

**Current Status**: None of the above exist yet. This is proactive architectural analysis.

## Current Context-Blocks Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BlockRenderer                             │
│  (Orchestration: Evaluates blocks in correct order)         │
└─────────────┬───────────────────────────────────┬───────────┘
              │                                   │
    ┌─────────▼─────────┐              ┌─────────▼──────────────┐
    │  BlockFormatter   │              │  ProviderFormatter     │
    │  (HOW to format   │              │  (HOW to assemble)     │
    │   context blocks) │              │                        │
    │                   │              │  - OpenAI (list)       │
    │ - XML             │              │  - Anthropic (dict)    │
    │ - Markdown        │              │                        │
    └─────────┬─────────┘              └────────────────────────┘
              │                               │
              └───────────┬───────────────────┘
                          │
        ┌─────────────────▼────────────────────┐
        │      Final Output                    │
        │  - OpenAI: list[dict]               │
        │  - Anthropic: dict                  │
        └────────────────────────────────────┘
```

### The System/History Split

**Context Section → System Message:**
- Blocks evaluate to strings
- All blocks formatted together by BlockFormatter (XML/Markdown)
- Single formatted string becomes system message

**Events Section → History Messages:**
- Blocks evaluate to `list[Event]`
- Events passed **raw** to ProviderFormatter (no BlockFormatter processing)
- Assembled directly into conversation history

**The Asymmetry:** Events skip the formatting layer entirely. Content in history never gets the same treatment as system context. Whether this is a problem depends on whether:
1. Consistent formatting across all content improves LLM performance
2. Users need customization hooks for event rendering
3. The cost of additional complexity is justified by the benefits

### Example: The Asymmetry in Practice

**Context Block (gets BlockFormatter treatment):**
```xml
<!-- Rendered with XML tags if BlockFormatter is XMLFormatter -->
<context>
  <task>Generate a weather report</task>
  <available_tools>
    <tool name="get_weather">Fetch current weather data</tool>
  </available_tools>
</context>
```

**History Event (bypasses BlockFormatter):**
```json
// Raw tool result - no XML wrapping, handled directly by ProviderFormatter
{
  "role": "tool",
  "tool_call_id": "call_123",
  "content": "{\"temperature\": 72, \"conditions\": \"sunny\"}"
}
```

**The Question**: Should the tool result also get XML structure for consistency?
```xml
<tool_result tool_call_id="call_123">
  <data>{"temperature": 72, "conditions": "sunny"}</data>
</tool_result>
```

**Current state**: No mechanism to apply this transformation uniformly. Each ProviderFormatter would need to implement it independently.

**Is this a problem?**: Unknown. Requires validation via:
- LLM performance testing (does XML wrapping help?)
- User feedback (do they need this capability?)
- Code audit (is there duplication in ProviderFormatters?)

---

## What POML Does Differently

### 1. Unified Intermediate Representation (IR)

POML uses a three-pass architecture:

```
Source → Parser → IR → Writer → Output
```

**All content** passes through the IR, regardless of whether it's:
- Role definitions
- Task descriptions
- Conversation history (`<conversation>` component)
- Examples

This ensures consistent formatting rules apply everywhere.

### 2. CSS-Like Styling System

POML separates content from presentation through stylesheets:

```xml
<stylesheet>
  role { verbosity: concise; syntax: markdown }
  conversation { format: compact }
</stylesheet>

<role>You are a helpful assistant</role>
<conversation>{{history}}</conversation>
```

Style rules target **component types**, not locations. A `conversation` component gets styled identically whether it appears in system context or elsewhere.

### 3. Syntax Attribute for Format Control

Any component can explicitly control its output format:

```xml
<task syntax="json">...</task>
<examples syntax="markdown">...</examples>
```

This allows mixing formats within a single prompt without hardcoding format logic into the assembly layer.

### Critical Assessment of POML

**Strengths:**
- Elegant form/content separation
- Consistent formatting pipeline
- Flexible styling system

**Unknowns and Concerns:**
- **Adoption**: POML appears to be a research project (2024 arXiv paper). Production usage unclear.
- **Complexity Cost**: Three-pass architecture (Source → IR → Output) adds overhead. Is it justified?
- **Performance**: Multiple transformation passes could impact latency for high-volume scenarios.
- **Type Safety Trade-off**: IR approach loses strongly-typed Event discriminated unions, relying instead on generic ContentNodes.
- **Problem Fit**: POML was designed for complex multi-agent orchestration scenarios. Our context-blocks may not need that level of abstraction.

**Verdict**: POML offers valuable architectural insights, but we should borrow ideas selectively rather than wholesale adoption. The IR approach may be over-engineered for our current needs.

---

## Analysis: Should We Keep the History/System Split?

### Arguments FOR Maintaining the Split

1. **Type Safety**: Events are typed (`UserEvent`, `AssistantEvent`, etc.) while context is free-form strings. Keeping them separate preserves type guarantees.

2. **Provider Requirements**: LLM APIs fundamentally distinguish system messages from conversation history. The split reflects API reality.

3. **Semantic Clarity**: System context is "what the agent knows/is" while history is "what happened." Different concerns.

### Arguments AGAINST the Current Implementation (Theoretical Concerns)

⚠️ **Note**: These are architectural observations not yet validated by user reports or performance data.

1. **Formatting Asymmetry**: *If* we determine XML tags help LLMs parse structure, *then* that benefit should logically apply to history too (e.g., wrapping tool results in tags).
   - **Validation needed**: Does consistent formatting across context/history actually improve LLM performance?

2. **No Hook Point**: Users can't transform event content before assembly. The pipeline assumes events arrive pre-formatted.
   - **Validation needed**: Have any users requested this capability?

3. **Inconsistent Mental Model**: "Use BlockFormatter for formatting" except when you don't.
   - **Counter-argument**: The split could be intentional—different content types warrant different handling.

### Preliminary Assessment: **If Changing, Keep the Split but Add Event Formatting**

The history/system split appears architecturally sound—they represent fundamentally different concerns. However, *if* evidence emerges that formatting events would be beneficial, we should add that capability rather than eliminating the split entirely.

---

## Proposed Architecture Updates

### Option D: Status Quo (Do Nothing)

Maintain the current architecture with no changes.

**Benefits:**
- **Zero implementation cost**: No development time required
- **No migration burden**: Existing code continues working
- **Proven in production**: Current system may be "good enough" for actual use cases
- **Type safety preserved**: Strong Event typing remains intact
- **Simplicity**: Fewer moving parts, less to maintain
- **Clear separation**: System and history remain semantically distinct

**Drawbacks:**
- No hook point for custom event formatting (if users need it)
- Duplicated format logic if ProviderFormatter implements formatting (needs validation)
- Asymmetry remains (if it's actually problematic)

**When this is the right choice:**
- No users are blocked by current design
- No performance evidence that formatted events improve LLM quality
- Cost of change exceeds benefit
- "Good enough" is sufficient

**Validation questions before rejecting this option:**
1. Have any users requested event formatting capabilities?
2. Do we have duplicated formatting code between BlockFormatter and ProviderFormatter?
3. Would consistent formatting measurably improve agent performance?

### Option A: EventFormatter Layer

Add a new formatter specifically for event content:

```
┌───────────────────────────────────────────────────────────────────┐
│                        BlockRenderer                               │
└───────────┬─────────────────────────────────────────┬─────────────┘
            │                                         │
  ┌─────────▼─────────┐                    ┌─────────▼─────────┐
  │  BlockFormatter   │                    │  EventFormatter   │
  │  (context blocks) │                    │  (event content)  │
  │  - XML            │                    │  - XML            │
  │  - Markdown       │                    │  - Markdown       │
  │  - Plain          │                    │  - Plain          │
  └─────────┬─────────┘                    └─────────┬─────────┘
            │                                        │
            └────────────────┬───────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │  ProviderFormatter  │
                  │  (assembly only)    │
                  └─────────────────────┘
```

**Benefits:**
- Same formatting logic can be shared (XML/Markdown classes)
- Event-specific formatting needs addressed (tool results, structured data)
- Clear separation: BlockFormatter for static context, EventFormatter for dynamic history
- ProviderFormatter becomes pure assembly (no content decisions)

**Implementation Effort:** ~2-3 days
- Create EventFormatter interface and implementations
- Refactor ProviderFormatter to use EventFormatter
- Update tests
- Migration: Likely backward compatible if EventFormatter defaults to current behavior

**Performance Impact:** Minimal (one additional function call per event)

**Migration Risk:** Low (can default to current behavior)

### Option B: Unified ContentFormatter with Type Dispatch

Single formatter that handles both blocks and events through a unified interface with type-specific methods.

**Benefits:**
- Single place to define XML/Markdown rules
- Guaranteed consistency across content types
- Simpler to reason about

**Drawbacks:**
- Mixes concerns (blocks vs events have different needs)
- Harder to customize independently

**Implementation Effort:** ~3-4 days
- Design unified interface with type dispatch
- Merge BlockFormatter and event formatting logic
- Refactor all callsites
- More extensive testing

**Performance Impact:** Minimal to none

**Migration Risk:** Medium (changes core formatting interface)

### Option C: POML-Style IR (Most Radical)

Introduce an intermediate representation (ContentNode) that all content passes through. Every piece of content—context blocks, user messages, tool results—becomes a typed node with content, style spec, and metadata. The pipeline becomes:

1. Render to IR (list of ContentNodes)
2. Format all nodes (single pass applies styles)
3. Assemble for provider

**Benefits:**
- True form/content separation (POML's main strength)
- Stylesheet-based styling possible
- Maximally extensible

**Drawbacks:**
- Major rewrite
- May be over-engineered for our needs
- Loses type safety of discriminated Event union

**Implementation Effort:** ~2-3 weeks
- Design IR (ContentNode) schema
- Rewrite BlockRenderer to produce IR
- Implement IR → Output writer
- Rewrite all formatters
- Extensive testing and migration

**Performance Impact:** Potentially negative (multiple transformation passes)

**Migration Risk:** High (fundamental architectural change, breaking changes likely)

---

## Does Formatter/Provider Split Make Sense?

**Yes, but the responsibilities need clarification.**

### Current Responsibilities

| Component | Current Role |
|-----------|--------------|
| BlockFormatter | Format context blocks → string |
| ProviderFormatter | Assemble string + events → API format |

### Proposed Responsibilities

| Component | Proposed Role |
|-----------|---------------|
| Formatter(s) | Transform content (add structure/tags) |
| ProviderFormatter | **Assembly only** (no content decisions) |

The issue is ProviderFormatter currently does both assembly AND some content decisions (how to represent events). These should be separated.

---

## Decision Framework: Should We Change Anything?

### Step 1: Validate the Need

**Before implementing any option, answer these questions:**

| Question | How to Answer | Decision Impact |
|----------|---------------|-----------------|
| Do users need event formatting hooks? | User interviews, GitHub issues, support requests | If NO → Status Quo (Option D) |
| Does consistent formatting improve LLM performance? | A/B test: XML-wrapped events vs plain | If NO improvement → Status Quo |
| Is there duplicated formatting code? | Code audit of ProviderFormatter | If NO duplication → Status Quo may be fine |
| What's the maintenance burden of current approach? | Developer experience survey | High burden → consider change |

**Default position**: Option D (Status Quo) unless evidence suggests otherwise.

### Step 2: If Changing, Which Option?

**Use this decision tree:**

```
Is this a production system with many users?
├─ YES → Start with Option A (incremental, low risk)
└─ NO → Consider Option B or C for greenfield flexibility

Do we need stylesheet-level customization?
├─ YES → Option C (or simplified version)
└─ NO → Option A or B

Is maintaining two formatters acceptable?
├─ YES → Option A (clearer separation)
└─ NO → Option B (unified interface)

Do we have 2-3 weeks for a rewrite?
├─ NO → Options A or B only
└─ YES → Option C becomes feasible
```

### Comparison Matrix

| Criterion | Option D | Option A | Option B | Option C |
|-----------|----------|----------|----------|----------|
| **Implementation Cost** | Free | 2-3 days | 3-4 days | 2-3 weeks |
| **Performance** | ✅ Current | ✅ Minimal impact | ✅ Minimal impact | ⚠️ Potential overhead |
| **Migration Risk** | None | Low | Medium | High |
| **Extensibility** | Limited | Good | Good | Excellent |
| **Type Safety** | ✅ Strong | ✅ Strong | ✅ Strong | ⚠️ Reduced |
| **Code Complexity** | Current | +1 component | Same LOC | +3 components |
| **Separation of Concerns** | OK | ✅ Excellent | ⚠️ Mixed | ✅ Excellent |

---

## Concrete Recommendations

### Phase 0: Validate Before Implementing (Recommended First Step)

**Before making architectural changes, gather evidence:**

1. **Code Audit**: Review ProviderFormatter implementations
   - Is there duplicated formatting logic?
   - How much complexity would EventFormatter eliminate?
   - Document findings in follow-up analysis

2. **User Research**: Check for evidence of need
   - Search GitHub issues for formatting-related requests
   - Review any extension/customization attempts
   - Ask: Has anyone tried to customize event rendering?

3. **Performance Experiment**: Test formatting impact (if applicable)
   - A/B test: XML-wrapped tool results vs plain
   - Measure: Task completion rate, response quality
   - If no measurable benefit → Status Quo may be sufficient

**Decision Point**: If validation shows no clear need, stay with Option D (Status Quo).

### Phase 1: If Validated, Start Incremental (Option A)

**Only if Phase 0 reveals clear benefits:**

Introduce EventFormatter as parallel to BlockFormatter:
- User content formatting
- Assistant content formatting
- Tool call representation
- Tool result wrapping

Same implementations available (XML, Markdown, Plain) ensuring consistency with context formatting.

**Why start here**: Lowest risk, reversible, provides data for further decisions.

### Phase 2: Consider Unification (Option B) - Optional

**Only if Phase 1 reveals duplication or maintenance burden:**

Create a single Formatter abstraction that handles both context blocks and events. This guarantees the same syntax/style rules apply everywhere while allowing type-specific formatting logic internally.

**Trigger**: If maintaining two formatters becomes burdensome.

### Phase 3: Style Configuration (Inspired by POML) - Future

**Only if users request declarative format control:**

Borrow POML's stylesheet concept—declarative configuration that specifies format rules per content type:
- Context blocks: syntax, metadata inclusion
- Tool results: wrapping behavior
- User messages: verbosity level

This separates "what format to use" from "how to apply that format."

**Note**: Could be implemented with Option A, B, or C architectures.

---

## Summary Table

| Question | Answer |
|----------|--------|
| Is the asymmetry a problem? | **Unknown** - needs validation (Phase 0) |
| Keep history/system split? | **Yes** - reflects API reality and semantic difference |
| Keep Formatter/Provider split? | **Yes** - but clarify responsibilities if changing |
| Should history get formatted? | **Maybe** - if evidence supports it (Option A or B) |
| Adopt POML's IR approach? | **Not recommended** - over-engineered for our needs |
| Adopt POML's stylesheets? | **Maybe later** - if users request declarative control |
| **Recommended first step** | **Phase 0: Validate** - gather evidence before changing |
| Default position if no evidence | **Option D: Status Quo** - current design may be fine |

---

## Next Steps (Action Items)

### Immediate Actions

1. **Decide**: Is this analysis worth pursuing, or premature optimization?
   - If project is early-stage: Consider doing nothing until pain points emerge
   - If production system: Proceed to Phase 0 validation

2. **Phase 0 Validation** (if proceeding):
   - [ ] Code audit: Review all ProviderFormatter implementations for duplication
   - [ ] User research: Search for related issues/requests in project history
   - [ ] Document findings: Create follow-up doc with audit results

3. **Decision meeting**: Review validation findings
   - If no clear need found → Close this proposal, revisit if problems emerge
   - If need validated → Proceed to Phase 1 (Option A) design

### Future Considerations (Only if Validated)

- **Phase 1 Design**: Detailed EventFormatter API specification
- **Phase 1 Implementation**: EventFormatter with backward-compatible defaults
- **Phase 1 Testing**: A/B test formatted vs unformatted events
- **Phase 2 Decision**: Unify or keep separate based on Phase 1 learnings

### Questions for Discussion

1. Does this analysis address a real problem or is it theoretical over-engineering?
2. Are there existing pain points in the current architecture this didn't capture?
3. Is Option D (Status Quo) actually fine for foreseeable needs?
4. Should we close this proposal until evidence of need emerges?

---

## References

- [Microsoft POML GitHub](https://github.com/microsoft/poml)
- [POML Documentation](https://microsoft.github.io/poml/latest/)
- [POML Paper (arXiv)](https://arxiv.org/html/2508.13948v1)
- Context-blocks source: `packages/context-blocks/src/context_blocks/`
