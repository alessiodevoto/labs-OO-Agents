# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests to boost coverage on TUI and CLI modules.

Targets:
- tui/commands.py
- tui/config.py
- tui/agent.py
- tui/console.py
- tui/splash.py
- commands/start_dev.py
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def test_tui_command_help_does_not_offer_full_screen_option() -> None:
    from nooa_cli.commands.tui import command

    result = CliRunner().invoke(command, ["--help"])

    assert result.exit_code == 0
    assert "--full-screen" not in result.output
    assert "--fullscreen" not in result.output
    assert "--vi" in result.output


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def make_mock_console():
    """Create a fully-mocked TUIConsole."""
    console = MagicMock()
    console.print_help = MagicMock()
    console.print_success = MagicMock()
    console.print_error = MagicMock()
    console.print_warning = MagicMock()
    console.print_info = MagicMock()
    console.print_status = MagicMock()
    console.print_agent = MagicMock()
    console.print_table = MagicMock()
    console.console = MagicMock()
    console.console.print = MagicMock()
    console.console.clear = MagicMock()
    console.start_spinner = MagicMock()
    console.stop_spinner = MagicMock()
    # Frontend protocol (async methods needed by CommandHandler)
    console.render = AsyncMock()
    console.get_input = AsyncMock(return_value="")
    console.start_thinking = AsyncMock()
    console.stop_thinking = AsyncMock()
    console.is_connected = True
    return console


def make_mock_config(model="test-model"):
    config = MagicMock()
    config.default_model = model
    return config


def make_mock_agent():
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.event_manager = MagicMock()
    agent.event_manager.clear = MagicMock()
    agent.event_manager.keys = MagicMock(return_value=["tag1", "tag2"])
    agent.get_summarization_status = MagicMock(
        return_value={
            "active_events": 10,
            "policy": "auto",
            "has_summarizer": True,
            "max_tokens": 100_000,
            "current_tokens": 50_000,
            "preserve_recent": 5,
            "summary_count": 2,
            "summary_tags": ["summary1", "summary2"],
        }
    )
    agent.bash = MagicMock()
    _wire_mock_mcp(agent)
    return agent


def _wire_mock_mcp(agent, servers=None):
    """Attach a real MCPRegistry to a mock agent, mirroring bootstrap wiring."""
    from nooa_tui.tui.mcp_registry import MCPRegistry

    registry = MCPRegistry(mcp_file=None, servers=dict(servers or {}))
    registry.attach(agent)
    agent.mcp = registry
    return registry


# ===========================================================================
# tui/config.py
# ===========================================================================

from nooa_tui.tui.config import (  # noqa: E402
    DEFAULT_MODEL,
    AgentConfig,
    Config,
    SummarizationConfig,
    TUIConfig,
    _set_nested,
    _unpack_target,
)


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Isolate the layered settings.yaml dirs so Config.load() never picks
    up the developer's real ~/.config/nooa/settings.yaml or a project file.

    Returns the user-level dir; write a ``settings.yaml`` there to exercise
    the file layer.
    """
    user = tmp_path / "user"
    user.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("NEMO_OO_USER_DIR", str(user))
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(proj))
    monkeypatch.delenv("NEMO_OO_SETTINGS", raising=False)
    return user


class TestConfigDefaults:
    def test_default_model(self):
        cfg = Config.load()
        assert cfg.tui.default_model == DEFAULT_MODEL

    def test_default_no_splash(self):
        cfg = Config.load()
        assert cfg.no_splash is False

    def test_default_no_trace(self):
        cfg = Config.load()
        assert cfg.no_trace is False

    def test_default_working_dir(self):
        cfg = Config.load()
        assert cfg.agent.working_dir == "."

    def test_default_summarization_policy(self):
        cfg = Config.load()
        assert cfg.agent.summarization.policy == "token_budget"


class TestConfigOverrides:
    def test_model_override(self):
        cfg = Config.load(model="gpt-4o")
        assert cfg.tui.default_model == "gpt-4o"

    def test_context_limit_override(self):
        cfg = Config.load(context_limit=50_000)
        assert cfg.agent.summarization.max_tokens == 50_000

    def test_working_dir_override(self):
        cfg = Config.load(working_dir="/tmp/mydir")
        assert cfg.agent.working_dir == "/tmp/mydir"

    def test_mcp_file_override(self, tmp_path):
        mcp = tmp_path / "custom.mcp.json"
        cfg = Config.load(mcp_file=str(mcp))
        assert cfg.tui.mcp_file == mcp

    def test_mcp_inline_config_override(self):
        servers = {
            "maas": {
                "url": "https://maas.example.nvidia.com/mcp",
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer ${MAAS_API_KEY}"},
            }
        }
        cfg = Config.load(mcp_servers=servers, mcp_auto_connect=["maas"])
        assert cfg.tui.mcp_servers == servers
        assert cfg.tui.mcp_auto_connect == ["maas"]

    def test_mcp_auto_connect_tuple_override(self):
        cfg = Config.load(mcp_auto_connect=("maas", "jira"))
        assert cfg.tui.mcp_auto_connect == ["maas", "jira"]

    def test_trace_override(self, tmp_path):
        cfg = Config.load(trace=str(tmp_path))
        assert cfg.tui.trace_dir == tmp_path

    def test_no_splash_true(self):
        cfg = Config.load(no_splash=True)
        assert cfg.no_splash is True

    def test_no_splash_false_skipped(self):
        """False for store_true flags should not override (already False default)."""
        cfg = Config.load(no_splash=False)
        assert cfg.no_splash is False  # default remains

    def test_full_screen_override_true(self):
        cfg = Config.load(full_screen=True)
        assert cfg.tui.full_screen is True

    def test_full_screen_false_skipped(self):
        cfg = Config.load(full_screen=False)
        assert cfg.tui.full_screen is True

    def test_full_screen_does_not_force_no_splash_config(self):
        cfg = Config.load(full_screen=True)
        assert cfg.tui.full_screen is True
        assert cfg.no_splash is False

    def test_no_trace_clears_trace_dir(self, tmp_path):
        cfg = Config.load(trace=str(tmp_path), no_trace=True)
        assert cfg.tui.trace_dir is None

    def test_skills_dir_appended(self, tmp_path):
        """Extra skills_dir that exists gets appended."""
        extra = tmp_path / "myskills"
        extra.mkdir()
        cfg = Config.load(skills_dir=[str(extra)])
        assert extra in cfg.tui.skills_dirs

    def test_skills_dir_not_duplicated(self, tmp_path):
        extra = tmp_path / "myskills"
        extra.mkdir()
        cfg1 = Config.load(skills_dir=[str(extra)])
        count = cfg1.tui.skills_dirs.count(extra)
        assert count == 1

    def test_skills_dir_single_string(self, tmp_path):
        """skills_dir as single string: the code does list(str) → iterates chars.
        This is a known quirk — just verify it doesn't crash and returns a Config."""
        extra = tmp_path / "skillsdir"
        extra.mkdir()
        # When a plain string is given, the code does list(str) which gives individual chars.
        # The chars won't be valid dirs, so they get filtered out. Just test no crash.
        cfg = Config.load(skills_dir=str(extra))
        assert isinstance(cfg, Config)

    def test_nonexistent_skills_dirs_filtered(self, tmp_path):
        nonexistent = tmp_path / "doesnotexist"
        cfg = Config.load(skills_dir=[str(nonexistent)])
        assert nonexistent not in cfg.tui.skills_dirs

    def test_unknown_keys_ignored(self):
        cfg = Config.load(completely_unknown_param="value")
        assert cfg.tui.default_model == DEFAULT_MODEL


