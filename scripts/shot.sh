#!/bin/sh
# usage: sh scripts/shot.sh preview/out.png [query]
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/preview/shot.png}"
Q="${2:-view=overview&hour=17.2&ui=0}"
export URL="${URL:-http://127.0.0.1:5173/}"
export TMO="${TMO:-180000}"
export WAIT="${WAIT:-3000}"
export COREPACK_HOME="${COREPACK_HOME:-/tmp/corepack-home}"
export PNPM_HOME="${PNPM_HOME:-/tmp/pnpm-home}"
cd "$ROOT/web"
node tools/shot.mjs "$OUT" "$Q" "${3:-1600}" "${4:-1000}"
