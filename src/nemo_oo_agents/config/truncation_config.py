"""Configuration for output truncation across the agent framework."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TruncationConfig(BaseModel):
    """Controls output size at render time.

    max_block_chars: Per-block character clamp. Applied to context blocks and events.
    max_context_tokens: Total token budget for system/context blocks (None = no limit).
                        Requires count_tokens to be passed to render_context().
    max_event_tokens: Total token budget for event/message blocks (None = no limit).
                      Requires count_tokens to be passed to render_context().
    max_stdout_chars: Max chars from execute_python() stdout per cell.
    max_stderr_chars: Max chars from execute_python() stderr per cell.
    max_pprint_elements: Max container elements in pprint output.
    max_pprint_string: Max string chars in pprint output.
    max_pprint_depth: Max nesting depth in pprint output.
    """

    model_config = ConfigDict(frozen=True)

    max_block_chars: Annotated[int, Field(description="Max chars per block")] = 20_000
    max_context_tokens: Annotated[int | None, Field(description="Total context token budget")] = (
        None
    )
    max_event_tokens: Annotated[int | None, Field(description="Total event token budget")] = None
    max_stdout_chars: Annotated[int, Field(description="Max stdout chars")] = 50_000
    max_stderr_chars: Annotated[int, Field(description="Max stderr chars")] = 20_000
    stdout_tail_chars: Annotated[
        int | None,
        Field(
            description=(
                "Chars reserved for the tail window in stdout/stderr capture. "
                "None = half of the total limit (50/50 split). "
                "Wired through for the file-writing escape hatch (#150)."
            )
        ),
    ] = None
    max_pprint_elements: Annotated[
        int | None, Field(description="Max container elements in pprint")
    ] = 50
    max_pprint_string: Annotated[int | None, Field(description="Max string chars in pprint")] = 500
    max_pprint_depth: Annotated[int | None, Field(description="Max nesting depth in pprint")] = 4
    max_pre_format_chars: Annotated[
        int,
        Field(
            description=(
                "Hard character cap applied to pformat output before block-level truncation. "
                "Prevents OOM when an agent returns a very large Python object (e.g. a 10 M-element "
                "list). Block-level truncation (max_block_chars) still applies afterwards; "
                "this is purely a safety net for the serialisation step. "
                "Default: 500,000 chars (25× max_block_chars)."
            )
        ),
    ] = 500_000

    @model_validator(mode="after")
    def _check_values(self) -> "TruncationConfig":
        errors = []

        for name in (
            "max_block_chars",
            "max_stdout_chars",
            "max_stderr_chars",
            "max_pre_format_chars",
        ):
            if getattr(self, name) <= 0:
                errors.append(f"{name} must be > 0, got {getattr(self, name)}")

        for name in ("max_context_tokens", "max_event_tokens"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                errors.append(f"{name} must be > 0 or None, got {v}")

        for name in ("max_pprint_elements", "max_pprint_string", "max_pprint_depth"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                errors.append(f"{name} must be > 0 or None, got {v}")

        if self.stdout_tail_chars is not None:
            if self.stdout_tail_chars < 0:
                errors.append(f"stdout_tail_chars must be >= 0, got {self.stdout_tail_chars}")
            elif self.stdout_tail_chars >= self.max_stdout_chars:
                errors.append(
                    f"stdout_tail_chars ({self.stdout_tail_chars:,}) must be less than "
                    f"max_stdout_chars ({self.max_stdout_chars:,})"
                )
            if self.stdout_tail_chars >= self.max_stderr_chars:
                errors.append(
                    f"stdout_tail_chars ({self.stdout_tail_chars:,}) must be less than "
                    f"max_stderr_chars ({self.max_stderr_chars:,}) — "
                    f"stdout_tail_chars is used as the tail window for both stdout and stderr capture"
                )

        if errors:
            raise ValueError("Invalid TruncationConfig:\n" + "\n".join(f"  - {e}" for e in errors))

        return self

    def merge_with(self, other: "TruncationConfig | None") -> "TruncationConfig":
        """Merge with another config (other takes precedence for explicitly-set fields).

        Only fields that were explicitly passed to the other config's __init__
        will override. This means TruncationConfig(max_block_chars=5000) correctly
        overrides, unlike approaches that compare against default values.

        Args:
            other: Config to merge with (overrides self). None returns self unchanged.
        """
        if other is None:
            return self
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: TruncationConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})


# Default configuration
DEFAULT_TRUNCATION_CONFIG = TruncationConfig()
