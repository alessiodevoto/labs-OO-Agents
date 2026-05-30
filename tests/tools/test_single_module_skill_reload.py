"""Single-module hot-reload for builtin framework tool skills (issue 225).

Builtin tool skills live under ``nemo_oo_agents`` / ``nemo_oo_agents_cli``, which
are in ``_NO_RELOAD`` because the package-purge reload would strand the live
framework. These tests pin the narrow leaf-module reload path that reloads ONLY
the skill's own module via ``importlib.reload`` and re-resolves the class by name.
"""

import sys
import types

from nemo_oo_agents.skill_registry import SkillRegistry


class _FakeAgent:
    pass


def _install_framework_module(mod_name: str, cls_name: str, version: str):
    """Create a module that lives under the ``nemo_oo_agents`` top package."""
    mod = types.ModuleType(mod_name)
    mod.__file__ = f"/fake/{mod_name.replace('.', '/')}.py"
    ns: dict = {}
    exec(
        f"""
class {cls_name}:
    version = {version!r}
    def __init__(self, cwd="."):
        self.cwd = cwd
    def attach(self, agent):
        self.agent = agent
    def detach(self):
        pass
""",
        ns,
    )
    cls = ns[cls_name]
    cls.__module__ = mod_name
    setattr(mod, cls_name, cls)
    sys.modules[mod_name] = mod
    return mod, cls


def test_framework_tool_skill_reloads_in_place(monkeypatch):
    """A skill in a _NO_RELOAD top package reloads via single-module path.

    Reproduces issue 225: previously this returned
    "Skill ... is not reloadable". The fix reloads only the leaf module.
    """
    mod_name = "nemo_oo_agents.tools._fake_shell_for_test"
    mod_v1, cls_v1 = _install_framework_module(mod_name, "FakeShell", "v1")

    agent = _FakeAgent()
    registry = SkillRegistry(agent)

    skill_v1 = cls_v1()
    skill_v1.attach(agent)
    agent.fake_shell = skill_v1
    registry._attr_map["nemo.fake_shell"] = "fake_shell"
    registry._loaded.add("nemo.fake_shell")

    # importlib.reload(mod) re-execs the module file. Simulate the on-disk edit by
    # having the reload swap in the v2 class definition.
    import importlib

    def fake_reload(mod):
        # Mirror real importlib.reload: re-exec into the SAME module object.
        assert mod is sys.modules[mod_name]
        ns: dict = {}
        exec(
            """
class FakeShell:
    version = "v2"
    def __init__(self, cwd="."):
        self.cwd = cwd
    def attach(self, agent):
        self.agent = agent
    def detach(self):
        pass
""",
            ns,
        )
        new_cls = ns["FakeShell"]
        new_cls.__module__ = mod_name
        mod.FakeShell = new_cls
        return mod

    monkeypatch.setattr(importlib, "reload", fake_reload)

    result = registry.reload("nemo.fake_shell")

    sys.modules.pop(mod_name, None)

    assert "not reloadable" not in result, result
    assert "Reloaded" in result, result
    # The agent now holds a fresh instance built from the reloaded module.
    assert agent.fake_shell is not skill_v1
    assert agent.fake_shell.version == "v2"
    # Re-attached to the same agent.
    assert agent.fake_shell.agent is agent


def test_framework_skill_needing_ctor_args_returns_clear_message(monkeypatch):
    """If the reloaded class can't be built zero-arg, return a clear message, not a crash."""
    mod_name = "nemo_oo_agents.tools._fake_needs_args_for_test"
    mod = types.ModuleType(mod_name)
    mod.__file__ = f"/fake/{mod_name.replace('.', '/')}.py"
    ns: dict = {}
    exec(
        """
class NeedsArgs:
    def __init__(self, required):
        self.required = required
    def attach(self, agent):
        self.agent = agent
""",
        ns,
    )
    cls = ns["NeedsArgs"]
    cls.__module__ = mod_name
    mod.NeedsArgs = cls
    sys.modules[mod_name] = mod

    agent = _FakeAgent()
    registry = SkillRegistry(agent)
    skill = cls(required="x")
    agent.needs_args = skill
    registry._attr_map["nemo.needs_args"] = "needs_args"
    registry._loaded.add("nemo.needs_args")

    import importlib

    monkeypatch.setattr(importlib, "reload", lambda m: m)

    result = registry.reload("nemo.needs_args")
    sys.modules.pop(mod_name, None)

    # No crash; the agent still has its original instance; message is informative.
    assert agent.needs_args is skill
    assert (
        "not reloadable" in result.lower()
        or "constructor" in result.lower()
        or "args" in result.lower()
    ), result


