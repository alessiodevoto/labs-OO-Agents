#!/bin/bash
# Colossus machine setup for NeMo OO Agents benchmarks
# Usage: curl -sSL https://raw.githubusercontent.com/.../setup.sh | bash
# Or: git clone ... && cd nooa && ./util/harbor/setup_colossus.sh

set -e

echo "=== NeMo OO Agents Benchmark Setup for Colossus ==="
echo ""

# 0. Check if we're on a Colossus machine
if ! hostname | grep -qE 'colossus|ipp|z590'; then
    echo "Warning: This doesn't look like a Colossus machine. Continuing anyway..."
fi

# 1. Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source ~/.local/bin/env
else
    echo "uv already installed"
fi

# 2. Clone repo if not in it already
if [ ! -f "pyproject.toml" ]; then
    echo "Cloning nooa..."
    git clone https://gitlab-master.nvidia.com/interactive-agents/nooa.git
    cd nooa
fi

# 3. Install project
echo "Installing project with uv..."
uv sync

# 4. Create directories
mkdir -p ~/harbor_jobs ~/3p/sif_cache ~/apptainer_tmp

# 5. Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker $USER
    echo "Docker installed. Please logout/login and re-run this script to continue."
    exit 0
else
    echo "Docker already installed"
fi

# 5b. Ensure dockerd is running and enabled (fresh machines may have it inactive)
if ! sudo systemctl is-active --quiet docker; then
    echo "Starting docker daemon..."
    sudo systemctl enable --now docker
fi
# If this shell isn't yet in the docker group, harbor must run under `sg docker`.
if ! id -nG "$USER" | grep -qw docker; then
    echo "NOTE: '$USER' is not yet in the docker group in THIS shell."
    echo "      Run harbor under:  sg docker -c '<harbor command>'   (or re-login)."
fi

# 6. Install Apptainer if not present  
if ! command -v apptainer &> /dev/null; then
    echo "Installing Apptainer..."
    sudo add-apt-repository -y ppa:apptainer/ppa
    sudo apt-get update
    sudo apt-get install -y apptainer
else
    echo "Apptainer already installed"
fi

# 7. Symlink ~/.apptainer to avoid duplicate cache
if [ ! -L ~/.apptainer ]; then
    echo "Setting up Apptainer cache symlink..."
    rm -rf ~/.apptainer 2>/dev/null || true
    mkdir -p ~/3p/sif_cache/.apptainer_cache
    ln -sfn ~/3p/sif_cache/.apptainer_cache ~/.apptainer
fi

# 8. Clone Harbor adapters
if [ ! -d "3p/harbor-nemo" ]; then
    echo "Cloning harbor-nemo..."
    git clone https://gitlab-master.nvidia.com/interactive-agents/harbor.git 3p/harbor-nemo
fi

if [ ! -d "3p/harbor" ]; then
    echo "Cloning upstream harbor..."
    git clone https://github.com/codeacme17/harbor.git 3p/harbor
fi

# 9. Set up environment
if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# Add your NVIDIA API key here
# NVIDIA_INTERNAL_API_KEY=sk-...

# Apptainer temp dir (prevent /tmp from filling)
export TMPDIR=/localhome/$USER/apptainer_tmp
mkdir -p "$TMPDIR"
EOF
    echo "Created .env file - please add your NVIDIA_INTERNAL_API_KEY"
fi

# 10. Source environment
source .env 2>/dev/null || true


# 11. Verify the benchmark campaign fixes are in place (see README "Benchmark campaign
#     fixes (replication-critical)"). A from-scratch run that scores 0 everywhere or shows
#     a high infra-exception count almost always means one of these regressed.
echo ""
echo "=== Verifying campaign fixes ==="

# (a) ultra model-name prefix must be nvidia/, not openai/
if grep -rq "openai/nvidia/nvidia/nemotron" packages/ util/harbor/ 2>/dev/null; then
    echo "  [FAIL] found 'openai/nvidia/...' ultra prefix — should be 'nvidia/nvidia/...'"
else
    echo "  [ok] ultra model-name prefix"
fi

# (b) harbor x86_64 cp312-overlay setup fix (TB1 Python-version mismatch)
HARBOR_SETUP=$(find ~/3p/harbor -path "*agents/installed/nooa.py" 2>/dev/null | head -1)
if [ -n "$HARBOR_SETUP" ] && grep -q "elif \[ -x /opt/harbor/cpython312/bin/python3.12 \]" "$HARBOR_SETUP"; then
    echo "  [ok] harbor x86_64 cp312-overlay setup fix present"
else
    echo "  [FAIL] harbor x86_64 cp312-overlay fix missing (TB1 will show ~20-50 agent-setup infra/run)"
    echo "         Pull harbor branch feat/skip-editable-installs-with-pth (commit a61ddaa4+)."
fi

# (c) overlay ships the cp312 interpreter + venv tarball
if [ -x ~/3p/harbor_bootstrap_overlay/opt/harbor/cpython312/bin/python3.12 ] \
   && [ -f ~/3p/harbor_bootstrap_overlay/opt/harbor/nemo-venv-base-cp312-x86_64.tar.gz ]; then
    echo "  [ok] overlay cpython312 + cp312 venv tarball"
else
    echo "  [WARN] overlay cpython312/venv-tarball not found — run build_bootstrap_overlay.sh + build_venv_tarballs.sh"
fi

# (d) uv bundled in overlay (SWEBench verifier needs it)
if [ -x ~/3p/harbor_bootstrap_overlay/opt/harbor/cpython312/bin/uv ] \
   || [ -f ~/3p/harbor_bootstrap_overlay/opt/harbor/uv ]; then
    echo "  [ok] uv in overlay"
else
    echo "  [WARN] uv not found in overlay — SWEBench verifier 'uv run parser.py' will fail (reward 0)"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit .env and add your NVIDIA_INTERNAL_API_KEY"
echo "2. For SWEBench, rsync SIF cache from DFW:"
echo "   rsync -av --progress rcabral@cw-dfw-cs-001-login-02.cw-dfw-cs-001.hpc.nvidia.com:/lustre/fs1/portfolios/coreai/projects/coreai_dlalgo_dle/users/agronskiy/apptainer_cache/ ~/3p/sif_cache/"
echo "3. Run a benchmark:"
echo "   harbor run --config util/harbor/terminal_bench_local_docker.yaml"
echo ""
