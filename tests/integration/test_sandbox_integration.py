"""Integration tests for ``nemo_oo_agents sandbox``.

These tests exercise the real ``openshell`` CLI.  They require:
  - ``openshell`` to be installed (``uv pip install nemo_oo_agents[sandbox]``)
  - Docker running (the gateway fixture starts/stops the gateway automatically)

Run explicitly:
    pytest tests/integration/test_sandbox_integration.py -v -m integration

The ``gateway`` fixture starts the openshell gateway before the session and
tears it down afterwards.  Tests that need the gateway depend on it; they are
skipped automatically if the gateway fails to start.
"""

from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path

import pytest
import yaml

# How long to wait for the gateway to become healthy after ``gateway start``.
_GATEWAY_STARTUP_TIMEOUT = 120  # seconds
_GATEWAY_POLL_INTERVAL = 5  # seconds

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def openshell_bin() -> str:
    """Return the path to the openshell binary, or skip the test."""
    import shutil

    path = shutil.which("openshell")
    if path is None:
        pytest.skip("openshell not installed — run: uv pip install nemo_oo_agents[sandbox]")
    return path


@pytest.fixture(scope="session")
def gateway(openshell_bin: str) -> None:
    """Start the openshell gateway for the test session and tear it down afterwards.

    If the gateway is already running this is a no-op (idempotent).
    Skips the test if the gateway cannot be started within the timeout.
    """
    # Check if already reachable — skip startup if so.
    probe = subprocess.run(
        [openshell_bin, "provider", "list", "--names"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    already_running = probe.returncode == 0

    if not already_running:
        # Start the gateway in the background.
        subprocess.Popen(
            [openshell_bin, "gateway", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll until reachable or timeout.
        deadline = time.monotonic() + _GATEWAY_STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            result = subprocess.run(
                [openshell_bin, "provider", "list", "--names"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                break
            time.sleep(_GATEWAY_POLL_INTERVAL)
        else:
            pytest.skip(
                f"openshell gateway did not become reachable within "
                f"{_GATEWAY_STARTUP_TIMEOUT}s — check Docker and try: openshell gateway start"
            )

    yield

    # Only stop the gateway if we started it; leave it running if it was already up.
    if not already_running:
        subprocess.run(
            [openshell_bin, "gateway", "stop"],
            capture_output=True,
            timeout=30,
        )


@pytest.fixture()
def sandbox_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with a pyproject.toml.

    Uses uv_build with ``package = false`` so ``uv sync`` completes without
    trying to build a Python package (there are no source files to build).
    """
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "sandbox-test"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = []

            [tool.uv]
            package = false
        """)
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_ensure_provider_creates_when_absent(gateway: None, openshell_bin: str) -> None:
    """_ensure_provider should create the env-vars provider when it doesn't exist."""
    from nemo_oo_agents_cli.commands.sandbox import _PROVIDER, _ensure_provider

    # Delete the provider first so we start from a clean state.
    subprocess.run([openshell_bin, "provider", "delete", _PROVIDER], capture_output=True)

    _ensure_provider()

    result = subprocess.run(
        [openshell_bin, "provider", "list", "--names"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert _PROVIDER in result.stdout.splitlines()


@pytest.mark.integration
def test_ensure_provider_idempotent(gateway: None) -> None:
    """_ensure_provider is safe to call multiple times — no duplicate/error."""
    from nemo_oo_agents_cli.commands.sandbox import _ensure_provider

    _ensure_provider()
    _ensure_provider()  # second call should be a no-op


@pytest.mark.integration
def test_create_and_delete_env_provider(gateway: None, openshell_bin: str) -> None:
    """_create_env_provider / _delete_provider round-trip against real openshell."""
    from nemo_oo_agents_cli.commands.sandbox import _create_env_provider, _delete_provider

    name = "nemo_oo_agents-integration-test-env"
    try:
        _create_env_provider(name, ("MY_TOKEN=test123", "OTHER_KEY=abc"))

        result = subprocess.run(
            [openshell_bin, "provider", "list", "--names"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert name in result.stdout.splitlines()
    finally:
        _delete_provider(name)

    # Verify it's gone.
    result = subprocess.run(
        [openshell_bin, "provider", "list", "--names"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert name not in result.stdout.splitlines()


# ---------------------------------------------------------------------------
# Policy helpers (no gateway required)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_write_policy_with_domains(tmp_path: Path) -> None:
    """_write_policy produces valid YAML with the extra domains in the right place."""
    from nemo_oo_agents_cli.commands.sandbox import _write_policy

    policy_path = _write_policy(domains=("api.example.com", "data.example.com"))
    try:
        policy = yaml.safe_load(policy_path.read_text())
        endpoints = policy["network_policies"]["user_domains"]["endpoints"]
        hosts = [e["host"] for e in endpoints]
        assert "api.example.com" in hosts
        assert "data.example.com" in hosts
        # Every endpoint must be on port 443.
        assert all(e["port"] == 443 for e in endpoints)
    finally:
        policy_path.unlink(missing_ok=True)


@pytest.mark.integration
def test_write_policy_with_readonly_paths(tmp_path: Path) -> None:
    """_write_policy adds read-only sandbox paths to filesystem_policy."""
    from nemo_oo_agents_cli.commands.sandbox import _write_policy

    policy_path = _write_policy(readonly_paths=["/sandbox/data", "/sandbox/models"])
    try:
        policy = yaml.safe_load(policy_path.read_text())
        readonly = policy["filesystem_policy"]["read_only"]
        assert "/sandbox/data" in readonly
        assert "/sandbox/models" in readonly
    finally:
        policy_path.unlink(missing_ok=True)


@pytest.mark.integration
def test_write_policy_temp_file_cleaned_up(tmp_path: Path) -> None:
    """Calling unlink on the returned path removes the temp file."""
    from nemo_oo_agents_cli.commands.sandbox import _write_policy

    policy_path = _write_policy(domains=("example.com",))
    assert policy_path.exists()
    policy_path.unlink()
    assert not policy_path.exists()


@pytest.mark.integration
def test_write_policy_extends_bundled() -> None:
    """_write_policy preserves existing entries from the bundled policy."""
    from nemo_oo_agents_cli.commands.sandbox import _POLICY, _write_policy

    bundled = yaml.safe_load(_POLICY.read_text())
    policy_path = _write_policy(domains=("extra.example.com",))
    try:
        patched = yaml.safe_load(policy_path.read_text())
        # All keys from the bundled policy should still be present.
        for key in bundled:
            assert key in patched, f"Key {key!r} missing from patched policy"
    finally:
        policy_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# End-to-end sandbox execution
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sandbox_runs_simple_command(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nemo_oo_agents sandbox should execute a trivial command and exit 0."""
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        ["nemo_oo_agents", "sandbox", "--", "python", "-c", "print('hello from sandbox')"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert "hello from sandbox" in result.stdout


@pytest.mark.integration
def test_sandbox_env_var_visible_inside(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--env KEY=VALUE should make the variable visible inside the sandbox."""
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        [
            "nemo_oo_agents",
            "sandbox",
            "--env",
            "MY_SECRET=hunter2",
            "--",
            "python",
            "-c",
            "import os; print(os.environ['MY_SECRET'])",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert "hunter2" in result.stdout


@pytest.mark.integration
def test_sandbox_uv_sync_runs(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uv sync should complete without error (minimal pyproject.toml has no deps)."""
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        ["nemo_oo_agents", "sandbox", "--", "python", "-c", "print('deps ok')"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0


@pytest.mark.integration
def test_sandbox_nonzero_exit_propagated(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command that exits non-zero should cause nemo_oo_agents sandbox to exit non-zero.

    openshell normalises the SSH session exit code to 1, so we only assert
    non-zero (not the specific inner exit code).  The actual inner exit code
    (42) is reported in stderr as informational text.
    """
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        ["nemo_oo_agents", "sandbox", "--", "python", "-c", "raise SystemExit(42)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "42" in result.stderr  # openshell reports the inner code in stderr


@pytest.mark.integration
def test_sandbox_readonly_upload_enforced(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A :ro upload should prevent writes to that directory inside the sandbox."""
    data_dir = sandbox_project / "data"
    data_dir.mkdir()
    (data_dir / "input.txt").write_text("read me")

    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        [
            "nemo_oo_agents",
            "sandbox",
            "--upload",
            "data:ro",
            "--",
            "python",
            "-c",
            "open('/sandbox/data/new_file.txt', 'w').write('oops')",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Write should be denied by Landlock — process should exit non-zero.
    assert result.returncode != 0


@pytest.mark.integration
def test_sandbox_allow_domain_accepted_by_cli(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--allow-domain should be accepted by the CLI and the sandbox should start.

    Full network verification (whether the domain is actually reachable) is
    environment-dependent and covered at the policy level by
    ``test_write_policy_with_domains``.  This test confirms the flag is
    forwarded end-to-end: the patched policy is used and the sandbox exits 0.
    """
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        [
            "nemo_oo_agents",
            "sandbox",
            "--allow-domain",
            "api.example.com",
            "--",
            "python",
            "-c",
            "print('allow-domain flag accepted')",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert "allow-domain flag accepted" in result.stdout


@pytest.mark.integration
def test_sandbox_unknown_domain_blocked(
    gateway: None,
    sandbox_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --allow-domain, an HTTPS request to an unknown domain should be denied.

    The sandbox policy is deny-by-default for outbound network; any domain not
    in the bundled policy or added via --allow-domain must be blocked.
    """
    monkeypatch.chdir(sandbox_project)

    result = subprocess.run(
        [
            "nemo_oo_agents",
            "sandbox",
            "--",
            "python",
            "-c",
            (
                "import urllib.request; "
                "urllib.request.urlopen('https://httpbin.org/get', timeout=10); "
                "print('should not reach here')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "should not reach here" not in result.stdout


@pytest.mark.integration
def test_sandbox_missing_pyproject_fails(
    gateway: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nemo_oo_agents sandbox should fail immediately when pyproject.toml is absent."""
    monkeypatch.chdir(tmp_path)  # no pyproject.toml here

    result = subprocess.run(
        ["nemo_oo_agents", "sandbox", "--", "python", "-c", "print('hi')"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "pyproject.toml" in result.stdout or "pyproject.toml" in result.stderr
