# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy
import inspect
import json
import logging
import re
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, cast

import litellm
from pydantic import BaseModel, RootModel, ValidationError

from .http_config import HttpConfig
from .retry import EmptyContentError, sync_retry, with_retry
from .retry_config import RetryConfig

logger = logging.getLogger(__name__)

# Bedrock/Anthropic reject requests where messages contain tool_call blocks but
# no tools= param is passed (e.g. PredictStrategy after a CodeAct turn).
# This flag tells litellm to auto-insert a dummy tool instead of raising.
litellm.modify_params = True

# Optional integration with nemo_oo_agents debug handler for LLM call tracking
# This allows the debug signal handler to show pending LLM calls
try:
    from nemo_oo_agents.runtime.debug_handler import llm_call_context as _llm_call_context

    _HAS_DEBUG_HANDLER = True
except ImportError:
    _HAS_DEBUG_HANDLER = False
    _llm_call_context = None


# Optional harness metrics callback — set by the agent framework via ContextVar.
# No reverse import needed: the callback is injected by actor.py at session start.
_llm_metrics_callback: ContextVar[Callable[[str, Any], None] | None] = ContextVar(
    "llm_metrics_callback", default=None
)


def _record_llm_metric(event: str, detail: Any = None) -> None:
    """Fire-and-forget metric recording. No-op if no callback is set.

    Swallows any exception from the callback — instrumentation must never
    break the LLM call flow.
    """
    cb = _llm_metrics_callback.get()
    if cb is not None:
        try:
            cb(event, detail)
        except Exception as e:  # noqa: BLE001
            logger.debug("Metric callback failed for event %r: %s", event, e)


@contextmanager
def _track_llm_call(model: str, endpoint: str | None = None, prompt_tokens: int | None = None):
    """Track LLM call for debug purposes (if nemo_oo_agents debug handler is available)."""
    if _HAS_DEBUG_HANDLER and _llm_call_context:
        with _llm_call_context(model=model, endpoint=endpoint, prompt_tokens=prompt_tokens):
            yield
    else:
        yield


# Suppress harmless warning about litellm's async callback not being awaited
# (occurs during shutdown when async logging callbacks aren't fully cleaned up)
warnings.filterwarnings(
    "ignore",
    message="coroutine 'Logging.async_success_handler' was never awaited",
    category=RuntimeWarning,
)


# ============================================================================
# CRITICAL FIX: Global httpx monkey-patch to prevent CLOSE_WAIT hangs
# ============================================================================
_httpx_patched = False
_active_http_config: "HttpConfig | None" = None


def _apply_httpx_no_pool_patch():
    """Monkey-patch httpx.AsyncClient to disable connection pooling globally."""
    global _httpx_patched

    if _httpx_patched:
        return  # Already patched — _active_http_config is read dynamically each call

    try:
        import httpx

        _original_async_init = httpx.AsyncClient.__init__

        def _no_pool_async_init(self, *args, **kwargs):
            """Patched __init__ that forces max_keepalive_connections=0 and read timeout."""
            cfg = _active_http_config or HttpConfig()
            # Force no connection pooling
            if "limits" not in kwargs:
                kwargs["limits"] = httpx.Limits(
                    max_connections=cfg.max_connections,
                    max_keepalive_connections=cfg.max_keepalive_connections,
                    keepalive_expiry=cfg.keepalive_expiry,
                )
            elif isinstance(kwargs["limits"], httpx.Limits):
                # Override user-provided limits to force no pooling
                kwargs["limits"] = httpx.Limits(
                    max_connections=kwargs["limits"].max_connections or cfg.max_connections,
                    max_keepalive_connections=0,  # FORCE no pooling
                    keepalive_expiry=0.0,
                )

            # Force read timeout to catch CLOSE_WAIT hangs
            # read timeout = time between receiving bytes (resets on every byte)
            # This catches frozen connections without killing long valid responses
            if "timeout" not in kwargs:
                kwargs["timeout"] = httpx.Timeout(
                    connect=cfg.connect_timeout,
                    read=cfg.read_timeout,
                    write=cfg.write_timeout,
                    pool=cfg.pool_timeout,
                )
            elif isinstance(kwargs["timeout"], (int, float)):
                # Convert simple timeout to full Timeout object with read timeout
                kwargs["timeout"] = httpx.Timeout(
                    connect=cfg.connect_timeout,
                    read=cfg.read_timeout,
                    write=cfg.write_timeout,
                    pool=cfg.pool_timeout,
                )

            return _original_async_init(self, *args, **kwargs)

        # Apply the patch
        httpx.AsyncClient.__init__ = _no_pool_async_init
        _httpx_patched = True

        logger.info(
            "Applied global httpx monkey-patch: ALL AsyncClient instances will use "
            "HttpConfig settings (default: max_keepalive_connections=0, read_timeout=60s)"
        )

    except ImportError:
        logger.warning("httpx not available, skipping connection pooling patch")
    except Exception as e:
        logger.error(f"Failed to apply httpx monkey-patch: {e}")


def _set_http_config(config: HttpConfig) -> None:
    """Set the active HttpConfig read by the httpx monkey-patch."""
    global _active_http_config
    _active_http_config = config
    _apply_httpx_no_pool_patch()


# Apply the patch immediately when module is imported
_apply_httpx_no_pool_patch()


def _recursively_parse_json_strings(obj: Any) -> Any:
    """Recursively parse any string values that are valid JSON objects/arrays.

    Some models double-encode nested JSON, e.g., {"value": '{"key": "val"}'}.
    This function detects and parses such strings.
    """
    if isinstance(obj, str):
        # Try to parse as JSON if it looks like an object or array
        stripped = obj.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
                _record_llm_metric("json_double_decoded")
                # Recursively process the parsed result
                return _recursively_parse_json_strings(parsed)
            except json.JSONDecodeError:
                pass
        return obj
    elif isinstance(obj, dict):
        return {k: _recursively_parse_json_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_recursively_parse_json_strings(item) for item in obj]
    return obj


# ── Response cleanup: JSON parsing ────────────────────────────────────
# Intercept point: JSON extraction and cleanup for structured output.
# Handles fence removal, control char cleanup, escape fixing, nested
# extraction. Consider making this an extensible pipeline in the future.


