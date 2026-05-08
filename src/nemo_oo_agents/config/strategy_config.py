# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strategy configuration for CodeAct, Predict, and Reflexion strategies."""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from nemo_oo_agents.runtime.restrictions import RestrictionsConfig

if TYPE_CHECKING:
    from nemo_oo_agents.strategies.prefill import Prefill  # noqa: F401


def _default_prefill() -> Any:
    """Lazy default-factory for ``CodeActConfig.prefill``.

    Imports inside the function so we avoid the
    ``config -> strategies.prefill -> strategies.__init__ -> config`` cycle.
    """
    from nemo_oo_agents.strategies.prefill import InspectInputsPrefill

    return InspectInputsPrefill()


class CodeActConfig(BaseModel):
    """Config for CodeActStrategy."""

    # ``arbitrary_types_allowed`` lets us put a ``Prefill`` protocol instance
    # in the config (Pydantic doesn't validate protocols natively).
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    max_iterations: int | None = None
    max_retries: int = 3
    cell_timeout: float | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tool_calls: int | None = None
    translate_tool_calls: bool = False
    restrictions: RestrictionsConfig = RestrictionsConfig()
    # Prefill plugin to run before the main generation loop.
    #
    #   * Default — ``InspectInputsPrefill()`` auto-renders every parameter via
    #     ``pformat`` (the slice-keys / items markers from truncation 3.0).
    #   * ``None`` — disable prefill entirely. Pre-ellipsis code (statements
    #     between the docstring and the ``...`` marker) still runs regardless.
    #   * Custom ``Prefill`` instance — full control over turn-1 setup. The
    #     ``Prefill`` protocol is a single method::
    #
    #         def get_code(self, call, config=None) -> str | None: ...
    #
    #     Returning a Python source string runs it as a synthetic prefill step
    #     in the REPL session; returning ``None`` skips that step.
    #
    # To override the *strategy prompt* (the "## Strategy" instruction block),
    # use the existing ``@strategy(..., context=ScopedContext(...))`` decorator
    # API — the decorator-context phase runs after strategy overrides and wins:
    #
    #     @strategy(
    #         CodeActStrategy(),
    #         ScopedContext(context={"strategy_prompt": "Custom instructions."}),
    #     )
    #     async def my_method(self, ...): ...
    # Typed as ``Any`` to sidestep Pydantic's forward-reference resolution for
    # the ``Prefill`` protocol (which lives in ``strategies.prefill`` and would
    # create an import cycle if eagerly resolved here). The runtime contract
    # is ``Prefill | None`` — a duck-typed object with a ``get_code`` method
    # or ``None`` to disable.
    prefill: Any = Field(default_factory=_default_prefill)

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
    # ``None`` = unconstrained (parameter-size guard disabled).
    max_param_chars: int | None = 200_000

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
