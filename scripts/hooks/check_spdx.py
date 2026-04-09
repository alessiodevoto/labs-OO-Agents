# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that Python source files contain an SPDX-License-Identifier header."""

import sys

MARKER = "SPDX-License-Identifier"
SEARCH_LINES = 5  # Only check the top of the file

retval = 0
for filename in sys.argv[1:]:
    try:
        with open(filename) as f:
            head = [f.readline() for _ in range(SEARCH_LINES)]
    except (UnicodeDecodeError, PermissionError):
        continue
    if not any(MARKER in line for line in head):
        print(f"Missing SPDX-License-Identifier header: {filename}")
        retval = 1
sys.exit(retval)
