#!/bin/bash
# Colossus machine setup for NeMo OO Agents benchmarks
# Usage: curl -sSL https://raw.githubusercontent.com/.../setup.sh | bash
# Or: git clone ... && cd nemo_oo_agents && ./util/harbor/setup_colossus.sh

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
    echo "Cloning nemo_oo_agents..."
    git clone https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git
    cd nemo_oo_agents
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
