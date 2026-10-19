#!/bin/sh
# Builds liblshhttpd.a for POSIX and Windows targets.
#
# POSIX uses OpenSSL for the TLS listener (serve_tls). Windows uses
# SChannel (secur32) plus Crypt32 for PEM cert/key loading — all shipped
# with Windows, so no external dependency is bundled.
#
# Usage:
#   sh build.sh                    # -> ../linux/liblshhttpd.a  (Linux)
#                                  # -> ../macos/liblshhttpd.a  (macOS)
#   TARGET_DIR=arm64 CC=aarch64-linux-gnu-gcc sh build.sh
#                                  # -> ../arm64/liblshhttpd.a  (ARM64 cross)
#   TARGET_DIR=win CC=x86_64-w64-mingw32-gcc sh build.sh
#                                  # -> ../win/liblshhttpd.a    (win64 cross)
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
OS_NAME="$(uname -s)"
BUNDLE_SSL=1

if [ "$TARGET_DIR" = "win" ]; then
    # Windows cross-build: no OpenSSL; Schannel/secur32 live on Windows.
    OUT_DIR="$DIR/../win"
    CC="${CC:-x86_64-w64-mingw32-gcc}"
    AR="${AR:-x86_64-w64-mingw32-ar}"
    RANLIB="${RANLIB:-x86_64-w64-mingw32-ranlib}"
    CFLAGS="${CFLAGS:-}"
    BUNDLE_SSL=0
elif [ -n "$TARGET_DIR" ]; then
    # Explicit cross-build (e.g. TARGET_DIR=arm64 with an aarch64 CC).
    OUT_DIR="$DIR/../$TARGET_DIR"
    CC="${CC:-gcc}"
    AR="${AR:-ar}"
    RANLIB="${RANLIB:-ranlib}"
    CFLAGS="${CFLAGS:-}"
else
    AR="${AR:-ar}"
    RANLIB="${RANLIB:-ranlib}"
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

# The POSIX TLS listener needs OpenSSL: bundle it statically on Linux so
# user programs need no extra development packages (same approach as http).
# macOS uses its Homebrew OpenSSL via LDFLAGS_EXTRA below. Windows uses
# SChannel and bundles nothing.
if [ "$BUNDLE_SSL" = 1 ] && [ -z "$LDFLAGS_EXTRA" ]; then
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

"$AR" rcs "$OUT_DIR/liblshhttpd.a" "$TMP/lshhttpd.o"
if [ "$BUNDLE_SSL" = 1 ]; then
    for o in "$TMP"/*.o; do
        case "$o" in
            */lshhttpd.o) ;;
            *) "$AR" rcs "$OUT_DIR/liblshhttpd.a" "$o" ;;
        esac
    done
fi
"$RANLIB" "$OUT_DIR/liblshhttpd.a" 2>/dev/null || true

echo "built $OUT_DIR/liblshhttpd.a"
