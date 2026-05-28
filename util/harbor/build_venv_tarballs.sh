#!/usr/bin/env bash
# Build the nemo-oo-agents venv tarball for the harbor bootstrap overlay.
#
# ## Why only cp312?
#
# The agent uses a "Three-Python Architecture" (see Paul's swe-datagen-reproduction-guide.md):
#   1. Task Python  — whatever the container ships (3.8–3.13, varies by task image)
#   2. Agent Python — always Python 3.12 from the overlay (/opt/harbor/cpython312/)
#   3. Sidecar      — same overlay Python
#
# The agent requires Python >=3.12. The overlay ships Python 3.12 at a fixed path
# (/opt/harbor/cpython312/bin/python3.12), so nemo_oo_agents.py always uses it
# regardless of what Python the task container has. That means PYVER is always cp312
# and one tarball covers all containers — no per-container version needed.
#
# Only rebuild when package versions change in _cached_pkgs in nemo_oo_agents.py.
# Never rebuild for agent code changes — code is git-cloned fresh each run.
#
# ## What's in the tarball
#
# A complete Python 3.12 venv with all third-party deps (pydantic, litellm, aiohttp,
# grpcio, tiktoken, etc.) installed from the pre-staged wheel cache. Extracted to
# /opt/nemo-oo-agents-venv/ in ~30s vs the full install chain (~10-18 min under QEMU).
# nemo-oo-agents first-party packages are NOT included — they're editable-installed
# from the git clone after extraction on each run.
#
# ## Prerequisites
#
#   - apptainer on an x86_64 host (NOT aarch64/QEMU — build natively for correct wheels)
#   - A Python 3.12 SIF in SIF_CACHE (pulled from Alex's Lustre cache or Docker Hub)
#   - Wheel cache (WHEELS_DIR) — rsync'd from the harbor bootstrap overlay on galaxy
#   - get-pip.py (fetched automatically if missing)
#
# ## Usage
#
#   bash util/harbor/build_venv_tarballs.sh [OUTPUT_DIR] [WHEELS_DIR] [SIF_CACHE]
#
# Defaults:
#   OUTPUT_DIR  — /tmp/nemo_venv_build
#   WHEELS_DIR  — rsync'd from lab@10.87.108.113 (galaxy) if not present locally
#   SIF_CACHE   — ~/3p/sif_cache
#
# After building, rsync the tarball to galaxy:
#   rsync -av /tmp/nemo_venv_build/nemo-venv-base-cp312-x86_64.tar.gz \
#     lab@10.87.108.113:/home/lab/3p/harbor_bootstrap_overlay_v2/opt/harbor/

set -euo pipefail

OUTPUT_DIR="${1:-/tmp/nemo_venv_build}"
WHEELS_DIR="${2:-}"
SIF_CACHE="${3:-$HOME/3p/sif_cache}"
GALAXY="lab@10.87.108.113"
GALAXY_OVERLAY="/home/lab/3p/harbor_bootstrap_overlay_v2/opt/harbor"

# Only cp312 is needed — see header comment. The SIF can be the t-bench base image
# (which already has Python 3.12) or any python:3.12 image from Docker Hub.
declare -A PYTHON_SIFS=(
    [cp312]="ghcr.io_laude-institute_t-bench_ubuntu-24-04_latest"
)

