# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Secret scrubbing for telemetry spans.

Intercepts OpenTelemetry spans before export and redacts any detected
secrets (API keys, tokens, private keys) from string attributes.

Uses ``detect-secrets`` with high-precision detectors only (no entropy-based
detection, which produces too many false positives on code/log output).

Usage::

    from nemo_oo_agents.tracing._secret_scrubber import SecretScrubSpanProcessor

    provider.add_span_processor(SecretScrubSpanProcessor(inner_processor))
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex-based secret patterns (high precision, low false positives)
# ---------------------------------------------------------------------------
# These patterns are designed to match real secrets with minimal noise.
# Ordered roughly by how common they are in practice.

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # AWS Access Key IDs (always start with AKIA, ASIA, AIDA, AROA)
    ("AWS Access Key", re.compile(r"(?:^|[^A-Z0-9])(?P<secret>(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16})(?:$|[^A-Z0-9])")),
    # AWS Secret Access Keys (40-char base64 after a known prefix)
    ("AWS Secret Key", re.compile(r"(?:aws_secret_access_key|secret.?key)\s*[=:]\s*[\"\'\s]*(?P<secret>[A-Za-z0-9/+=]{40})(?:[\"\'\s]|$)", re.IGNORECASE)),
    # Generic API key/token patterns (key=value or key: value with high-entropy value)
    ("API Key/Token", re.compile(
        r"(?:api.?key|api.?token|auth.?token|access.?token|bearer|secret.?key|private.?key|password|passwd|credentials?)\s*[=:]+\s*[\"\'\s]*(?P<secret>[A-Za-z0-9_.\-/+=]{20,})",
        re.IGNORECASE,
    )),
    # Bearer tokens in Authorization headers
    ("Bearer Token", re.compile(r"[Bb]earer\s+(?P<secret>[A-Za-z0-9_.\-/+=]{20,})")),
    # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    ("GitHub Token", re.compile(r"(?P<secret>gh[pousr]_[A-Za-z0-9_]{36,})")),
    # GitLab tokens
    ("GitLab Token", re.compile(r"(?P<secret>glpat-[A-Za-z0-9\-_]{20,})")),
    # Slack tokens
    ("Slack Token", re.compile(r"(?P<secret>xox[bporas]-[A-Za-z0-9\-]{10,})")),
    # Stripe keys
    ("Stripe Key", re.compile(r"(?P<secret>(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,})")),
    # Private keys (PEM format)
    ("Private Key", re.compile(r"(?P<secret>-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----)")),
    # NVIDIA API keys (nvapi-)
    ("NVIDIA API Key", re.compile(r"(?P<secret>nvapi-[A-Za-z0-9_\-]{20,})")),
    # OpenAI API keys
    ("OpenAI Key", re.compile(r"(?P<secret>sk-[A-Za-z0-9]{20,})")),
    # Anthropic API keys
    ("Anthropic Key", re.compile(r"(?P<secret>sk-ant-[A-Za-z0-9_\-]{20,})")),
    # Google API keys
    ("Google Key", re.compile(r"(?P<secret>AIza[A-Za-z0-9_\-]{35})")),
    # Generic hex tokens (64+ char hex strings that look like keys)
    ("Hex Token", re.compile(r"(?:token|key|secret|hash)\s*[=:]\s*[\"\'\s]*(?P<secret>[0-9a-f]{64,})", re.IGNORECASE)),
]

_REDACTED = "[REDACTED]"


def scrub_string(text: str) -> str:
    """Scrub secrets from a string, replacing them with [REDACTED].

    Returns the original string if no secrets are found (fast path).
    """
    if not text or len(text) < 20:
        return text

    result = text
    for _name, pattern in _SECRET_PATTERNS:
        def _replace(m: re.Match) -> str:
            full = m.group(0)
            secret = m.group("secret")
            return full.replace(secret, _REDACTED)
        result = pattern.sub(_replace, result)

    return result


def scrub_value(value: Any) -> Any:
    """Scrub secrets from a span attribute value.

    Handles strings, lists of strings, and passes through other types unchanged.
    """
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# OpenTelemetry SpanProcessor
# ---------------------------------------------------------------------------
try:
    from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

    class SecretScrubSpanProcessor(SpanProcessor):
        """Wraps another SpanProcessor, scrubbing secrets from span attributes before export.

        Usage::

            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            inner = SimpleSpanProcessor(exporter)
            provider.add_span_processor(SecretScrubSpanProcessor(inner))
        """

        def __init__(self, inner: SpanProcessor) -> None:
            self._inner = inner

        def on_start(self, span: Span, parent_context: Any = None) -> None:
            self._inner.on_start(span, parent_context)

        def on_end(self, span: ReadableSpan) -> None:
            # Scrub all string attributes before passing to inner processor
            if span.attributes:
                scrubbed = {}
                changed = False
                for key, value in span.attributes.items():
                    new_value = scrub_value(value)
                    scrubbed[key] = new_value
                    if new_value is not value:
                        changed = True

                if changed:
                    # ReadableSpan.attributes is a MappingProxy — we need to
                    # replace it. The span._attributes dict is the backing store.
                    if hasattr(span, "_attributes"):
                        span._attributes = scrubbed  # type: ignore[attr-defined]

            self._inner.on_end(span)

        def shutdown(self) -> None:
            self._inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return self._inner.force_flush(timeout_millis)

except ImportError:
    # OpenTelemetry not installed — provide a no-op stub
    pass