def extract_and_parse_json(text: str) -> dict[str, Any]:
    """Extract and parse JSON from text, with multiple fallback strategies"""
    original_text = text
    text = text.strip()

    markdown_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    markdown_match = re.search(markdown_pattern, text, re.DOTALL)
    if markdown_match:
        _record_llm_metric("json_fence_removed")
        text = markdown_match.group(1).strip()

    # Strip leading/trailing markdown bold/italic markers (* or **)
    text_before = text
    text = re.sub(r"^\*{1,3}\s*", "", text)
    text = re.sub(r"\s*\*{1,3}$", "", text)
    if text != text_before:
        _record_llm_metric("json_markdown_bold_stripped")

    if not text:
        raise json.JSONDecodeError(
            f"Empty text after processing. Original: `{original_text[:200]}` ...", original_text, 0
        )

    try:
        result = json.loads(text)
        # Handle double-encoded JSON in nested values
        return _recursively_parse_json_strings(result)
    except json.JSONDecodeError as first_error:
        if "[...]" in text or '"..."' in text or ": ..." in text:
            raise json.JSONDecodeError(
                "JSON contains abbreviations/ellipsis ([...] or \"...\" or ': ...'). "
                "You MUST provide the complete, unabbreviated JSON. Do not truncate or use placeholders. "
                "Write out ALL values in full.",
                text,
                first_error.pos,
            ) from first_error

    json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group(0))
            _record_llm_metric("json_nested_extraction")
            return _recursively_parse_json_strings(result)
        except json.JSONDecodeError:
            pass

    text_before = text
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    if text != text_before:
        _record_llm_metric("json_control_chars_removed")
    text_before = text
    text = re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r"\\\\", text)
    if text != text_before:
        _record_llm_metric("json_escape_fixed")

    try:
        result = json.loads(text)
        return _recursively_parse_json_strings(result)
    except json.JSONDecodeError as e:
        preview = text[:500] if len(text) > 500 else text
        raise json.JSONDecodeError(
            f"Failed to parse JSON after multiple cleanup attempts. Text preview: {preview}",
            text,
            e.pos,
        ) from e


def _resolve_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref references by inlining $defs definitions.

    Pydantic generates $ref/$defs for nested models. LLM APIs need
    flat schemas with type on every property. This inlines all
    references and removes $defs.
    """
    defs = schema.get("$defs", {})
    if not defs:
        return schema

    def _inline(node, _resolving=frozenset()):
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref_path = node["$ref"]
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path[len("#/$defs/") :]
                if def_name in _resolving:
                    return {"type": "object"}  # break cycle for recursive models
                if def_name in defs:
                    return _inline(dict(defs[def_name]), _resolving | {def_name})
            return node
        result = {}
        for k, v in node.items():
            if k == "$defs":
                continue
            elif k == "properties" and isinstance(v, dict):
                result[k] = {pk: _inline(pv, _resolving) for pk, pv in v.items()}
            elif k in ("items", "additionalProperties") and isinstance(v, dict):
                result[k] = _inline(v, _resolving)
            elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
                result[k] = [_inline(i, _resolving) if isinstance(i, dict) else i for i in v]
            else:
                result[k] = v
        # Unwrap single-item allOf (Pydantic wraps $ref in allOf when Field has description)
        if "allOf" in result and len(result["allOf"]) == 1:
            merged = dict(result["allOf"][0])
            del result["allOf"]
            # Preserve sibling keys (e.g., description) alongside the unwrapped schema
            for k, v in result.items():
                if k not in merged:
                    merged[k] = v
            result = merged
        return result

    return _inline(schema)


def _strip_schema_noise(schema: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Strip Pydantic noise (title, default) from a JSON schema recursively.

    These fields are auto-generated by Pydantic but add no value for LLM APIs
    and cause strict-mode rejections on some providers.

    When ``strict=True`` (OpenAI Responses API / Chat Completions strict mode):
    - Forces ``additionalProperties: false`` on every object type
    - Ensures every object has ``properties`` and ``required`` keys
    - Strips extra keys not in the strict-mode allowlist
    """
    STRIP_KEYS = frozenset({"title", "default"})
    # Strict mode only allows these keys (OpenAI spec)
    STRICT_ALLOWED = frozenset(
        {
            "type",
            "description",
            "enum",
            "const",
            "properties",
            "required",
            "items",
            "additionalProperties",
            "anyOf",
            "oneOf",
        }
    )

    def _strip(node):
        if not isinstance(node, dict):
            return node
        if strict:
            cleaned = {k: v for k, v in node.items() if k in STRICT_ALLOWED}
        else:
            cleaned = {k: v for k, v in node.items() if k not in STRIP_KEYS}
        if "properties" in cleaned:
            cleaned["properties"] = {k: _strip(v) for k, v in cleaned["properties"].items()}
        if "items" in cleaned and isinstance(cleaned["items"], dict):
            cleaned["items"] = _strip(cleaned["items"])
        if "additionalProperties" in cleaned and isinstance(cleaned["additionalProperties"], dict):
            cleaned["additionalProperties"] = _strip(cleaned["additionalProperties"])
        for key in ("anyOf", "oneOf", "allOf"):
            if key in cleaned and isinstance(cleaned[key], list):
                cleaned[key] = [_strip(i) if isinstance(i, dict) else i for i in cleaned[key]]
        if strict and cleaned.get("type") == "object":
            cleaned["additionalProperties"] = False
            cleaned.setdefault("properties", {})
            cleaned["required"] = list(cleaned["properties"].keys())
        return cleaned

    return _strip(schema)


