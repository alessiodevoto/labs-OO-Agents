#!/usr/bin/env bash
# Build harbor_bootstrap_overlay/ — a read-only Apptainer directory overlay
# providing Python 3.11 + uvicorn at /opt/harbor/ for task images that ship
# no Python interpreter.
#
# The overlay is a plain directory (not a SIF) mounted via --overlay DIR:ro.
# Files at /opt/harbor/ don't shadow any existing task container paths.
#
# The bootstrap script in apptainer.py checks /opt/harbor/bin/python3 as a
# final fallback candidate, enabling any harbor benchmark to run in Apptainer
# even when the task image ships no Python.
#
# Prerequisites:
#   - docker (builds the Python layer on Bullseye/glibc-2.31 for compatibility)
#
# Usage:
#   bash util/harbor/build_bootstrap_overlay.sh [OUTPUT_DIR]
#
# Default output: /raid/rcabral/home/3p/harbor_bootstrap_overlay/
# Add to harbor yaml under environment.kwargs:
#   apptainer_bootstrap_overlay: /raid/rcabral/home/3p/harbor_bootstrap_overlay

set -euo pipefail

OUTPUT_DIR="${1:-/raid/rcabral/home/3p/harbor_bootstrap_overlay}"
IMAGE_TAG="harbor_bootstrap_overlay:build_$$"

echo "[harbor-bootstrap] Building Python 3.11 + uvicorn on Bullseye (glibc 2.31)..."

# Two-stage build:
#   Stage 1  — install Python 3.11 + uvicorn on python:3.11-slim-bullseye
#               (Debian 11 / glibc 2.31 → works on any modern Linux task image)
#   Stage 2  — copy ONLY /opt/harbor to a minimal image for clean extraction
#
# /opt/harbor layout:
#   bin/python3.11   — Python interpreter binary
#   bin/python3      — symlink → python3.11
#   lib/python3.11/  — stdlib + site-packages (uvicorn, fastapi, anyio, httpx)
#   lib/libpython*   — shared lib needed via LD_LIBRARY_PATH=/opt/harbor/lib
#   lib/libssl.so.1.1 + libcrypto.so.1.1 — OpenSSL 1.1 for task images without it
#
# NOTE: All RUN commands are single lines (no heredoc comment-continuation parsing issues).
# NOTE: tar extracts as opt/harbor/... (no --strip-components) so overlay mounts at /opt/harbor.
docker build --platform linux/amd64 -t "$IMAGE_TAG" - << 'DOCKERFILE'
FROM python:3.11-slim-bullseye AS builder
RUN pip install --quiet uvicorn fastapi anyio httpx && pip cache purge && find /usr/local/lib -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN set -e && mkdir -p /opt/harbor/bin /opt/harbor/lib && cp /usr/local/bin/python3.11 /opt/harbor/bin/python3.11 && ln -sf python3.11 /opt/harbor/bin/python3 && cp -a /usr/local/lib/python3.11 /opt/harbor/lib/ && find /usr/local/lib /usr/lib -name 'libpython3.11*.so*' -exec cp -P {} /opt/harbor/lib/ ';' 2>/dev/null || true && find /usr/lib -name 'libssl.so.1.1' -exec cp -P {} /opt/harbor/lib/ ';' 2>/dev/null || true && find /usr/lib -name 'libcrypto.so.1.1' -exec cp -P {} /opt/harbor/lib/ ';' 2>/dev/null || true
FROM busybox:stable-musl
COPY --from=builder /opt/harbor /opt/harbor
DOCKERFILE

echo "[harbor-bootstrap] Extracting /opt/harbor to: $OUTPUT_DIR"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
docker run --rm "$IMAGE_TAG" tar cf - opt | tar xf - -C "$OUTPUT_DIR"
docker rmi "$IMAGE_TAG" --force 2>/dev/null || true

SIZE=$(du -sh "$OUTPUT_DIR" | cut -f1)
echo "[harbor-bootstrap] Done: $OUTPUT_DIR ($SIZE)"
echo ""
echo "Verify with:"
echo "  apptainer exec --overlay $OUTPUT_DIR:ro /path/to/task.sif \\"
echo "    bash -c 'export LD_LIBRARY_PATH=/opt/harbor/lib:\$LD_LIBRARY_PATH && /opt/harbor/bin/python3 --version'"
echo ""
echo "Add to any harbor yaml under environment.kwargs:"
echo "  apptainer_bootstrap_overlay: $OUTPUT_DIR"
