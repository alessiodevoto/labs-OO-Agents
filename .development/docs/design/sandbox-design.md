# Sandbox Design

**Status:** Decided — Openshell
**Authors:** Agent006 Team
**Date:** 2026-02-19
**Updated:** 2026-03-11

---

## Problem

We need a way to run untrusted or experimental code — agent scripts, LLM-generated
code, evaluation harnesses — safely on developer machines and CI without risking
network exfiltration, runaway processes, or filesystem damage.

The requirements, in priority order:

**Must have**

1. **Bidirectional stdin/stdout streaming.** The core use case is driving
   interactive processes — feeding input and reading output in real time.
2. **URL-based network allowlist.** Deny-by-default: the sandbox has no internet
   access unless the caller explicitly declares which domains to permit. Enforced
   at the network layer, not the application layer.
3. **Isolated file access.** The sandbox sees only a declared workspace directory.
   Host files outside that directory are not accessible.
4. **Port forwarding.** The sandboxed process can listen on a port and the caller
   can reach it on the host.

**Stretch**

5. **Secret hiding.** API keys passed via environment should not be readable by the
   sandboxed process through `/proc` or environment inspection.

---

## Solutions Evaluated

### Sandboxkit

A custom Docker-backed Python library with a clean API and a clear extension point
for alternative backends. The design is minimal: `Sandbox.create(config)` returns
a running sandbox; three execution modes — buffered, streaming, interactive.

Network security is enforced at the infrastructure layer via Squid: the agent
container is attached to an `internal=True` Docker bridge, with all outbound
traffic going through a proxy configured with deny-by-default ACLs.

**Why not chosen.** Requires building and maintaining the library ourselves.
Docker adds operational overhead (image management, container lifecycle). The
approach is sound but not necessary given Openshell's existence in-house.

### Matchlock

A VM-based sandbox (stronger isolation than Docker) developed internally at NVIDIA.
Full virtual machine per sandbox — not a container.

**Why not chosen.** At evaluation time, Matchlock was still in active development
with an unstable feature set. Critically, there was no out-of-the-box way to
interact with stdin of a running process, making it unsuitable for interactive
use cases (our primary requirement).

### Openshell (formerly Nemoclaw)

NVIDIA's internal runtime environment for autonomous agents. Openshell provides the
infrastructure to run agent workloads with policy-driven enforcement: filesystem
isolation via Linux Landlock, network policy enforcement via an in-process OPA/Rego
engine and HTTP CONNECT proxy, and process-level restrictions via privilege dropping.