def _clean_schema(schema: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Clean a Pydantic-generated JSON schema for LLM tool use.

    1. Resolves $ref/$defs inline (providers need flat schemas)
    2. Strips title/default noise (Pydantic artifacts, no LLM value)
    3. Ensures top-level structure has type/properties/required
    4. When ``strict=True``: enforces OpenAI strict-mode constraints
       (additionalProperties: false everywhere, properties+required on all objects)
    """
    resolved = _resolve_schema_refs(schema)
    cleaned = _strip_schema_noise(resolved, strict=strict)
    # Ensure standard top-level structure
    result = {
        "type": cleaned.get("type", "object"),
        "properties": cleaned.get("properties", {}),
        "required": cleaned.get("required", []),
    }
    if strict:
        result["additionalProperties"] = False
    return result


def _strict_schema_valid(schema: dict[str, Any]) -> bool:
    """Check whether a schema satisfies OpenAI strict-mode requirements.

    Every property node must have a ``type`` key (or ``anyOf``/``oneOf``),
    and every object must have ``required`` listing ALL property keys.
    Returns False if any node violates these constraints.
    """

    def _check(node: dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            return True
        # A property node needs type or a union discriminator
        if "type" not in node and "anyOf" not in node and "oneOf" not in node:
            return False
        # Object nodes: required must list every property key
        if (
            node.get("type") == "object"
            and "properties" in node
            and set(node.get("required", [])) != set(node["properties"].keys())
        ):
            return False
        for v in node.get("properties", {}).values():
            if isinstance(v, dict) and not _check(v):
                return False
        if isinstance(node.get("items"), dict) and not _check(node["items"]):
            return False
        for key in ("anyOf", "oneOf"):
            for item in node.get(key, []):
                if isinstance(item, dict) and not _check(item):
                    return False
        return True

    return _check(schema)


@dataclass
class Tool:
    """Standardized tool representation across all LLM APIs

    Clean design: Either provide parameters_model (Pydantic) or it's auto-generated from callable.

    - parameters_model: Pydantic model defining parameter schema (recommended)
    - If None, auto-generates from callable's signature
    - Preserves all type information (Union, nested models, TypedDict, etc.)
    """

    name: str
    description: str
    callable: Callable
    parameters_model: type["BaseModel"] | None = None  # Pydantic model for parameters

    def get_parameter_schema(self, *, strict: bool = False) -> dict[str, Any]:
        """Get the JSON schema for parameters.

        If parameters_model is provided, use its schema.
        Otherwise, auto-generate from callable signature.

        The returned schema has $ref resolved inline and Pydantic noise
        (title, default) stripped — clean for any LLM provider.

        Args:
            strict: When True, enforce OpenAI strict-mode constraints
                (additionalProperties: false everywhere, all objects need
                properties+required).
        """
        if self.parameters_model is not None:
            schema = self.parameters_model.model_json_schema()
        else:
            schema = self._auto_generate_raw_schema()

        return _clean_schema(schema, strict=strict)

    def _auto_generate_raw_schema(self) -> dict[str, Any]:
        """Auto-generate parameter schema from callable signature."""
        from pydantic import create_model

        sig = inspect.signature(self.callable)
        field_definitions = {}

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            param_type = param.annotation if param.annotation != inspect.Parameter.empty else str
            default = ... if param.default == inspect.Parameter.empty else param.default

            field_definitions[param_name] = (param_type, default)

        if not field_definitions:
            # No parameters
            return {"type": "object", "properties": {}, "required": []}

        # Create temporary Pydantic model
        TempModel = create_model(f"{self.name}_params", **field_definitions)
        return TempModel.model_json_schema()


def create_tool_from_callable(tool_callable: Callable) -> Tool:
    """Extract Tool metadata from a Python function

    Creates a Tool with auto-generated parameter schema from the callable's signature.
    """
    docstring = tool_callable.__doc__ or f"Call the {tool_callable.__name__} function"

    # Let Tool auto-generate the schema from the callable
    return Tool(
        name=tool_callable.__name__,
        description=docstring,
        callable=tool_callable,
        parameters_model=None,  # Will auto-generate from signature
    )


@dataclass
class ToolCall:
    """Standardized tool call representation across all LLM APIs"""

    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    """Standardized response from any LLM API"""

    raw_response: Any
    content: str | BaseModel
    tool_calls: list[ToolCall]
    finish_reason: Literal["stop", "tool_calls", "length", "error"]
    assistant_message: dict[str, Any]
    reasoning: str | None = None  # o1-style or DeepSeek/QwQ reasoning
    usage: dict[str, int] | None = None  # Token usage stats

    @property
    def message(self) -> str | BaseModel | None:
        """Backward-compatible alias for content."""
        return self.content


# --- Bedrock JSON schema sanitization (gl-134) ---
# Bedrock Claude rejects schemas with certain JSON schema keywords.
# We strip/fix these for Bedrock models and rely on Pydantic's client-side
# validation instead.
#
# Source of truth: AWS ML Blog "Structured outputs on Amazon Bedrock"
# https://aws.amazon.com/blogs/machine-learning/structured-outputs-on-amazon-bedrock-schema-compliant-ai-responses/
# The blog lists numerical constraints, string constraints, and
# additionalProperties != false as "Not supported". As of 2025-04-21,
# numerical + maxItems actively 400; string constraints are silently
# accepted today but could be tightened at any time (like the numerical
# constraints were on Apr 20), so we strip them defensively.

_BEDROCK_STRIP_KEYWORDS = frozenset(
    {
        # Numerical — actively rejected (HTTP 400)
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        # Array — maxItems actively rejected
        "maxItems",
        # String — blog says unsupported; currently accepted but stripped
        # defensively to avoid the next silent enforcement tightening.
        "minLength",
        "maxLength",
        "pattern",
    }
)


def _is_bedrock_model(model: str) -> bool:
    """Return True if the model string routes to AWS Bedrock."""
    m = model.lower()
    return "bedrock" in m or "/aws/" in m or m.startswith("aws/")


def _sanitize_schema_for_bedrock(schema: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy *schema* and strip/fix keywords unsupported by Bedrock.

    Bedrock Claude rejects:
    - Numerical: minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf
    - String: minLength, maxLength, pattern (docs say unsupported; stripped defensively)
    - Array: maxItems (rejected), minItems > 1 (only 0 and 1 allowed)
    - Object: additionalProperties set to anything other than false

    Assumes Pydantic v2 schema output shapes. Does not recurse into
    prefixItems, patternProperties, or dependentSchemas (Pydantic v2
    does not emit these for typical models).
    """
    schema = copy.deepcopy(schema)
    _strip_unsupported_keys(schema)
    return schema


def _strip_unsupported_keys(node: Any) -> None:
    """Recursively fix unsupported Bedrock keywords in a schema node in-place."""
    if not isinstance(node, dict):
        return

    # Strip keywords that Bedrock rejects outright
    for key in list(node.keys()):
        if key in _BEDROCK_STRIP_KEYWORDS:
            del node[key]

    # minItems: Bedrock only accepts 0 or 1; clamp higher values to 1
    if "minItems" in node and isinstance(node["minItems"], int) and node["minItems"] > 1:
        node["minItems"] = 1

    # additionalProperties: Bedrock only accepts false
    if "additionalProperties" in node and node["additionalProperties"] is not False:
        node["additionalProperties"] = False

    # Recurse into sub-schemas
    for key in ("properties", "$defs", "definitions"):
        if key in node and isinstance(node[key], dict):
            for v in node[key].values():
                _strip_unsupported_keys(v)
    if "items" in node and isinstance(node["items"], dict):
        _strip_unsupported_keys(node["items"])
    for key in ("allOf", "anyOf", "oneOf"):
        if key in node and isinstance(node[key], list):
            for item in node[key]:
                _strip_unsupported_keys(item)
    if "not" in node and isinstance(node["not"], dict):
        _strip_unsupported_keys(node["not"])


def _maybe_sanitize_response_format(
    model: str, output_model: type[BaseModel]
) -> type[BaseModel] | dict[str, Any]:
    """Return *output_model* as-is for most providers, or a sanitized dict for Bedrock."""
    if not _is_bedrock_model(model):
        return output_model
    schema = _sanitize_schema_for_bedrock(output_model.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_model.__name__,
            "strict": True,
            "schema": schema,
        },
    }


# Bedrock/Anthropic reject messages containing tool_call blocks when no tools= param
# is set. litellm.modify_params=True should add a dummy tool, but doesn't work in all
# code paths (e.g. litellm router). We detect and handle this ourselves.
_DUMMY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "_placeholder",
        "description": "Placeholder tool (not callable).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# Providers that reject messages containing tool_call blocks without tools= param.
_STRICT_TOOL_PROVIDERS = ("bedrock/", "anthropic/", "anthropic.")


def _needs_dummy_tool(model: str) -> bool:
    """Return True if the model's provider requires tools= when tool_calls are present."""
    model_lower = model.lower()
    return any(model_lower.startswith(prefix) for prefix in _STRICT_TOOL_PROVIDERS)


def _messages_have_tool_calls(messages: list[dict[str, Any]]) -> bool:
    """Return True if any message contains tool_call blocks."""
    for msg in messages:
        if msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                return True
            # Anthropic-style: content list with tool_use blocks
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        return True
    return False


