#!/bin/sh
# Builds liblshhttpd.a for POSIX platforms (Linux/macOS/BSD).
#
# The server needs only the system socket library — no external
# dependencies on any platform.
#
# Usage:
#   sh build.sh                 # -> ../linux/liblshhttpd.a  (Linux)
#                               # -> ../macos/liblshhttpd.a  (macOS)
#   TARGET_DIR=arm64 CC=aarch64-linux-gnu-gcc sh build.sh
#                               # -> ../arm64/liblshhttpd.a  (ARM64 cross)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
OS_NAME="$(uname -s)"

if [ -n "$TARGET_DIR" ]; then
    # Explicit cross-build (e.g. TARGET_DIR=arm64 with an aarch64 CC).
    OUT_DIR="$DIR/../$TARGET_DIR"
    CC="${CC:-gcc}"
else
    case "$OS_NAME" in
        Darwin) OUT_DIR="$DIR/../macos"; CC="${CC:-cc}" ;;
        *)      OUT_DIR="$DIR/../linux"; CC="${CC:-gcc}" ;;
    esac
fi

mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "compiling lshhttpd.c..."
"$CC" -c "$DIR/lshhttpd.c" -o "$TMP/lshhttpd.o" -O2 -Wall -Wextra

ar rcs "$OUT_DIR/liblshhttpd.a" "$TMP/lshhttpd.o"
ranlib "$OUT_DIR/liblshhttpd.a" 2>/dev/null || true

echo "built $OUT_DIR/liblshhttpd.a"
