#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# nemo oo TUI installer — designed for `curl ... | sh` one-liner UX.
#
# What it does:
#   1. Installs `uv` if not already present.
#   2. Installs a managed Python interpreter (>=3.12).
#   3. Installs `nemo-oo-agents-cli` as a `uv tool`, alongside
#      `nemo-oo-agents` (core) and `nemo-oo-agents-nvidia` (NVIDIA-gateway
#      model aliases registered via the
#      `nemo_oo_agents.bundled_configs` entry-point group).
#   4. Optionally prompts for `NVIDIA_INTERNAL_API_KEY` (via /dev/tty) and
#      writes it to `~/.config/nat/oo/env` as a sh-style export line.
#
# Skip the API-key prompt by setting `NEMO_OO_INSTALL_NONINTERACTIVE=1`
# or by piping into a non-TTY environment (e.g. CI).
#
# Force-reinstall everything with `NEMO_OO_INSTALL_REINSTALL=1`.

set -eu

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_URL="${NEMO_OO_INSTALL_REPO:-https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git}"
REPO_REF="${NEMO_OO_INSTALL_REF:-main}"
ENV_FILE="${NEMO_OO_INSTALL_ENV_FILE:-$HOME/.config/nat/oo/env}"

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

bold() { printf '\033[1m%s\033[0m' "$1"; }
green() { printf '\033[32m%s\033[0m' "$1"; }
yellow() { printf '\033[33m%s\033[0m' "$1"; }
red() { printf '\033[31m%s\033[0m' "$1"; }

info() { printf '%s %s\n' "$(green '==>')" "$1"; }
warn() { printf '%s %s\n' "$(yellow '!!!')" "$1" >&2; }
die() { printf '%s %s\n' "$(red 'ERROR:')" "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: uv
# ---------------------------------------------------------------------------

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        info "uv already installed ($(uv --version 2>/dev/null | head -1))"
        return 0
    fi
    info "Installing uv (Astral) — running the official installer..."
    # The official uv installer puts the binary in ~/.local/bin and adds
    # PATH lines to common shell rc files.
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Make uv visible for the rest of this script even before the user
    # opens a new shell.
    if [ -x "$HOME/.local/bin/uv" ]; then
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
    if ! command -v uv >/dev/null 2>&1; then
        die "uv install reported success but \`uv\` is still not on PATH. \
Open a new shell and re-run this installer, or add \`\$HOME/.local/bin\` to PATH."
    fi
}

# ---------------------------------------------------------------------------
# Step 2: managed Python
# ---------------------------------------------------------------------------

ensure_python() {
    info "Ensuring uv-managed Python 3.12 is available..."
    uv python install 3.12
}

# ---------------------------------------------------------------------------
# Step 3: nemo-oo-agents-cli (+ core + nvidia)
# ---------------------------------------------------------------------------

install_tool() {
    if [ "${NEMO_OO_INSTALL_REINSTALL:-0}" = "1" ]; then
        info "Force-reinstalling nemo-oo-agents-cli..."
        UV_INSTALL_FLAGS="--reinstall"
    else
        UV_INSTALL_FLAGS=""
    fi

    info "Installing nemo-oo-agents-cli (with core + NVIDIA aliases) as a uv tool..."
    uv tool install $UV_INSTALL_FLAGS \
        "nemo-oo-agents-cli @ git+${REPO_URL}@${REPO_REF}#subdirectory=packages/nemo-oo-agents-cli" \
        --with "nemo-oo-agents @ git+${REPO_URL}@${REPO_REF}" \
        --with "nemo-oo-agents-nvidia @ git+${REPO_URL}@${REPO_REF}#subdirectory=packages/nemo-oo-agents-nvidia"
}

# ---------------------------------------------------------------------------
# Step 4: API key prompt
# ---------------------------------------------------------------------------

