# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared utilities for nemo_oo_agents CLI commands.

Import from here instead of duplicating helpers across command modules:

    from nemo_oo_agents_cli._common import format_size, load_dotenv_into
"""

from pathlib import Path


def format_size(size_bytes: int) -> str:
    """Format a byte count as human-readable (e.g. '4.2 MB')."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def load_dotenv_into(env_file: Path, env: dict[str, str]):
    """Simple .env loader — parse KEY=VALUE lines into an env dict.

    No external dependencies needed. Handles comments, quoting, blank lines.
    """
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key:
                        env[key] = value
    except OSError:
        pass