# Packages installed into each venv — must match _cached_pkgs in nemo_oo_agents.py
CACHED_PKGS=(
    aiohappyeyeballs==2.6.1
    aiohttp==3.13.3
    aiosignal==1.4.0
    annotated_doc==0.0.4
    annotated_types==0.7.0
    anyio==4.13.0
    attrs==26.1.0
    certifi==2026.4.22
    cffi==2.0.0
    charset_normalizer==3.4.7
    click==8.1.8
    cryptography==47.0.0
    distro==1.9.0
    fastuuid==0.14.0
    filelock==3.29.0
    frozenlist==1.8.0
    fsspec==2026.4.0
    grpcio==1.67.1
    h11==0.16.0
    hf_xet==1.4.3
    httpcore==1.0.9
    httpx==0.28.1
    httpx_sse==0.4.3
    huggingface_hub==1.13.0
    idna==3.13
    importlib_metadata==8.5.0
    jinja2==3.1.6
    jiter==0.14.0
    jsonschema==4.23.0
    jsonschema_specifications==2025.9.1
    litellm==1.80.11
    markdown_it_py==4.0.0
    markupsafe==3.0.3
    mcp==1.27.0
    mdurl==0.1.2
    multidict==6.7.1
    openai==2.24.0
    openinference_instrumentation==0.1.48
    openinference_instrumentation_litellm==0.1.30
    openinference_semantic_conventions==0.1.29
    opentelemetry_api==1.41.1
    opentelemetry_instrumentation==0.62b1
    opentelemetry_sdk==1.41.1
    opentelemetry_semantic_conventions==0.62b1
    packaging==26.2
    platformdirs==4.9.6
    propcache==0.4.1
    pycparser==3.0
    pydantic==2.12.5
    pydantic_core==2.41.5
    pydantic_settings==2.14.0
    pygments==2.20.0
    pyjwt==2.12.1
    python_dotenv==1.0.1
    python_multipart==0.0.27
    pyyaml==6.0.3
    referencing==0.37.0
    regex==2026.4.4
    requests==2.33.1
    rich==15.0.0
    rpds_py==0.30.0
    setuptools==82.0.1
    shellingham==1.5.4
    sniffio==1.3.1
    sse_starlette==3.4.1
    starlette==1.0.0
    tiktoken
    tokenizers==0.22.2
    tqdm==4.67.3
    typer==0.23.1
    typing_extensions==4.15.0
    typing_inspection==0.4.2
    urllib3==2.6.3
    uvicorn==0.46.0
    wrapt==1.17.3
    yarl==1.23.0
    zipp==3.23.1
)