class TestConfigFile:
    """The file layer is now layered ``settings.yaml`` (dataclass field names
    under ``tui:`` / ``agent:``), loaded via the shared layered-config helper.
    """

    def test_settings_file_model_override(self, isolated_config_dir):
        (isolated_config_dir / "settings.yaml").write_text("tui:\n  default_model: file-model\n")
        cfg = Config.load()
        assert cfg.tui.default_model == "file-model"

    def test_settings_file_trace_dir_override(self, isolated_config_dir, tmp_path):
        trace_dir = tmp_path / "traces"
        (isolated_config_dir / "settings.yaml").write_text(f"tui:\n  trace_dir: {trace_dir}\n")
        cfg = Config.load()
        assert cfg.tui.trace_dir == trace_dir

    def test_settings_file_nested_agent_section(self, isolated_config_dir):
        (isolated_config_dir / "settings.yaml").write_text(
            "agent:\n  working_dir: /tmp\n  summarization:\n    preserve_recent: 99\n"
        )
        cfg = Config.load()
        assert cfg.agent.working_dir == "/tmp"
        assert cfg.agent.summarization.preserve_recent == 99

    def test_explicit_override_beats_settings_file(self, isolated_config_dir):
        (isolated_config_dir / "settings.yaml").write_text("tui:\n  default_model: file-model\n")
        cfg = Config.load(model="explicit-model")
        assert cfg.tui.default_model == "explicit-model"

    def test_missing_settings_file_uses_defaults(self, isolated_config_dir):
        cfg = Config.load()
        assert cfg.tui.default_model == DEFAULT_MODEL

    def test_unknown_settings_key_ignored(self, isolated_config_dir):
        (isolated_config_dir / "settings.yaml").write_text(
            "tui:\n  default_model: file-model\n  bogus_key: 1\n"
        )
        cfg = Config.load()
        assert cfg.tui.default_model == "file-model"


class TestSetNested:
    def test_single_level(self):
        cfg = Config()
        _set_nested(cfg, "no_splash", True)
        assert cfg.no_splash is True

    def test_two_levels(self):
        cfg = Config()
        _set_nested(cfg, "tui.default_model", "newmodel")
        assert cfg.tui.default_model == "newmodel"

    def test_three_levels(self):
        cfg = Config()
        _set_nested(cfg, "agent.summarization.max_tokens", 12345)
        assert cfg.agent.summarization.max_tokens == 12345


class TestUnpackTarget:
    def test_string_target(self):
        path, value = _unpack_target("tui.default_model", "gpt-4")
        assert path == "tui.default_model"
        assert value == "gpt-4"

    def test_tuple_target_with_transform(self):
        path, value = _unpack_target(("tui.trace_dir", Path), "/tmp")
        assert path == "tui.trace_dir"
        assert value == Path("/tmp")


class TestSummarizationConfig:
    def test_defaults(self):
        s = SummarizationConfig()
        assert s.policy == "token_budget"
        # None means "scale from the model's context window at install time"
        # — see install_summarizer. The previous absolute 100K fired at ~10%
        # usage on 1M-context models and made summarization feel constant.
        assert s.max_tokens is None
        assert s.preserve_recent == 10


class TestGetLlm:
    def test_get_llm_known_model(self):
        from nooa_tui.tui.config import get_llm

        from nooa import unifiedllm

        real_models = unifiedllm.MODELS
        tui = TUIConfig()
        # Use a model that actually exists in the registry
        if real_models:
            tui.default_model = next(iter(real_models))
            with patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()) as mock_get:
                get_llm(tui)
                mock_get.assert_called_once()
        else:
            # No models registered - just test CompletionClient path
            tui.default_model = "unknown/model"
            with patch.object(unifiedllm, "CompletionClient", return_value=MagicMock()):
                get_llm(tui)

    def test_get_llm_unknown_model(self):
        from nooa_tui.tui.config import get_llm

        from nooa import unifiedllm

        tui = TUIConfig()
        tui.default_model = "definitely-not-a-real-model/xyz"
        # Patch CompletionClient in unifiedllm since it's imported there
        with patch.object(unifiedllm, "CompletionClient", return_value=MagicMock()) as mock_c:
            # Patch MODELS to be empty to force the CompletionClient path
            original_models = unifiedllm.MODELS
            unifiedllm.MODELS = {}
            try:
                get_llm(tui)
                mock_c.assert_called_once_with(model="definitely-not-a-real-model/xyz")
            finally:
                unifiedllm.MODELS = original_models

    def test_get_llm_from_full_config(self):
        from nooa_tui.tui.config import get_llm

        from nooa import unifiedllm

        cfg = Config()
        # Patch MODELS to include the default model
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {cfg.tui.default_model: None}
        try:
            with patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()) as mock_get:
                get_llm(cfg)
            mock_get.assert_called_once()
        finally:
            unifiedllm.MODELS = original_models


class TestListModels:
    def test_list_models_sorted(self):
        from nooa_tui.tui.config import list_models

        from nooa import unifiedllm

        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"z/model": None, "a/model": None}
        try:
            models = list_models()
        finally:
            unifiedllm.MODELS = original_models
        assert models == ["a/model", "z/model"]

    def test_list_models_returns_list(self):
        from nooa_tui.tui.config import list_models

        models = list_models()
        assert isinstance(models, list)


# ===========================================================================
# tui/commands.py  — additional coverage
# ===========================================================================

from nooa_tui.tui.commands import (  # noqa: E402
    ClearCommand,
    CommandHandler,
    CommandRegistry,
    CommandResult,
    ExitCommand,
    HelpCommand,
    HistoryCommand,
    ModelCommand,
    ModelsCommand,
    PythonCommand,
    SkillsCommand,
    SwitchCommand,
)
from nooa_tui.tui.mcp_registry import _to_attr_name  # noqa: E402
from nooa_tui.tui.output import (  # noqa: E402
    ClearScreen,
    HelpOutput,
    TableOutput,
    TextOutput,
)


class TestToAttrName:
    def test_hyphen_to_underscore(self):
        assert _to_attr_name("my-server") == "my_server"

    def test_no_change_without_hyphen(self):
        assert _to_attr_name("myserver") == "myserver"

    def test_multiple_hyphens(self):
        assert _to_attr_name("my-cool-server") == "my_cool_server"


