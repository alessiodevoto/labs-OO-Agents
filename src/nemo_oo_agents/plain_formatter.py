"""PlainBlockFormatter — renders conversation messages as plain text.

Strips XML wrapping and renders events as clean, human-readable text.
Event fields marked repr=False in the model are excluded from output.

Use by setting _block_formatter on your agent:
    class MyAgent(Agent):
        _block_formatter = PlainBlockFormatter()
"""

from context_blocks.events import EventBase
from context_blocks.formatter import FORMAT_PLAIN, FormatType, XMLBlockFormatter
from context_blocks.utils import _MAX_PRE_FORMAT_CHARS, safe_pformat


class PlainBlockFormatter(XMLBlockFormatter):
    """Renders system blocks as XML (same as XMLBlockFormatter) but serializes
    conversation events as plain text with no XML wrapper.

    Produces cleaner, more token-efficient messages for the LLM.
    Which fields are shown is controlled by repr=False markers on the event
    model — fields marked repr=False are infrastructure and are skipped.

    Single-field events return the value directly (no wrapper tag).
    Multi-field events use <field>value</field> XML element tags.
    """

    @property
    def format_type(self) -> FormatType:
        return FORMAT_PLAIN

    def format_event(self, event: EventBase, max_chars: int = _MAX_PRE_FORMAT_CHARS) -> str:
        """Serialize event fields not marked repr=False as plain XML elements."""

        def render(value: object) -> str:
            return safe_pformat(value, max_chars=max_chars)

        def is_empty(value: object) -> bool:
            return value is None or value == ""

        items = [
            (name, getattr(event, name))
            for name, fi in type(event).model_fields.items()
            if fi.repr is not False and not is_empty(getattr(event, name))
        ]

        if not items:
            return "(no output)"

        if len(items) == 1:
            return render(items[0][1])

        return "\n".join(f"<{name}>{render(value).rstrip()}</{name}>" for name, value in items)
