"""ContextVar sideband for passing rendered context block strings to the journal callback.

The agent006 runtime sets this before each litellm call so that
``MessageJournalCallback`` can content-address individual context blocks
instead of the entire merged system message.  This is transparent to litellm
and the LLM provider — the ContextVar carries no data into API calls.

**Async-boundary note:** ``ContextVar.set()`` only modifies the value in the
*current* async task's context copy.  The consumer (``_send_new_messages`` in
``_litellm_journal.py``) calls ``set_context_blocks([])`` to consume the
sideband after reading it.  This works correctly as long as the litellm call
and the callback both execute in the *same* async task as the ``set_context_blocks``
call — which is true for agent006's normal execution model (``_build_messages`` →
``litellm.acompletion`` → callback, all in the same coroutine).

If litellm ever dispatches callbacks into a *separate* spawned task, the reset
would only affect that child task's copy and the caller's ContextVar would retain
its previous value.  Should that scenario arise, use the ``Token``-based reset
pattern (``token = _current_blocks.set(...); ...; _current_blocks.reset(token)``)
to guarantee rollback in the originating context.
"""

from __future__ import annotations

from contextvars import ContextVar

# Each string is one rendered block as it appears in the system message,
# e.g. "<notes expr=\"...\">\n...\n</notes>".  Blocks are in order.
_current_blocks: ContextVar[list[str] | None] = ContextVar("_agent006_context_blocks", default=None)


def set_context_blocks(rendered_block_strings: list[str]) -> None:
    """Called by the runtime just before each litellm call."""
    _current_blocks.set(rendered_block_strings)


def get_context_blocks() -> list[str] | None:
    """Called by the journal callback inside log_pre_api_call."""
    return _current_blocks.get()
