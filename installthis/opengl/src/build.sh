#!/bin/sh
# Build the native OpenGL shim archive for one or all targets.
#
#   sh build.sh                 # default: linux64 (backwards compatible)
#   sh build.sh all             # every target whose toolchain is available
#   sh build.sh linux32         # 32-bit x86 Linux
#   sh build.sh linux-arm       # ARM64 Linux
#   sh build.sh win64           # Windows x86-64 (WGL)
#   sh build.sh macos           # macOS x86-64 (CGL/AppKit)
#   sh build.sh macos-arm       # macOS ARM64 (CGL/AppKit)
#
# TARGET_DIR/CC still select an arbitrary output directory and compiler for
# custom cross builds of the POSIX GLX/EGL backend.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

build_archive() {
    name="$1"; src="$2"; cc="$3"; outdir="$4"; shift 4
    mkdir -p "$DIR/../$outdir"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    $cc -O2 -Wall -Wextra -Wno-cast-function-type -fPIC -I"$DIR" -c "$DIR/$src" -o "$TMP/lshgl.o" "$@"
    ar rcs "$DIR/../$outdir/liblshgl.a" "$TMP/lshgl.o"
    echo "built $DIR/../$outdir/liblshgl.a"
}

if [ -n "$TARGET_DIR" ]; then
    build_archive "$TARGET_DIR" lshgl.c "${CC:-cc}" "$TARGET_DIR"
    exit 0
fi

target="${1:-linux64}"
case "$target" in
    linux64)
        build_archive linux64 lshgl.c "${CC:-cc}" linux ;;
    linux32)
        build_archive linux32 lshgl.c "${CC:-cc}" linux32 -m32 ;;
    linux-arm)
        build_archive linux-arm lshgl.c "${CC:-aarch64-linux-gnu-gcc}" arm64 ;;
    win64)
        build_archive win64 lshgl_win.c "${CC:-x86_64-w64-mingw32-gcc}" win ;;
    macos)
        build_archive macos lshgl_mac.c "${CC:-clang}" macos -target x86_64-apple-darwin ;;
    macos-arm)
        build_archive macos-arm lshgl_mac.c "${CC:-clang}" macosarm64 -target arm64-apple-darwin ;;
    all)
        sh "$0" linux64
        command -v cc >/dev/null 2>&1 && sh "$0" linux32 || true
        command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 && sh "$0" linux-arm || true
        command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1 && sh "$0" win64 || true
        command -v clang >/dev/null 2>&1 && sh "$0" macos || true
        command -v clang >/dev/null 2>&1 && sh "$0" macos-arm || true ;;
    *)
        echo "unknown target: $target" >&2
        exit 1 ;;
esac
