# Plain Formatter Redesign

**Date:** 2026-02-24

## Problem

The current `PlainBlockFormatter.format_event()` uses `[field_name]` section headers for multi-field events and bare values for single-field events. The XML formatter uses `agentdoc_pformat(event)` which outputs the full Pydantic repr including the class name (`PythonOutput(...)`). Both include `expr=` attributes on message wrappers that add noise without value.

## Design

### format_event() — inner content only

`PlainBlockFormatter.format_event()` returns the inner content without an outer wrapper:

- **Single visible field** → bare value (no tag)
- **Multiple visible fields** → one `<field>value</field>` element per field

Field visibility is still governed by `repr=False` on the Pydantic model (unchanged).

```text
# Single-field event (e.g. ThinkingOutput)
I think through this carefully...

# Multi-field event (e.g. PythonOutput)
<status>complete</status>
<value>42</value>
<tool_call_id>tc_1</tool_call_id>
```

### format_message_content() plain branch — outer wrapper

`format_message_content()` in `renderer.py` wraps the serialized event content with an outer tag. In the plain branch:

- **Event blocks** → outer tag is snake_case of the event class name, `tag=` attribute only (no `expr=`)
- **Regular message blocks** → outer tag is `{role}_message`, `tag=` attribute only

```xml
<!-- Multi-field event -->
<python_output tag="1">
<status>complete</status>
<value>42</value>
<tool_call_id>tc_1</tool_call_id>
</python_output>

<!-- Single-field event -->
<thinking_output tag="2">I think through this carefully...</thinking_output>

<!-- Regular assistant message -->
<assistant_message tag="3">Hello world</assistant_message>
```

### What is NOT changed

- `XMLBlockFormatter` — unchanged, keeps `expr=` on both system blocks and message wrappers
- `PlainBlockFormatter.format()` (system blocks) — inherited from `XMLBlockFormatter`, keeps `expr=`
- `repr=False` field filtering — unchanged, same fields hidden as before

## Files

| File | Change |
|------|--------|
| `src/agent006/plain_formatter.py` | Rewrite `format_event()`: inner `<field>value</field>` tags (or bare for single field) |
| `packages/context-blocks/src/context_blocks/renderer.py` | Plain branch of `format_message_content()`: event type name as outer tag, `tag=` only |
| `packages/context-blocks/tests/test_renderer.py` | Update plain format tests |
| `tests/test_plain_formatter.py` | Update tests to match new inner-field XML format |