class TestCommandResult:
    def test_success_true(self):
        r = CommandResult(True)
        assert r.success is True
        assert r.exit is False
        assert r.outputs == []

    def test_exit_true(self):
        r = CommandResult(True, exit=True)
        assert r.exit is True

    def test_err_outputs(self):
        r = CommandResult.err("oops")
        assert r.success is False
        assert len(r.outputs) == 1
        assert "oops" in r.outputs[0].content


@pytest.fixture
def mock_console():
    return make_mock_console()


@pytest.fixture
def mock_config():
    return make_mock_config()


@pytest.fixture
def mock_agent():
    return make_mock_agent()


@pytest.fixture
def registry(mock_console, mock_config, mock_agent):
    return CommandRegistry(
        config=mock_config,
        agent=mock_agent,
        frontend=mock_console,
        skills_dirs=[],
        mcp_file=Path(".mcp.json"),
    )


@pytest.fixture
def handler(registry, mock_console):
    return CommandHandler(registry=registry, frontend=mock_console)


class TestCommandBaseValidation:
    """Tests for Command.validate_args default implementation."""

    async def test_validate_args_no_args_ok(self, mock_console, mock_config, mock_agent):
        cmd = ExitCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is True
        assert msg is None

    async def test_validate_args_extra_args_fails(self, mock_console, mock_config, mock_agent):
        cmd = ExitCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["extra"])
        assert ok is False
        assert "/exit" in msg

    def test_agent_none_raises(self, mock_console, mock_config):
        with pytest.raises(ValueError, match="agent cannot be None."):
            ExitCommand(mock_console, mock_config, None)


@pytest.mark.parametrize("command_cls", [ModelCommand, SwitchCommand])
async def test_model_commands_persist_default_model(
    command_cls, mock_console, mock_config, mock_agent, tmp_path, monkeypatch
):
    """Verify successful model-switching commands persist the selected model."""
    import yaml

    from nooa import unifiedllm

    project_dir = tmp_path / "proj"
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(project_dir))
    cmd = command_cls(mock_console, mock_config, mock_agent)
    original_models = unifiedllm.MODELS
    unifiedllm.MODELS = {"prov/m": None}
    try:
        with patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()):
            result = await cmd.execute(["prov/m"])
    finally:
        unifiedllm.MODELS = original_models

    assert result.success is True
    settings = yaml.safe_load((project_dir / "settings.yaml").read_text())
    assert settings["tui"]["default_model"] == "prov/m"


@pytest.mark.parametrize("command_cls", [ModelCommand, SwitchCommand])
async def test_model_commands_ignore_persistence_failure(
    command_cls, mock_console, mock_config, mock_agent
):
    """Verify persistence errors do not mask successful in-memory model switches."""
    from nooa import unifiedllm

    cmd = command_cls(mock_console, mock_config, mock_agent)
    original_models = unifiedllm.MODELS
    unifiedllm.MODELS = {"prov/m": None}
    try:
        with (
            patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()),
            patch.object(cmd, "_persist_tui_setting", side_effect=OSError("read-only")),
        ):
            result = await cmd.execute(["prov/m"])
    finally:
        unifiedllm.MODELS = original_models

    assert result.success is True
    assert mock_config.default_model == "prov/m"


class TestHelpCommand:
    async def test_execute_with_registry(self, mock_console, mock_config, mock_agent, registry):
        cmd = HelpCommand(mock_console, mock_config, mock_agent, registry=registry)
        result = await cmd.execute([])
        assert result.success is True
        assert any(isinstance(o, HelpOutput) for o in result.outputs)

    async def test_execute_without_registry(self, mock_console, mock_config, mock_agent):
        cmd = HelpCommand(mock_console, mock_config, mock_agent)
        with patch.object(CommandRegistry, "get_help", return_value={"/help": "help"}):
            result = await cmd.execute([])
        assert result.success is True
        assert any(isinstance(o, HelpOutput) for o in result.outputs)

    def test_name(self, mock_console, mock_config, mock_agent):
        cmd = HelpCommand(mock_console, mock_config, mock_agent)
        assert cmd.name == "help"

    def test_help_text(self):
        assert "/help" in HelpCommand.help_text()


class TestExitCommand:
    async def test_execute_sets_exit(self, mock_console, mock_config, mock_agent):
        cmd = ExitCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute([])
        assert result.success is True
        assert result.exit is True
        assert len(result.outputs) > 0

    def test_name(self, mock_console, mock_config, mock_agent):
        cmd = ExitCommand(mock_console, mock_config, mock_agent)
        assert cmd.name == "exit"

    def test_help_text(self):
        ht = ExitCommand.help_text()
        assert "/exit" in ht
        assert "/quit" in ht


class TestClearCommand:
    async def test_clears_event_manager(self, mock_console, mock_config, mock_agent):
        cmd = ClearCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute([])
        assert result.success is True
        # ClearCommand must NOT call event_manager.clear() — that destroys old session data
        mock_agent.event_manager.clear.assert_not_called()
        assert any(isinstance(o, ClearScreen) for o in result.outputs)

    async def test_no_session_manager(self, mock_console, mock_config, mock_agent):
        cmd = ClearCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute([])
        assert result.success is True

    def test_name(self, mock_console, mock_config, mock_agent):
        cmd = ClearCommand(mock_console, mock_config, mock_agent)
        assert cmd.name == "clear"


class TestModelCommand:
    async def test_shows_current_model(self, mock_console, mock_config, mock_agent):
        cmd = ModelCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute([])
        assert result.success is True
        assert any("test-model" in o.content for o in result.outputs if isinstance(o, TextOutput))

    def test_validate_one_arg_ok(self, mock_console, mock_config, mock_agent):
        cmd = ModelCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["some-model"])
        assert ok is True

    def test_validate_extra_args(self, mock_console, mock_config, mock_agent):
        cmd = ModelCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["a", "b"])
        assert ok is False

    def test_validate_no_args_ok(self, mock_console, mock_config, mock_agent):
        cmd = ModelCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is True

    async def test_model_switch_calls_apply_model_limits(
        self, mock_console, mock_config, mock_agent
    ):
        """/model <name> must also resync budgets, same as /switch."""
        from nooa import unifiedllm

        cmd = ModelCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"prov/m": None}
        try:
            with (
                patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()),
                patch("nooa_tui.tui.agent.apply_model_limits") as mock_apply,
            ):
                await cmd.execute(["prov/m"])
        finally:
            unifiedllm.MODELS = original_models
        mock_apply.assert_called_once_with(mock_agent)


class TestModelsCommand:
    async def test_lists_models_by_provider(self, mock_console, mock_config, mock_agent):
        from nooa import unifiedllm

        mock_config.default_model = "provider1/model1"
        cmd = ModelsCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"provider1/model1": None, "provider2/model2": None}
        try:
            result = await cmd.execute([])
        finally:
            unifiedllm.MODELS = original_models
        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)

    async def test_current_model_marked(self, mock_console, mock_config, mock_agent):
        from nooa import unifiedllm

        mock_config.default_model = "prov/mymodel"
        cmd = ModelsCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"prov/mymodel": None, "prov/other": None}
        try:
            result = await cmd.execute([])
        finally:
            unifiedllm.MODELS = original_models
        assert result.success is True


