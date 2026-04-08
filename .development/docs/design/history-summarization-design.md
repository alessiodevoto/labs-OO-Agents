# History Summarization Design

**Status**: Draft - Future Implementation
**Author**: Claude + Developer
**Date**: 2026-01-31

---

## Implementation Status

This document describes a **future design** for automatic LLM-based history summarization.

**Currently Implemented (this branch):**
- `BeforeTurnEvent` / `AfterTurnEvent` - Turn lifecycle events
- `HistoryManager.apply_policy()` - Policy hook infrastructure
- `HistoryManager.process_turn_events()` - Event-driven async policy dispatch
- `HistoryBackend` protocol - Pluggable storage abstraction
- `collapse()` method - Manual range collapse/summarization
- `RuntimeServices.get_generation_id()` / `get_parent_generation_id()` - Generation context access

**Not Yet Implemented:**
- `SummarizerSpec` configuration class
- `HistoryPolicy` implementations (TokenBudgetPolicy, SlidingWindowPolicy, etc.)
- `HistoryRenderer` abstraction
- Async background summarization (`schedule_summarization`, `apply_pending_summary`)
- Agent-accessible APIs (`summarize_now()`, `request_summarization()`)

---

## Executive Summary

This design extends the existing history management mechanics (`collapse()`, `SummaryEvent`, string tags) with **automatic LLM-based summarization**. The key challenge is running summarization **asynchronously** without blocking agent generation, while providing a clean developer API with sensible defaults.

### Key Goals

1. **Nice developer interface** - Easy to configure with sensible defaults, possible to customize
2. **Async summarization** - Runs in background, doesn't block generation
3. **History rendering abstraction** - Clean way to render history for summarization LLM
4. **Dual API** - Available to developers (configuration) and agents (runtime)

---

## Design Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           HistoryManager                                 │
│                                                                          │
│  ┌────────────────┐   ┌─────────────────┐   ┌────────────────────────┐  │
│  │ SummarizerSpec │   │   Summarizer    │   │   HistoryRenderer      │  │
│  │ (Configuration)│ → │ (LLM + Render)  │ → │ (Events → Markdown)    │  │
│  └────────────────┘   └─────────────────┘   └────────────────────────┘  │
│           │                    │                                         │
│           ▼                    ▼                                         │
│  ┌────────────────┐   ┌─────────────────┐                               │
│  │  Policy Layer  │   │ SummarizationTask│                              │
│  │ (When/What)    │   │ (Background Mgmt)│                              │
│  └────────────────┘   └─────────────────┘                               │
│                                                                          │
│  Triggers: BeforeTurnEvent, AfterTurnEvent                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Core Flow

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────┐
│ BeforeTurn  │───▶│ policy.on_before │───▶│ LLM Call    │
│ Event       │    │ _turn()          │    │             │
│ (turn_num=N)│    │                  │    │             │
└─────────────┘    └──────────────────┘    └─────────────┘
       │                  │                       │
       │                  ▼                       │
       │           ┌──────────────────┐           │
       │           │ apply_pending_   │           │
       │           │ summary() if     │           │
       │           │ ready            │           │
       │           └──────────────────┘           │
       │                                          │
       │  is_first_turn?                          │
       │  → method initialization                 ▼
       │                                   ┌─────────────┐
       │                                   │ AfterTurn   │
       │                                   │ Event       │
       │                                   │ (turn_num=N)│
       │                                   └─────────────┘
       │                                          │
       │                              ┌───────────┴───────────┐
       │                              │                       │
       │                              ▼                       ▼
       │                       is_last_turn?           not last turn
       │                              │                       │
       │                              ▼                       │
       │                    ┌──────────────────┐              │
       │                    │ schedule_        │              │
       │                    │ summarization()  │◀─────────────┘
       │                    │ (async task)     │  (or skip)
       │                    └──────────────────┘
       │
       └──────────────── loop back for next turn ────────────────┘
```

---

## 1. Developer API: SummarizerSpec

The primary configuration interface. Provides sensible defaults but allows deep customization.

### 1.1 Basic Usage (Sensible Defaults)

```python
from agent006 import Agent

# Default: No summarization, history grows unbounded
agent = Agent()

# Enable auto-summarization with defaults
agent = Agent(
    history_policy="auto"  # or HistoryPolicy.auto()
)
```

**Default "auto" policy behavior:**
- Trigger: After method completion (`AfterTurnEvent` where `is_last_turn=True`)
- Threshold: When history exceeds 100 events or ~50k tokens
- Target: Summarize oldest 80% of events, keep recent 20%
- Summarizer: LLM call with default prompt

### 1.2 Customized Configuration

```python
from agent006.runtime.summarization import SummarizerSpec, TokenBudgetPolicy