def _instantiate_output_model(output_model: type[BaseModel], json_data: Any) -> BaseModel:
    """Instantiate a Pydantic model from parsed JSON data.

    Handles both regular BaseModel (kwargs) and RootModel (positional arg).
    RootModel is used for dict/list return types where the value is returned directly.
    """
    if issubclass(output_model, RootModel):
        # RootModel takes the value directly as a positional argument
        return output_model(json_data)
    else:
        # Regular BaseModel takes kwargs
        return output_model(**json_data)


class UnifiedLLM(ABC):
    _registry_config: dict[str, Any] | None

    def __init__(self, model: str, **config):
        self.model = model
        self.config = config
        self._registry_config = None

    def count_tokens(self, text: str) -> int:
        """Count tokens using model-appropriate tokenizer.

        Uses litellm's token_counter for accurate counting.
        Raises if litellm cannot count tokens for this model.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens in the text.
        """
        return litellm.token_counter(model=self.model, text=text)

    def supports_vision(self) -> bool:
        """Check if this model supports vision (image inputs).

        Uses litellm's model registry to determine vision capability.
        Returns False for unknown models.
        """
        try:
            return litellm.supports_vision(model=self.model)
        except Exception:
            return False

    def get_model_info(self) -> "Any":
        """Get model metadata from litellm registry.

        Returns:
            Dict with model info (max_input_tokens, max_output_tokens, etc.)
            or None if model is not in litellm's registry.
        """
        try:
            return litellm.get_model_info(self.model)
        except Exception:
            return None

    @property
    def context_window(self) -> int | None:
        """Get context window size (max input tokens).

        Resolution order:
        1. Registry config (if created via get_llm_client())
        2. Registry lookup by model name or model_name field
        3. litellm model info (for known models)
        4. None (unknown model)

        Returns:
            Maximum input tokens for this model, or None if unknown.
        """
        # First, check registry config (set by get_llm_client())
        if self._registry_config is not None:
            cw = self._registry_config.get("context_window")
            if cw is not None:
                return cw
            # Registry entry exists but lacks context_window — fall through

        # Try registry lookup by model string
        from nemo_oo_agents.unifiedllm.registry import MODELS

        model_str = self.model

        # Direct key match
        if model_str in MODELS:
            cw = MODELS[model_str].get("context_window")
            if cw is not None:
                return cw

        # Reverse lookup: check if any registry entry's model_name matches
        for _key, cfg in MODELS.items():
            if cfg.get("model_name") == model_str:
                cw = cfg.get("context_window")
                if cw is not None:
                    return cw

        # Fallback to litellm
        info = self.get_model_info()
        return info.get("max_input_tokens") if info else None

    @abstractmethod
    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Single method that:
        1. Transforms messages to API-specific format (if needed)
        2. Calls the LLM API
        3. Extracts tool calls (if any) and returns early
        4. If no tool calls, parses structured output (if requested)
        5. Returns everything in standardized LLMResponse

        Raises:
        - ValidationError: if output_model validation fails
        - json.JSONDecodeError: if JSON parsing fails
        - Other exceptions for API errors
        """
        pass

    @abstractmethod
    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async version of call"""
        pass

    def _format_error_message(self, error: Exception) -> str:
        """Format parsing errors into helpful feedback for the LLM"""
        if isinstance(error, ValidationError):
            error_messages = []
            for err in error.errors():
                location = " -> ".join(str(loc) for loc in err.get("loc", []))
                message = err.get("msg", "Unknown error")
                error_messages.append(f"Field '{location}': {message}")

            return (
                "Your previous response did not match the required format. "
                "Please fix the following validation errors:\n"
                + "\n".join(f"  - {msg}" for msg in error_messages)
            )

        elif isinstance(error, json.JSONDecodeError):
            return (
                "Your previous response contained invalid JSON. "
                f"Error at position {error.pos}: {error.msg}. "
                "Please provide a valid JSON response."
            )

        return f"Error parsing your response: {str(error)}"

    def call_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        output_model: type[BaseModel] | None,
        max_retries: int = 3,
        **kwargs,
    ) -> LLMResponse:
        """
        Call LLM with automatic retry on parsing errors.

        Args:
            messages: Chat history
            tools: Available tools
            output_model: Optional Pydantic model for structured output
            max_retries: Maximum number of retry attempts (default 3)
            **kwargs: Additional parameters for LLM

        Returns:
            LLMResponse with parsed content and validation applied

        Raises:
            Exception: After max retries exhausted
        """
        messages_copy = messages.copy()

        for retry in range(max_retries):
            try:
                response = self.call(
                    messages=messages_copy,
                    tools=tools,
                    output_model=output_model,
                    **kwargs,
                )

                # Apply sync validation if output_model supports it
                if (
                    output_model
                    and isinstance(response.content, BaseModel)
                    and hasattr(output_model, "validate_sync")
                ):
                    response.content = output_model.validate_sync(response.content)  # type: ignore[attr-defined]

                return response

            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                if retry == max_retries - 1:
                    raise Exception("LLM output parsing error. Max retries reached.") from e

                error_message = self._format_error_message(e)
                messages_copy.append({"role": "user", "content": error_message})

        raise Exception("LLM output parsing error. Max retries reached.")

    async def acall_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        output_model: type[BaseModel] | None,
        max_retries: int = 3,
        **kwargs,
    ) -> LLMResponse:
        """
        Call LLM with automatic retry on parsing errors (async version).

        Args:
            messages: Chat history
            tools: Available tools
            output_model: Optional Pydantic model for structured output
            max_retries: Maximum number of retry attempts (default 3)
            **kwargs: Additional parameters for LLM

        Returns:
            LLMResponse with parsed content and validation applied

        Raises:
            Exception: After max retries exhausted
        """
        messages_copy = messages.copy()

        for retry in range(max_retries):
            try:
                response = await self.acall(
                    messages=messages_copy,
                    tools=tools,
                    output_model=output_model,
                    **kwargs,
                )

                # Apply async validation if output_model supports it
                if (
                    output_model
                    and isinstance(response.content, BaseModel)
                    and hasattr(output_model, "validate_async")
                ):
                    response.content = await output_model.validate_async(response.content)  # type: ignore[attr-defined]

                return response

            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                if retry == max_retries - 1:
                    raise Exception("LLM output parsing error. Max retries reached.") from e

                error_message = self._format_error_message(e)
                messages_copy.append({"role": "user", "content": error_message})

        raise Exception("LLM output parsing error. Max retries reached.")


def _collect_sync(raw: Any) -> "litellm.ModelResponse":
    """Consume a sync streaming or non-streaming litellm response, returning ModelResponse."""
    if isinstance(raw, litellm.CustomStreamWrapper):
        chunks = list(raw)
        result = litellm.stream_chunk_builder(chunks)
        if result is None:
            raise ValueError("stream_chunk_builder returned None for empty stream")
        if not isinstance(result, litellm.ModelResponse):
            raise TypeError(f"Expected ModelResponse, got {type(result)}")
        return result
    if not isinstance(raw, litellm.ModelResponse):
        raise TypeError(f"Expected ModelResponse, got {type(raw)}")
    return raw


