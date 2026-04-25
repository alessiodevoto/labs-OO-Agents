# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for allow_all_imports in RestrictionsConfig."""

import ast

from nemo_oo_agents.runtime.code_validator import SecurityValidator, ValidationContext


class TestAllowAllImports:
    """Verify that allow_all_imports bypasses import checking."""

    def _validate(self, code: str, *, allow_all_imports: bool = False) -> list:
        tree = ast.parse(code)
        ctx = ValidationContext(
            code=code,
            importable_modules={"json", "re"},
            allow_all_imports=allow_all_imports,
        )
        v = SecurityValidator()
        return v.validate(tree, ctx)

    def test_unknown_import_blocked_by_default(self):
        issues = self._validate("import requests")
        assert any("requests" in i.message for i in issues)

    def test_unknown_import_allowed_when_flag_set(self):
        issues = self._validate("import requests", allow_all_imports=True)
        import_issues = [i for i in issues if "requests" in i.message]
        assert import_issues == []

    def test_known_import_always_allowed(self):
        issues = self._validate("import json")
        import_issues = [i for i in issues if "json" in i.message]
        assert import_issues == []

    def test_from_import_allowed_when_flag_set(self):
        issues = self._validate("from pathlib import Path", allow_all_imports=True)
        import_issues = [i for i in issues if "pathlib" in i.message]
        assert import_issues == []

    def test_from_import_blocked_by_default(self):
        issues = self._validate("from pathlib import Path")
        assert any("pathlib" in i.message for i in issues)

    def test_star_import_allowed_when_flag_set(self):
        """from X import * is allowed with allow_all_imports."""
        issues = self._validate("from json import *", allow_all_imports=True)
        star_issues = [i for i in issues if "*" in i.message]
        assert star_issues == []

    def test_star_import_blocked_by_default(self):
        """from X import * is blocked without allow_all_imports."""
        issues = self._validate("from json import *")
        assert any("*" in i.message for i in issues)

    def test_forbidden_builtins_still_blocked(self):
        """__import__ is still forbidden even with allow_all_imports."""
        issues = self._validate("__import__('os')", allow_all_imports=True)
        assert any("__import__" in i.message for i in issues)

    def test_blocked_modules_allowed_when_flag_set(self):
        """subprocess/socket are allowed with allow_all_imports — the developer opted in."""
        issues = self._validate("import subprocess", allow_all_imports=True)
        import_issues = [i for i in issues if "subprocess" in i.message]
        assert import_issues == [], (
            "import subprocess should be allowed with allow_all_imports=True"
        )

        issues = self._validate("import socket", allow_all_imports=True)
        import_issues = [i for i in issues if "socket" in i.message]
        assert import_issues == [], "import socket should be allowed with allow_all_imports=True"