def test_user_skill_reload_path_unchanged(monkeypatch):
    """No regression: a skill in a non-framework top package still uses the package-purge path."""
    import sys as _sys

    from nemo_oo_agents.skill import Skill

    ns = {"Skill": Skill}
    exec(
        """
class LibSkill(Skill):
    version = "v1"
""",
        ns,
    )
    SkillV1 = ns["LibSkill"]
    SkillV1.__module__ = "mylib"

    mod_v1 = types.ModuleType("mylib")
    mod_v1.LibSkill = SkillV1
    _sys.modules["mylib"] = mod_v1

    agent = _FakeAgent()
    registry = SkillRegistry(agent)
    skill_v1 = SkillV1()
    agent.mylib = skill_v1
    registry._attr_map["local.mylib"] = "mylib"
    registry._loaded.add("local.mylib")

    ns2 = {"Skill": Skill}
    exec(
        """
class LibSkill(Skill):
    version = "v2"
""",
        ns2,
    )
    SkillV2 = ns2["LibSkill"]
    SkillV2.__module__ = "mylib"
    mod_v2 = types.ModuleType("mylib")
    mod_v2.LibSkill = SkillV2

    import importlib

    def fake_import(name):
        _sys.modules[name] = mod_v2
        return mod_v2

    monkeypatch.setattr(importlib, "import_module", fake_import)

    result = registry.reload("local.mylib")
    _sys.modules.pop("mylib", None)

    assert "Reloaded" in result, result
    assert agent.mylib.version == "v2"


def test_underscore_top_package_uses_single_module_path(monkeypatch):
    """A skill whose top package starts with '_' takes the single-module reload path.

    Pins the deliberate routing of `top_pkg.startswith("_")` to the in-place
    leaf reload (never the package purge) — so e.g. dynamically-loaded skill
    modules (`_nemo_oo_skill_*`) get a clear, accurate result instead of a
    framework-wide purge.
    """
    mod_name = "_nemo_oo_skill_fake_for_test"
    mod = types.ModuleType(mod_name)
    mod.__file__ = f"/fake/{mod_name}.py"
    ns: dict = {}
    exec(
        """
class FakeUnderscore:
    version = "v1"
    def __init__(self):
        pass
    def attach(self, agent):
        self.agent = agent
""",
        ns,
    )
    cls = ns["FakeUnderscore"]
    cls.__module__ = mod_name
    mod.FakeUnderscore = cls
    sys.modules[mod_name] = mod

    agent = _FakeAgent()
    registry = SkillRegistry(agent)
    skill = cls()
    skill.attach(agent)
    agent.fake_u = skill
    registry._attr_map["ext.fake_u"] = "fake_u"
    registry._loaded.add("ext.fake_u")

    import importlib

    def fake_reload(m):
        assert m is sys.modules[mod_name]
        ns2: dict = {}
        exec(
            """
class FakeUnderscore:
    version = "v2"
    def __init__(self):
        pass
    def attach(self, agent):
        self.agent = agent
""",
            ns2,
        )
        nc = ns2["FakeUnderscore"]
        nc.__module__ = mod_name
        m.FakeUnderscore = nc
        return m

    monkeypatch.setattr(importlib, "reload", fake_reload)
    result = registry.reload("ext.fake_u")
    sys.modules.pop(mod_name, None)

    assert "Reloaded" in result, result
    assert agent.fake_u.version == "v2"


def test_attach_failure_leaves_agent_untouched(monkeypatch):
    """If the reloaded skill's attach() raises, the agent keeps its old skill.

    Mutation (setattr + context-block re-registration) must happen only after a
    successful attach, so a failing reload can't leave a half-reloaded skill.
    """
    mod_name = "nemo_oo_agents.tools._fake_attach_raises_for_test"
    mod = types.ModuleType(mod_name)
    mod.__file__ = f"/fake/{mod_name.replace('.', '/')}.py"
    ns: dict = {}
    exec(
        """
class Boom:
    version = "v1"
    def __init__(self):
        pass
    def attach(self, agent):
        self.agent = agent
""",
        ns,
    )
    cls = ns["Boom"]
    cls.__module__ = mod_name
    mod.Boom = cls
    sys.modules[mod_name] = mod

    agent = _FakeAgent()
    registry = SkillRegistry(agent)
    original = cls()
    original.attach(agent)
    agent.boom = original
    registry._attr_map["nemo.boom"] = "boom"
    registry._loaded.add("nemo.boom")

    import importlib

    def fake_reload(m):
        assert m is sys.modules[mod_name]
        ns2: dict = {}
        exec(
            """
class Boom:
    version = "v2"
    def __init__(self):
        pass
    def attach(self, agent):
        raise RuntimeError("attach boom")
""",
            ns2,
        )
        nc = ns2["Boom"]
        nc.__module__ = mod_name
        m.Boom = nc
        return m

    monkeypatch.setattr(importlib, "reload", fake_reload)
    result = registry.reload("nemo.boom")
    sys.modules.pop(mod_name, None)

    assert "Reload failed" in result, result
    # Agent must still hold the original, attached skill — not the broken v2.
    assert agent.boom is original
    assert agent.boom.version == "v1"
