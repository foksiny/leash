#!/bin/sh
# Builds liblshhttp.a for POSIX platforms (Linux/macOS/BSD).
#
# The Linux archive bundles OpenSSL statically so user programs need no
# extra development packages. On macOS install OpenSSL first
#   brew install openssl
# then point the build at it:
#   OPENSSL_ROOT=$(brew --prefix openssl) sh build.sh
#
# Usage:
#   sh build.sh                 # -> ../linux/liblshhttp.a  (Linux)
#                               # -> ../macos/liblshhttp.a  (macOS)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
OS_NAME="$(uname -s)"

case "$OS_NAME" in
    Darwin)
        OUT_DIR="$DIR/../macos"
        CC="${CC:-cc}"
        if [ -n "$OPENSSL_ROOT" ]; then
            CFLAGS="-I$OPENSSL_ROOT/include"
            LDFLAGS_EXTRA="-L$OPENSSL_ROOT/lib"
        elif [ -d /opt/homebrew/opt/openssl ]; then
            CFLAGS="-I/opt/homebrew/opt/openssl/include"
            LDFLAGS_EXTRA="-L/opt/homebrew/opt/openssl/lib"
        fi
        ;;
    *)
        OUT_DIR="$DIR/../linux"
        CC="${CC:-gcc}"
        CFLAGS=""
        LDFLAGS_EXTRA=""
        ;;
esac

mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "compiling lshhttp.c..."
"$CC" -c "$DIR/lshhttp.c" -o "$TMP/lshhttp.o" -O2 -Wall -Wextra $CFLAGS

if [ -n "$LDFLAGS_EXTRA" ]; then
    # macOS/Homebrew OpenSSL: link its static archives into ours.
    ar x "$OPENSSL_ROOT/lib/libssl.a" 2>/dev/null || ar x /opt/homebrew/opt/openssl/lib/libssl.a
    ar x "$OPENSSL_ROOT/lib/libcrypto.a" 2>/dev/null || ar x /opt/homebrew/opt/openssl/lib/libcrypto.a
fi

cp "$TMP/lshhttp.o" .
ar rcs "$OUT_DIR/liblshhttp.a" lshhttp.o *.o 2>/dev/null || ar rcs "$OUT_DIR/liblshhttp.a" lshhttp.o
ranlib "$OUT_DIR/liblshhttp.a" 2>/dev/null || true

echo "built $OUT_DIR/liblshhttp.a"
