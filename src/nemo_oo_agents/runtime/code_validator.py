# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unified AST validation for agent-generated code.

This module provides a single entry point for all code validation:
- SecurityValidator: Prevents security risks (forbidden builtins, imports, escapes)
- BlockingCallValidator: Prevents blocking calls that freeze the event loop
- REPLPolicyValidator: Enforces REPL-style coding conventions

Usage:
    validator = UnifiedCodeValidator()
    context = ValidationContext(
        code=code,
        available_names=["self", "asyncio"],
        importable_modules={"asyncio"},
    )
    validator.validate(code, context)  # Raises ValidationError on failure
"""

# =============================================================================
# Error Code Registry
# =============================================================================
# SecurityValidator:
#   E001 — Forbidden builtin usage (exec, eval, compile, __import__, etc.)
#   E002 — Forbidden import (module not in importable_modules)
#   E003 — Forbidden dunder attribute access (__class__, __subclasses__, etc.)
#   E004 — Forbidden escape via sys/os attributes (sys.modules, os.system, etc.)
# REPLPolicyValidator:
#   E101 — Bare function/class definition without assignment or call
#   E104 — Import statement (use exec_globals instead)
#   E301 — Missing await on async call
#   W303 — Async def without await (warning)
# ClassAssignmentValidator:
#   E401 — Forbidden class attribute assignment (self.X = ...)
#   E402 — Forbidden class method call (self.X(...))
# BlockingCallValidator:
#   E310 — Blocking call that would freeze the event loop
# =============================================================================
import ast
import inspect
import logging
import types
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from nemo_oo_agents.errors import RestrictedCodeError as ValidationError
from nemo_oo_agents.runtime.restrictions import (
    RestrictionsConfig,
    match_blocked_module,
)

logger = logging.getLogger(__name__)

# Re-export ValidationError for convenience
__all__ = [
    "UnifiedCodeValidator",
    "SecurityValidator",
    "BlockingCallValidator",
    "REPLPolicyValidator",
    "ClassAssignmentValidator",
    "ValidationContext",
    "ValidationError",
    "ValidationIssue",
]


# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class ValidationIssue:
    """Single validation issue with location and severity."""

    line: int
    col: int
    message: str
    severity: Literal["error", "warning"] = "error"
    code: str = ""  # Error code like "E001", "W001"
    fix_hint: str | None = None
    doc_link: str | None = None  # Link to documentation


@dataclass
class ValidationContext:
    """Shared context for all validators."""

    code: str = ""
    agent_class: type | None = None
    available_names: set[str] = field(default_factory=set)
    importable_modules: set[str] = field(default_factory=set)
    forbidden_self_calls: set[str] = field(default_factory=set)
    execution_count: int = 1
    agent: Any = None  # Agent instance for method introspection
    exec_globals: dict[str, Any] = field(default_factory=dict)


class Validator(Protocol):
    """Protocol for individual validators."""

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        """Validate AST and return list of issues."""
        ...


# =============================================================================
# Security Validator
# =============================================================================

# Functions that are always forbidden in generated code
FORBIDDEN_BUILTINS = frozenset(
    {
        # Dynamic code execution (security risk)
        "exec",
        "eval",
        "compile",
        "__import__",
        # Blocking stdin operations (cause hangs)
        "input",
        "breakpoint",
        # Namespace access (security risk)
        "globals",
        "locals",
        "vars",  # Similar to locals(), gives access to __dict__
        # Process termination
        "exit",
        "quit",
    }
)

# Dangerous dunder attributes that enable sandbox escapes
DANGEROUS_DUNDER_ATTRS = frozenset(
    {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__dict__",
    }
)


class SecurityValidator:
    """Validates code for security risks.

    Checks for:
    - Forbidden builtins (exec, eval, compile, __import__, input, breakpoint, globals, locals)
    - Forbidden imports (modules not in importable_modules)
    - Import * (always forbidden)
    - Dangerous dunder attribute access (__class__, __bases__, etc.)
    - Recursive self-calls (infinite recursion prevention)
    - Aliased forbidden builtins
    """

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        """Validate security rules and return issues."""
        visitor = _SecurityVisitor(context)
        visitor.visit(tree)
        return visitor.issues


class _SecurityVisitor(ast.NodeVisitor):
    """AST visitor for security validation."""

    def __init__(self, context: ValidationContext):
        self.context = context
        self.issues: list[ValidationIssue] = []
        # Track aliases: local_name -> original_forbidden_name
        self.forbidden_aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> Any:
        """Check import statements."""
        for alias in node.names:
            if not self._is_module_available(alias.name):
                self.issues.append(self._make_import_error(node, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        """Check from-import statements."""
        # from X import * is always forbidden
        if any(alias.name == "*" for alias in node.names):
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="'from ... import *' is forbidden for security reasons",
                    code="E003",
                )
            )
            self.generic_visit(node)
            return

        # Check if module is available
        module_name = node.module or ""
        if not self._is_module_available(module_name):
            self.issues.append(self._make_import_error(node, module_name))
        else:
            # Track aliases of forbidden builtins
            for alias in node.names:
                if alias.name in FORBIDDEN_BUILTINS:
                    local_name = alias.asname or alias.name
                    self.forbidden_aliases[local_name] = alias.name

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        """Check function calls."""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id

            # Check direct forbidden calls
            if func_name in FORBIDDEN_BUILTINS:
                self.issues.append(
                    ValidationIssue(
                        line=node.lineno,
                        col=node.col_offset,
                        message=f"{func_name}() is forbidden - it blocks or allows code execution",
                        code="E001",
                    )
                )
            # Check aliased forbidden calls
            elif func_name in self.forbidden_aliases:
                original = self.forbidden_aliases[func_name]
                self.issues.append(
                    ValidationIssue(
                        line=node.lineno,
                        col=node.col_offset,
                        message=f"{func_name}() is forbidden (alias for {original})",
                        code="E001",
                    )
                )
            # Check setattr/delattr/getattr with dunder names
            elif func_name in ("setattr", "delattr", "getattr"):
                self._check_attr_modification_with_dunder(node, func_name)

        # Check for forbidden self.method() calls (prevents recursion)
        if self.context.forbidden_self_calls and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                method_name = node.func.attr
                if method_name in self.context.forbidden_self_calls:
                    self.issues.append(
                        ValidationIssue(
                            line=node.lineno,
                            col=node.col_offset,
                            message=f"calling self.{method_name}() is forbidden - "
                            "this would cause infinite recursion",
                            code="E004",
                        )
                    )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        """Check attribute access for dangerous dunders."""
        if node.attr in DANGEROUS_DUNDER_ATTRS:
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Access to '{node.attr}' is forbidden - "
                    "this could bypass security restrictions",
                    code="E101",
                )
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:
        """Check name access for __builtins__."""
        if node.id == "__builtins__":
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="Access to '__builtins__' is forbidden - "
                    "this could bypass security restrictions",
                    code="E101",
                )
            )
        self.generic_visit(node)

    def _is_module_available(self, module_name: str) -> bool:
        """Check if module (or parent) is importable."""
        modules = self.context.importable_modules
        if not modules:
            # Fallback to available_names for backwards compat
            modules = self.context.available_names

        # Exact match
        if module_name in modules:
            return True
        # Top-level package match (e.g., "asyncio" for "asyncio.tasks")
        top_level = module_name.split(".")[0]
        return top_level in modules

    def _check_attr_modification_with_dunder(self, node: ast.Call, func_name: str) -> None:
        """Check if setattr/delattr is being used with dunder attribute names."""
        # setattr(obj, name, value) or delattr(obj, name)
        # The attribute name is the second argument
        if len(node.args) < 2:
            return

        attr_arg = node.args[1]

        # Check if it's a string literal
        if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
            attr_name = attr_arg.value
            if attr_name in DANGEROUS_DUNDER_ATTRS:
                self.issues.append(
                    ValidationIssue(
                        line=node.lineno,
                        col=node.col_offset,
                        message=f"{func_name}() with '{attr_name}' is forbidden - "
                        "this could bypass security restrictions",
                        code="E104",
                    )
                )
            elif attr_name.startswith("__") and attr_name.endswith("__"):
                self.issues.append(
                    ValidationIssue(
                        line=node.lineno,
                        col=node.col_offset,
                        message=f"{func_name}() with dunder attribute '{attr_name}' is forbidden - "
                        "this could bypass security restrictions",
                        code="E104",
                    )
                )

    def _make_import_error(
        self, node: ast.Import | ast.ImportFrom, module_name: str
    ) -> ValidationIssue:
        """Create error for forbidden import."""
        msg = (
            f"import of '{module_name}' is forbidden. "
            f"All imports are pre-loaded — use what's in scope. "
            f"Also forbidden: eval(), exec(), compile(), __import__(), "
            f"input(), globals(), locals(), breakpoint()"
        )
        if self.context.available_names:
            useful = sorted(
                n
                for n in self.context.available_names
                if not n.startswith("_") and (n[0].isupper() or n.islower())
            )
            if useful:
                hint = ", ".join(useful)
                msg += f". Available in scope: {hint}"
        return ValidationIssue(
            line=node.lineno,
            col=node.col_offset,
            message=msg,
            code="E002",
        )


# =============================================================================
# REPL Policy Validator
# =============================================================================
class REPLPolicyValidator:
    """Validates REPL-style coding conventions.

    Checks for:
    - Missing await on async method calls
    """

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        """Validate REPL policy rules and return issues."""
        visitor = _REPLPolicyVisitor(context)
        visitor.visit(tree)
        return visitor.issues


class _REPLPolicyVisitor(ast.NodeVisitor):
    """AST visitor for REPL policy validation."""

    def __init__(self, context: ValidationContext):
        self.context = context
        self.issues: list[ValidationIssue] = []
        self.async_method_names: set[str] = set()
        self.parent_map: dict[ast.AST, ast.AST] = {}
        self._collect_async_methods()

    def visit(self, node: ast.AST) -> Any:
        """Build parent map while visiting."""
        for child in ast.iter_child_nodes(node):
            self.parent_map[child] = node
        return super().visit(node)

    def visit_While(self, node: ast.While) -> Any:
        """Check for infinite loops (while True without break/return)."""
        if self._is_infinite_loop(node) and not self._has_exit_statement(node):
            from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

            get_harness_metrics().infinite_loop()
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="Potential infinite loop detected (while True without break/return) - "
                    "add a break condition or use an iteration limit",
                    code="W303",
                    severity="error",  # Make this an error, not warning
                )
            )
        self.generic_visit(node)

    def _is_infinite_loop(self, node: ast.While) -> bool:
        """Check if while loop has a constant True condition."""
        # while True:
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            return True
        # while 1:
        if isinstance(node.test, ast.Constant) and node.test.value == 1:
            return True
        # while not False:
        if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
            if isinstance(node.test.operand, ast.Constant) and node.test.operand.value is False:
                return True
        return False

    def _has_exit_statement(self, node: ast.While) -> bool:
        """Check if while loop body has a break, return, or raise at the loop level.

        Does NOT count:
        - break/return/raise inside nested function/class definitions
        - break inside nested loops (only exits the inner loop)
        """
        for child in node.body:
            if self._has_exit_in_subtree(child, check_break=True):
                return True
        return False

    def _has_exit_in_subtree(self, node: ast.AST, check_break: bool = True) -> bool:
        """Check if node or its children (excluding nested defs/loops) have exit statements.

        Args:
            node: AST node to check
            check_break: If True, count Break as exit. Set to False when entering nested loops
                        since their breaks don't exit the outer loop.
        """
        # Direct exit statements
        if isinstance(node, ast.Return):
            return True
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Break) and check_break:
            return True

        # Don't recurse into function/class definitions - their exits don't affect outer loop
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False

        # For nested loops, don't count their breaks as exiting the outer loop
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            # Check the loop body, but breaks inside only exit THIS loop, not outer
            for child in node.body:
                if self._has_exit_in_subtree(child, check_break=False):
                    return True
            for child in node.orelse:
                if self._has_exit_in_subtree(child, check_break=False):
                    return True
            return False

        # Check children
        for subnode in ast.iter_child_nodes(node):
            if self._has_exit_in_subtree(subnode, check_break):
                return True

        return False

    def visit_Call(self, node: ast.Call) -> Any:
        """Check for missing await on async method calls."""
        if not self.async_method_names:
            self.generic_visit(node)
            return

        # Check self.method() calls
        if not isinstance(node.func, ast.Attribute):
            self.generic_visit(node)
            return

        if not isinstance(node.func.value, ast.Name):
            self.generic_visit(node)
            return

        if node.func.value.id != "self":
            self.generic_visit(node)
            return

        method_name = node.func.attr
        if method_name not in self.async_method_names:
            self.generic_visit(node)
            return

        # Check if already awaited or in a gather-friendly context
        if not self._is_awaited_or_gathered(node):
            from nemo_oo_agents.runtime.harness_metrics import get_harness_metrics

            get_harness_metrics().missing_await(method_name)
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Method `{method_name}` is async and must be called with `await`",
                    code="E301",
                    fix_hint=f"await self.{method_name}(...)",
                )
            )

        self.generic_visit(node)

    def _collect_async_methods(self) -> None:
        """Collect names of async methods from agent."""
        from agentdoc.visibility import is_hidden_method

        agent = self.context.agent
        if not agent:
            return

        # Check class-level methods
        for attr_name in dir(agent.__class__):
            if attr_name.startswith("__"):
                continue
            try:
                attr = getattr(agent.__class__, attr_name, None)
                if attr and inspect.iscoroutinefunction(attr):
                    if not is_hidden_method(attr):
                        self.async_method_names.add(attr_name)
            except Exception:
                continue

        # Check instance-level methods
        for attr_name in dir(agent):
            if attr_name.startswith("__"):
                continue
            try:
                attr = getattr(agent, attr_name)
                if callable(attr) and inspect.iscoroutinefunction(attr):
                    if not is_hidden_method(attr):
                        self.async_method_names.add(attr_name)
            except Exception:
                continue

    def _is_awaited_or_gathered(self, node: ast.AST) -> bool:
        """Check if call is awaited or in a gather-friendly context."""
        current = node
        while current in self.parent_map:
            parent = self.parent_map[current]

            # Direct await
            if isinstance(parent, ast.Await) and getattr(parent, "value", None) == current:
                return True

            # In list/generator comprehension (for gather patterns)
            if isinstance(parent, (ast.ListComp, ast.GeneratorExp)):
                return True

            current = parent

        return False


# =============================================================================
# Class Assignment Validator
# =============================================================================
class ClassAssignmentValidator:
    """Detect dangerous class attribute assignments like ClassName.method = ...

    When LLM-generated code assigns directly to a class (instead of an instance),
    it corrupts all subsequent instances that share that class definition.
    This validator blocks patterns like:
    - ParentAgent.method = lambda: ...
    - SubAgentClass.work = factory_result
    - ClassName.attr += value

    Safe patterns that are NOT blocked:
    - self.attr = value (instance assignment)
    - obj.attr = value (non-class object)
    - data["key"] = value (subscript, not attribute)
    """

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        """Validate AST for class assignment patterns."""
        visitor = _ClassAssignmentVisitor(context)
        visitor.visit(tree)
        return visitor.issues


class _ClassAssignmentVisitor(ast.NodeVisitor):
    """AST visitor for detecting class attribute assignments."""

    def __init__(self, context: ValidationContext):
        self.context = context
        self.issues: list[ValidationIssue] = []
        self.known_class_names = self._collect_class_names()
        # Track variables assigned from type(self) - these hold class references
        self.class_ref_vars: set[str] = set()
        # Track variables assigned from self - type(var) is equivalent to type(self)
        self.self_ref_vars: set[str] = set()

    def _collect_class_names(self) -> set[str]:
        """Collect names that refer to classes in the execution context."""
        names: set[str] = set()
        agent = self.context.agent
        if not agent:
            return names

        # The agent's class and ALL parent classes via MRO
        # This prevents LLM from assigning to BaseAgent.method when agent extends BaseAgent
        for cls in type(agent).__mro__:
            if cls is object:
                continue
            names.add(cls.__name__)

        # Class attributes that are themselves classes (sub-agent classes)
        from agentdoc.visibility import is_hidden_field

        for attr_name in dir(type(agent)):
            if attr_name.startswith("__"):
                continue
            try:
                attr = getattr(type(agent), attr_name, None)
                if isinstance(attr, type):
                    if not is_hidden_field(type(agent), attr_name):
                        names.add(attr_name)
            except Exception:
                continue

        return names

    def _is_type_self_call(self, node: ast.expr) -> bool:
        """Check if expression is type(self) or type(self_alias) call.

        Returns True for:
        - type(self)
        - type(var) where var was assigned from self (e.g., agent = self; type(agent))
        """
        if not isinstance(node, ast.Call):
            return False
        if not isinstance(node.func, ast.Name):
            return False
        if node.func.id != "type":
            return False
        if len(node.args) != 1:
            return False
        arg = node.args[0]
        if not isinstance(arg, ast.Name):
            return False
        # Direct type(self) call
        if arg.id == "self":
            return True
        # type(var) where var is an alias for self
        if arg.id in self.self_ref_vars:
            return True
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check for ClassName.attr = value and track self/type(self) assignments."""
        # Track variables assigned from self (e.g., agent = self)
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.self_ref_vars.add(target.id)

        # Track variables assigned from type(self)
        if self._is_type_self_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.class_ref_vars.add(target.id)

        for target in node.targets:
            self._check_class_attribute_target(target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Check for ClassName.attr += value patterns."""
        self._check_class_attribute_target(node.target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check for ClassName.attr: Type = value patterns."""
        if node.value is not None:  # Has assignment, not just annotation
            # Track variables assigned from self (e.g., agent: MyAgent = self)
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                if isinstance(node.target, ast.Name):
                    self.self_ref_vars.add(node.target.id)

            # Track variables assigned from type(self)
            if self._is_type_self_call(node.value):
                if isinstance(node.target, ast.Name):
                    self.class_ref_vars.add(node.target.id)

            self._check_class_attribute_target(node.target, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check for setattr(ClassName, 'attr', value) patterns."""
        if isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if len(node.args) >= 2:
                obj_arg = node.args[0]

                # Check setattr(ClassName, ...)
                if isinstance(obj_arg, ast.Name):
                    if obj_arg.id in self.known_class_names:
                        self.issues.append(
                            ValidationIssue(
                                line=node.lineno,
                                col=node.col_offset,
                                message=f"Cannot use setattr() on class '{obj_arg.id}'. "
                                f"This would corrupt all instances. "
                                f"Use setattr(self, ...) to modify the instance instead.",
                                code="E402",
                                severity="error",
                                fix_hint=f"setattr(self, ...) instead of setattr({obj_arg.id}, ...)",
                            )
                        )
                    elif obj_arg.id in self.class_ref_vars:
                        self.issues.append(
                            ValidationIssue(
                                line=node.lineno,
                                col=node.col_offset,
                                message=f"Cannot use setattr() on '{obj_arg.id}' (assigned from type(self)). "
                                f"This would corrupt all instances. "
                                f"Use setattr(self, ...) to modify the instance instead.",
                                code="E402",
                                severity="error",
                                fix_hint="setattr(self, ...) instead",
                            )
                        )

                # Check setattr(type(self), ...)
                elif self._is_type_self_call(obj_arg):
                    self.issues.append(
                        ValidationIssue(
                            line=node.lineno,
                            col=node.col_offset,
                            message="Cannot use setattr() on type(self). "
                            "This would corrupt all instances. "
                            "Use setattr(self, ...) to modify the instance instead.",
                            code="E402",
                            severity="error",
                            fix_hint="setattr(self, ...) instead of setattr(type(self), ...)",
                        )
                    )

        self.generic_visit(node)

    def _check_class_attribute_target(
        self, target: ast.expr, node: ast.Assign | ast.AugAssign | ast.AnnAssign
    ) -> None:
        """Check if assignment target is a class attribute."""
        if not isinstance(target, ast.Attribute):
            return

        # Check for type(self).attr = value (inline pattern)
        if self._is_type_self_call(target.value):
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message="Cannot assign to type(self) attribute. "
                    "This would corrupt all instances. "
                    "Use 'self.attr = ...' to assign to the instance instead.",
                    code="E401",
                    severity="error",
                    fix_hint=f"self.{target.attr} = ... instead of type(self).{target.attr} = ...",
                )
            )
            return

        # Only check direct Name.attr patterns (not chained like self.obj.attr)
        if not isinstance(target.value, ast.Name):
            return

        name = target.value.id

        # Skip 'self' - that's instance assignment, which is fine
        if name == "self":
            return

        # Check if name is a known class
        if name in self.known_class_names:
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Cannot assign to class attribute '{name}.{target.attr}'. "
                    f"This would corrupt all instances. "
                    f"Use 'self.{target.attr} = ...' to assign to the instance instead.",
                    code="E401",
                    severity="error",
                    fix_hint=f"self.{target.attr} = ... instead of {name}.{target.attr} = ...",
                )
            )
        # Check if name is a variable holding a class reference (from type(self))
        elif name in self.class_ref_vars:
            self.issues.append(
                ValidationIssue(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Cannot assign to '{name}.{target.attr}' ('{name}' holds a class reference from type(self)). "
                    f"This would corrupt all instances. "
                    f"Use 'self.{target.attr} = ...' to assign to the instance instead.",
                    code="E401",
                    severity="error",
                    fix_hint=f"self.{target.attr} = ... instead of {name}.{target.attr} = ...",
                )
            )


# =============================================================================
# Blocking Call Validator
# =============================================================================
class BlockingCallValidator:
    """Validates code for blocking calls that would freeze the event loop.

    Resolves AST names against exec_globals to determine module of origin.
    Replaces AsyncSafetyValidator with runtime-aware name resolution instead
    of string matching.

    Note: This validator does NOT check for missing ``await`` on async calls.
    For example, ``asyncio.sleep(1)`` (without ``await``) passes this validator
    because ``asyncio.sleep`` is not a blocking call — it's an async function
    called incorrectly. The missing ``await`` is caught by REPLPolicyValidator
    (error code E301). Both validators must be active for complete coverage.
    """

    def __init__(
        self,
        restrictions: RestrictionsConfig | None = None,
    ):
        self.restrictions = restrictions or RestrictionsConfig()

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        visitor = _BlockingCallVisitor(
            exec_globals=context.exec_globals,
            restrictions=self.restrictions,
        )
        visitor.visit(tree)
        return visitor.issues


class _BlockingCallVisitor(ast.NodeVisitor):
    """AST visitor that detects blocking calls using runtime-resolved names."""

    def __init__(
        self,
        exec_globals: dict[str, Any],
        restrictions: RestrictionsConfig,
    ):
        self.exec_globals = exec_globals
        self.blocked_modules = restrictions.blocked_modules
        self.blocked_calls = restrictions.blocked_calls
        self.issues: list[ValidationIssue] = []
        # Track local variables assigned from constructors on blocked-call modules.
        # Maps var_name -> (module_name, class_name)
        self.tracked_locals: dict[str, tuple[str, str]] = {}

    def _resolve_module_from_call(self, node: ast.Call) -> str | None:
        """Resolve the module of a chained call like asyncio.get_event_loop().

        For `asyncio.get_event_loop().run_until_complete(...)`, the inner call
        `asyncio.get_event_loop()` has its function's value as the `asyncio` Name.
        We resolve that to the asyncio module.
        """
        if isinstance(node.func, ast.Attribute):
            return self._resolve_module(node.func.value)
        return None

    def _resolve_module(self, node: ast.expr) -> str | None:
        """Resolve an AST expression to its module name via exec_globals.

        For non-module objects, falls back to obj.__module__. Same caveat as
        is_from_blocked_module(): safe with curated block lists but could
        over-match if a broadly-used module like "io" were added.
        """
        if isinstance(node, ast.Name):
            obj = self.exec_globals.get(node.id)
            if isinstance(obj, types.ModuleType):
                return obj.__name__
            return getattr(obj, "__module__", None)
        if isinstance(node, ast.Attribute):
            # For chained attributes like os.path.join, resolve the leftmost name
            return self._resolve_module(node.value)
        return None

    def _add_issue(self, node: ast.AST, module_name: str, call_name: str) -> None:
        self.issues.append(
            ValidationIssue(
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                message=(
                    f"{module_name}.{call_name}() blocks the event loop and is not "
                    f"allowed in agent code. Use 'await' with an async alternative "
                    f"or an appropriate agent tool."
                ),
                code="E310",
                severity="error",
            )
        )

    def visit_Assign(self, node: ast.Assign) -> Any:
        """Track local variables assigned from constructors on blocked-call modules."""
        # Track: t = threading.Thread(...) -> tracked_locals["t"] = ("threading", "Thread")
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            module_name = self._resolve_module(node.value.func.value)
            if module_name:
                matched = match_blocked_module(module_name, self.blocked_calls)
                if matched:
                    class_name = node.value.func.attr
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.tracked_locals[target.id] = (matched, class_name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        """Check each call against blocked modules and blocked calls."""
        if isinstance(node.func, ast.Attribute):
            # e.g., subprocess.run(), time.sleep(), t.join()
            module_name = self._resolve_module(node.func.value)
            call_name = node.func.attr

            if module_name:
                # Check fully blocked modules
                matched = match_blocked_module(module_name, self.blocked_modules)
                if matched:
                    self._add_issue(node, matched, call_name)
                    return self.generic_visit(node)

                # Check partially blocked calls
                matched = match_blocked_module(module_name, self.blocked_calls)
                if matched:
                    if call_name in self.blocked_calls[matched]:
                        self._add_issue(node, matched, call_name)
                        return self.generic_visit(node)

            # Check local variable tracking (for Thread.join, Lock.acquire etc.)
            if isinstance(node.func.value, ast.Name):
                var_name = node.func.value.id
                if var_name in self.tracked_locals:
                    tracked_module, class_name = self.tracked_locals[var_name]
                    if tracked_module in self.blocked_calls:
                        blocked = self.blocked_calls[tracked_module]
                        dotted = f"{class_name}.{call_name}"
                        if dotted in blocked or call_name in blocked:
                            self._add_issue(node, tracked_module, call_name)

            # Check chained calls: e.g. asyncio.get_event_loop().run_until_complete()
            if isinstance(node.func.value, ast.Call):
                chained_module = self._resolve_module_from_call(node.func.value)
                if chained_module:
                    matched = match_blocked_module(chained_module, self.blocked_calls)
                    if matched and call_name in self.blocked_calls[matched]:
                        self._add_issue(node, matched, call_name)

        elif isinstance(node.func, ast.Name):
            # e.g., run(['ls']) where run = subprocess.run
            obj = self.exec_globals.get(node.func.id)
            if obj is not None:
                obj_module = getattr(obj, "__module__", None)
                if obj_module:
                    matched_blocked = match_blocked_module(obj_module, self.blocked_modules)
                    if matched_blocked:
                        self._add_issue(node, matched_blocked, node.func.id)
                    else:
                        matched_calls = match_blocked_module(obj_module, self.blocked_calls)
                        if matched_calls:
                            fn_name = getattr(obj, "__name__", node.func.id)
                            if fn_name in self.blocked_calls[matched_calls]:
                                self._add_issue(node, matched_calls, fn_name)

        self.generic_visit(node)


# =============================================================================
# Unified Code Validator
# =============================================================================
class UnifiedCodeValidator:
    """Orchestrates multiple validators with consistent error handling.

    This is the main entry point for code validation. It runs all validators
    and formats errors in IPython-style with source context.

    By default, includes SecurityValidator and BlockingCallValidator,
    which are the checks performed by execute_code(). The REPLPolicyValidator
    (class definitions, missing await) is used by strategies separately.
    """

    def __init__(
        self,
        validators: list[Validator] | None = None,
        *,
        include_repl_policy: bool = False,
        restrictions: RestrictionsConfig | None = None,
    ):
        """Initialize with validators.

        Args:
            validators: List of validators to use. If provided, overrides defaults.
            include_repl_policy: If True, include REPLPolicyValidator (class defs,
                missing await). Default False since strategies handle this separately.
            restrictions: Code execution restrictions (blocked modules/calls).
                None uses defaults from RestrictionsConfig().
        """
        if validators is not None:
            self.validators: list[Validator] = validators
        else:
            self.validators = [
                SecurityValidator(),
                BlockingCallValidator(
                    restrictions=restrictions,
                ),
                ClassAssignmentValidator(),
            ]
            if include_repl_policy:
                self.validators.append(REPLPolicyValidator())

    def validate(
        self,
        code: str,
        context: ValidationContext,
        *,
        stop_on_first_error: bool = True,
    ) -> None:
        """Validate code against all registered validators.

        Args:
            code: Python source code to validate
            context: Validation context with settings
            stop_on_first_error: If True, stop at first error (default)

        Raises:
            ValidationError: With IPython-style formatting
        """
        # Handle empty/whitespace code
        if not code or not code.strip():
            return

        # Update context with code
        context.code = code

        # Parse AST
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ValidationError(f"Syntax error: {e}", original_exception=e) from e

        # Run validators
        all_issues: list[ValidationIssue] = []
        for validator in self.validators:
            issues = validator.validate(tree, context)
            all_issues.extend(issues)

            if stop_on_first_error and any(i.severity == "error" for i in issues):
                break

        # Format and raise errors
        errors = [i for i in all_issues if i.severity == "error"]
        if errors:
            raise ValidationError(self._format_error(code, errors[0], context))

        # Log warnings
        warnings = [i for i in all_issues if i.severity == "warning"]
        for warning in warnings:
            logger.warning(f"Validation warning [{warning.code}]: {warning.message}")

    def _format_error(self, code: str, issue: ValidationIssue, context: ValidationContext) -> str:
        """Format error in IPython style with source context."""
        lines = code.split("\n")
        source_line = lines[issue.line - 1] if 1 <= issue.line <= len(lines) else ""

        cell_name = f"Cell In[{context.execution_count}]"
        indent = "    "
        caret = " " * issue.col + "^"

        parts = [
            f"{cell_name}, line {issue.line}",
            f"{indent}{source_line}",
            f"{indent}{caret}",
            issue.message,
        ]

        if issue.fix_hint:
            parts.append(f"\nFix: {issue.fix_hint}")

        if issue.doc_link:
            parts.append(f"\nSee: {issue.doc_link}")

        return "\n".join(parts)


# =============================================================================
# Import Pre-processing
# =============================================================================
def strip_redundant_imports(code: str, available_names: set[str]) -> str:
    """Remove import statements where all imported names are already in scope.

    LLMs habitually prepend imports (``from typing import Literal``,
    ``from strategy import ...``) even when those names are pre-loaded.
    Rather than erroring, we silently strip these lines so both the
    validator and the runtime see clean code.

    Only strips an import when *every* name it would introduce is already
    present in ``available_names``.  Imports that bring genuinely new names
    are left untouched (and will be caught by the security validator if
    the module is forbidden).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    indices_to_remove: set[int] = set()

    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Import):
            # ``import X`` / ``import X as Y`` — check alias or module name
            all_present = all(
                (alias.asname or alias.name) in available_names for alias in node.names
            )
            if all_present:
                indices_to_remove.add(i)

        elif isinstance(node, ast.ImportFrom):
            # ``from X import a, b`` — check each imported name
            if any(alias.name == "*" for alias in node.names):
                continue  # never strip star imports
            all_present = all(
                (alias.asname or alias.name) in available_names for alias in node.names
            )
            if all_present:
                indices_to_remove.add(i)

    if not indices_to_remove:
        return code

    # Collect the 1-based line numbers covered by each removed import node.
    # Use lineno/end_lineno so multi-line imports are fully removed.
    lines_to_remove: set[int] = set()
    for i, node in enumerate(tree.body):
        if i in indices_to_remove:
            for line_num in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines_to_remove.add(line_num)

    # Handle the semicolon edge case: a kept node starts on the same physical
    # line as a removed import (e.g. `from typing import Literal; x = 1`).
    # We reconstruct using the original source character ranges so that
    # inline comments and multi-line formatting are preserved exactly.
    source_lines = code.splitlines(keepends=True)

    # Group kept nodes by starting line, but only for mixed lines.
    kept_on_removed: dict[int, list[ast.stmt]] = {}
    for i, node in enumerate(tree.body):
        if i not in indices_to_remove and node.lineno in lines_to_remove:
            kept_on_removed.setdefault(node.lineno, []).append(node)

    # For each mixed line, extract the kept nodes' text from the original source.
    # For the last (rightmost) kept node, we take from its col_offset to end of
    # the physical line — this picks up any trailing comment as well as the
    # opening of a multi-line expression.  For preceding nodes we take the exact
    # character range [col_offset:end_col_offset] to avoid including the import.
    # Multi-line kept nodes: only the first line is reconstructed here;
    # continuation lines are not in lines_to_remove so they emit normally below.
    reconstructed: dict[int, list[str]] = {}
    for mixed_line, nodes in kept_on_removed.items():
        raw_line = source_lines[mixed_line - 1].rstrip("\n\r")
        nodes_sorted = sorted(nodes, key=lambda n: n.col_offset)

        # Effective end column on this line: multi-line nodes extend to EOL.
        def eff_end(n: ast.stmt, line: str) -> int:
            end_lineno = n.end_lineno or n.lineno
            return len(line) if end_lineno > n.lineno else (n.end_col_offset or 0)

        max_end = max(eff_end(n, raw_line) for n in tree.body if n.lineno == mixed_line)

        parts: list[str] = []
        for j, node in enumerate(nodes_sorted):
            is_rightmost_last = j == len(nodes_sorted) - 1 and eff_end(node, raw_line) == max_end
            if is_rightmost_last or (node.end_lineno or node.lineno) > node.lineno:
                # Take to end of physical line: captures trailing comment and
                # the opening of any parenthesised multi-line expression.
                parts.append(raw_line[node.col_offset :])
            else:
                # Exact range only — more nodes follow on this line.
                parts.append(raw_line[node.col_offset : node.end_col_offset])
        reconstructed[mixed_line] = parts

    # Reconstruct source by dropping only removed lines, preserving comments,
    # blank lines, and original line numbering so validation error messages
    # reference the correct lines when shown back to the LLM.
    # Continuation lines of multi-line kept nodes are not in lines_to_remove
    # and emit verbatim via the else branch below.
    result_parts: list[str] = []
    for line_num, line in enumerate(source_lines, start=1):
        if line_num in reconstructed:
            for text in reconstructed[line_num]:
                result_parts.append(text + "\n")
        elif line_num in lines_to_remove:
            pass  # pure import line — drop it
        else:
            result_parts.append(line)
    return "".join(result_parts)


# =============================================================================
# Convenience Functions
# =============================================================================
def validate_code(
    code: str,
    *,
    agent_class: type | None = None,
    available_names: list[str] | None = None,
    importable_modules: set[str] | None = None,
    forbidden_self_calls: list[str] | None = None,
    execution_count: int = 1,
    agent: Any = None,
) -> None:
    """Convenience function to validate code.

    This wraps UnifiedCodeValidator for backwards compatibility and convenience.

    Args:
        code: Python source code to validate
        agent_class: Agent class for decorator checking
        available_names: Names available in scope
        importable_modules: Actual module names that can be imported
        forbidden_self_calls: Method names that can't be called on self
        execution_count: Execution count for Cell In[N] format
        agent: Agent instance for method introspection

    Raises:
        ValidationError: If validation fails
    """
    context = ValidationContext(
        code=code,
        agent_class=agent_class,
        available_names=set(available_names or []),
        importable_modules=importable_modules or set(),
        forbidden_self_calls=set(forbidden_self_calls or []),
        execution_count=execution_count,
        agent=agent,
    )
    validator = UnifiedCodeValidator()
    validator.validate(code, context)
