#!/bin/sh
# Build the native OpenGL shim as a static archive for POSIX targets.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "$TARGET_DIR" ]; then
    OUT_DIR="$DIR/../$TARGET_DIR"
    CC="${CC:-cc}"
else
    OUT_DIR="$DIR/../linux"
    CC="${CC:-cc}"
fi
mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$CC" -O2 -Wall -Wextra -fPIC -I"$DIR" -c "$DIR/lshgl.c" -o "$TMP/lshgl.o"
ar rcs "$OUT_DIR/liblshgl.a" "$TMP/lshgl.o"
echo "built $OUT_DIR/liblshgl.a"
