# Trace Viewer: Context Block XML Rendering

**Date:** 2026-03-23
**Status:** Approved for implementation

## Problem

System messages contain XML context blocks. The actual format from `XMLBlockFormatter` is:
```
<system_prompt>
You are an agent...
</system_prompt>

<self expr="doc(self)">
class MyAgent...
</self>
```

Tag name = block key. `expr` attribute is optional (only present when `block.metadata.expr` is set).
`PlainBlockFormatter` uses the same XML format for system blocks; conversation event fields inside blocks may use `<field>value</field>` inline XML.

`agent006.system_message.is_diff` (bool attribute) — when `true`, the content is a unified diff of the system message change, NOT parsed XML blocks.

Correct parsing regex:
```
/<([a-zA-Z_][a-zA-Z0-9_-]*)([^>]*)>\n([\s\S]*?)\n<\/\1>/g
```

Currently all this shows as unformatted plain text in `CodeBox` (with markdown hljs coloring). Hard to scan when there are many blocks.

## 5 Variants Considered

### Variant 1: XML Syntax Highlighting Only
Add `xml` as a hljs language and switch `language="markdown"` → `language="xml"` for system messages.
**Pros:** Zero structural change, minimal code.
**Cons:** Tags get colored but content is still one big wall of text. Doesn't help with navigation or readability of large messages.

### Variant 2: Collapsible Accordion Panels ✅ CHOSEN
Parse `<context key="...">...</context>` blocks from the text. Render each as a collapsible `<details>/<summary>` panel (like the existing `ReasoningSection`). Non-block text rendered as a plain CodeBox.
**Pros:** Matches existing UI patterns exactly. Scannable key names. Collapsed by default = no info overload. Inner content can still be syntax-highlighted.
**Cons:** Adds a parse step; need to handle edge cases (malformed XML, nested tags).

### Variant 3: Tabbed Interface
Parse blocks into tabs. Click a tab to view one block at a time.
**Pros:** Compact. Good for quick comparison.
**Cons:** You can only see one block at a time. Needs React state management. Harder to read blocks in sequence.

### Variant 4: Card Grid with Color-Coded Headers
Render each block as a colored card (like `MessageBox` left-border cards). Each key gets a distinct color.
**Pros:** Visual differentiation at a glance.
**Cons:** Color palette runs out quickly. Cards are always expanded = still a wall of text. No structural benefit over current state.

### Variant 5: Inline with Visual Separators
Keep single CodeBox but inject styled separators (horizontal rules + key badges) between blocks using post-processing.
**Pros:** Simple, preserves search-in-box behavior.
**Cons:** Hard to implement correctly with hljs post-processing. No collapsing.

## Decision: Variant 2 — Collapsible Accordion Panels

Best matches the existing `ReasoningSection` component pattern. Provides:
- Scannable list of block key names
- Collapsed by default (first block auto-opens, the rest collapsed)
- Inner content with markdown CodeBox
- Char count in header for quick orientation

## Implementation Plan

### New File: `ContextBlockRenderer.tsx`

Location: `packages/agent006-viewer/frontend-react/src/components/shared/ContextBlockRenderer.tsx`

Responsibilities:
1. Parse the raw text for `<key_name ...>...\n</key_name>` blocks using the correct regex
2. Split content into segments: `{type: 'text', content}` | `{type: 'block', key, expr?, content}`
3. Render text segments as `<CodeBox language="markdown" showLineNumbers={false} />` (only if non-empty/non-whitespace)
4. Render block segments as collapsible panels:
   - First block open by default, rest collapsed
   - Header: key name badge + optional expr label + char count
   - Body: `<CodeBox language="markdown" showLineNumbers={false} maxHeight="400px" />`
5. If no context blocks found, fall through to a plain `<CodeBox>` (backwards compatible)

Parsing regex:
```
/<([a-zA-Z_][a-zA-Z0-9_-]*)([^>]*)>\n([\s\S]*?)\n<\/\1>/g
```
Capture groups: `[1]` = key name, `[2]` = attributes string (for extracting `expr`), `[3]` = content.

Extract `expr` from attributes string: `/expr="([^"]+)"/`.

### Modified: `LLMCallPlugin.tsx`

In `MessageBox`: when `role === 'system'`, use `<ContextBlockRenderer>` instead of `<CodeBox>`.

### Modified: `SpanPlugin.tsx`

The `agent006.system_message` hero content:
- Check `attrs['agent006.system_message.is_diff']`: if `true`, render as plain `<CodeBox>` (it's a diff, not XML blocks)
- If `false`/absent: render through `<ContextBlockRenderer>`

Implement by adding a check in the `SpanPlugin` render body (not in `findHeroContent`), after extracting the hero.
`agent006.user_message` — intentionally unchanged (plain prose, not XML blocks).

### Styling

- `<details>` with `open` prop for first block (matches `ReasoningSection` pattern)
- Summary: `bg-teal-900/20 border-teal-700 text-teal-300` for user-defined blocks
- Summary: `bg-gray-800/40 border-gray-600 text-gray-400` for framework blocks (`system_prompt`, `self`, `doc`, `instructions`)
- Inner: `<CodeBox language="markdown" showLineNumbers={false} maxHeight="400px" />`

Framework key names (gray): `system_prompt`, `self`, `doc`, `instructions`
User blocks (teal): everything else

## Files Changed

| File | Change |
|------|--------|
| `src/components/shared/ContextBlockRenderer.tsx` | **NEW** — parser + accordion renderer |
| `src/components/plugins/LLMCallPlugin.tsx` | `MessageBox` uses `ContextBlockRenderer` for `role==='system'` |
| `src/components/plugins/SpanPlugin.tsx` | System message hero uses `ContextBlockRenderer` |

## Tests / Verification

- `npm run build` in the frontend package must succeed (TypeScript must compile)
- Open trace viewer and verify system messages render as accordions with block key names
- Verify non-system messages (user/assistant) still use plain CodeBox
- Verify graceful fallback when content has no `<tag>` blocks (no accordions, plain CodeBox)
- Verify `is_diff=true` system message renders as plain CodeBox (unified diff stays intact)