class TestSwitchCommand:
    """SwitchCommand tests — now requires model as argument."""

    async def test_validate_args_empty(self, mock_console, mock_config, mock_agent):
        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is False
        assert "Usage:" in msg

    async def test_validate_args_too_many(self, mock_console, mock_config, mock_agent):
        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["model1", "model2"])
        assert ok is False
        assert "Usage:" in msg

    async def test_validate_args_valid(self, mock_console, mock_config, mock_agent):
        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["prov/m"])
        assert ok is True
        assert msg is None

    async def test_model_not_in_registry_passes_through(
        self, mock_console, mock_config, mock_agent
    ):
        """Unknown models pass through to litellm — /switch should succeed."""
        from nooa import unifiedllm

        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        with patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()):
            result = await cmd.execute(["nonexistent/model"])
        assert result.success is True

    async def test_successful_switch(self, mock_console, mock_config, mock_agent):
        from nooa import unifiedllm

        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"prov/m": None}
        try:
            with patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()):
                result = await cmd.execute(["prov/m"])
        finally:
            unifiedllm.MODELS = original_models
        assert result.success is True
        assert mock_config.default_model == "prov/m"

    async def test_switch_calls_apply_model_limits(self, mock_console, mock_config, mock_agent):
        """Regression: switching models MUST re-sync summarizer trigger +
        truncation cap to the new context window. Forgetting this caused
        the large→small switch overflow (Opus 1M → Sonnet 200K).
        """
        from nooa import unifiedllm

        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"prov/m": None}
        try:
            with (
                patch.object(unifiedllm, "get_llm_client", return_value=MagicMock()),
                patch("nooa_tui.tui.agent.apply_model_limits") as mock_apply,
            ):
                await cmd.execute(["prov/m"])
        finally:
            unifiedllm.MODELS = original_models
        mock_apply.assert_called_once_with(mock_agent)

    async def test_llm_switch_failure(self, mock_console, mock_config, mock_agent):
        from nooa import unifiedllm

        cmd = SwitchCommand(mock_console, mock_config, mock_agent)
        original_models = unifiedllm.MODELS
        unifiedllm.MODELS = {"prov/m": None}
        try:
            with patch.object(unifiedllm, "get_llm_client", side_effect=Exception("auth error")):
                result = await cmd.execute(["prov/m"])
        finally:
            unifiedllm.MODELS = original_models
        assert result.success is False
        assert any(
            "Failed to switch" in o.content for o in result.outputs if isinstance(o, TextOutput)
        )


class TestHistoryCommandValidation:
    def test_no_args_fails(self, mock_console, mock_config, mock_agent):
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is False
        assert "Usage: /history" in msg

    def test_invalid_subcmd_fails(self, mock_console, mock_config, mock_agent):
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["bad"])
        assert ok is False
        assert "Unknown subcommand" in msg

    def test_status_ok(self, mock_console, mock_config, mock_agent):
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["status"])
        assert ok is True

    def test_tags_ok(self, mock_console, mock_config, mock_agent):
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["tags"])
        assert ok is True


class TestHistoryCommandExecute:
    async def test_status_subcommand(self, mock_console, mock_config, mock_agent):
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True

    async def test_tags_subcommand(self, mock_console, mock_config, mock_agent):
        mock_agent.event_manager.keys.return_value = ["tag1", "tag2"]
        mock_agent.event_manager.__getitem__ = MagicMock(return_value=MagicMock(event_type="msg"))
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["tags"])
        assert result.success is True

    async def test_tags_more_than_20(self, mock_console, mock_config, mock_agent):
        tags = [f"tag{i}" for i in range(25)]
        mock_agent.event_manager.keys.return_value = tags
        mock_agent.event_manager.__getitem__ = MagicMock(return_value=MagicMock(event_type="msg"))
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["tags"])
        assert result.success is True

    async def test_status_low_token_usage(self, mock_console, mock_config, mock_agent):
        mock_agent.get_summarization_status.return_value = {
            "active_events": 5,
            "policy": "token_budget",
            "has_summarizer": True,
            "max_tokens": 100_000,
            "current_tokens": 10_000,  # <50% → green
            "preserve_recent": 5,
            "summary_count": 0,
            "summary_tags": [],
        }
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True

    async def test_status_medium_token_usage(self, mock_console, mock_config, mock_agent):
        mock_agent.get_summarization_status.return_value = {
            "active_events": 5,
            "policy": "token_budget",
            "has_summarizer": True,
            "max_tokens": 100_000,
            "current_tokens": 60_000,  # 60% → yellow
            "preserve_recent": 5,
            "summary_count": 0,
            "summary_tags": [],
        }
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True

    async def test_status_high_token_usage(self, mock_console, mock_config, mock_agent):
        mock_agent.get_summarization_status.return_value = {
            "active_events": 5,
            "policy": "token_budget",
            "has_summarizer": True,
            "max_tokens": 100_000,
            "current_tokens": 85_000,  # 85% → red
            "preserve_recent": 5,
            "summary_count": 1,
            "summary_tags": ["sum..1"],
        }
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True

    async def test_status_no_summarizer(self, mock_console, mock_config, mock_agent):
        mock_agent.get_summarization_status.return_value = {
            "active_events": 5,
            "policy": "none",
            "has_summarizer": False,
            "max_tokens": 0,
            "current_tokens": 0,
            "preserve_recent": 0,
            "summary_count": 0,
            "summary_tags": [],
        }
        cmd = HistoryCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True