**Why chosen.** Covers all core requirements without us building or maintaining
anything. See [What is Openshell](#what-is-openshell) below.

---

## Decision

After evaluating sandboxkit (a custom Docker-backed sandbox library) and Openshell
(NVIDIA's internal sandbox runtime), we are going with **Openshell**.

Openshell already covers our core requirements — filesystem isolation, network
policy enforcement, and process-level restrictions — without us needing to build
or maintain anything. We provide:

1. **A policy file** (`sandbox-policy.yaml`) — declares what the sandbox is
   allowed to do (filesystem, process, network).
2. **The `agent006 sandbox` CLI command** — an opinionated zero-config wrapper
   around `openshell sandbox` that handles dependency installation, file uploads,
   and credential injection automatically.

---

## What is Openshell

Openshell is NVIDIA's runtime environment for autonomous agents — the infrastructure
layer that lets agents run, verify, and iterate safely. It transforms a machine
(local or remote) from a static execution target into a programmable sandbox factory:
agents can spin up isolated environments, run arbitrary code, and interact with
external services only within declared policy boundaries.

### Architecture

Each sandbox is a Kubernetes pod (inside a single Docker container running k3s)
managed by a gateway (`navigator-server`). The key components inside a sandbox pod:

- **Sandbox supervisor** (privileged): embeds an SSH server (russh), an HTTP CONNECT
  proxy, and an OPA/Rego policy engine (regorus) that evaluates every outbound
  network connection.
- **Agent process** (restricted user): runs under Linux Landlock (filesystem
  isolation) and seccomp (syscall filtering), with all outbound traffic forced
  through the proxy via a network namespace veth pair.

All CLI-to-gateway communication uses mutual TLS (mTLS) with a cluster CA.
Policy and provider credentials are fetched by the sandbox supervisor over gRPC
at startup.

### Isolation mechanisms

| Layer | Technology | What it restricts |
|-------|-----------|-------------------|
| Filesystem | Linux Landlock + `best_effort` mode | Which paths the process can read or write |
| Network | HTTP CONNECT proxy + OPA/Rego | Per-binary allowlist of outbound hosts/ports |
| Syscalls | seccomp BPF | Kernel syscall surface |
| Process | `run_as_user`/`run_as_group` | Privilege dropping to `sandbox:sandbox` |
| Credentials | Provider system (gRPC) | Secret injection without embedding in env/process list |

### Default sandbox image tools

| Category | Tools |
|----------|-------|
| Agent | `claude`, `opencode`, `codex` |
| Language | `python` (3.12), `node` (22) |
| Developer | `gh`, `git`, `vim`, `nano` |
| Networking | `ping`, `dig`, `nslookup`, `nc`, `traceroute`, `netstat` |

### Installation

```bash
uv pip install openshell \
  --upgrade \
  --pre \
  --index-url https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple
```

---

## agent006 Interface to Openshell

The `agent006 sandbox` CLI is an **opinionated, zero-config** wrapper around
`openshell sandbox`. The design goal is that common use cases work with minimal
flags; advanced users drop down to `openshell` directly.

### Design principles

- **Always requires `pyproject.toml`.** Dependencies are installed via `uv sync`
  before the command runs, picking up the exact agent006 version pinned in the
  project.
- **Upload by default.** The current directory is uploaded into the sandbox
  automatically. Use `--upload` to specify explicit paths.
- **Ephemeral.** Sandboxes are not kept alive after the command exits.
- **No subcommands.** Unlike the previous nemoclaw-based design, there is no
  `list`, `connect`, `delete`, etc. — these are `openshell` concerns. The
  `agent006 sandbox` command does exactly one thing: run a command in a sandbox.

### Command interface

```
agent006 sandbox [OPTIONS] CMD...

  Run a command in an isolated sandbox.

Options:
  --upload PATH[:ro|:rw]   Path to upload (repeatable). Defaults to '.'.
                           Append :ro for read-only, :rw for read-write.
                           pyproject.toml always included.
  --env KEY=VALUE          Inject an environment variable (repeatable).
                           Creates a short-lived credential provider.
  --allow-domain HOST      Allow outbound HTTPS to HOST (repeatable).
                           Extends the bundled network policy.
```

### How it works

For a call like `agent006 sandbox --upload src:ro --env HF_TOKEN=x -- python agent.py`:

1. Check `openshell` is installed and `pyproject.toml` exists.
2. Parse `--upload` entries — strip `:ro`/`:rw` suffixes, collect read-only paths.
3. If `--allow-domain` or `:ro` paths are present, write a patched policy YAML
   (temp file extending the bundled `sandbox-policy.yaml`).
4. If `--env` vars are present, create a named openshell credential provider
   (e.g. `agent006-<timestamp>-env`) with those credentials.
5. Call `openshell sandbox create` with:
   - Auto-generated `agent006-<timestamp>` name
   - Patched or bundled policy
   - `env-vars` provider (always) + env provider (if any)
   - `--upload` for each path
   - Bash command: `cd /sandbox && uv sync && export PATH=... && python agent.py`
6. After exit: delete temp policy and env provider.

### Usage examples

```bash
# Run a script (uploads cwd, installs deps, runs command)
agent006 sandbox -- python agent.py

# Launch the agent006 TUI
agent006 sandbox -- agent006 tui

# Open a shell for debugging
agent006 sandbox -- bash

# Upload specific paths; make data read-only
agent006 sandbox --upload src --upload data:ro -- python agent.py

# Inject extra credentials
agent006 sandbox --env HF_TOKEN=abc123 -- python agent.py

# Allow an extra outbound domain
agent006 sandbox --allow-domain api.myservice.com -- python agent.py

# Combine options
agent006 sandbox \
  --upload src:ro \
  --env HF_TOKEN=abc123 \
  --allow-domain api.myservice.com \
  -- python agent.py
```

---

## Policy File (`sandbox-policy.yaml`)

The policy file declares everything the sandbox is allowed to do. It is passed
to `openshell sandbox create --policy`. The bundled `sandbox-policy.yaml` extends
the Openshell default with the network access agent006 workflows require.

### Dynamic policy patching

When `--allow-domain` or `:ro` upload paths are used, `agent006 sandbox` writes
a temporary policy YAML that extends the bundled policy:

- **`--allow-domain HOST`**: adds a `user_domains` network policy entry allowing
  all sandbox Python/venv/uv binaries to reach those hosts on port 443.
- **`--upload PATH:ro`**: adds the corresponding sandbox path (`/sandbox/<name>`)
  to `filesystem_policy.read_only`.

The temp file is deleted after the sandbox exits.

### Changes from the Openshell default

The comparison is against the Openshell default dev sandbox policy.

**Filesystem and process:** identical to the default. No changes.

**Network policies — unchanged from default:**

| Policy | Purpose |
|--------|---------|
| `claude_code` | Claude Code → Anthropic API, Statsig, Sentry, GitHub raw |
| `github_ssh_over_https` | Git clone/fetch over HTTPS (read-only) |
| `github_rest_api` | `gh` CLI + Claude → GitHub REST API (read-only) |
| `vscode` | VS Code Remote-SSH server download |

**Network policies — agent006 additions or expansions:**

| Policy | Change | Reason |
|--------|--------|--------|
| `nvidia_inference` | Expanded | Added `inference.nvidia.com`, `inference-api.nvidia.com` (IP-pinned), and Python/venv/uv binary paths. Agent workflows call inference APIs directly from Python. |
| `pypi` | Expanded | Added `urm.nvidia.com` with IP pins. NVIDIA internal Artifactory mirror for packages not yet on public PyPI. |
| `nvidia_mcp` | New | Agent workflows call NVIDIA-internal MCP servers (Confluence, etc.). Only the agent006 venv Python binary is allowed. |

See `src/agent006_cli/commands/sandbox-policy.yaml` for the full policy with
inline comments explaining each addition.

---

## Sandboxkit Design (Archived)

The following is the original sandboxkit design document preserved for historical
reference. It describes the Docker-backed library that was evaluated but not adopted.

### Goals

1. **Working today.** Docker is the target backend.
2. **Simple API.** `Sandbox.create(config)` returns a running sandbox.
3. **Deny-by-default networking.** All outbound traffic blocked unless allowlisted.
4. **Pluggable backends.** `SandboxBackend` ABC — new backends (Matchlock, etc.)
   can be added without changing the public API.
5. **Minimal surface area.** No runner coordination, no agent-specific logic.

### Non-goals

- Multi-tenant isolation (single-user tool, not a hosted service).
- Cross-platform support (Linux + macOS with Docker, Windows out of scope).
- Image management (callers supply the base image).

### API sketch

```python
@dataclass
class SandboxConfig:
    workspace: Path | None = None
    allowed_domains: list[str] = field(default_factory=list)  # deny-by-default
    env: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    memory_limit: str | None = None
    cpu_limit: float | None = None
    timeout: int | None = None
    image: str = "python:3.12"

async with Sandbox.create(config) as sb:
    result = await sb.run("pip install httpx")          # buffered
    await sb.run("python train.py", stream=True)        # streaming
    async with sb.interactive("python3 -i") as proc:   # bidirectional
        await proc.write(b"x = 1 + 1\nprint(x)\n")
        line = await proc.readline(timeout=5.0)         # "2"
```

### Network isolation model

```
agent container (internal Docker bridge — no direct internet)
    │
    ▼
squid proxy ──► internet (HTTPS only, allowlisted domains)
(port 3128)
```
