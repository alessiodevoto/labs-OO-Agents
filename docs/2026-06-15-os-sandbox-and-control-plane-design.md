# Design: OS-Level Sandbox & Agent Control Plane for NeMo OO Agents

**Status:** Draft / RFC
**Date:** 2026-06-15
**Author:** rcabral (via nemo_oo_agents agent)
**Motivation:** Critique of `nemo_oo_agents` against Databricks **Omnigent** (the "meta-harness" — blog: *Introducing Omnigent: A Meta-Harness to Combine, Control and Share Your Agents*).

---

## 1. Summary

NeMo OO Agents is a best-in-class *agent authoring harness*: agents are Python
classes, `...`-bodied methods become LLM calls, strategies are pluggable, and
context/introspection are first-class. Databricks Omnigent operates one layer
up — a *meta-harness* / **control plane** that wraps harnesses (Claude Code,
Codex, Pi, SDK agents) behind a uniform runner interface and centralizes the
cross-cutting concerns harnesses don't own: **OS-level security, contextual
policy/governance, multi-interface live sessions, identity, and an agent
registry.**

This doc proposes closing the **operational control-plane gap** in nemo, in
priority order, *without* abandoning nemo's Python-first authoring model (which
is a genuine advantage over Omnigent's YAML-as-agent floor). The throughline:
**nemo should become a first-class harness backend that an Omnigent-style
control plane can wrap — and ship the most security-critical control-plane
pieces itself.**

Three of these gaps bit us in real sessions on this very box: hand-rolled
session duplication, manual SSH-to-leased-compute, and writing raw credentials
to `/tmp`.

---

## 2. Background: the layer mismatch

| Layer | What it owns | Omnigent | nemo_oo_agents |
|---|---|---|---|
| **Model** | tokens→tokens | (delegated) | UnifiedLLM / litellm registry |
| **Harness** | working dir, file edits, tool loop, session history | wraps Claude Code/Codex/Pi/SDK | **this is nemo** (Agent + strategies) |
| **Meta-harness / control plane** | security perimeter, policy, multi-interface sessions, identity, registry | **this is Omnigent** | mostly absent |

nemo is excellent at the harness layer and partially present at the
control-plane layer (SQLite event persistence, OpenInference tracing, eval
pipeline, the opt-in `agent_mesh` comms bus). It is materially behind on
security isolation, governance, shared live sessions, identity, and an agent
registry.

---

## 3. Gap analysis (grounded in current code)

### 3.1 Security is in-process, not OS-level — **highest priority**

Today, agent-generated code is validated by `UnifiedCodeValidator`
(`src/nemo_oo_agents/runtime/code_validator.py`) against a `RestrictionsConfig`
(`src/nemo_oo_agents/runtime/restrictions.py`): an AST pass that rejects
forbidden builtins (`exec`/`eval`/`compile`/`__import__`), restricted/blocked
imports (`DEFAULT_BLOCKED_MODULES` = `subprocess`, `socket`, `urllib.request`,
…), and dunder-escape attribute access. The validated code is then run **in the
same Python process** via:

```python
# src/nemo_oo_agents/strategies/generated_code.py
exec(compile(method_code, "<generated_helper>", "exec"), namespace)
```

**Problem:** this is a *lint*, not a *sandbox*. It runs in-process with full
ambient authority. `ShellTools.run()` already shells out to a real persistent
bash with the user's full filesystem, network, and credentials — entirely
outside the AST validator's reach. A prompt-injected agent that calls
`self.shell.run("curl … | sh")` or reads `~/.ssh/id_rsa` is unconstrained. The
blocklist is also fundamentally bypassable (string tricks, alternate import
paths, the shell escape hatch).

Omnigent's **Omnibox** solves exactly this with kernel-enforced isolation:
`bubblewrap` + `seccomp` (Linux) / Seatbelt `sandbox-exec` (macOS), inherited by
every spawned subprocess, plus a default-deny network proxy and credential
injection (placeholder tokens to the agent; real secrets swapped in at the
proxy only for allow-listed hosts, never in logs/context).

Repo check: **zero** references to `bubblewrap`/`seccomp`/`sandbox-exec`/
`firejail`/`nsjail`.

### 3.2 No contextual policy / governance layer

nemo has no cost budget, no per-tool-call approval gate, no permission policy,
no audit boundary. Confirmation of destructive actions is a *prompt convention*
in the system prompt, not an enforced gate. Omnigent makes "pause and ask before
`git push`" a stateful, session-scoped policy evaluated server-side.

### 3.3 No multi-interface live session

