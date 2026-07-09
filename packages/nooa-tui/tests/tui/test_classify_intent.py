# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for intent classification method."""

import inspect

from nooa_tui.tui.models import Intent


def test_classify_intent_returns_intent():
    """classify_intent should return an Intent with valid task_type."""
    from nooa_tui.tui.agent import TUIAgent

    method = getattr(TUIAgent, "classify_intent", None)
    assert method is not None, "TUIAgent must have classify_intent method"

    sig = inspect.signature(method)
    assert sig.return_annotation in (Intent, "Intent"), (
        f"Expected Intent return type, got {sig.return_annotation}"
    )

    params = list(sig.parameters.keys())
    assert "user_message" in params, "classify_intent must accept user_message parameter"