class MyAgent(Agent, llm=my_llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Full control via SummarizerSpec
        self.history.summarizer = SummarizerSpec(
            # LLM Configuration
            llm=cheap_llm,  # Use cheaper model for summarization

            # When to trigger (Policy)
            policy=TokenBudgetPolicy(
                max_tokens=50_000,          # Trigger when history exceeds this
                target_tokens=30_000,       # Summarize down to this
                preserve_recent=10,         # Always keep last N events
            ),

            # How to render history for summarization LLM
            renderer=MarkdownRenderer(
                max_chars_per_event=500,    # Truncate long events
                include_metadata=False,     # Skip timestamps, etc.
            ),

            # Summarization behavior
            prompt_template="...",          # Custom prompt
        )
```

### 1.3 SummarizerSpec Fields

```python
@dataclass
class SummarizerSpec:
    """Configuration for history summarization."""

    # === LLM ===
    llm: UnifiedLLM | None = None  # None = use agent's LLM

    # === Policy (when to summarize) ===
    policy: HistoryPolicy = field(default_factory=TokenBudgetPolicy)

    # === Rendering (how to show history to summarization LLM) ===
    renderer: HistoryRenderer = field(default_factory=MarkdownRenderer)

    # === Summarization parameters ===
    prompt_template: str | None = None  # None = use default
```

---

## 2. HistoryRenderer: Events → Markdown

An abstraction to render events in a format suitable for the summarization LLM.

### 2.1 Interface

```python
from abc import ABC, abstractmethod

class HistoryRenderer(ABC):
    """Renders history events for summarization."""

    @abstractmethod
    def render(self, events: list[EventBase], max_tokens: int | None = None) -> str:
        """Render events into text for summarization LLM.

        Args:
            events: Events to render (may include truncated content).
            max_tokens: Optional token budget for output.

        Returns:
            Text representation suitable for summarization.
        """
        pass

    @abstractmethod
    def render_event(self, event: EventBase, index: int) -> str:
        """Render a single event.

        Args:
            event: The event to render.
            index: Position in the list (for reference in summary).

        Returns:
            Text representation of this event.
        """
        pass
```

### 2.2 Default Implementation: MarkdownRenderer

```python
class MarkdownRenderer(HistoryRenderer):
    """Default renderer - outputs Markdown."""

    def __init__(
        self,
        max_chars_per_event: int = 2000,
        include_metadata: bool = True,
        show_truncation_notice: bool = True,
    ):
        self.max_chars_per_event = max_chars_per_event
        self.include_metadata = include_metadata
        self.show_truncation_notice = show_truncation_notice

    def render(self, events: list[EventBase], max_tokens: int | None = None) -> str:
        lines = []
        for i, event in enumerate(events):
            lines.append(self.render_event(event, i))

        text = "\n\n---\n\n".join(lines)

        if max_tokens:
            text = truncate_to_tokens(text, max_tokens)

        return text

    def render_event(self, event: EventBase, index: int) -> str:
        # Check if already truncated by formatter
        content = self._extract_content(event)
        truncation_info = self._detect_truncation(content)

        if truncation_info:
            shown, total = truncation_info
            content = f"[Content truncated: {shown}/{total} chars]\n{content}"

        # Apply our own truncation if needed
        if len(content) > self.max_chars_per_event:
            content = content[:self.max_chars_per_event]
            if self.show_truncation_notice:
                content += f"\n[...truncated at {self.max_chars_per_event} chars...]"

        return self._format_event(event, index, content)

    def _format_event(self, event: EventBase, index: int, content: str) -> str:
        match event.tag:
            case "task":
                return f"## [{index}] User\n{content}"
            case "assistant":
                return f"## [{index}] Assistant\n{content}"
            case "tool_call":
                result = event.result.output[:1000] if event.result else "_pending_"
                return f"## [{index}] Tool: {event.name}\n**Args:** `{event.arguments}`\n**Result:** {result}"
            case "summary":
                return f"## [{index}] Summary [{event.history_tag}]\n{event.summary_text or '_truncated_'}"
            case "error":
                return f"## [{index}] Error\n**{event.error_type}:** {content}"
            case _:
                return f"## [{index}] {event.tag}\n{content[:500]}"

    def _detect_truncation(self, content: str) -> tuple[int, int] | None:
        """Detect if content was already truncated by formatter."""
        import re
        match = re.search(r"(\d+) chars total.*first (\d+) chars", content)
        if match:
            return int(match.group(2)), int(match.group(1))
        return None
```

**Why Markdown:**
- Readable by humans for debugging
- Clear structure for LLM consumption
- Easy to truncate while maintaining readability

---

## 3. Summarization Policies

Policies determine **when** and **what** to summarize.

### 3.1 Policy Interface

```python
from abc import ABC, abstractmethod

class HistoryPolicy(ABC):
    """Determines when and what to summarize.

    Policies register sync handlers via install(). All methods are SYNC
    because the actual summarization runs in a background task.
    """

    @abstractmethod
    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        """Register event handlers with history manager.

        Called once when policy is configured. Register handlers via:
        - history.on("before_turn", handler)
        - history.on("after_turn", handler)
        """
        pass

    def uninstall(self, history: HistoryManager) -> None:
        """Remove registered handlers. Optional."""
        pass
```

**Why sync?** All policy operations are sync:
- `apply_pending_summary()` - checks `.ready`, calls `collapse()`
- `schedule_summarization()` - creates `asyncio.Task`, returns immediately
- `_emergency_truncate()` - iterates and calls `collapse()`

The only async part is the background LLM call, which runs independently.

### 3.2 Built-in Policies

#### TokenBudgetPolicy (Reactive)

```python
class TokenBudgetPolicy(HistoryPolicy):
    """Summarize when history exceeds token budget."""

    def __init__(
        self,
        max_tokens: int = 100_000,
        target_tokens: int = 50_000,
        preserve_recent: int = 10,
    ):
        self.max_tokens = max_tokens
        self.target_tokens = target_tokens
        self.preserve_recent = preserve_recent

    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        def on_before_turn(event: BeforeTurnEvent):
            # Apply any ready summaries (sync)
            history.apply_pending_summary()

            # Emergency truncation if still over (sync)
            if history.estimate_tokens() > self.max_tokens * 1.2:
                self._emergency_truncate(history)

        def on_after_turn(event: AfterTurnEvent):
            # Only summarize after method completes
            if not event.is_last_turn:
                return

            if history.estimate_tokens() > self.max_tokens:
                tags = history.active_tags()
                end_idx = len(tags) - self.preserve_recent
                if end_idx > 0:
                    # Sync - creates background task
                    history.schedule_summarization(tags[0], tags[end_idx])

        history.on("before_turn", on_before_turn)
        history.on("after_turn", on_after_turn)
```

#### SlidingWindowPolicy

```python
class SlidingWindowPolicy(HistoryPolicy):
    """Keep a fixed window of recent events."""

    def __init__(
        self,
        window_size: int = 50,
        summarize_batch: int = 30,
    ):
        self.window_size = window_size
        self.summarize_batch = summarize_batch

    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        def on_before_turn(event: BeforeTurnEvent):
            history.apply_pending_summary()

        def on_after_turn(event: AfterTurnEvent):
            if not event.is_last_turn:
                return

            tags = history.active_tags()
            if len(tags) > self.window_size:
                overflow = len(tags) - self.window_size
                batch_end = min(overflow, self.summarize_batch)
                history.schedule_summarization(tags[0], tags[batch_end])

        history.on("before_turn", on_before_turn)
        history.on("after_turn", on_after_turn)
```

#### ProactiveMethodPolicy (Per-Method)

```python
class ProactiveMethodPolicy(HistoryPolicy):
    """Summarize each method's events when it completes.

    Requires events to have 'generation_id' in metadata.
    """

    def __init__(
        self,
        min_events: int = 3,       # Don't summarize tiny methods
        exclude_root: bool = True,  # Don't summarize top-level method
    ):
        self.min_events = min_events
        self.exclude_root = exclude_root

    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        def on_before_turn(event: BeforeTurnEvent):
            history.apply_pending_summary()

        def on_after_turn(event: AfterTurnEvent):
            if not event.is_last_turn:
                return

            # Skip root method if configured
            if self.exclude_root and event.parent_generation_id is None:
                return

            # Find events belonging to this method call
            events_for_method = history.for_generation(event.generation_id)

            if len(events_for_method) < self.min_events:
                return

            start_tag = events_for_method[0][0]
            end_tag = events_for_method[-1][0]
            history.schedule_summarization(start_tag, end_tag)

        history.on("before_turn", on_before_turn)
        history.on("after_turn", on_after_turn)
```

#### CompositePolicy (Combine Multiple)

```python
class CompositePolicy(HistoryPolicy):
    """Combine multiple policies."""

    def __init__(self, *policies: HistoryPolicy):
        self.policies = policies

    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        # Each policy registers its own handlers
        for policy in self.policies:
            policy.install(history, budget_tokens)


# Convenience function
def combine_policies(*policies: HistoryPolicy) -> CompositePolicy:
    """Combine multiple summarization policies."""
    return CompositePolicy(*policies)
```

#### TruncationOnlyPolicy (No Summarization)

```python
class TruncationOnlyPolicy(HistoryPolicy):
    """No summarization - relies on global emergency truncation.

    Use this when you want history to grow naturally.
    HistoryManager's global safety net will truncate if needed.
    """

    def install(self, history: HistoryManager, budget_tokens: int) -> None:
        # No handlers registered - global failsafe handles truncation
        pass
```

### 3.3 Example: Combined Policies

```python
from agent006.runtime.summarization import (
    SummarizerSpec,
    TokenBudgetPolicy,
    ProactiveMethodPolicy,
    combine_policies,
)

class ExperimentalAgent(Agent, llm=my_llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Experiment: summarize methods proactively + token budget as safety net
        self.history.summarizer = SummarizerSpec(
            policy=combine_policies(
                # First: check if method just finished → summarize it
                ProactiveMethodPolicy(min_events=3, exclude_root=True),
                # Second: safety net for token budget
                TokenBudgetPolicy(max_tokens=80_000, preserve_recent=5),
            ),
        )
```

---

## 4. Event Generation Tagging

For `ProactiveMethodPolicy` to work, events must have `generation_id` in metadata.

### 4.1 Runtime Tagging

```python
# In RuntimeServices or strategy execution
class RuntimeServices:
    def add_event(self, event: EventBase) -> str:
        """Add event to history with generation context."""
        if self._current_generation_id:
            event.metadata["generation_id"] = self._current_generation_id
        if self._current_method_name:
            event.metadata["method_name"] = self._current_method_name
        if self._parent_generation_id:
            event.metadata["parent_generation_id"] = self._parent_generation_id

        return self.history.add(event)
```

### 4.2 Query Support

```python
class HistoryManager:
    def for_generation(self, generation_id: str) -> list[tuple[str, EventBase]]:
        """Return (tag, event) pairs for events in a generation session."""
        return [
            (tag, event) for tag, event in self.active()
            if event.metadata.get("generation_id") == generation_id
        ]
```

---

## 4.5 Event-Driven Policy Dispatch via Sync Handlers

Policy work uses **sync `on()` handlers** - no async needed because:
- `apply_pending_summary()` is sync (checks `.ready`, calls `collapse()`)
- `schedule_summarization()` is sync (just calls `asyncio.create_task()`)
- `_emergency_truncate()` is sync
- Only the background summarization LLM call is async, and it runs independently

### The Pattern: Sync Handlers + Background Tasks

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Turn Lifecycle                                   │
│                                                                          │
│   history.add(BeforeTurnEvent)                                          │
│        │                                                                 │
│        ▼                                                                 │
│   on("before_turn") handlers run (SYNC)                                 │
│        │                                                                 │
│        ├── apply_pending_summary()  ← if ready, collapse now            │
│        └── _emergency_truncate()    ← failsafe if over budget           │
│                                                                          │
│   ... LLM generation happens ...                                        │
│                                                                          │
│   history.add(AfterTurnEvent)                                           │
│        │                                                                 │
│        ▼                                                                 │
│   on("after_turn") handlers run (SYNC)                                  │
│        │                                                                 │
│        └── schedule_summarization() ← creates background task           │
│                    │                                                     │
│                    ▼                                                     │
│              asyncio.create_task()  ← runs independently                │
└─────────────────────────────────────────────────────────────────────────┘
```

### Implementation

**Policy Registration:**

```python
class TokenBudgetPolicy:
    def install(self, history: HistoryManager, budget_tokens: int):
        """Register sync handlers with history manager."""

        def on_before_turn(event: BeforeTurnEvent):
            # Apply any ready summaries (sync - just checks and collapses)
            history.apply_pending_summary()

            # Emergency truncation if needed (sync)
            if history.estimate_tokens() > budget_tokens * 1.2:
                history._emergency_truncate(budget_tokens)

        def on_after_turn(event: AfterTurnEvent):
            if not event.is_last_turn:
                return

            # Schedule background summarization (sync - creates task)
            if history.estimate_tokens() > budget_tokens:
                tags = history.active_tags()
                history.schedule_summarization(tags[0], tags[-10])

        history.on("before_turn", on_before_turn)
        history.on("after_turn", on_after_turn)
```

**HistoryManager - All Sync:**

```python
class HistoryManager:
    def apply_pending_summary(self) -> bool:
        """Apply completed summary if ready. SYNC."""
        if not self._pending_summary or not self._pending_summary.ready:
            return False

        task = self._pending_summary
        self.collapse(task.start_tag, task.end_tag, summary_text=task._result)
        self._pending_summary = None
        return True

    def schedule_summarization(self, start_tag: str, end_tag: str) -> SummarizationTask:
        """Schedule background summarization. SYNC (creates async task)."""
        if self._pending_summary:
            return self._pending_summary  # Already have one pending

        events = self._events_in_range(start_tag, end_tag)
        task = SummarizationTask(start_tag=start_tag, end_tag=end_tag, status="pending")

        async def _do_summarize():
            task.status = "running"
            try:
                task._result = await self._summarizer.summarize(events)
                task.status = "completed"
            except Exception as e:
                task._error = e
                task.status = "failed"

        task._task = asyncio.create_task(_do_summarize())  # Fire and forget
        self._pending_summary = task
        return task
```

### Why This Works

1. **Handlers run in `add()`** - guaranteed timing, before/after LLM calls
2. **All policy work is sync** - no awaiting needed
3. **Background task runs independently** - doesn't block anything
4. **`apply_pending_summary()` is the sync point** - only applies when called

### No `on_async()` Needed

Since `schedule_summarization()` just creates a task and returns immediately, we don't need async handlers. The actual LLM work runs in a detached background task that completes whenever it completes - we just check `.ready` on the next `before_turn`.

---

## 5. Async Summarization: Schedule + Apply Pattern

The key innovation: summarization runs **asynchronously** but application is **controlled by policy**.

### 5.1 Why Schedule + Apply (Not Fire-and-Forget)

**Fire-and-Forget (rejected):**
```python
asyncio.create_task(self._run_summarization(task))
# Applies immediately when done - could be mid-generation!
```

**Problems:**
- History changes unpredictably mid-generation
- No control over when summary is applied
- Race conditions with concurrent operations

**Schedule + Apply (chosen):**
```python
# Schedule returns task, doesn't apply yet
task = history.schedule_summarization(start, end)

# Policy controls when to apply (at turn boundary)
async def on_before_turn(self, history, budget, event):
    applied = history.apply_pending_summary()  # Only if ready
```

**Benefits:**
- Summaries only applied at turn boundaries (predictable)
- Policy controls timing
- Debuggable: can see pending/running/completed/failed
- Cancelable: `task.cancel()` if priorities change

### 5.2 SummarizationTask

```python
@dataclass
class SummarizationTask:
    """Tracks a background summarization job."""

    task_id: str
    start_tag: str
    end_tag: str
    status: Literal["pending", "running", "completed", "failed"]

    # Async task reference
    _task: asyncio.Task | None = None
    _result: str | None = None
    _error: Exception | None = None

    @property
    def ready(self) -> bool:
        return self.status == "completed"

    def cancel(self) -> bool:
        if self._task and not self._task.done():
            self._task.cancel()
            return True
        return False
```

### 5.3 Global Emergency Truncation (Failsafe)

Emergency truncation runs as a **sync handler** registered by HistoryManager itself, after all policy handlers. This is a global failsafe that prevents context overflow regardless of policy.

```python
class HistoryManager:
    def _install_failsafe(self, budget_tokens: int) -> None:
        """Register global failsafe handler (runs after policy handlers)."""

        def failsafe_truncate(event: BeforeTurnEvent):
            # Triggers at 150% of budget (more aggressive than policy threshold)
            if self.estimate_tokens() > budget_tokens * 1.5:
                self._emergency_truncate(budget_tokens)

        # Register with low priority so it runs after policy handlers
        self.on("before_turn", failsafe_truncate, priority=-100)

    def _emergency_truncate(
        self,
        budget_tokens: int,
        preserve_recent: int = 5,
    ) -> None:
        """FIFO removal as last resort before context overflow. SYNC."""
        tags = self.active_tags()

        while self.estimate_tokens() > budget_tokens * 1.2:
            if len(tags) <= preserve_recent:
                break  # Can't truncate further

            oldest = tags.pop(0)

            # Don't orphan tool results
            if self._would_orphan_tool_result(oldest):
                continue

            # Truncate without summary text
            self.collapse(oldest, oldest, summary_text=None)
            logger.warning(f"Emergency truncation: collapsed event {oldest}")
```

**Threshold hierarchy:**
- Policy truncation: ~120% of budget (configurable per policy)
- Global failsafe: 150% of budget (always runs as last resort)

### 5.4 HistoryManager Summarization Methods

```python
class HistoryManager:
    _pending_summary: SummarizationTask | None = None
    _summarizer: Summarizer | None = None

    def schedule_summarization(
        self,
        start_tag: str,
        end_tag: str,
        priority: bool = False,
    ) -> SummarizationTask:
        """Schedule background summarization.

        Args:
            start_tag: First event to summarize
            end_tag: Last event to summarize
            priority: If True, cancel existing task

        Returns:
            SummarizationTask for tracking
        """
        if self._pending_summary and not priority:
            return self._pending_summary  # Already have pending work

        if self._pending_summary:
            self._pending_summary.cancel()

        events = self._events_in_range(start_tag, end_tag)

        task = SummarizationTask(
            task_id=str(uuid4()),
            start_tag=start_tag,
            end_tag=end_tag,
            status="pending",
        )

        async def _do_summarize():
            task.status = "running"
            try:
                context = SummarizationContext(
                    total_events=len(self._events),
                    active_events=len(self._active_tags),
                )
                task._result = await self._summarizer.summarize(events, context)
                task.status = "completed"
            except Exception as e:
                task._error = e
                task.status = "failed"

        task._task = asyncio.create_task(_do_summarize())
        self._pending_summary = task
        return task

    def apply_pending_summary(self) -> bool:
        """Apply completed summary if ready.

        Returns True if summary was applied.
        """
        if not self._pending_summary or not self._pending_summary.ready:
            return False

        task = self._pending_summary

        # Validate range still valid (events might have changed)
        if not self._range_still_valid(task.start_tag, task.end_tag):
            self._pending_summary = None
            return False

        self.collapse(
            task.start_tag,
            task.end_tag,
            summary_text=task._result,
        )
        self._pending_summary = None
        return True

    def _range_still_valid(self, start_tag: str, end_tag: str) -> bool:
        """Check if the range is still valid for collapsing."""
        active = self.active_tags()
        if start_tag not in active or end_tag not in active:
            return False
        start_idx = active.index(start_tag)
        end_idx = active.index(end_tag)
        return start_idx <= end_idx
```

---

## 6. Agent-Accessible APIs

### 6.1 Agent History Interface

```python
class AgentHistoryInterface:
    """History interface available to agents via self.history."""

    # === Read Operations ===
    def recent(self, limit: int = 50) -> list[EventBase]: ...
    def active_tags(self) -> list[str]: ...
    def __getitem__(self, tag: str) -> EventBase: ...
    def children(self, summary_tag: str) -> list[EventBase]: ...
    def estimate_tokens(self) -> int: ...

    # === Summarization Operations ===
    def summarization_status(self) -> SummarizationStatus:
        """Check if background summarization is running/ready."""
        return SummarizationStatus(
            pending=self._pending_summary is not None,
            ready=self._pending_summary.ready if self._pending_summary else False,
            range=(task.start_tag, task.end_tag) if task else None,
        )

    def request_summarization(
        self,
        start_tag: str | None = None,
        end_tag: str | None = None,
    ) -> bool:
        """Request background summarization.

        Returns True if request was accepted.
        Does NOT block - summarization runs in background.
        """
        ...

    async def summarize_now(
        self,
        start_tag: str | None = None,
        end_tag: str | None = None,
    ) -> str:
        """Force immediate summarization (BLOCKS until complete).

        Use sparingly - this blocks the current generation.
        Prefer request_summarization() for non-critical cases.

        Returns the summary text.
        """
        events = self._events_in_range(start_tag, end_tag)
        context = SummarizationContext(...)
        summary = await self._summarizer.summarize(events, context)
        self.collapse(start_tag, end_tag, summary_text=summary)
        return summary
```

### 6.2 Agent Usage Example

```python
class MyAgent(Agent):
    @generation
    async def think(self, task: str) -> str:
        # Check if we should request summarization
        if self.history.estimate_tokens() > 50_000:
            tags = self.history.active_tags()
            self.history.request_summarization(
                start_tag=tags[0],
                end_tag=tags[-20],
            )

        # Check if pending summary is ready
        status = self.history.summarization_status()
        if status.ready:
            # Policy will apply it before next turn
            pass

        return "Thinking about: " + task

    @generation
    async def critical_operation(self, task: str) -> str:
        # For critical operations, force immediate summarization
        # to ensure we have maximum context budget available
        if self.history.estimate_tokens() > 80_000:
            tags = self.history.active_tags()
            summary = await self.history.summarize_now(
                start_tag=tags[0],
                end_tag=tags[-30],
            )
            # History is now compressed, proceed with full budget

        return await self._do_critical_work(task)
```

---

## 7. Turn Events

### 7.1 Event Definitions

```python
@dataclass
class BeforeTurnEvent(EventBase):
    """Emitted before each LLM generation turn."""
    tag: Literal["before_turn"] = "before_turn"
    _role: ClassVar[Role] = Role.RUNTIME_EVENT

    method_name: str
    turn_number: int           # 0, 1, 2, ...
    generation_id: str

    @property
    def is_first_turn(self) -> bool:
        """True if this is turn 0 (start of method)."""
        return self.turn_number == 0


@dataclass
class AfterTurnEvent(EventBase):
    """Emitted after each LLM generation turn."""
    tag: Literal["after_turn"] = "after_turn"
    _role: ClassVar[Role] = Role.RUNTIME_EVENT

    method_name: str
    turn_number: int
    generation_id: str
    is_last_turn: bool         # True if method is complete
    success: bool | None       # None if not last turn
    exception_type: str | None
    parent_generation_id: str | None = None  # For nested calls
```

---

## 8. OpenTelemetry Instrumentation

All summarization operations are traced for observability.

### 8.1 Span Hierarchy

```
summarization (root span)
├── summarization.render
│   └── attributes: chars, renderer_class
├── summarization.llm_call
│   └── attributes: response_chars, model
└── summarization.apply
    └── attributes: success, skip_reason
```

### 8.2 Implementation

```python
from opentelemetry import trace

tracer = trace.get_tracer("agent006.summarization")

class HistoryManager:
    async def _do_summarize(self, task: SummarizationTask) -> None:
        with tracer.start_as_current_span(
            "summarization",
            attributes={
                "summarization.start_tag": task.start_tag,
                "summarization.end_tag": task.end_tag,
                "summarization.event_count": len(task.events_snapshot),
            },
        ) as span:
            try:
                with tracer.start_as_current_span("summarization.render") as render_span:
                    rendered = self._renderer.render(task.events_snapshot)
                    render_span.set_attribute("render.chars", len(rendered))

                with tracer.start_as_current_span("summarization.llm_call"):
                    task._result = await self._summarizer.summarize(rendered)

                task.status = "completed"
                span.set_status(trace.StatusCode.OK)

            except Exception as e:
                task._error = e
                task.status = "failed"
                span.set_status(trace.StatusCode.ERROR, str(e))
                span.record_exception(e)
```

### 8.3 Policy Decision Events

```python
async def on_after_turn(self, history, budget, event):
    span = trace.get_current_span()

    should_summarize = self._evaluate(history)

    span.add_event(
        "summarization.policy_decision",
        attributes={
            "decision.should_summarize": should_summarize,
            "decision.reason": self._reason,
            "policy.class": self.__class__.__name__,
        },
    )
```

---

## 9. Default Summarization Prompt

```python
SUMMARIZATION_PROMPT = """You are summarizing a conversation history for an AI agent.

Your summary will replace the original messages, so preserve:
1. Key decisions and their reasoning
2. Important facts and information learned
3. Tasks completed and their outcomes
4. Current state and pending work

Keep the summary concise but complete. Use bullet points for clarity.
The agent will use this summary to maintain context in future turns.

Format:
## Key Decisions
- ...

## Information Learned
- ...

## Completed Tasks
- ...

## Current State
- ...
"""
```

---

## 10. Configuration Examples

### Minimal Setup

```python
agent = Agent(history_policy="auto")
```

### Explicit Configuration

```python
agent = Agent(
    history_policy=TokenBudgetPolicy(
        max_tokens=80_000,
        target_tokens=40_000,
        preserve_recent=5,
    )
)
```

### Custom Everything

```python
class MyPolicy(HistoryPolicy):
    async def on_before_turn(self, history, budget, event):
        history.apply_pending_summary()

    async def on_after_turn(self, history, budget, event):
        if event.is_last_turn and should_summarize(history):
            history.schedule_summarization(...)

agent = Agent(
    history_policy=MyPolicy(),
    summarizer=MySummarizer(model="claude-3-haiku"),
)
```

### Full Example with Different LLMs

```python
from agent006 import Agent
from agent006.runtime.summarization import (
    SummarizerSpec,
    TokenBudgetPolicy,
    MarkdownRenderer,
)
from unifiedllm import UnifiedLLM

# Agent LLM (smart, expensive)
agent_llm = UnifiedLLM(model="nvidia_nim/qwen/qwen3-next-80b-a3b-instruct")

# Summarization LLM (fast, cheap)
summary_llm = UnifiedLLM(model="nvidia_nim/meta/llama-3.1-8b-instruct")

class ResearchAgent(Agent, llm=agent_llm):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.history.summarizer = SummarizerSpec(
            llm=summary_llm,
            policy=TokenBudgetPolicy(
                max_tokens=60_000,
                target_tokens=40_000,
                preserve_recent=15,
            ),
            renderer=MarkdownRenderer(max_chars_per_event=300),
        )
```

---

## 11. Implementation Plan

### Phase 1: Core Infrastructure
- [ ] `SummarizerSpec` dataclass
- [ ] `Summarizer` protocol and `LLMSummarizer` default
- [ ] `SummarizationTask` dataclass
- [ ] `HistoryManager.schedule_summarization()`
- [ ] `HistoryManager.apply_pending_summary()`
- [ ] Default summarization prompt

### Phase 2: Rendering
- [ ] `HistoryRenderer` protocol
- [ ] `MarkdownRenderer` with truncation detection
- [ ] Handle nested summaries

### Phase 3: Built-in Policies
- [ ] `TokenBudgetPolicy`
- [ ] `SlidingWindowPolicy`
- [ ] `TruncationOnlyPolicy`
- [ ] Policy resolution from string ("auto", "truncation-only")

### Phase 4: Event Generation Tagging
- [ ] Update RuntimeServices to tag events with generation_id
- [ ] Add `HistoryManager.for_generation()` query
- [ ] `ProactiveMethodPolicy`
- [ ] `CompositePolicy`

### Phase 5: Agent Interface
- [ ] `summarization_status()` method
- [ ] `request_summarization()` method
- [ ] `summarize_now()` blocking method
- [ ] Documentation for agent developers

### Phase 6: Observability
- [ ] OTel spans for summarization operations
- [ ] Span events for policy decisions
- [ ] Optional metrics

### Phase 7: Testing & Polish
- [ ] Unit tests for all policies
- [ ] Integration tests with real LLM calls
- [ ] Performance benchmarks
- [ ] Documentation and examples

---

## 12. Design Decisions Summary

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Token estimation | litellm.token_counter() | Accurate, model-specific |
| 2 | Pending summaries | Single (no queue) | Simpler mental model |
| 3 | Emergency truncation | Global failsafe at HistoryManager | Prevents overflow regardless of policy |
| 4 | Agent blocking | `summarize_now()` | Available for critical ops |
| 5 | Turn events | Before/AfterTurnEvent with flags | Cleaner than separate events |
| 6 | Summary validation | None | Trust the LLM |
| 7 | Render format | Markdown (swappable) | Human-readable, LLM-friendly |
| 8 | Persistence | Deferred | SummaryEvent is regular event |
| 9 | Async pattern | Schedule + Apply | Predictable, debuggable |
| 10 | TruncationOnlyPolicy | No summarization, relies on failsafe | Simple option for minimal management |
| 11 | Handler type | Sync `on()` handlers | All policy work is sync; only background task is async |

---

## 13. Appendix: Microsoft Agent Framework Comparison

| Aspect | Microsoft Agent Framework | Agent006 |
|--------|---------------------------|----------|
| **Pattern** | Middleware + custom stores | Policy + background tasks |
| **Built-in summarization** | No (left to implementations) | Yes, with defaults |
| **Async** | Sync middleware | Background async |
| **Application timing** | Immediate in middleware | Controlled by policy (turn boundaries) |
| **Developer API** | Implement your own middleware | Configure SummarizerSpec |
| **Agent API** | N/A | `summarize_now()`, `request_summarization()` |
| **Rendering** | N/A | HistoryRenderer abstraction |
| **Triggers** | Middleware intercept | Runtime events |
| **Observability** | N/A | Full OTel instrumentation |

We adopt:
- ✅ Lifecycle hooks (via `BeforeTurnEvent`, `AfterTurnEvent`)
- ✅ Pluggable policies (via `HistoryPolicy`)
- ✅ Single-reducer model (no queue)
- ✅ Emergency FIFO truncation pattern (as global failsafe)
- ❌ Middleware (we use event handlers instead)
- ❌ Thread abstraction (we have `HistoryManager` directly)
- ❌ Fire-and-forget application (we use schedule + apply)

Our approach is more event-driven and fits Agent006's architecture.

---

## 14. Success Criteria

1. **Developer experience**: Can enable summarization with one line (`history_policy="auto"`)
2. **Non-blocking**: Summarization never blocks LLM generation
3. **Predictable**: Summaries only applied at turn boundaries
4. **Configurable**: Power users can customize policy, renderer, LLM
5. **Observable**: Agents can check summarization status; full OTel tracing
6. **Robust**: Handles edge cases (race conditions, LLM errors, truncation)