prompt_api_key() {
    if [ "${NEMO_OO_INSTALL_NONINTERACTIVE:-0}" = "1" ]; then
        info "Skipping API-key prompt (NEMO_OO_INSTALL_NONINTERACTIVE=1)."
        return 0
    fi

    # When piped through `| sh`, stdin is the script — read from /dev/tty
    # instead so the prompt actually reaches the user's terminal.
    if [ ! -r /dev/tty ]; then
        info "No /dev/tty available (CI / non-interactive shell). Skipping API-key prompt."
        return 0
    fi

    printf '\n'
    info "$(bold 'NVIDIA Inference HUB API key')"
    printf '    Most bundled aliases (claude-*, nemotron-*, gpt-5.x, …) route\n'
    printf '    through inference.nvidia.com and need NVIDIA_INTERNAL_API_KEY.\n'
    printf '    Get a key at https://inference.nvidia.com — or press Enter to skip.\n'
    printf '\n'
    printf '    NVIDIA_INTERNAL_API_KEY: '
    # `< /dev/tty` is the cross-shell way to read from the user even when
    # stdin is the install script.
    api_key=""
    read -r api_key < /dev/tty || true

    if [ -z "$api_key" ]; then
        info "No API key entered — you can set NVIDIA_INTERNAL_API_KEY later."
        return 0
    fi

    mkdir -p "$(dirname "$ENV_FILE")"
    # Write atomically so a concurrent installer can't read a half-written file.
    tmp_file="${ENV_FILE}.tmp.$$"
    {
        printf '# Written by nemo oo install.sh on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '# Source this file from your shell rc, or pass it through your\n'
        printf '# preferred env-loading mechanism.\n'
        printf 'export NVIDIA_INTERNAL_API_KEY=%s\n' "$api_key"
    } > "$tmp_file"
    chmod 600 "$tmp_file"
    mv "$tmp_file" "$ENV_FILE"
    info "Wrote NVIDIA_INTERNAL_API_KEY to $ENV_FILE (mode 600)."
}

# ---------------------------------------------------------------------------
# Step 5: final instructions
# ---------------------------------------------------------------------------

print_next_steps() {
    printf '\n'
    info "$(green 'Install complete.')"
    printf '\n'

    if [ -f "$ENV_FILE" ]; then
        printf '%s\n' "$(bold 'Next steps:')"
        printf '  1. Add the env file to your shell rc so the key is loaded automatically:\n'
        printf '\n'
        printf '       echo "source %s" >> ~/.bashrc   # or ~/.zshrc, ~/.profile\n' "$ENV_FILE"
        printf '\n'
        printf '  2. Open a new shell (or `source` the rc file once) so the key\n'
        printf '     and `~/.local/bin` PATH change take effect.\n'
        printf '\n'
        printf '  3. Run the TUI:\n'
        printf '\n'
        printf '       %s\n' "$(bold 'nemo oo tui')"
        printf '\n'
    else
        printf '%s\n' "$(bold 'Next steps:')"
        printf '  1. Open a new shell so the PATH change picks up.\n'
        printf '  2. Set your API key (or skip and rely on UNIFIEDLLM_CONFIG paths):\n'
        printf '\n'
        printf '       export NVIDIA_INTERNAL_API_KEY=sk-…\n'
        printf '\n'
        printf '  3. Run the TUI:\n'
        printf '\n'
        printf '       %s\n' "$(bold 'nemo oo tui')"
        printf '\n'
    fi

    printf '%s\n' "$(bold 'Inspect / customize the bundled LLM aliases:')"
    printf '       nemo oo config show     # which YAMLs are loading\n'
    printf '       nemo oo config eject    # local copy at ~/.config/nat/oo/llm_config.yaml\n'
    printf '\n'
    printf '%s\n' "$(bold 'Upgrade later:')"
    printf '       uv tool upgrade nemo-oo-agents-cli\n'
    printf '\n'
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

main() {
    info "nemo oo TUI installer — ref=${REPO_REF}"
    ensure_uv
    ensure_python
    install_tool
    prompt_api_key
    print_next_steps
}

main "$@"
