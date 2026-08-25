#!/bin/sh
# Builds liblshsql.a for POSIX platforms (Linux/macOS/BSD).
#
# The archive is fully self-contained: it embeds the SQLite amalgamation
# (sqlite3.c, vendored in this directory), so user programs need no extra
# development packages on any platform.
#
# Usage:
#   sh build.sh                 # -> ../linux/liblshsql.a  (Linux)
#                               # -> ../macos/liblshsql.a  (macOS)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
OS_NAME="$(uname -s)"

case "$OS_NAME" in
    Darwin) OUT_DIR="$DIR/../macos"; CC="${CC:-cc}" ;;
    *)      OUT_DIR="$DIR/../linux"; CC="${CC:-gcc}" ;;
esac

mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SQLITE_FLAGS="-DSQLITE_THREADSAFE=1 -DSQLITE_OMIT_LOAD_EXTENSION"

echo "compiling sqlite3.c (this can take a minute)..."
"$CC" -c "$DIR/sqlite3.c" -o "$TMP/sqlite3.o" -O1 $SQLITE_FLAGS

echo "compiling lshsql.c..."
"$CC" -c "$DIR/lshsql.c" -o "$TMP/lshsql.o" -O2 -Wall -Wextra

ar rcs "$OUT_DIR/liblshsql.a" "$TMP/sqlite3.o" "$TMP/lshsql.o"
ranlib "$OUT_DIR/liblshsql.a" 2>/dev/null || true

echo "built $OUT_DIR/liblshsql.a"
