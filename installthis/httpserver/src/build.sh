#!/bin/sh
# Builds liblshhttpd.a for POSIX platforms (Linux/macOS/BSD).
#
# The server needs only the system socket library plus OpenSSL for the
# TLS listener (serve_tls). On macOS install OpenSSL first
#   brew install openssl
# then point the build at it:
#   OPENSSL_ROOT=$(brew --prefix openssl) sh build.sh
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
    CFLAGS="${CFLAGS:-}"
else
    case "$OS_NAME" in
        Darwin)
            OUT_DIR="$DIR/../macos"; CC="${CC:-cc}"
            if [ -n "$OPENSSL_ROOT" ]; then
                CFLAGS="-I$OPENSSL_ROOT/include"
            elif [ -d /opt/homebrew/opt/openssl ]; then
                CFLAGS="-I/opt/homebrew/opt/openssl/include"
            fi
            ;;
        *)      OUT_DIR="$DIR/../linux"; CC="${CC:-gcc}"; CFLAGS="" ;;
    esac
fi

mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "compiling lshhttpd.c..."
"$CC" -c "$DIR/lshhttpd.c" -o "$TMP/lshhttpd.o" -O2 -Wall -Wextra $CFLAGS

# The TLS listener needs OpenSSL: bundle it statically on Linux so user
# programs need no extra development packages (same approach as http).
# macOS uses its Homebrew OpenSSL via LDFLAGS_EXTRA below.
if [ -z "$LDFLAGS_EXTRA" ]; then
    for ssl_a in /usr/lib/x86_64-linux-gnu/libssl.a /usr/lib64/libssl.a /usr/lib/libssl.a; do
        [ -f "$ssl_a" ] && break
    done
    for crypto_a in /usr/lib/x86_64-linux-gnu/libcrypto.a /usr/lib64/libcrypto.a /usr/lib/libcrypto.a; do
        [ -f "$crypto_a" ] && break
    done
    if [ -f "$ssl_a" ] && [ -f "$crypto_a" ]; then
        cd "$TMP" && ar x "$ssl_a" && ar x "$crypto_a" && cd "$DIR"
    else
        echo "warning: static OpenSSL not found; TLS serving needs -lssl -lcrypto at link time"
    fi
fi

cp "$TMP/lshhttpd.o" .
ar rcs "$OUT_DIR/liblshhttpd.a" lshhttpd.o "$TMP"/*.o 2>/dev/null || ar rcs "$OUT_DIR/liblshhttpd.a" "$TMP/lshhttpd.o"
ranlib "$OUT_DIR/liblshhttpd.a" 2>/dev/null || true

echo "built $OUT_DIR/liblshhttpd.a"