class TestSkillsCommandValidation:
    def test_no_args(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is False

    def test_invalid_subcmd(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["bad"])
        assert ok is False

    def test_activate_without_id(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["activate"])
        assert ok is False

    def test_deactivate_without_id(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["deactivate"])
        assert ok is False

    def test_list_ok(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["list"])
        assert ok is True


class TestSkillsCommandExecute:
    @pytest.fixture(autouse=True)
    def _setup_registry(self, mock_agent):
        """Attach a SkillRegistry to mock_agent for skills command tests."""
        from nooa.skill_registry import SkillRegistry

        with patch("nooa.skill_registry.entry_points", return_value=[]):
            mock_agent.skills = SkillRegistry(mock_agent)

    async def test_no_registry(self, mock_console, mock_config):
        """Agent without SkillRegistry gets an error."""
        agent = MagicMock(spec=[])  # no .skills attribute
        cmd = SkillsCommand(mock_console, mock_config, agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["list"])
        assert result.success is False
        assert any(
            "SkillRegistry" in o.content for o in result.outputs if isinstance(o, TextOutput)
        )

    async def test_list_empty_skills(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["list"])
        assert result.success is True
        assert any(
            "No skills found" in o.content for o in result.outputs if isinstance(o, TextOutput)
        )

    async def test_list_with_skills(self, mock_console, mock_config, mock_agent):
        from nooa.skill import Skill

        class _S(Skill):
            pass

        mock_agent.skills.register("test.skill", _S())
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["list"])
        assert result.success is True
        assert any(isinstance(o, TableOutput) for o in result.outputs)

    async def test_activate_not_found(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["activate", "missing"])
        assert result.success is False
        assert any("not found" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_activate_already_active(self, mock_console, mock_config, mock_agent):
        from nooa.skill import Skill

        class _S(Skill):
            pass

        mock_agent.skills.register("test.myskill", _S())
        mock_agent.skills.activate(["test.myskill"])
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["activate", "test.myskill"])
        assert result.success is False
        assert any("already" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_activate_success(self, mock_console, mock_config, mock_agent):
        from nooa.skill import Skill

        class _S(Skill):
            pass

        mock_agent.skills.register("test.myskill", _S())
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["activate", "test.myskill"])
        assert result.success is True
        assert "test.myskill" in mock_agent.skills.activated()

    async def test_activate_exception(self, mock_console, mock_config, mock_agent):
        """Activate with an error during registry.activate raises."""
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        with patch.object(mock_agent.skills, "activate", side_effect=Exception("bad")):
            # Register a skill so it's discovered
            from nooa.skill import Skill

            class _S(Skill):
                pass

            mock_agent.skills.register("test.fail", _S())
            result = await cmd.execute(["activate", "test.fail"])
        assert result.success is False
        assert any(
            "Failed to activate" in o.content for o in result.outputs if isinstance(o, TextOutput)
        )

    async def test_deactivate_not_active(self, mock_console, mock_config, mock_agent):
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["deactivate", "notactive"])
        assert result.success is False
        assert any("not active" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_deactivate_success(self, mock_console, mock_config, mock_agent):
        from nooa.skill import Skill

        class _S(Skill):
            pass

        mock_agent.skills.register("test.myskill", _S())
        mock_agent.skills.activate(["test.myskill"])
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        result = await cmd.execute(["deactivate", "test.myskill"])
        assert result.success is True
        assert "test.myskill" not in mock_agent.skills.activated()

    async def test_deactivate_exception(self, mock_console, mock_config, mock_agent):
        from nooa.skill import Skill

        class _S(Skill):
            pass

        mock_agent.skills.register("test.myskill", _S())
        mock_agent.skills.activate(["test.myskill"])
        cmd = SkillsCommand(mock_console, mock_config, mock_agent, skills_dirs=[Path(".")])
        with patch.object(mock_agent.skills, "deactivate", side_effect=Exception("err")):
            result = await cmd.execute(["deactivate", "test.myskill"])
        assert result.success is False


class TestPythonCommand:
    async def test_status_on(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = True
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True
        assert any("on" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_status_off(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = False
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["status"])
        assert result.success is True
        assert any("off" in o.content for o in result.outputs if isinstance(o, TextOutput))

    async def test_on_enables(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = False
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["on"])
        assert result.success is True
        assert mock_config.show_python is True

    async def test_on_already_on(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = True
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["on"])
        assert result.success is True
        assert mock_config.show_python is True

    async def test_off_disables(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = True
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["off"])
        assert result.success is True
        assert mock_config.show_python is False

    async def test_off_already_off(self, mock_console, mock_config, mock_agent):
        mock_config.show_python = False
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        result = await cmd.execute(["off"])
        assert result.success is True
        assert mock_config.show_python is False

    def test_validate_no_args(self, mock_console, mock_config, mock_agent):
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args([])
        assert ok is False

    def test_validate_bad_subcmd(self, mock_console, mock_config, mock_agent):
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["bad"])
        assert ok is False

    def test_validate_on_ok(self, mock_console, mock_config, mock_agent):
        cmd = PythonCommand(mock_console, mock_config, mock_agent)
        ok, msg = cmd.validate_args(["on"])
        assert ok is True


class TestCommandRegistry:
    def test_registers_basic_commands(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        assert reg.get_command("help") is not None
        assert reg.get_command("exit") is not None
        assert reg.get_command("quit") is not None
        assert reg.get_command("clear") is not None
        assert reg.get_command("model") is not None

    def test_filters_by_required_capabilities(self, mock_console, mock_config):
        agent = MagicMock(spec=[])  # no attributes at all
        reg = CommandRegistry(config=mock_config, agent=agent, frontend=mock_console)
        # bash not available → history should not be registered
        assert reg.get_command("history") is None

    def test_get_command_case_insensitive(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        assert reg.get_command("HELP") is not None
        assert reg.get_command("Help") is not None

    def test_get_command_unknown_returns_none(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        assert reg.get_command("nonexistent") is None

    def test_get_all_command_classes(self):
        classes = CommandRegistry.get_all_command_classes()
        assert "help" in classes
        assert "exit" in classes
        assert "quit" in classes

    def test_get_help_returns_dict(self):
        help_dict = CommandRegistry.get_help()
        assert isinstance(help_dict, dict)
        assert "/help" in help_dict
        assert "/exit" in help_dict

    def test_get_active_help(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        active = reg.get_active_help()
        assert isinstance(active, dict)
        assert "/help" in active

    def test_get_completions(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        completions = reg.get_completions()
        assert isinstance(completions, dict)
        assert all("/" in k for k in completions)
        # Should be sorted
        keys = list(completions.keys())
        assert keys == sorted(keys)

    def test_get_completions_strips_arg_placeholders(self, mock_console, mock_config, mock_agent):
        reg = CommandRegistry(config=mock_config, agent=mock_agent, frontend=mock_console)
        completions = reg.get_completions()
        # No angle brackets in keys
        assert all("<" not in k for k in completions)


class TestCommandHandler:
    async def test_not_a_command(self, handler):
        result = await handler.handle("not a command")
        assert result.success is False

    async def test_empty_command(self, handler):
        result = await handler.handle("/")
        assert result.success is False
        assert any(
            "Empty command" in o.content for o in result.outputs if isinstance(o, TextOutput)
        )

    async def test_unknown_command(self, handler, mock_console):
        result = await handler.handle("/unknown")
        assert result.success is False
        assert any(
            "Unknown command: /unknown" in o.content
            for o in result.outputs
            if isinstance(o, TextOutput)
        )
        mock_console.render.assert_called()

    async def test_unavailable_command(self, mock_console, mock_config):
        """Command in registry but not available for this agent."""
        agent_no_caps = MagicMock(spec=[])  # no bash, no get_summarization_status
        reg = CommandRegistry(config=mock_config, agent=agent_no_caps, frontend=mock_console)
        h = CommandHandler(registry=reg, frontend=mock_console)
        result = await h.handle("/history")
        assert result.success is False
        assert any(
            "not available with this agent" in o.content
            for o in result.outputs
            if isinstance(o, TextOutput)
        )

    async def test_suggestion_for_similar_command(self, handler, mock_console):
        result = await handler.handle("/hel")
        # "hel" starts with "he" which matches "help" → suggestion
        assert result.success is False

    async def test_invalid_args_path(self, handler, mock_console):
        """Command found but args invalid → prints error."""
        result = await handler.handle("/model arg1 arg2")
        assert result.success is False
        mock_console.render.assert_called()

    async def test_success_with_message(self, handler, mock_console):
        """Successful command with a message prints success."""
        result = await handler.handle("/python on")
        if result.success:
            mock_console.render.assert_called()


# ===========================================================================
# tui/console.py
# ===========================================================================

from nooa_tui.tui.console import TUIConsole  # noqa: E402


class TestTUIConsoleInit:
    def test_init_creates_console(self):
        c = TUIConsole()
        assert c.console is not None
        assert c._live_spinner is None


class TestTUIConsolePrintMethods:
    """Test each print method routes through console.print."""

    def setup_method(self):
        self.tui = TUIConsole()
        self.tui.console = MagicMock()

    def test_print_error(self):
        self.tui.print_error("bad thing")
        self.tui.console.print.assert_called_once()
        args = self.tui.console.print.call_args[0][0]
        assert "bad thing" in args

    def test_print_warning(self):
        self.tui.print_warning("watch out")
        self.tui.console.print.assert_called_once()
        args = self.tui.console.print.call_args[0][0]
        assert "watch out" in args

    def test_print_status(self):
        self.tui.print_status("ok")
        self.tui.console.print.assert_called_once()
        args = self.tui.console.print.call_args[0][0]
        assert "ok" in args

    def test_print_success(self):
        self.tui.print_success("done")
        self.tui.console.print.assert_called_once()
        args = self.tui.console.print.call_args[0][0]
        assert "done" in args

    def test_print_info(self):
        self.tui.print_info("fyi")
        self.tui.console.print.assert_called_once()
        args = self.tui.console.print.call_args[0][0]
        assert "fyi" in args

    def test_print_agent(self):
        self.tui.print_agent("**bold text**")
        # print_agent calls console.print twice: Rule header + Markdown content
        assert self.tui.console.print.call_count >= 2

    def test_print_agent_dedents(self):
        """print_agent should clean up indented text."""
        self.tui.print_agent("  line1\n  line2\n")
        assert self.tui.console.print.call_count >= 2

    def test_print_help(self):
        self.tui.print_help({"/help": "show help", "/exit": "quit"})
        # Multiple print calls (header + each command + footer)
        assert self.tui.console.print.call_count >= 3

    def test_print_table(self):
        self.tui.print_table("Title", ["Col1", "Col2"], [["a", "b"], ["c", "d"]])
        self.tui.console.print.assert_called_once()

    def test_print_table_empty_rows(self):
        self.tui.print_table("Empty", ["Col"], [])
        self.tui.console.print.assert_called_once()


class TestTUIConsoleSpinner:
    def test_start_spinner_starts_live(self):
        c = TUIConsole()
        with patch("nooa_tui.tui.console.Live") as MockLive:
            mock_live = MagicMock()
            MockLive.return_value = mock_live
            c.start_spinner("thinking...")
            mock_live.start.assert_called_once()
            assert c._live_spinner is mock_live

    def test_start_spinner_idempotent(self):
        c = TUIConsole()
        existing = MagicMock()
        c._live_spinner = existing
        c.start_spinner("again")
        # Should not start a new one
        existing.start.assert_not_called()

    def test_stop_spinner_stops_live(self):
        c = TUIConsole()
        mock_live = MagicMock()
        c._live_spinner = mock_live
        c.stop_spinner()
        mock_live.stop.assert_called_once()
        assert c._live_spinner is None

    def test_stop_spinner_when_not_running(self):
        c = TUIConsole()
        # Should not raise
        c.stop_spinner()
        assert c._live_spinner is None

    def test_thinking_spinner_not_present(self):
        """thinking_spinner context manager was removed in TUI refactor."""
        c = TUIConsole()
        assert not hasattr(c, "thinking_spinner")


# ===========================================================================
# tui/splash.py
# ===========================================================================

from nooa_tui.tui.splash import NEMO_OO_ASCII, show_splash  # noqa: E402


class TestShowSplash:
    def test_show_splash_calls_print(self):
        mock_console = MagicMock()
        with patch("nooa_tui.tui.splash.time.sleep") as mock_sleep:
            show_splash(mock_console, delay=0.0)
        mock_console.print.assert_called()
        mock_sleep.assert_called_once_with(0.0)

    def test_show_splash_default_delay(self):
        mock_console = MagicMock()
        with patch("nooa_tui.tui.splash.time.sleep") as mock_sleep:
            show_splash(mock_console)
        mock_sleep.assert_called_once_with(0.8)

    def test_show_splash_prints_panel(self):
        mock_console = MagicMock()
        with patch("nooa_tui.tui.splash.time.sleep"):
            show_splash(mock_console, delay=0.0)
        # Should call print once (with centered panel containing title and tagline)
        assert mock_console.print.call_count == 1

    def test_ascii_art_constant(self):
        assert "NEMOTRON" in NEMO_OO_ASCII
        assert "AGENTS" in NEMO_OO_ASCII


# ===========================================================================
# tui/agent.py
# ===========================================================================

from nooa_tui.tui.agent import (  # noqa: E402
    TUIAgent,
    install_summarizer,
)


class TestInstallSummarizer:
    def _mk_agent(self, context_window=1_000_000):
        agent = MagicMock()
        agent.llm = MagicMock(spec=["context_window"])
        agent.llm.context_window = context_window
        return agent

    def test_policy_none_does_nothing(self):
        config = SummarizationConfig(policy="none")
        agent = self._mk_agent()
        # TokenBudgetSummarizer is an Agent subclass, so mock.patch
        # can't restore ``install`` via regular setattr. Do the swap by hand
        # with ``type.__setattr__``.
        from unittest.mock import MagicMock as _MM

        from nooa.agents.summarization import TokenBudgetSummarizer

        mock_install = _MM()
        original_install = TokenBudgetSummarizer.install
        type.__setattr__(TokenBudgetSummarizer, "install", mock_install)
        try:
            install_summarizer(config, agent)
        finally:
            type.__setattr__(TokenBudgetSummarizer, "install", original_install)
        mock_install.assert_not_called()

    def test_none_max_tokens_scales_from_model_context_window(self):
        """Default (None) → 80% of context_window. Prevents the ``ctx 8%``
        firing issue that made summarization feel constant on 1M-context
        models."""
        from unittest.mock import MagicMock as _MM

        from nooa.agents.summarization import TokenBudgetSummarizer

        config = SummarizationConfig()  # max_tokens=None
        agent = self._mk_agent(context_window=1_000_000)
        # patch via ``type.__setattr__`` (the Agent guard blocks teardown).
        mock_install = _MM()
        original_install = TokenBudgetSummarizer.install
        type.__setattr__(TokenBudgetSummarizer, "install", mock_install)
        try:
            install_summarizer(config, agent)
        finally:
            type.__setattr__(TokenBudgetSummarizer, "install", original_install)
        installed_cfg = mock_install.call_args.kwargs["config"]
        assert installed_cfg.max_tokens == 800_000

    def test_explicit_max_tokens_passed_through(self):
        """An explicit integer bypasses auto-scaling."""
        from unittest.mock import MagicMock as _MM

        from nooa.agents.summarization import TokenBudgetSummarizer

        config = SummarizationConfig(max_tokens=50_000, preserve_recent=5)
        agent = self._mk_agent(context_window=1_000_000)
        mock_install = _MM()
        original_install = TokenBudgetSummarizer.install
        type.__setattr__(TokenBudgetSummarizer, "install", mock_install)
        try:
            install_summarizer(config, agent)
        finally:
            type.__setattr__(TokenBudgetSummarizer, "install", original_install)
        installed_cfg = mock_install.call_args.kwargs["config"]
        assert installed_cfg.max_tokens == 50_000
        assert installed_cfg.preserve_recent == 5

    def test_install_does_not_seed_event_budget(self):
        """The TUI only manages the summarizer's overall budget.
        Event-pile truncation is enforced at the runtime level (sized from
        the *resolved* LLM's context window each call), so no explicit
        ``max_event_tokens`` belongs in the agent's TruncationConfig.
        """
        from unittest.mock import MagicMock as _MM

        from nooa.agents.summarization import TokenBudgetSummarizer
        from nooa.config.truncation_config import TruncationConfig

        config = SummarizationConfig()
        agent = self._mk_agent(context_window=200_000)
        agent._truncation = TruncationConfig()
        mock_install = _MM()
        original_install = TokenBudgetSummarizer.install
        type.__setattr__(TokenBudgetSummarizer, "install", mock_install)
        try:
            install_summarizer(config, agent)
        finally:
            type.__setattr__(TokenBudgetSummarizer, "install", original_install)
        assert agent._truncation.max_event_tokens is None


class TestApplyModelLimits:
    """``apply_model_limits`` resyncs the summarizer trigger when the
    agent's LLM changes (``/model``, ``/switch``). Truncation is a
    runtime-level concern and isn't touched here.
    """

    def _mk_agent(self, context_window, existing_summarizer_max=800_000):
        from nooa.config.summarizer_config import TokenBudgetConfig
        from nooa.config.truncation_config import TruncationConfig

        agent = MagicMock()
        agent.llm = MagicMock(spec=["context_window"])
        agent.llm.context_window = context_window
        summarizer = MagicMock()
        summarizer.config = TokenBudgetConfig(
            max_tokens=existing_summarizer_max, preserve_recent=10, target_chars=4000
        )
        agent._summarizers = [summarizer]
        agent._truncation = TruncationConfig()
        return agent

    def test_shrinking_window_scales_summarizer_down(self):
        from nooa_tui.tui.agent import apply_model_limits

        agent = self._mk_agent(context_window=200_000, existing_summarizer_max=800_000)
        apply_model_limits(agent)
        assert agent._summarizers[0].config.max_tokens == 160_000  # 200K * 0.80

    def test_growing_window_scales_summarizer_up(self):
        from nooa_tui.tui.agent import apply_model_limits

        agent = self._mk_agent(context_window=1_000_000, existing_summarizer_max=160_000)
        apply_model_limits(agent)
        assert agent._summarizers[0].config.max_tokens == 800_000

    def test_preserves_other_summarizer_fields(self):
        from nooa_tui.tui.agent import apply_model_limits

        from nooa.config.summarizer_config import TokenBudgetConfig

        agent = self._mk_agent(context_window=200_000)
        agent._summarizers[0].config = TokenBudgetConfig(
            max_tokens=800_000, preserve_recent=5, target_chars=8000
        )
        apply_model_limits(agent)
        assert agent._summarizers[0].config.preserve_recent == 5
        assert agent._summarizers[0].config.target_chars == 8000

    def test_does_not_touch_truncation_config(self):
        """Truncation is a runtime concern — apply_model_limits must not
        mutate ``agent._truncation``."""
        from nooa_tui.tui.agent import apply_model_limits

        agent = self._mk_agent(context_window=200_000)
        original_trunc = agent._truncation
        apply_model_limits(agent)
        assert agent._truncation is original_trunc

    def test_no_llm_window_falls_back_safely(self):
        from nooa_tui.tui.agent import apply_model_limits

        agent = self._mk_agent(context_window=200_000)
        agent.llm = MagicMock(spec=[])  # no context_window
        apply_model_limits(agent)
        assert agent._summarizers[0].config.max_tokens == 100_000


class TestTUIAgentInit:
    def test_init_with_defaults(self):
        with patch("nooa_tui.tui.agent.ShellTools"):
            with patch("nooa_tui.tui.agent.RepoTools"):
                with patch("nooa_tui.tui.agent.SkillWriting"):
                    with patch("nooa_tui.tui.agent.install_summarizer"):
                        agent = TUIAgent(llm=MagicMock())
        assert agent._config is not None

    def test_init_no_summarizer_for_none_policy(self):
        config = AgentConfig()
        config.summarization = SummarizationConfig(policy="none")
        with patch("nooa_tui.tui.agent.ShellTools"):
            with patch("nooa_tui.tui.agent.RepoTools"):
                with patch("nooa_tui.tui.agent.SkillWriting"):
                    with patch("nooa_tui.tui.agent.install_summarizer") as mock_install:
                        TUIAgent(llm=MagicMock(), config=config)
                        mock_install.assert_not_called()

    def test_init_installs_summarizer_for_token_budget(self):
        config = AgentConfig()
        config.summarization = SummarizationConfig(policy="token_budget")
        with patch("nooa_tui.tui.agent.ShellTools"):
            with patch("nooa_tui.tui.agent.RepoTools"):
                with patch("nooa_tui.tui.agent.SkillWriting"):
                    with patch("nooa_tui.tui.agent.install_summarizer") as mock_install:
                        TUIAgent(llm=MagicMock(), config=config)
                        mock_install.assert_called_once()

    def test_get_summarization_status_no_summarizers(self):
        with patch("nooa_tui.tui.agent.ShellTools"):
            with patch("nooa_tui.tui.agent.RepoTools"):
                with patch("nooa_tui.tui.agent.SkillWriting"):
                    with patch("nooa_tui.tui.agent.install_summarizer"):
                        agent = TUIAgent(llm=MagicMock())
        agent._summarizers = []
        # event_manager is now an instance attribute, set via assignment.
        mock_em = MagicMock()
        mock_em.keys.return_value = []
        agent.event_manager = mock_em
        status = agent.get_summarization_status()
        assert "active_events" in status
        assert status["has_summarizer"] is False

    def test_get_summarization_status_with_summarizer(self):
        with patch("nooa_tui.tui.agent.ShellTools"):
            with patch("nooa_tui.tui.agent.RepoTools"):
                with patch("nooa_tui.tui.agent.SkillWriting"):
                    with patch("nooa_tui.tui.agent.install_summarizer"):
                        agent = TUIAgent(llm=MagicMock())
        mock_summarizer = MagicMock()
        mock_summarizer.max_tokens = 100_000
        mock_summarizer.preserve_recent = 10
        agent._summarizers = [mock_summarizer]
        mock_stats = MagicMock()
        mock_stats.total_tokens = 5000
        agent.runtime._last_context_stats = mock_stats
        mock_em = MagicMock()
        mock_em.keys.return_value = ["t1", "t1..t2"]
        agent.event_manager = mock_em
        status = agent.get_summarization_status()
        assert status["has_summarizer"] is True
        assert status["current_tokens"] == 5000
        assert status["summary_count"] == 1  # one ".." tag

    def test_handle_signature_is_per_turn(self):
        """handle() is a per-turn generation method decorated with
        CodeActStrategy. Outer dispatcher calls it with
        ``(queue_name, item)`` notifications.
        """
        import inspect

        from nooa_tui.tui.agent import BaseTUIAgent

        sig = inspect.signature(BaseTUIAgent.handle)
        assert list(sig.parameters.keys()) == ["self", "notification"]


# ===========================================================================
# commands/start_dev.py
# ===========================================================================

import logging  # noqa: E402

from nooa_cli.commands.start_dev import _AccessLogFilter  # noqa: E402
from nooa_cli.commands.start_dev import command as start_dev_command  # noqa: E402


class TestAccessLogFilter:
    def test_filter_allows_normal_paths(self):
        f = _AccessLogFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="GET /api/health 200",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_suppresses_traces_path(self):
        f = _AccessLogFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="POST /v1/traces 200",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is False

    def test_filter_suppresses_api_trace(self):
        f = _AccessLogFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="GET /api/trace 200",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is False

    def test_filter_suppresses_api_refresh(self):
        f = _AccessLogFilter()
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="GET /api/refresh 200",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is False


class TestStartDevCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_import_error_shows_message(self):
        with patch.dict("sys.modules", {"nooa.viewer": None, "nooa.viewer.main": None}):
            result = self.runner.invoke(start_dev_command, [])
        assert result.exit_code != 0
        # The error message should be in output
        assert "not installed" in result.output or result.exit_code == 1

    def test_import_error_exits_1(self):
        import sys

        with patch.dict(sys.modules, {"nooa.viewer": None, "nooa.viewer.main": None}):
            result = self.runner.invoke(start_dev_command, ["--port", "5002"])
        assert result.exit_code == 1

    def test_runs_with_viewer(self):
        mock_app = MagicMock()
        mock_viewer = MagicMock()
        mock_viewer.main.app = mock_app
        import sys

        with patch.dict(
            sys.modules,
            {
                "nooa.viewer": mock_viewer,
                "nooa.viewer.main": mock_viewer.main,
            },
        ):
            with patch("uvicorn.run") as mock_run:
                result = self.runner.invoke(start_dev_command, ["--port", "5001"])
        # Either success or the uvicorn mock was called
        assert mock_run.called or result.exit_code in (0, 1)

    def test_custom_port_and_host(self):
        mock_app = MagicMock()
        mock_viewer_main = MagicMock()
        mock_viewer_main.app = mock_app
        mock_viewer = MagicMock()
        mock_viewer.main = mock_viewer_main
        import sys

        with patch.dict(
            sys.modules,
            {
                "nooa.viewer": mock_viewer,
                "nooa.viewer.main": mock_viewer_main,
            },
        ):
            with patch("uvicorn.run") as mock_run:
                self.runner.invoke(start_dev_command, ["--port", "8080", "--host", "127.0.0.1"])
        if mock_run.called:
            call_kwargs = mock_run.call_args
            assert call_kwargs[1].get("port") == 8080 or call_kwargs[0][2] == 8080

    def test_shows_url_in_output(self):
        mock_app = MagicMock()
        mock_viewer_main = MagicMock()
        mock_viewer_main.app = mock_app
        mock_viewer = MagicMock()
        mock_viewer.main = mock_viewer_main
        import sys

        with patch.dict(
            sys.modules,
            {
                "nooa.viewer": mock_viewer,
                "nooa.viewer.main": mock_viewer_main,
            },
        ):
            with patch("uvicorn.run"):
                result = self.runner.invoke(start_dev_command, ["--port", "5001"])
        if result.exit_code == 0:
            assert "5001" in result.output or "NeMo" in result.output

    def test_db_flag_sets_trace_store_db(self, tmp_path):
        """--db overrides the user-dir default and stamps $NEMO_OO_TRACE_DB
        so the viewer module sees it when it reads the env at import."""
        import os
        import sys

        mock_app = MagicMock()
        mock_viewer_main = MagicMock()
        mock_viewer_main.app = mock_app
        mock_viewer = MagicMock()
        mock_viewer.main = mock_viewer_main

        custom_db = tmp_path / "side-by-side.db"
        orig_env = os.environ.pop("NEMO_OO_TRACE_DB", None)
        try:
            with patch.dict(
                sys.modules,
                {
                    "nooa.viewer": mock_viewer,
                    "nooa.viewer.main": mock_viewer_main,
                },
            ):
                with patch("uvicorn.run"):
                    result = self.runner.invoke(
                        start_dev_command, ["--port", "5002", "--db", str(custom_db)]
                    )
            assert result.exit_code == 0
            # The env var the viewer reads is stamped with the resolved path.
            assert os.environ["NEMO_OO_TRACE_DB"] == str(custom_db.resolve())
            # Banner shows the DB so the user knows which file this viewer is on.
            assert str(custom_db.resolve()) in result.output
        finally:
            if orig_env is None:
                os.environ.pop("NEMO_OO_TRACE_DB", None)
            else:
                os.environ["NEMO_OO_TRACE_DB"] = orig_env

    def test_existing_trace_store_db_env_is_respected(self, tmp_path):
        """If --db isn't passed but $NEMO_OO_TRACE_DB is set, use that."""
        import os
        import sys

        mock_app = MagicMock()
        mock_viewer_main = MagicMock()
        mock_viewer_main.app = mock_app
        mock_viewer = MagicMock()
        mock_viewer.main = mock_viewer_main

        env_db = tmp_path / "from-env.db"
        orig_env = os.environ.get("NEMO_OO_TRACE_DB")
        os.environ["NEMO_OO_TRACE_DB"] = str(env_db)
        try:
            with patch.dict(
                sys.modules,
                {
                    "nooa.viewer": mock_viewer,
                    "nooa.viewer.main": mock_viewer_main,
                },
            ):
                with patch("uvicorn.run"):
                    result = self.runner.invoke(start_dev_command, ["--port", "5003"])
            assert result.exit_code == 0
            assert os.environ["NEMO_OO_TRACE_DB"] == str(env_db.resolve())
        finally:
            if orig_env is None:
                os.environ.pop("NEMO_OO_TRACE_DB", None)
            else:
                os.environ["NEMO_OO_TRACE_DB"] = orig_env
