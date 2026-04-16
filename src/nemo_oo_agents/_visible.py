# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""No-op context manager for backward compatibility.

``with visible:`` is a no-op because everything is visible by default in
nemo_oo_agents. This exists purely for compatibility with agent files that were
written against an older API that had an explicit ``visible`` context manager.
"""

from __future__ import annotations

from typing import Literal


class _Visible:
    """No-op context manager — everything is visible by default."""

    def __enter__(self) -> _Visible:
        return self

    def __exit__(self, *_: object) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return "visible"


visible = _Visible()