async def _collect_async(raw: Any) -> "litellm.ModelResponse":
    """Consume an async streaming or non-streaming litellm response, returning ModelResponse."""
    if isinstance(raw, litellm.CustomStreamWrapper):
        chunks = [chunk async for chunk in raw]  # type: ignore[attr-defined]
        result = litellm.stream_chunk_builder(chunks)
        if result is None:
            raise ValueError("stream_chunk_builder returned None for empty stream")
        if not isinstance(result, litellm.ModelResponse):
            raise TypeError(f"Expected ModelResponse, got {type(result)}")
        return result
    if not isinstance(raw, litellm.ModelResponse):
        raise TypeError(f"Expected ModelResponse, got {type(raw)}")
    return raw


def _extract_reasoning_and_usage(raw_response: Any) -> tuple[str | None, dict[str, int] | None]:
    """Extract reasoning and usage from raw LLM response."""
    reasoning = None
    usage = None

    # Extract reasoning (o1-style or DeepSeek/QwQ)
    if hasattr(raw_response, "choices") and raw_response.choices:
        msg = raw_response.choices[0].message
        reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)

    # Extract usage
    if hasattr(raw_response, "usage") and raw_response.usage:
        usage_obj = raw_response.usage
        if hasattr(usage_obj, "_asdict"):
            usage = usage_obj._asdict()
        elif hasattr(usage_obj, "model_dump"):
            usage = usage_obj.model_dump()
        elif isinstance(usage_obj, dict):
            usage = usage_obj
        else:
            # Try to extract common fields
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }

    return reasoning, usage


def _extract_xml_tool_calls(content: str) -> list["ToolCall"]:
    """Extract tool calls from XML format used by Nemotron/NIM models.

    vLLM's hermes parser expects JSON inside <tool_call> but these models output:
        <tool_call><function=name><parameter=p>v</parameter>...</function></tool_call>

    This is called as a fallback when raw_tool_calls is empty but content has <tool_call>.
    """
    import uuid as _uuid

    tool_calls = []
    tool_call_pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

    for match in tool_call_pattern.finditer(content):
        block = match.group(1).strip()

        # Try JSON format first (standard hermes: {"name": ..., "arguments": ...})
        try:
            import json as _json

            data = _json.loads(block)
            name = data.get("name", "")
            args = _json.dumps(data.get("arguments", data.get("parameters", {})))
            if name:
                tool_calls.append(
                    ToolCall(id=f"call_{_uuid.uuid4().hex[:8]}", name=name, arguments=args)
                )
            continue
        except (ValueError, TypeError):
            pass

        # XML format: <function=name><parameter=p1>v1</parameter>...</function>
        func_match = re.match(r"<function=([^>]+)>(.*?)</function>", block, re.DOTALL)
        if not func_match:
            continue

        func_name = func_match.group(1).strip()
        params_block = func_match.group(2)
        params: dict[str, str] = {}
        for param_match in re.finditer(
            r"<parameter=([^>]+)>(.*?)</parameter>", params_block, re.DOTALL
        ):
            params[param_match.group(1).strip()] = param_match.group(2).strip()

        import json as _json

        tool_calls.append(
            ToolCall(
                id=f"call_{_uuid.uuid4().hex[:8]}", name=func_name, arguments=_json.dumps(params)
            )
        )

    return tool_calls


def _extract_think_tags(content: str) -> tuple[str, str | None]:
    """Response cleanup: extract <think>...</think> tags from content.

    Returns (cleaned_content, reasoning) where:
    - cleaned_content is the content with think tags removed
    - reasoning is the extracted thinking content (or None if no tags found)

    Handles both complete tags and malformed tags (missing opening tag due to litellm bug).
    """
    # Pattern for complete <think>...</think> tags
    think_pattern = r"<think>(.*?)</think>"
    match = re.search(think_pattern, content, re.DOTALL)

    if match:
        reasoning = match.group(1).strip()
        cleaned = re.sub(think_pattern, "", content, flags=re.DOTALL).strip()
        _record_llm_metric("think_tag_extracted")
        return cleaned, reasoning

    # Handle malformed case: content starts with thinking and ends with </think>
    # (litellm bug strips opening <think> but leaves closing </think>)
    if "</think>" in content:
        parts = content.split("</think>", 1)
        if len(parts) == 2:
            reasoning = parts[0].strip()
            cleaned = parts[1].strip()
            _record_llm_metric("malformed_think_tag_fixed")
            return cleaned, reasoning

    return content, None


DEFAULT_CACHE_CONTROL_INJECTION_POINTS = [{"location": "message", "role": "system"}]


# ============================================================================
# PATCH: Prevent litellm from stripping cache_control for Anthropic models
# ============================================================================
# litellm's OpenAIGPTConfig.remove_cache_control_flag_from_messages_and_tools()
# unconditionally strips cache_control from all messages and tools before sending.
# For Anthropic models behind an OpenAI-compatible endpoint (e.g., NVIDIA gateway),
# we need cache_control to survive so the API can enable prompt caching.
_cache_control_patch_applied = False


def _apply_cache_control_preserve_patch():
    """Patch litellm to preserve cache_control for Anthropic models."""
    global _cache_control_patch_applied
    if _cache_control_patch_applied:
        return

    try:
        from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig

        _original_remove = OpenAIGPTConfig.remove_cache_control_flag_from_messages_and_tools

        def _patched_remove(self, model, messages, tools=None):
            if "anthropic" in model.lower():
                return messages, tools
            return _original_remove(self, model, messages, tools)

        OpenAIGPTConfig.remove_cache_control_flag_from_messages_and_tools = _patched_remove
        _cache_control_patch_applied = True
        logger.debug("Applied cache_control preserve patch for Anthropic models")
    except (ImportError, AttributeError) as e:
        logger.warning(f"Could not apply cache_control preserve patch: {e}")


_apply_cache_control_preserve_patch()


