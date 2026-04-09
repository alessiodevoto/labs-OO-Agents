# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tool configuration for BashTool."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class BashConfig(BaseModel):
    """Config for BashTool. Set via: BashTool(config=BashConfig(...))"""

    model_config = ConfigDict(frozen=True)

    default_timeout: float = 30.0
    use_sandbox: bool = False
    srt_settings: str | Path | None = None
    srt_executable: str | None = None

    def merge_with(self, other: "BashConfig") -> "BashConfig":
        if not other.model_fields_set:
            raise ValueError(
                "merge_with() received a config with no model_fields_set. "
                "Was it constructed from model_dump() or model_validate()? "
                "Config objects must be freshly constructed: BashConfig(field=value)."
            )
        return self.model_copy(update={k: getattr(other, k) for k in other.model_fields_set})
