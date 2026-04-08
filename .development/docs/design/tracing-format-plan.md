# Tracing Format Reconciliation Plan

## Current State

We have three different trace formats in use:

### 1. Old OpenTelemetry Format (pre-refactor)
**Source**: Old `otel_instrumentation` package
**Example**: `trace_20251204_151123.jsonl`

```json
{
  "span_id": "616551e551ace145",
  "trace_id": "1dcc6bfd5f9720cce9dd5cd36cce1c4d",
  "parent_span_id": "35f2b7f17879f4dd",
  "name": "llm.generate",
  "start_time": 1764857479133264000,  // nanoseconds
  "end_time": 1764857483785209000,
  "duration_ns": 4651945000,
  "attributes": {
    "llm.model_name": "nvidia_nim/qwen...",
    "llm.prompt_length": 2501,
    "llm.input_messages": "[...]",  // Full messages JSON
    "llm.token_count.prompt": 617,
    "llm.token_count.completion": 250,
    "llm.finish_reason": "stop"
  },
  "events": [{
    "name": "llm.prompt",
    "timestamp": 1764857479133300000,
    "attributes": { "messages": "...", "tools": "..." }
  }, {
    "name": "llm.completion",
    "timestamp": 1764857483785148000,
    "attributes": { "message": "...", "reasoning": "" }
  }],
  "status": {"status_code": "UNSET"}
}
```

**Characteristics**:
- Rich nested structure with OTel span hierarchy
- Embedded `events` array within spans
- All data in `attributes` dict with dotted keys (`llm.model_name`)
- Timestamps in nanoseconds since epoch
- Full LLM input/output messages embedded
- Token counts and usage metrics
- Parent-child span relationships

### 2. New Hook-Based Format (current)
**Source**: `tracing_hooks.py` (just created)
**Example**: `trace_20251205_080417.jsonl`

```json
{"type": "agent_call_started", "call_id": "b14defd2-...", "parent_call_id": null, "agent": "SentimentAgent", "method": "classify_single", "args": ["..."], "kwargs": {}, "timestamp": "2025-12-05T08:04:17.588570", "service": "prompt_opt_capabilities"}
{"type": "generation_started", "generation_id": "18d2950d-...", "parent_generation_id": null, "agent": "SentimentAgent", "method": "classify_single", "strategy": "PURE_PYTHON", "timestamp": "2025-12-05T08:04:17.588704", "service": "prompt_opt_capabilities"}
{"type": "generation_finished", "generation_id": "18d2950d-...", "agent": "SentimentAgent", "method": "classify_single", "duration_ms": 59998, "success": true, "timestamp": "2025-12-05T08:05:17.587052", "service": "prompt_opt_capabilities"}
{"type": "agent_call_finished", "call_id": "b14defd2-...", "agent": "SentimentAgent", "method": "classify_single", "duration_ms": 59998, "success": true, "result": null, "timestamp": "2025-12-05T08:05:17.587428", "service": "prompt_opt_capabilities"}
```

**Characteristics**:
- Flat event structure (no nesting)
- Simple `type` field for event kind
- ISO timestamp strings
- Duration in milliseconds
- **Missing**: LLM input/output content, code, token counts

### 3. Demo/Expected Format
**Source**: `demo_code_execution.jsonl`

```json
{
  "type": "generation.llm_input",
  "timestamp": "2025-11-20T10:00:00.200Z",
  "body": "Calling LLM",
  "ids": {
    "span_id": "gen-001",
    "generation_id": "gen-001",
    "llm_call_id": "llm-001"
  },
  "attributes": {
    "model": "gpt-4",
    "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
  }
}
```

**Characteristics**:
- `type` with dotted names (`generation.llm_input`, `span.code_execution`)
- `ids` object for correlation IDs
- `body` for human-readable description
- `attributes` for detailed data
- ISO timestamp strings

---

## Viewer Expectations

### Trace Viewer Plugins

The trace viewer has 35+ plugins expecting specific event types:

