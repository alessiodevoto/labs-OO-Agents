"""Strategy configuration for CodeAct, Predict, and Reflexion strategies."""

from pydantic import BaseModel, ConfigDict

from nemo_oo_agents.runtime.restrictions import RestrictionsConfig


class CodeActConfig(BaseModel):
    """Config for CodeActStrategy."""

    model_config = ConfigDict(frozen=True)

    max_iterations: int = 50
    max_retries: int = 3
    cell_timeout: float = 600.0
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tool_calls: int | None = None
    translate_tool_calls: bool = False
    restrictions: RestrictionsConfig = RestrictionsConfig()

    def merge_with(self, other: "CodeActConfig") -> "CodeActConfig":
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: CodeActConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})


class PredictConfig(BaseModel):
    """Config for PredictStrategy."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = 10
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_error_chars: int = 1000

    def merge_with(self, other: "PredictConfig") -> "PredictConfig":
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: PredictConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})


class ReflexionConfig(BaseModel):
    """Config for ReflexionStrategy.

    Deprecated: ReflexionStrategy is now experimental.
    This config class is kept here for backward compatibility.
    """

    model_config = ConfigDict(frozen=True)

    max_iterations: int = 3
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None

    def merge_with(self, other: "ReflexionConfig") -> "ReflexionConfig":
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: ReflexionConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
