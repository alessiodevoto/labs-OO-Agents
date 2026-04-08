"""Tests for the ``nemo_oo_agents sandbox`` CLI command.

Covers:
- openshell availability check
- always requires pyproject.toml
- always runs uv sync
- default: uploads full cwd (--upload .)
- --no-upload: uploads only pyproject.toml
- --allow-domain: patches policy with extra domains
- --env: creates a temporary provider, attaches it, deletes it after
- name is auto-generated with nemo_oo_agents- prefix
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from nemo_oo_agents_cli.commands.sandbox import _PREFIX, command


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def mock_openshell(monkeypatch):
    """Make shutil.which('openshell') always succeed."""
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/openshell" if name == "openshell" else None
    )


def _run(runner, args, *, pyproject=True):
    """Invoke command inside an isolated filesystem, optionally with pyproject.toml."""
    with runner.isolated_filesystem():
        if pyproject:
            Path("pyproject.toml").write_text("[project]\nname = 'test'\n")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("nemo_oo_agents_cli.commands.sandbox._ensure_provider"):
                with patch("sys.exit"):
                    result = runner.invoke(command, args, catch_exceptions=False)
    return result, mock_run


# ------------------------------------------------------------------ #
# openshell availability                                              #
# ------------------------------------------------------------------ #


def test_openshell_missing_raises(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = CliRunner().invoke(command, ["tui"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "openshell not found" in result.output


# ------------------------------------------------------------------ #
# pyproject.toml required                                             #
# ------------------------------------------------------------------ #


def test_requires_pyproject(runner):
    _, mock_run = _run(runner, ["bash"], pyproject=False)
    # subprocess.run should never be called — error raised before sandbox creation
    assert mock_run.call_count == 0


def test_requires_pyproject_error_message(runner):
    with runner.isolated_filesystem():
        result = runner.invoke(command, ["bash"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "pyproject.toml" in result.output


# ------------------------------------------------------------------ #
# uv sync always runs                                                 #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("args", [["tui"], ["python", "agent.py"], ["bash"]])
def test_uv_sync_always_present(runner, args):
    _, mock_run = _run(runner, args)
    bash_cmd = mock_run.call_args[0][0]
    bash_cmd = bash_cmd[bash_cmd.index("-c") + 1]
    assert "uv sync" in bash_cmd


# ------------------------------------------------------------------ #
# upload behaviour                                                    #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("args", [["tui"], ["python", "agent.py"], ["bash"]])
def test_uploads_cwd_by_default(runner, args):
    _, mock_run = _run(runner, args)
    cmd = mock_run.call_args[0][0]
    assert "--upload" in cmd
    assert cmd[cmd.index("--upload") + 1] == "."


def test_explicit_upload_paths(runner):
    _, mock_run = _run(runner, ["--upload", "src", "--upload", "data", "python", "agent.py"])
    cmd = mock_run.call_args[0][0]
    uploads = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--upload"]
    assert "src" in uploads
    assert "data" in uploads
    # pyproject.toml auto-injected since neither src nor data is .
    assert "pyproject.toml" in uploads


def test_explicit_upload_skips_pyproject_if_dot(runner):
    _, mock_run = _run(runner, ["--upload", ".", "bash"])
    cmd = mock_run.call_args[0][0]
    uploads = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--upload"]
    assert uploads == ["."]  # . already covers everything, no duplicate pyproject.toml


# ------------------------------------------------------------------ #
# --upload permissions                                                #
# ------------------------------------------------------------------ #


def test_upload_ro_adds_path_to_policy_readonly(runner):
    import yaml

    captured_policy: dict = {}

    def capture_run(cmd, **kwargs):
        if "sandbox" in cmd and "create" in cmd and "--policy" in cmd:
            policy_path = Path(cmd[cmd.index("--policy") + 1])
            captured_policy.update(yaml.safe_load(policy_path.read_text()))
        return MagicMock(returncode=0)

    with runner.isolated_filesystem():
        Path("pyproject.toml").write_text("[project]\nname = 'test'\n")
        with patch("subprocess.run", side_effect=capture_run):
            with patch("nemo_oo_agents_cli.commands.sandbox._ensure_provider"):
                with patch("sys.exit"):
                    runner.invoke(
                        command,
                        ["--upload", "data:ro", "python", "agent.py"],
                        catch_exceptions=False,
                    )

    assert "/sandbox/data" in captured_policy["filesystem_policy"]["read_only"]


def test_upload_rw_does_not_patch_policy(runner):
    _, mock_run = _run(runner, ["--upload", "src:rw", "bash"])
    cmd = mock_run.call_args[0][0]
    # no temp policy — bundled policy path used directly
    policy_path = Path(cmd[cmd.index("--policy") + 1])
    assert (
        policy_path
        == Path(__file__).parent.parent.parent / "src/nemo_oo_agents_cli/commands/sandbox-policy.yaml"
        or "nemo_oo_agents-policy-" not in policy_path.name
    )


# ------------------------------------------------------------------ #
# --allow-domain                                                      #
# ------------------------------------------------------------------ #


def test_allow_domain_patches_policy(runner):
    import yaml

    captured_policy: dict = {}

    def capture_run(cmd, **kwargs):
        if "sandbox" in cmd and "create" in cmd and "--policy" in cmd:
            policy_path = Path(cmd[cmd.index("--policy") + 1])
            captured_policy.update(yaml.safe_load(policy_path.read_text()))
        return MagicMock(returncode=0)

    with runner.isolated_filesystem():
        Path("pyproject.toml").write_text("[project]\nname = 'test'\n")
        with patch("subprocess.run", side_effect=capture_run):
            with patch("nemo_oo_agents_cli.commands.sandbox._ensure_provider"):
                with patch("sys.exit"):
                    runner.invoke(
                        command,
                        ["--allow-domain", "api.myservice.com", "python", "agent.py"],
                        catch_exceptions=False,
                    )

    hosts = [e["host"] for e in captured_policy["network_policies"]["user_domains"]["endpoints"]]
    assert "api.myservice.com" in hosts


# ------------------------------------------------------------------ #
# --env                                                               #
# ------------------------------------------------------------------ #


class TestEnv:
    def _invoke_with_calls(self, runner, args):
        calls = []

        def capture(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0)

        with runner.isolated_filesystem():
            Path("pyproject.toml").write_text("[project]\nname = 'test'\n")
            with patch("subprocess.run", side_effect=capture):
                with patch("nemo_oo_agents_cli.commands.sandbox._ensure_provider"):
                    with patch("sys.exit"):
                        runner.invoke(command, args, catch_exceptions=False)
        return calls

    def test_creates_provider_with_credential(self, runner):
        calls = self._invoke_with_calls(runner, ["--env", "HF_TOKEN=abc123", "bash"])
        create_call = next(c for c in calls if "provider" in c and "create" in c)
        assert "--credential" in create_call
        assert "HF_TOKEN=abc123" in create_call

    def test_provider_attached_to_sandbox(self, runner):
        calls = self._invoke_with_calls(runner, ["--env", "HF_TOKEN=abc123", "bash"])
        sandbox_call = next(c for c in calls if "sandbox" in c and "create" in c)
        providers = [sandbox_call[i + 1] for i, v in enumerate(sandbox_call) if v == "--provider"]
        # default env-vars provider + the temporary one
        assert len(providers) == 2

    def test_provider_deleted_after_sandbox(self, runner):
        calls = self._invoke_with_calls(runner, ["--env", "HF_TOKEN=abc123", "bash"])
        delete_call = next(c for c in calls if "provider" in c and "delete" in c)
        # the deleted provider name matches the one that was created
        create_call = next(c for c in calls if "provider" in c and "create" in c)
        created_name = create_call[create_call.index("--name") + 1]
        assert created_name in delete_call

    def test_multiple_env_vars(self, runner):
        calls = self._invoke_with_calls(
            runner, ["--env", "HF_TOKEN=abc", "--env", "WANDB_KEY=xyz", "bash"]
        )
        create_call = next(c for c in calls if "provider" in c and "create" in c)
        credentials = [create_call[i + 1] for i, v in enumerate(create_call) if v == "--credential"]
        assert "HF_TOKEN=abc" in credentials
        assert "WANDB_KEY=xyz" in credentials

    def test_no_provider_created_without_env(self, runner):
        calls = self._invoke_with_calls(runner, ["bash"])
        provider_creates = [c for c in calls if "provider" in c and "create" in c]
        assert len(provider_creates) == 0


# ------------------------------------------------------------------ #
# name prefix                                                         #
# ------------------------------------------------------------------ #


def test_name_has_prefix(runner):
    _, mock_run = _run(runner, ["bash"])
    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--name") + 1].startswith(_PREFIX)
