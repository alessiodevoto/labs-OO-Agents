# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Entry point for NeMo OO Agents TUI.

Usage:
    python -m tui
    python -m tui --model gpt-4o
"""

import asyncio
import sys

from .config import Config
from .main import main, parse_args

if __name__ == "__main__":
    try:
        args = parse_args()
        asyncio.run(main(config=Config.load(**vars(args))))
    except KeyboardInterrupt:
        sys.exit(0)
