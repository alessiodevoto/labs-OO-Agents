"""Tests for configurable import restrictions.

Tests the new restricted_imports deny-list feature on RestrictionsConfig,
SecurityValidator, and the ValidationContext integration.
"""

import pytest

from nemo_oo_agents.runtime.code_validator import (
    UnifiedCodeValidator,
    ValidationContext,
    ValidationError,
)
from nemo_oo_agents.runtime.restrictions import (
    DEFAULT_BLOCKED_MODULES,
    RestrictionsConfig,
)

# =============================================================================
# RestrictionsConfig — restricted_imports field
# =============================================================================


class TestRestrictedImportsConfig:
    """Tests for the new restricted_imports field on RestrictionsConfig."""

    def test_default_restricted_imports_is_empty(self):
        """Default restricted_imports should be empty (all imports allowed)."""
        rc = RestrictionsConfig()
        assert rc.restricted_imports == frozenset()
        assert isinstance(rc.restricted_imports, frozenset)

    def test_default_restricted_imports_includes_key_modules(self):
        """DEFAULT_RESTRICTED_IMPORTS should include os, shutil, pathlib, sys, ctypes, importlib."""
        from nemo_oo_agents.runtime.restrictions import DEFAULT_RESTRICTED_IMPORTS

        expected = {"os", "shutil", "pathlib", "sys", "ctypes", "importlib"}
        assert expected <= DEFAULT_RESTRICTED_IMPORTS

    def test_custom_restricted_imports(self):
        """Developers can set a custom restricted_imports set."""
        rc = RestrictionsConfig(restricted_imports=frozenset({"numpy", "pandas"}))
        assert rc.restricted_imports == frozenset({"numpy", "pandas"})

    def test_empty_restricted_imports_allows_all(self):
        """Empty frozenset means no import restrictions."""
        rc = RestrictionsConfig(restricted_imports=frozenset())
        assert rc.restricted_imports == frozenset()

    def test_restricted_imports_frozen(self):
        """restricted_imports field should be immutable (frozen config)."""
        from pydantic import ValidationError as PydanticValidationError

        rc = RestrictionsConfig()
        with pytest.raises(PydanticValidationError):
            rc.restricted_imports = frozenset()

    def test_importlib_in_blocked_calls(self):
        """importlib.import_module should be in DEFAULT_BLOCKED_CALLS."""
        from nemo_oo_agents.runtime.restrictions import DEFAULT_BLOCKED_CALLS

        assert "importlib" in DEFAULT_BLOCKED_CALLS
        assert "import_module" in DEFAULT_BLOCKED_CALLS["importlib"]


# =============================================================================
# SecurityValidator — deny-list import validation
# =============================================================================


class TestSecurityValidatorRestrictedImports:
    """Tests for SecurityValidator using restricted_imports deny list."""

    @pytest.fixture
    def validator(self) -> UnifiedCodeValidator:
        return UnifiedCodeValidator()

    def test_unrestricted_import_allowed(self, validator):
        """Modules not in restricted_imports should be importable."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset({"os", "sys"}),
        )
        code = "import json"
        validator.validate(code, context)  # should not raise

    def test_restricted_import_rejected(self, validator):
        """Modules in restricted_imports should be rejected."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset({"os", "sys"}),
        )
        code = "import os"
        with pytest.raises(ValidationError, match="os.*restricted"):
            validator.validate(code, context)

    def test_restricted_from_import_rejected(self, validator):
        """from-import of restricted modules should be rejected."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset({"os", "sys"}),
        )
        code = "from os import path"
        with pytest.raises(ValidationError, match="os.*restricted"):
            validator.validate(code, context)

    def test_restricted_child_module_rejected(self, validator):
        """Child modules of restricted parents should be rejected."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset({"os"}),
        )
        code = "import os.path"
        with pytest.raises(ValidationError, match="os.*restricted"):
            validator.validate(code, context)

    def test_empty_restricted_allows_everything(self, validator):
        """Empty restricted_imports means all imports are allowed."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset(),
        )
        # os would normally be restricted by default, but empty set = allow all
        code = "import os"
        validator.validate(code, context)  # should not raise

    def test_empty_restricted_allows_any_stdlib(self, validator):
        """With empty restricted_imports, any stdlib module is importable."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset(),
        )
        for mod in ["json", "csv", "re", "collections", "itertools", "math", "os", "sys"]:
            code = f"import {mod}"
            validator.validate(code, context)  # should not raise

    def test_blocked_modules_still_blocked_with_empty_restricted(self, validator):
        """blocked_modules are always blocked regardless of restricted_imports."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset(),
            blocked_modules=DEFAULT_BLOCKED_MODULES,
        )
        # subprocess is in DEFAULT_BLOCKED_MODULES — should still be blocked
        code = "import subprocess"
        with pytest.raises(ValidationError):
            validator.validate(code, context)

    def test_default_config_allows_os(self, validator):
        """With default RestrictionsConfig (empty deny list), 'os' is allowed."""
        rc = RestrictionsConfig()
        context = ValidationContext(
            code="",
            restricted_imports=rc.restricted_imports,
        )
        code = "import os"
        validator.validate(code, context)  # should not raise

    def test_default_config_allows_json(self, validator):
        """With default RestrictionsConfig, 'json' should be allowed."""
        rc = RestrictionsConfig()
        context = ValidationContext(
            code="",
            restricted_imports=rc.restricted_imports,
        )
        code = "import json"
        validator.validate(code, context)  # should not raise

    def test_default_config_allows_csv(self, validator):
        """With default RestrictionsConfig, 'csv' should be allowed."""
        rc = RestrictionsConfig()
        context = ValidationContext(
            code="",
            restricted_imports=rc.restricted_imports,
        )
        code = "import csv"
        validator.validate(code, context)  # should not raise

    def test_parent_not_restricted_by_child(self, validator):
        """If only 'os.path' is restricted, 'os' itself should be allowed."""
        context = ValidationContext(
            code="",
            restricted_imports=frozenset({"os.path"}),
        )
        code = "import os"
        validator.validate(code, context)  # should not raise


# =============================================================================
# ValidationContext — restricted_imports field
# =============================================================================


class TestValidationContextRestrictedImports:
    """Tests for the restricted_imports field on ValidationContext."""

    def test_default_restricted_imports_is_empty(self):
        """ValidationContext defaults to empty restricted_imports."""
        ctx = ValidationContext()
        assert ctx.restricted_imports == frozenset()

    def test_restricted_imports_set_explicitly(self):
        """restricted_imports can be set explicitly."""
        ctx = ValidationContext(restricted_imports=frozenset({"os", "sys"}))
        assert ctx.restricted_imports == frozenset({"os", "sys"})
