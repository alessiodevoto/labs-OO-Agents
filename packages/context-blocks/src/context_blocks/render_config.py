from pydantic import BaseModel, ConfigDict, Field

from context_blocks.formatter import (
    BlockFormatter,
    OpenAIProviderFormatter,
    ProviderFormatter,
    XMLBlockFormatter,
)


class RenderConfig(BaseModel):
    """Controls how context blocks are formatted and how messages are assembled.

    block_formatter: How system prompt blocks are serialized (XML or Markdown).
    provider_formatter: How the message list is assembled for the LLM provider.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    block_formatter: BlockFormatter = Field(default_factory=XMLBlockFormatter)
    provider_formatter: ProviderFormatter = Field(default_factory=OpenAIProviderFormatter)

    def merge_with(self, other: "RenderConfig | None") -> "RenderConfig":
        if other is None:
            return self
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: RenderConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
