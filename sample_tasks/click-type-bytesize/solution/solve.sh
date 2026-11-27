#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /workspace/repo

if [[ -f "${SCRIPT_DIR}/reference.patch" ]]; then
    git apply --whitespace=nowarn "${SCRIPT_DIR}/reference.patch"
else
    echo "ERROR: reference.patch not found in ${SCRIPT_DIR}" >&2
    exit 1
fi