nemo persists well (`SQLiteStorageManager`: events + active-tag order +
snapshots; `self.v` survives turns and sessions — that's how this agent loaded
another session's context read-only earlier). But a session is bound to one
process/TUI. There is no runner↔server split letting terminal + web + mobile
attach to the *same live session*. The `/session duplicate` request raised in
this conversation is precisely the missing primitive.

### 3.4 No runner/server compute split

nemo runs in-process. Relocating a run to leased x86 compute meant SSHing into
Colossus by hand this session. Omnigent's cloud sandbox hosts (Modal/Daytona)
make "move the runner" a one-liner with the same UI/policy following along.

### 3.5 No team/identity layer

Omnigent ships built-in accounts, OIDC SSO, header-auth-behind-proxy, domain
allowlists, role-gated session join. nemo is single-user-on-a-box; `agent_mesh`
is an opt-in TLS comms bus (handles/channels), not governance.

### 3.6 No agent registry / catalog

Omnigent's YAML-as-artifact is version-controlled, shared, portable across
deployments. nemo distributes *code* (pip packages) and *skills* (entry-points)
— reusable, but no governed "publish/discover this agent" story.

---

## 4. Proposal

### Principle
Keep nemo's Python-first authoring (do **not** chase YAML-as-agent — that's
Omnigent's deliberate low-ceiling floor). Add the control-plane pieces that are
either (a) security-critical regardless of any outer platform, or (b) the MVP of
a shared-session server. Make nemo cleanly wrappable as an Omnigent backend.

### P0 — OS-level execution sandbox (the one that matters most)

Introduce a `Sandbox` abstraction that the CodeAct executor and `ShellTools`
both route through, defaulting to a kernel-enforced backend on Linux.

- **`LinuxBwrapSandbox`** — run `execute_python` cells *and* `ShellTools.run`
  inside `bwrap` with `seccomp` syscall filtering. Filesystem: only
  user-granted paths bind-mounted; dotfile secrets (`~/.ssh`, `~/.aws`,
  `~/.config/nemo_oo/secrets.yaml`) masked even under a broad grant.
- **`MacSeatbeltSandbox`** — `sandbox-exec` profile equivalent.
- **`NullSandbox`** — current in-process behavior, explicit opt-out
  (`Agent(sandbox=NullSandbox())`) so "no sandbox" is a deliberate choice, not
  the silent default.
- Keep `UnifiedCodeValidator` as defense-in-depth (fast-fail on obvious
  mistakes), but stop treating it as the security boundary.

**Why first:** it's the only gap that's a *security* problem rather than a
*convenience* one, it's bounded in scope (wrap two existing exec paths), and it
removes the largest reason nemo couldn't be trusted in YOLO/unattended mode.

### P1 — Credential broker

A proxy that holds real secrets and injects them into outgoing requests only for
allow-listed destinations; the agent process sees placeholders. Today we wrote a
Colossus password to `/tmp/z590_pass` to drive `sshpass` — exactly the pattern a
broker eliminates. Pairs naturally with the P0 network default-deny.

### P2 — Policy / approval hook in the strategy loop

A `PolicyEngine` consulted by `CodeActStrategy` *before* each tool call (shell
command, file write, network egress). Decisions: `allow` / `deny` /
`require_approval`. Carries session state (cumulative cost, files touched).
Replaces the prompt-convention confirmation with an enforced gate. Hooks the
existing `self.message()` path for the human-approval round-trip.

### P3 — Shared-session server (promote `agent_mesh` → runner/server)

Generalize the mesh inbox-pump pattern into a session server: a runner exposes
its live event stream; multiple clients (TUI / web viewer / another agent)
attach to the same session. **MVP = `/session duplicate <id>`**: read-only
ingest of another session's events/summary into a fresh agent (the manual recipe
we executed: `sqlite3 'file:<id>.db?mode=ro&immutable=1'`, replay
`TUIUserInput`/`TUIAgentMessage`/`Summary` by `insertion_order`, hydrate from
latest `Summary.summary_text` + tail). Already requested on the mesh this
session.

### P4 — Remote runner hosts

A `RunnerHost` interface with a Colossus backend (and Modal/Daytona later) so
`spawn this run on x86` is a method call, not a manual SSH dance. Reuses the
existing `colossus` skill for lease lifecycle.

### P5 — Agent registry (lowest priority)

A catalog where a published agent (its module + pinned config + skill deps) is
discoverable and governed. Build on the existing entry-point skill discovery and
the `nemo_oo_agents.bundled_configs` group; don't invent YAML-as-agent to get
there.

---

## 5. Non-goals

- **YAML-defined agents.** nemo's Python OOP authoring is a feature; matching
  Omnigent's declarative floor would be a regression in expressiveness.
- **Reimplementing Omnigent.** Where an Omnigent-style platform exists, nemo
  should be a *backend* to it (standardized runner I/O: messages+files in,
  text+tool-calls out), not a competitor for the control plane.
- **Replacing `agent_mesh`** wholesale — P3 generalizes it, doesn't discard it.

---

## 6. Rollout / sequencing

1. **P0 sandbox** behind a feature flag, `NullSandbox` default initially →
   flip default to `LinuxBwrapSandbox` once benchmark parity is shown (the
   Harbor/SWEbench runs are the natural validation harness — they already run in
   Docker, so a bwrap layer should compose).
2. **P1 broker** + P0 network default-deny together (they're co-dependent).
3. **P2 policy hook** — small, high-value, independent.
4. **P3 `/session duplicate`** as the shared-session MVP; expand to live attach.
5. **P4 remote runner**, **P5 registry** as platform maturity follows.

---

## 7. Open questions

- Sandbox vs. the existing Docker-mode Harbor runner: layer bwrap *inside* the
  container, or treat the container as the sandbox for benchmark runs and bwrap
  only for interactive TUI sessions?
- Does the P0 network default-deny break the NVIDIA inference gateway / MCP /
  mesh by default? (Allow-list those egress hosts out of the box.)
- Policy authoring surface: Python callables vs. a small declarative DSL — and
  who owns the policy (per-agent class? per-deployment config?).
- Identity: do we adopt OIDC directly, or assume an upstream control plane
  (Omnigent / SSO proxy) owns identity and nemo just consumes a trusted header?