class CompletionClient(UnifiedLLM):
    def __init__(
        self,
        model: str,
        retry_config: RetryConfig | None = None,
        http_config: HttpConfig | None = None,
        # use system as default for cache_control_injection_points
        cache_control_injection_points: list[dict[str, Any]] | None = None,
        **config,
    ):
        """
        Initialize CompletionClient.

        Args:
            model: The model identifier (e.g., "gpt-4o-mini", "nvidia_nim/...").
            retry_config: Optional retry configuration for API-level retries.
                         Handles 429, 500, timeouts, etc. Set retry_on_empty_content=True
                         to also retry when reasoning models return empty content.
            http_config: Optional HTTP connection pool and timeout settings. Values
                         are applied process-wide via the httpx monkey-patch; the most
                         recent CompletionClient's config applies to new httpx clients.
            cache_control_injection_points: Optional list of message indices to enable prompt caching.
                This will be applied to all calls made with this client.
                Example: [0] to cache the first (system) message in every request.
                Note: Do NOT manually add cache_control to message content when using this.
            **config: Additional configuration passed to litellm (api_key, api_base, etc.)
        """
        super().__init__(model, **config)
        self.retry_config = retry_config
        self._http_config = http_config or HttpConfig()
        _set_http_config(self._http_config)
        # Only set default if explicitly None (not if empty list is passed)
        if cache_control_injection_points is not None:
            self.cache_control_injection_points = cache_control_injection_points
        else:
            self.cache_control_injection_points = DEFAULT_CACHE_CONTROL_INJECTION_POINTS

    def _convert_tool_to_schema(self, tool: Tool) -> dict[str, Any]:
        """Convert Tool object to Completion API schema format"""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.get_parameter_schema(),
            },
        }

    def _inject_cache_control(
        self, messages: list[dict[str, Any]], injection_points: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add cache_control to designated messages for prompt caching.

        Adds cache_control at the message level (sibling of role/content), which
        is the format expected by NVIDIA's OpenAI-compatible Anthropic endpoint.
        This format also survives OpenAI SDK validation since the SDK only strips
        extra fields from content blocks, not from messages themselves.

        Args:
            messages: The message list to process.
            injection_points: List of dicts with "role" key indicating which
                messages should have cache_control added.
                Example: [{"location": "message", "role": "system"}]

        Returns:
            A shallow copy of messages with cache_control injected into matching messages.
        """
        if not injection_points:
            return messages

        roles_to_cache = {p["role"] for p in injection_points if "role" in p}
        if not roles_to_cache:
            return messages

        messages = [msg.copy() for msg in messages]
        for msg in messages:
            if msg.get("role") in roles_to_cache:
                msg["cache_control"] = {"type": "ephemeral"}
        return messages

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        cache_control_injection_points: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Sync version: Completion API uses standard message format, so no transformation needed.
        Messages are passed directly to the API.

        If retry_config.retry_on_empty_content is True, will retry when the model
        returns empty content but has reasoning_content (common with some reasoning models).
        """
        # Inject cache_control at the message level for prompt caching
        cache_points = cache_control_injection_points or self.cache_control_injection_points
        prepared_messages = self._inject_cache_control(messages, cache_points)

        api_params = {
            "model": self.model,
            "messages": prepared_messages,
            "_skip_mcp_handler": True,
            **self.config,
            **kwargs,
        }

        if tools:
            api_params["tools"] = [self._convert_tool_to_schema(tool) for tool in tools]
            api_params["parallel_tool_calls"] = False

        if output_model is not None:
            api_params["response_format"] = _maybe_sanitize_response_format(
                self.model, output_model
            )

        # Bedrock/Anthropic reject messages with tool_call blocks when tools= is absent.
        if (
            "tools" not in api_params
            and _needs_dummy_tool(self.model)
            and _messages_have_tool_calls(prepared_messages)
        ):
            api_params["tools"] = [_DUMMY_TOOL_SCHEMA]

        retry_on_empty = self.retry_config.retry_on_empty_content if self.retry_config else False

        def _make_call():
            raw_response = _collect_sync(litellm.completion(**api_params))
            reasoning, _ = _extract_reasoning_and_usage(raw_response)
            text_content = raw_response.choices[0].message.content or ""  # type: ignore[union-attr]

            # Raise EmptyContentError to trigger retry if configured
            if not text_content and reasoning and retry_on_empty:
                raise EmptyContentError(reasoning)

            return raw_response

        # Track LLM call for debugging (visible via SIGUSR2 if nemo_oo_agents debug handler installed)
        with _track_llm_call(model=self.model, endpoint=self.config.get("api_base")):
            raw_response = (
                sync_retry(_make_call, config=self.retry_config)
                if self.retry_config
                else _make_call()
            )

        reasoning, usage = _extract_reasoning_and_usage(raw_response)
        if usage:
            _record_llm_metric("token_usage", usage)
        raw_tool_calls = raw_response.choices[0].message.tool_calls  # type: ignore[union-attr]

        if raw_tool_calls:
            tool_calls = [
                ToolCall(id=tc.id, name=tc.function.name or "", arguments=tc.function.arguments)
                for tc in raw_tool_calls
            ]

            return LLMResponse(
                raw_response=raw_response,
                content="",
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                assistant_message={
                    "role": "assistant",
                    "content": raw_response.choices[0].message.content or "",  # type: ignore[union-attr]
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in raw_tool_calls
                    ],
                },
                reasoning=reasoning,
                usage=usage,
            )

        text_content = raw_response.choices[0].message.content or ""  # type: ignore[union-attr]

        # Fallback: vLLM's hermes parser fails on Nemotron's XML tool call format.
        # Extract <tool_call><function=name><parameter=...> from content.
        if not raw_tool_calls and tools and "<tool_call>" in text_content:
            xml_tool_calls = _extract_xml_tool_calls(text_content)
            if xml_tool_calls:
                return LLMResponse(
                    raw_response=raw_response,
                    content="",
                    tool_calls=xml_tool_calls,
                    finish_reason="tool_calls",
                    assistant_message={
                        "role": "assistant",
                        "content": text_content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": tc.arguments},
                            }
                            for tc in xml_tool_calls
                        ],
                    },
                    reasoning=reasoning,
                    usage=usage,
                )

        if output_model:
            # ── Response cleanup: reasoning-as-content fallback ───────
            # Intercept point: some reasoning models (e.g. Nemotron) put
            # structured output JSON in reasoning_content instead of content.
            # Consider making this an extensible transform in the future.
            parseable_content = text_content if text_content else (reasoning or "")
            if not text_content and reasoning:
                _record_llm_metric("reasoning_as_structured_output")
            json_data = extract_and_parse_json(parseable_content)
            parsed_content = _instantiate_output_model(output_model, json_data)

            return LLMResponse(
                raw_response=raw_response,
                content=parsed_content,
                tool_calls=[],
                finish_reason="stop",
                assistant_message={"role": "assistant", "content": text_content},
                reasoning=reasoning if text_content else None,
                usage=usage,
            )

        return LLMResponse(
            raw_response=raw_response,
            content=text_content,
            tool_calls=[],
            finish_reason="stop",
            assistant_message={"role": "assistant", "content": text_content},
            reasoning=reasoning,
            usage=usage,
        )

    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        cache_control_injection_points: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Async version: Completion API uses standard message format, so no transformation needed.
        Messages are passed directly to the API.

        If retry_config.retry_on_empty_content is True, will retry when the model
        returns empty content but has reasoning_content (common with some reasoning models).
        """
        # Inject cache_control at the message level for prompt caching
        cache_points = cache_control_injection_points or self.cache_control_injection_points
        prepared_messages = self._inject_cache_control(messages, cache_points)

        api_params = {
            "model": self.model,
            "messages": prepared_messages,
            "_skip_mcp_handler": True,
            **self.config,
            **kwargs,
        }

        if tools:
            api_params["tools"] = [self._convert_tool_to_schema(tool) for tool in tools]
            api_params["parallel_tool_calls"] = False

        if output_model is not None:
            api_params["response_format"] = _maybe_sanitize_response_format(
                self.model, output_model
            )

        # Bedrock/Anthropic reject messages with tool_call blocks when tools= is absent.
        if (
            "tools" not in api_params
            and _needs_dummy_tool(self.model)
            and _messages_have_tool_calls(prepared_messages)
        ):
            api_params["tools"] = [_DUMMY_TOOL_SCHEMA]

        retry_on_empty = self.retry_config.retry_on_empty_content if self.retry_config else False

        async def _make_call():
            raw_response = await _collect_async(await litellm.acompletion(**api_params))
            reasoning, _ = _extract_reasoning_and_usage(raw_response)
            text_content = raw_response.choices[0].message.content or ""  # type: ignore[union-attr]

            # Raise EmptyContentError to trigger retry if configured
            if not text_content and reasoning and retry_on_empty:
                raise EmptyContentError(reasoning)

            return raw_response

        # Track LLM call for debugging (visible via SIGUSR2 if nemo_oo_agents debug handler installed)
        with _track_llm_call(model=self.model, endpoint=self.config.get("api_base")):
            raw_response = (
                await with_retry(_make_call, config=self.retry_config)
                if self.retry_config
                else await _make_call()
            )

        reasoning, usage = _extract_reasoning_and_usage(raw_response)
        if usage:
            _record_llm_metric("token_usage", usage)
        raw_tool_calls = raw_response.choices[0].message.tool_calls  # type: ignore[union-attr]

        if raw_tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id, name=tc.function.name or "", arguments=tc.function.arguments or ""
                )  # type: ignore[union-attr]
                for tc in raw_tool_calls
            ]

            return LLMResponse(
                raw_response=raw_response,
                content="",
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                assistant_message={
                    "role": "assistant",
                    "content": raw_response.choices[0].message.content or "",  # type: ignore[union-attr]
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in raw_tool_calls
                    ],
                },
                reasoning=reasoning,
                usage=usage,
            )

        text_content = raw_response.choices[0].message.content or ""  # type: ignore[union-attr]

        # Fallback: vLLM's hermes parser fails on Nemotron's XML tool call format.
        # Extract <tool_call><function=name><parameter=...> from content.
        if not raw_tool_calls and tools and "<tool_call>" in text_content:
            xml_tool_calls = _extract_xml_tool_calls(text_content)
            if xml_tool_calls:
                return LLMResponse(
                    raw_response=raw_response,
                    content="",
                    tool_calls=xml_tool_calls,
                    finish_reason="tool_calls",
                    assistant_message={
                        "role": "assistant",
                        "content": text_content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": tc.arguments},
                            }
                            for tc in xml_tool_calls
                        ],
                    },
                    reasoning=reasoning,
                    usage=usage,
                )

        if output_model:
            # ── Response cleanup: reasoning-as-content fallback ───────
            # Intercept point: some reasoning models (e.g. Nemotron) put
            # structured output JSON in reasoning_content instead of content.
            # Consider making this an extensible transform in the future.
            parseable_content = text_content if text_content else (reasoning or "")
            if not text_content and reasoning:
                _record_llm_metric("reasoning_as_structured_output")
            json_data = extract_and_parse_json(parseable_content)
            parsed_content = _instantiate_output_model(output_model, json_data)

            return LLMResponse(
                raw_response=raw_response,
                content=parsed_content,
                tool_calls=[],
                finish_reason="stop",
                assistant_message={"role": "assistant", "content": text_content},
                reasoning=reasoning if text_content else None,
                usage=usage,
            )

        return LLMResponse(
            raw_response=raw_response,
            content=text_content,
            tool_calls=[],
            finish_reason="stop",
            assistant_message={"role": "assistant", "content": text_content},
            reasoning=reasoning,
            usage=usage,
        )


class ReasoningCompletionClient(CompletionClient):
    """
    CompletionClient for reasoning models that output <think>...</think> tags.

    This client:
    1. Extracts reasoning from <think>...</think> tags in the content
    2. Handles litellm's bug where opening <think> tag is stripped
    3. Returns clean content with reasoning in the `reasoning` field

    Use this for models like:
    - nvidia/Nemotron-3-Nano-30B-A3B
    - nvidia/llama-3.3-nemotron-super-49b-v1.5
    - Any other model that outputs <think> tags

    Example:
        client = ReasoningCompletionClient(
            model="nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1.5",
            api_base="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
            temperature=0.6,
            top_p=0.95,
        )
        response = await client.acall(messages)
        print(response.content)    # Clean content without think tags
        print(response.reasoning)  # Extracted reasoning
    """

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        cache_control_injection_points: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Call with <think> tag extraction."""
        response = super().call(
            messages, tools, output_model, cache_control_injection_points, **kwargs
        )

        # Extract think tags from content
        if isinstance(response.content, str) and response.content:
            cleaned_content, think_reasoning = _extract_think_tags(response.content)

            # Combine extracted reasoning with any existing reasoning
            if think_reasoning:
                existing_reasoning = response.reasoning or ""
                combined_reasoning = (
                    f"{existing_reasoning}\n\n{think_reasoning}".strip()
                    if existing_reasoning
                    else think_reasoning
                )

                return LLMResponse(
                    raw_response=response.raw_response,
                    content=cleaned_content,
                    tool_calls=response.tool_calls,
                    finish_reason=response.finish_reason,
                    assistant_message={
                        "role": "assistant",
                        "content": cleaned_content,
                    },
                    reasoning=combined_reasoning,
                    usage=response.usage,
                )

        return response

    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        cache_control_injection_points: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Async call with <think> tag extraction."""
        response = await super().acall(
            messages, tools, output_model, cache_control_injection_points, **kwargs
        )

        # Extract think tags from content
        if isinstance(response.content, str) and response.content:
            cleaned_content, think_reasoning = _extract_think_tags(response.content)

            # Combine extracted reasoning with any existing reasoning
            if think_reasoning:
                existing_reasoning = response.reasoning or ""
                combined_reasoning = (
                    f"{existing_reasoning}\n\n{think_reasoning}".strip()
                    if existing_reasoning
                    else think_reasoning
                )

                return LLMResponse(
                    raw_response=response.raw_response,
                    content=cleaned_content,
                    tool_calls=response.tool_calls,
                    finish_reason=response.finish_reason,
                    assistant_message={
                        "role": "assistant",
                        "content": cleaned_content,
                    },
                    reasoning=combined_reasoning,
                    usage=response.usage,
                )

        return response


class ResponsesClient(UnifiedLLM):
    def _convert_tool_to_schema(self, tool: Tool) -> dict[str, Any]:
        """Convert Tool object to Responses API schema format."""
        schema_loose = tool.get_parameter_schema()
        required = schema_loose.get("required", [])
        properties = schema_loose.get("properties", {})
        use_strict = len(required) >= len(properties)

        if use_strict:
            schema = tool.get_parameter_schema(strict=True)
            if not _strict_schema_valid(schema):
                logger.warning(
                    "[ResponsesClient] Tool '%s' has parameters that cannot satisfy "
                    "strict-mode schema requirements (e.g. Any type, untyped properties). "
                    "Falling back to non-strict mode.",
                    tool.name,
                )
                schema = schema_loose
                use_strict = False
        else:
            schema = schema_loose

        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": schema,
            "strict": use_strict,
        }

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Sync version: Call LLM and parse response.

        Handles both native Responses format (from ResponsesProviderFormatter) and
        legacy OpenAI Chat format. System messages are extracted to the `instructions` param.
        """
        input_messages, instructions = self._transform_messages(messages)

        api_params = {
            "model": self.model,
            "input": input_messages,
            "truncation": "disabled",
            **self.config,
            **kwargs,
        }

        if instructions:
            api_params["instructions"] = instructions

        if "base_url" in api_params:
            api_params["api_base"] = api_params.pop("base_url")

        if tools:
            api_params["tools"] = [self._convert_tool_to_schema(tool) for tool in tools]
            api_params["tool_choice"] = "auto"
            api_params["parallel_tool_calls"] = False

        if output_model is not None:
            api_params["text_format"] = output_model

        raw_response = cast("litellm.ResponsesAPIResponse", litellm.responses(**api_params))

        # Extract usage if available (Responses API may have different structure)
        usage = None
        if hasattr(raw_response, "usage") and raw_response.usage:
            usage_obj = raw_response.usage
            if hasattr(usage_obj, "model_dump"):
                usage = usage_obj.model_dump()
            elif isinstance(usage_obj, dict):
                usage = usage_obj

        output: list[Any] = raw_response.output  # type: ignore[assignment]
        raw_tool_calls = [item for item in output if item.type == "function_call"]

        if raw_tool_calls:
            tool_calls = [
                ToolCall(id=tc.call_id or "", name=tc.name or "", arguments=tc.arguments or "")
                for tc in raw_tool_calls
            ]

            assistant_messages = []
            for item in output:
                if hasattr(item, "model_dump"):
                    assistant_messages.append(item.model_dump())
                else:
                    assistant_messages.append(item)

            return LLMResponse(
                raw_response=raw_response,
                content="",
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                assistant_message={"_batch": assistant_messages},
                reasoning=None,  # Responses API doesn't have reasoning
                usage=usage,
            )

        text_content = self._extract_text_from_output(raw_response)

        if output_model:
            json_data = extract_and_parse_json(text_content)
            parsed_content = _instantiate_output_model(output_model, json_data)

            return LLMResponse(
                raw_response=raw_response,
                content=parsed_content,
                tool_calls=[],
                finish_reason="stop",
                assistant_message={"role": "assistant", "content": text_content},
                reasoning=None,
                usage=usage,
            )

        return LLMResponse(
            raw_response=raw_response,
            content=text_content,
            tool_calls=[],
            finish_reason="stop",
            assistant_message={"role": "assistant", "content": text_content},
            reasoning=None,
            usage=usage,
        )

    async def acall(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        output_model: type[BaseModel] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Async version: Call LLM and parse response.

        Handles both native Responses format (from ResponsesProviderFormatter) and
        legacy OpenAI Chat format. System messages are extracted to the `instructions` param.
        """
        input_messages, instructions = self._transform_messages(messages)

        api_params = {
            "model": self.model,
            "input": input_messages,
            "truncation": "disabled",
            **self.config,
            **kwargs,
        }

        if instructions:
            api_params["instructions"] = instructions

        if "base_url" in api_params:
            api_params["api_base"] = api_params.pop("base_url")

        if tools:
            api_params["tools"] = [self._convert_tool_to_schema(tool) for tool in tools]
            api_params["tool_choice"] = "auto"
            api_params["parallel_tool_calls"] = False

        if output_model is not None:
            api_params["text_format"] = output_model

        raw_response = cast("litellm.ResponsesAPIResponse", await litellm.aresponses(**api_params))

        # Extract usage if available (Responses API may have different structure)
        usage = None
        if hasattr(raw_response, "usage") and raw_response.usage:
            usage_obj = raw_response.usage
            if hasattr(usage_obj, "model_dump"):
                usage = usage_obj.model_dump()
            elif isinstance(usage_obj, dict):
                usage = usage_obj

        output: list[Any] = raw_response.output  # type: ignore[assignment]
        raw_tool_calls = [item for item in output if item.type == "function_call"]

        if raw_tool_calls:
            tool_calls = [
                ToolCall(id=tc.call_id or "", name=tc.name or "", arguments=tc.arguments or "")
                for tc in raw_tool_calls
            ]

            assistant_messages = []
            for item in output:
                if hasattr(item, "model_dump"):
                    assistant_messages.append(item.model_dump())
                else:
                    assistant_messages.append(item)

            return LLMResponse(
                raw_response=raw_response,
                content="",
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                assistant_message={"_batch": assistant_messages},
                reasoning=None,
                usage=usage,
            )

        text_content = self._extract_text_from_output(raw_response)

        if output_model:
            json_data = extract_and_parse_json(text_content)
            parsed_content = _instantiate_output_model(output_model, json_data)

            return LLMResponse(
                raw_response=raw_response,
                content=parsed_content,
                tool_calls=[],
                finish_reason="stop",
                assistant_message={"role": "assistant", "content": text_content},
                reasoning=None,
                usage=usage,
            )

        return LLMResponse(
            raw_response=raw_response,
            content=text_content,
            tool_calls=[],
            finish_reason="stop",
            assistant_message={"role": "assistant", "content": text_content},
            reasoning=None,
            usage=usage,
        )

    def _transform_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Transform messages to Responses API format and extract instructions.

        Handles two input formats:
        1. Native Responses format (from ResponsesProviderFormatter): messages contain
           "type": "function_call" / "function_call_output" items alongside role-based messages.
           System messages have {"role": "system", ...} and are extracted to instructions.
        2. Legacy OpenAI Chat format: messages use {"role": "tool", "tool_call_id": ...} and
           {"role": "assistant", "tool_calls": [...]}. These are converted to native format.

        Returns (input_messages, instructions) where instructions is the concatenated
        system message content (or None if no system messages).
        """
        instructions_parts: list[str] = []
        transformed: list[dict[str, Any]] = []

        for msg in messages:
            # System messages → extract to instructions
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:
                    instructions_parts.append(content)
                continue

            # Already in native Responses format (from ResponsesProviderFormatter)
            if "type" in msg:
                transformed.append(msg)
                continue

            # Legacy OpenAI format: tool result messages
            if msg.get("role") == "tool":
                transformed.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg["tool_call_id"],
                        "output": msg.get("content", ""),
                    }
                )
                continue

            # Legacy OpenAI format: assistant messages with tool_calls
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    transformed.append(
                        {
                            "type": "function_call",
                            "call_id": tc["id"],
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", ""),
                        }
                    )
                continue

            # User/Assistant text messages → passthrough
            if msg.get("role") in ["user", "assistant"]:
                content = msg.get("content", "")
                if content is None:
                    content = ""
                transformed.append({"role": msg["role"], "content": content})
                continue

            # Unknown format → passthrough
            transformed.append(msg)

        instructions = "\n\n".join(instructions_parts) if instructions_parts else None
        return transformed, instructions

    def _extract_text_from_output(self, response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        if hasattr(response, "output"):
            for item in response.output:
                if item.type == "message":
                    texts = []
                    for content_item in item.content:
                        if hasattr(content_item, "text"):
                            texts.append(content_item.text)  # type: ignore
                    return "\n".join(texts)
        return ""