| Plugin | Expected `type` | Key Attributes |
|--------|-----------------|----------------|
| `llm_output.js` | `generation.llm_output` | `message`, `reasoning`, `tool_calls` |
| `llm_input.js` | `generation.llm_input` | `messages`, `tools`, `model` |
| `code_execution.js` | `span.code_execution` | `code`, `method_name`, `duration_ns`, `result` |
| `span_llm_generate.js` | `span.llm.generate` | `llm.model_name`, `llm.token_count.*`, `duration_ns` |
| `generation_started.js` | `generation_started` | (basic) |
| `generation_finished.js` | `generation_finished` | (basic) |
| `agent_call_started.js` | `agent_call_started` | `agent`, `method`, `args` |
| `agent_call_finished.js` | `agent_call_finished` | `result`, `duration_ms` |

### Prompt Optimizer Viewer

The prompt optimizer uses `extract_llm_trace()` which now looks for history events:
- `task` - TaskEvent with docstring
- `assistant` - AssistantEvent with generated code
- `error` - ErrorEvent
- `feedback` - FeedbackEvent

---

## Options

### Option A: Enrich Hook-Based Format

**Approach**: Add LLM input/output and code execution events to the hooks system.

**Changes Required**:
1. Add hooks for LLM calls (before/after with messages, response)
2. Add code to `code_execution_started` and `code_execution_finished` events
3. Map to demo format types (`generation.llm_input`, etc.)
4. Add `ids` object for correlation

**Pros**:
- Minimal viewer changes (already supports demo format)
- Forward-compatible with OTel if we add it back later
- Simple implementation in Python

**Cons**:
- Duplicates some event data (LLM content in events vs history)
- Need to add more hook points to runtime

**Effort**: Medium (2-3 hours)

### Option B: Update Viewers to New Format

**Approach**: Modify viewers to understand the simplified new format + history events.

**Changes Required**:
1. Update trace viewer plugins to handle new event types
2. Update prompt optimizer viewer to use history events directly
3. Add adapter layer in viewers for backwards compatibility

**Pros**:
- Simpler runtime (fewer hook points)
- Format matches runtime events directly

**Cons**:
- More viewer code changes (35+ plugins)
- Harder backwards compatibility
- Might lose useful observability features

**Effort**: High (1-2 days)

### Option C: Hybrid - Emit Both Formats

**Approach**: Enhance hooks to emit comprehensive events that work with both viewers.

**Changes Required**:
1. Update `tracing_hooks.py` to emit richer events with:
   - `type`: Use dotted format (`generation.llm_input`, `span.code_execution`)
   - `ids`: Add span_id, generation_id, etc.
   - `attributes`: Include all relevant data
2. Add hook points for LLM calls and code execution content
3. Use `ids` object for correlation (viewer-compatible)

**Pros**:
- Works with existing viewers immediately
- Clean separation (hooks only emit, runtime handles logic)
- Easy to extend for OTel export later

**Cons**:
- Need to add hook points to runtime

**Effort**: Medium (3-4 hours)

---

## Recommendation: Option C (Hybrid)

The hybrid approach gives us:

1. **Immediate viewer compatibility** - Events match demo format
2. **Clean architecture** - Hooks are the single observability point
3. **Future-proof** - Can add OTel exporter later that consumes hook events
4. **Complete data** - LLM messages, code, results all captured

### Implementation Steps

1. **Add missing hook points to runtime**:
   - `before_llm_call(agent, messages, tools, model)` → returns context
   - `after_llm_call(agent, response, context)`
   - Already have: `before_code_execution`, `after_code_execution`

2. **Update `tracing_hooks.py`** to emit:
   ```python
   # LLM Input
   {"type": "generation.llm_input", "ids": {...}, "attributes": {"messages": [...], "model": "..."}}

   # LLM Output
   {"type": "generation.llm_output", "ids": {...}, "attributes": {"message": "...", "reasoning": "...", "tool_calls": [...]}}

   # Code Execution
   {"type": "span.code_execution", "ids": {...}, "attributes": {"code": "...", "result": "...", "method_name": "..."}}
   ```

3. **Keep existing events** for basic structure:
   - `agent_call_started`, `agent_call_finished`
   - `generation_started`, `generation_finished`

4. **Update runner.py `extract_llm_trace()`** to also look for:
   - `generation.llm_input` for messages
   - `generation.llm_output` for response
   - `span.code_execution` for code

This gives us a clean, comprehensive tracing system that works with both viewers.
