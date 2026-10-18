#!/usr/bin/env bash
# Full self-hosted bootstrap: Stage 0 -> Stage 1 -> Stage 2 -> Stage 3 fixed-point check.
set -euo pipefail
# Guardrail: cap the address space of every compiler/clang child so a
# runaway stage can never eat the host's RAM.
ulimit -v 2097152 2>/dev/null || true
cd "$(dirname "$0")/.."

mkdir -p bin

# Stage 0 produces the first self-hosted compiler binary. Prefer an
# already-built self-hosted seed (bin/leashc) so the bootstrap can run
# with NO Python dependency; fall back to the checked-in bootstrap
# compiler binary.
if [ -x bin/leashc ]; then
    echo "[bootstrap] Stage 0: seeding from existing bin/leashc"
    cp bin/leashc bin/leashc_stage1
    chmod +x bin/leashc_stage1
elif [ -x bootstrap/leash-1.5.1-target-x86_64 ]; then
    echo "[bootstrap] Stage 0: seeding from bootstrap/leash-1.5.1-target-x86_64"
    cp bootstrap/leash-1.5.1-target-x86_64 bin/leashc_stage1
    chmod +x bin/leashc_stage1
else
    echo "[bootstrap] ERROR: no seed compiler found." >&2
    echo "  Provide bin/leashc (a self-hosted seed) or" >&2
    echo "  bootstrap/leash-1.5.1-target-x86_64." >&2
    exit 1
fi

echo "[bootstrap] Stage 1: bin/leashc_stage1 compiles compiler/main.lsh -> bin/leashc_stage2"
bin/leashc_stage1 compile compiler/main.lsh to bin/leashc_stage2

echo "[bootstrap] Stage 2: bin/leashc_stage2 compiles compiler/main.lsh -> bin/leashc_stage3"
bin/leashc_stage2 compile compiler/main.lsh to bin/leashc_stage3

# Always regenerate both IRs explicitly: a plain compile only writes the
# temporary .ll when its object cache misses, so relying on those leftovers
# made this check silently vacuous.
IR2="/tmp/leashc_boot_stage2"
IR3="/tmp/leashc_boot_stage3"
rm -f "$IR2.ll" "$IR3.ll"
bin/leashc_stage2 compile compiler/main.lsh --emit-llvm to "$IR2"
bin/leashc_stage3 compile compiler/main.lsh --emit-llvm to "$IR3"

echo "[bootstrap] Comparing Stage 2 IR vs Stage 3 IR for fixed point..."
if diff <(grep -v '^;' "$IR2.ll" | tr -d ' ') <(grep -v '^;' "$IR3.ll" | tr -d ' ') > /dev/null; then
    echo "[bootstrap] OK: fixed point reached (Stage 2 IR == Stage 3 IR)."
else
    echo "[bootstrap] ERROR: IR differs between stages:" >&2
    diff "$IR2.ll" "$IR3.ll" | head -50 >&2 || true
    exit 1
fi

echo "[bootstrap] Installing bin/leashc"
cp bin/leashc_stage3 bin/leashc
chmod +x bin/leashc

echo "[bootstrap] Done. Try: bin/leashc run examples/hello.lsh"