echo "=== nemo-oo-agents venv tarball builder ==="
echo "Output: $OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# --- Fetch wheel cache from galaxy if not provided locally ---
if [ -z "$WHEELS_DIR" ]; then
    WHEELS_DIR="$OUTPUT_DIR/nemo-wheels"
    if [ ! -d "$WHEELS_DIR" ] || [ -z "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]; then
        echo "=== Fetching wheels from galaxy ($GALAXY) ==="
        mkdir -p "$WHEELS_DIR"
        rsync -av "$GALAXY:$GALAXY_OVERLAY/nemo-wheels/" "$WHEELS_DIR/"
    else
        echo "=== Using existing wheels at $WHEELS_DIR ==="
    fi
fi

# --- Fetch get-pip.py ---
if [ ! -f "$OUTPUT_DIR/get-pip.py" ]; then
    echo "=== Downloading get-pip.py ==="
    curl -sSo "$OUTPUT_DIR/get-pip.py" https://bootstrap.pypa.io/get-pip.py
fi

# --- Write inner build script (runs inside each SIF) ---
cat > "$OUTPUT_DIR/_inner_build.sh" << 'INNER'
#!/bin/bash
set -e

WHEELS=/opt/harbor/nemo-wheels
PYBIN=$(which python3.12 2>/dev/null || which python3.13 2>/dev/null || which python3)
PYVER=$($PYBIN -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')
VENV_DIR="/tmp/nemo-oo-agents-venv-${PYVER}"
GET_PIP=/opt/harbor/get-pip.py

echo "=== Building venv for $PYVER using $PYBIN ==="

rm -rf "$VENV_DIR"
$PYBIN -m venv --without-pip "$VENV_DIR"
"$VENV_DIR/bin/python3" "$GET_PIP" --no-cache-dir --quiet
source "$VENV_DIR/bin/activate"

echo "=== Installing packages ==="
PKGS_FILE=/opt/harbor/_pkgs.txt
pip install --no-cache-dir --no-build-isolation --no-index \
    --find-links "$WHEELS" \
    $(cat "$PKGS_FILE") || {
    echo "=== Batch failed, falling back to per-package ==="
    while IFS= read -r P; do
        pip install --no-cache-dir --no-build-isolation --no-deps --no-index \
            --find-links "$WHEELS" "$P" 2>/dev/null || echo "SKIP: $P"
    done < "$PKGS_FILE"
}

echo "=== Verifying critical imports ==="
python3 -c "
import pydantic, pydantic_core, litellm
print('pydantic', pydantic.__version__)
print('pydantic_core', pydantic_core.__version__)
print('litellm', litellm.__version__)
"

echo "=== Creating .pth files (eliminates need for pip install -e at runtime) ==="
SITE_PKGS="$VENV_DIR/lib/python3.12/site-packages"
# These .pth files make all first-party packages importable by adding their
# source directories to sys.path.  This eliminates the runtime pip install -e
# step entirely — the tarball is self-sufficient.
cat > "$SITE_PKGS/nemo_oo_agents.pth" << 'PTH'
/installed-agent/nemo_oo_agents/src
PTH
cat > "$SITE_PKGS/nemo_oo_agents_benchmarks.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/nemo-oo-agents-benchmarks/src
PTH
cat > "$SITE_PKGS/unifiedllm.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/unifiedllm/src
PTH
cat > "$SITE_PKGS/agentdoc.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/agentdoc/src
PTH
cat > "$SITE_PKGS/nat_oo_agents.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/nat_oo_agents/src
PTH
cat > "$SITE_PKGS/evaluation.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/evaluation/src
PTH
cat > "$SITE_PKGS/openinference_nemo.pth" << 'PTH'
/installed-agent/nemo_oo_agents/packages/openinference-instrumentation-nemo-oo-agents/src
PTH

echo "=== Creating nemo-harbor entry point ==="
cat > "$VENV_DIR/bin/nemo-harbor" << 'ENTRY'
#!/opt/nemo-oo-agents-venv/bin/python3
from nemo_oo_agents_benchmarks.runner import main
if __name__ == "__main__": main()
ENTRY
chmod +x "$VENV_DIR/bin/nemo-harbor"

echo "=== Creating tarball ==="
TARBALL="/opt/harbor/nemo-venv-base-${PYVER}-x86_64.tar.gz"
# Rename to the expected dir name before tarring, restore after
mv "$VENV_DIR" /tmp/nemo-oo-agents-venv
tar -czf "$TARBALL" -C /tmp nemo-oo-agents-venv/
mv /tmp/nemo-oo-agents-venv "$VENV_DIR"
echo "=== Done: $TARBALL ($(du -sh "$TARBALL" | cut -f1)) ==="
INNER
chmod +x "$OUTPUT_DIR/_inner_build.sh"

# Write the package list (one per line for easy iteration)
printf '%s\n' "${CACHED_PKGS[@]}" > "$OUTPUT_DIR/_pkgs.txt"

# --- Build each Python version sequentially ---
BUILT=()
FAILED=()

for CPVER in "${!PYTHON_SIFS[@]}"; do
    SIF_NAME="${PYTHON_SIFS[$CPVER]}"
    SIF_PATH="$SIF_CACHE/${SIF_NAME}.sif"
    TARBALL="$OUTPUT_DIR/nemo-venv-base-${CPVER}-x86_64.tar.gz"

    if [ -f "$TARBALL" ]; then
        echo "=== $CPVER: already exists, skipping ==="
        BUILT+=("$CPVER")
        continue
    fi

    if [ ! -f "$SIF_PATH" ]; then
        echo "=== $CPVER: SIF not found at $SIF_PATH — skipping ==="
        FAILED+=("$CPVER (SIF missing: $SIF_NAME.sif)")
        continue
    fi

    echo ""
    echo "=== Building $CPVER from $SIF_PATH ==="
    if apptainer exec \
        --bind "$OUTPUT_DIR:/opt/harbor" \
        --writable-tmpfs \
        "$SIF_PATH" \
        /opt/harbor/_inner_build.sh; then
        BUILT+=("$CPVER")
        echo "=== $CPVER: built $(du -sh "$TARBALL" | cut -f1) ==="
    else
        FAILED+=("$CPVER (build failed)")
        echo "=== $CPVER: FAILED ==="
    fi
done

echo ""
echo "=== Summary ==="
echo "Built:  ${BUILT[*]:-none}"
echo "Failed: ${FAILED[*]:-none}"
echo ""
echo "=== Tarballs ==="
ls -lh "$OUTPUT_DIR"/nemo-venv-base-*.tar.gz 2>/dev/null || echo "  (none)"
echo ""
echo "To deploy to galaxy:"
echo "  rsync -av $OUTPUT_DIR/nemo-venv-base-cp312-x86_64.tar.gz \\"
echo "    $GALAXY:$GALAXY_OVERLAY/"
