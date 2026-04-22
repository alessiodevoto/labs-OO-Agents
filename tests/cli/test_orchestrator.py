# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the TUI agent orchestrator — removed with the forever-loop rewrite.

Orchestrator mode (phase-tracking ``respond()`` dispatcher that routed
user messages through classify/brainstorm/plan/implement/verify) was
removed when ``respond()`` became a single forever-loop. The phase
methods (``classify_intent``, ``_legacy_brainstorm``, ``write_plan``,
etc.) still exist on ``TUIAgent`` and can be invoked directly by the
LLM via CodeAct — they just no longer drive a state machine from
``respond()``.

If orchestrator mode returns it will need new tests designed against
the queue-pump model.
"""

import pytest

pytest.skip(
    "Orchestrator mode was removed with the forever-loop respond() rewrite "
    "(feat/tui-input-queues). Phase methods still exist as LLM-callable "
    "tools on TUIAgent but there's no longer a state-machine dispatcher "
    "to test end-to-end.",
    allow_module_level=True,
)
