#!/usr/bin/env bash
# Build one self-contained release bundle per target.
#
#   ./scripts/make_release.sh            # builds dist/leash-<version>-<target>.*
#
# Each bundle ships the compiler for that target, the runtime sources, the
# bundled standard library, docs, compiler sources (for `leash self-host`),
# the matching bootstrap seed, and install.sh / uninstall.sh. Run install.sh
# from the extracted bundle to lay everything out under ~/.leash (or $PREFIX).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(awk '/fnc leash_version\(\)/{f=1;next} f && /return "/{sub(/.*return "/,""); sub(/".*/,""); print; exit}' compiler/main.lsh)
[ -n "$VERSION" ] || { echo "cannot read version" >&2; exit 1; }
DIST="dist"
WORK=$(mktemp -d /tmp/leash_release_XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$DIST"
rm -f "$DIST/leash-${VERSION}"-*

# Runtime + stdlib + docs + compiler sources are identical in every bundle.
common() {
    local root="$1"
    mkdir -p "$root/leash/jvm" "$root/compiler" "$root/docs" "$root/bin"
    cp leash/gc.c leash/gc.h leash/cross_compile_stubs.c leash/windows_stubs.c "$root/leash/"
    cp leash/jvm/*.class "$root/leash/jvm/"
    cp compiler/*.lsh compiler/*.lshc "$root/compiler/"
    cp -R docs/. "$root/docs/"
    cp -R installthis "$root/stdlib"
    cp README.md LICENSE CHANGELOG.md "$root/"
}

emit_install_sh() {
    local root="$1" binfile="$2" launcher="$3"
    cat > "$root/install.sh" <<EOF
#!/usr/bin/env sh
# Leash $VERSION installer. Override the destination with PREFIX=/some/root.
set -eu
HERE=\$(CDPATH= cd -- "\$(dirname -- "\$0")" && pwd)
PREFIX=\${PREFIX:-\$HOME}
BIN="\$PREFIX/.leash/bin"
RT="\$PREFIX/.leash/leash"
LIBS="\$PREFIX/.leash/libs"
SHARE="\$PREFIX/.leash/share/leash"
mkdir -p "\$BIN" "\$RT" "\$LIBS" "\$SHARE"
cp -f "\$HERE/bin/$binfile" "\$BIN/$binfile"
chmod 755 "\$BIN/$binfile"
ln -sf "$binfile" "\$BIN/$launcher"
cp -R "\$HERE/leash/." "\$RT/"
cp -R "\$HERE/stdlib/." "\$LIBS/"
cp -R "\$HERE/compiler" "\$HERE/docs" "\$HERE/README.md" "\$HERE/LICENSE" "\$HERE/CHANGELOG.md" "\$SHARE/" 2>/dev/null || true
echo "Leash $VERSION installed to \$PREFIX/.leash"
case ":\$PATH:" in
  *":\$BIN:"*) ;;
  *) echo "Add \$BIN to your PATH: export PATH=\"\$BIN:\$PATH\"" ;;
esac
EOF
    chmod 755 "$root/install.sh"
    cat > "$root/uninstall.sh" <<EOF
#!/usr/bin/env sh
set -eu
PREFIX=\${PREFIX:-\$HOME}
rm -rf "\$PREFIX/.leash/bin/$binfile" "\$PREFIX/.leash/bin/$launcher" \\
       "\$PREFIX/.leash/leash" "\$PREFIX/.leash/libs" "\$PREFIX/.leash/share/leash"
echo "Leash $VERSION removed from \$PREFIX/.leash"
EOF
    chmod 755 "$root/uninstall.sh"
}

emit_install_bat() {
    local root="$1" binfile="$2"
    cat > "$root/install.bat" <<EOF
@echo off
setlocal
set "DEST=%USERPROFILE%\\.leash"
if not exist "%DEST%\\bin" mkdir "%DEST%\\bin"
if not exist "%DEST%\\leash" mkdir "%DEST%\\leash"
if not exist "%DEST%\\libs" mkdir "%DEST%\\libs"
copy /Y "%~dp0bin\\$binfile" "%DEST%\\bin\\$binfile" >nul
xcopy /E /Y /Q "%~dp0leash\\*" "%DEST%\\leash\\" >nul
xcopy /E /Y /Q "%~dp0stdlib\\*" "%DEST%\\libs\\" >nul
echo Leash $VERSION installed to %DEST%
echo Add %DEST%\\bin to your PATH.
EOF
}

echo "[release] version $VERSION"

for spec in "linux-x86_64:x86_64:leashc:leash" \
            "linux-x86:x86:leashc:leash" \
            "linux-arm64:arm:leashc:leash"; do
    IFS=: read -r label seed binfile launcher <<EOF
$spec
EOF
    root="$WORK/leash-${VERSION}-${label}"
    mkdir -p "$root/bin"
    common "$root"
    cp "bootstrap/leash-${VERSION}-target-${seed}" "$root/bin/$binfile"
    cp "bootstrap/leash-${VERSION}-target-${seed}" "$root/bootstrap-seed"
    emit_install_sh "$root" "$binfile" "$launcher"
    tar -C "$WORK" -czf "$DIST/leash-${VERSION}-${label}.tar.gz" "leash-${VERSION}-${label}"
    echo "  built $DIST/leash-${VERSION}-${label}.tar.gz"
done

# Windows: zip the PE binary plus the same runtime/stdlib tree.
label="windows-x86_64"
root="$WORK/leash-${VERSION}-${label}"
mkdir -p "$root/bin"
common "$root"
cp "bootstrap/leash-${VERSION}-target-win64.exe" "$root/bin/leashc.exe"
cp "bootstrap/leash-${VERSION}-target-win64.exe" "$root/bootstrap-seed.exe"
emit_install_bat "$root" "leashc.exe"
(cd "$WORK" && zip -q -r "$OLDPWD/$DIST/leash-${VERSION}-${label}.zip" "leash-${VERSION}-${label}")
echo "  built $DIST/leash-${VERSION}-${label}.zip"

# JVM target bundle. The native compiler already emits JVM bytecode
# (`--target jvm`) and `leash/jvm/*.class` holds the runtime shims, so ship
# that functional toolchain. The JVM-compiled compiler classes are included
# as a reference artifact: the self-hosted compiler's CodeGen method exceeds
# the JVM's 64 KiB method limit, so that class set cannot run as a compiler.
label="jvm"
root="$WORK/leash-${VERSION}-${label}"
mkdir -p "$root/bin" "$root/artifacts/jvm-compiler"
common "$root"
cp "bootstrap/leash-${VERSION}-target-x86_64" "$root/bin/leashc"
cp "bootstrap/leash-${VERSION}-target-x86_64" "$root/bootstrap-seed"
bin/leashc compile compiler/main.lsh --target jvm \
    to "$WORK/leash_1_10_0_target_java.class" >/dev/null
mv "$WORK"/*.class "$root/artifacts/jvm-compiler/"
emit_install_sh "$root" "leashc" "leash"
cat > "$root/README-JVM.txt" <<EOF
Leash $VERSION - JVM target bundle

The installer sets up the native compiler with JVM-target support:

  leash compile program.lsh --target jvm to program
  java -cp . program

The runtime shims (leash/jvm/*.class) are installed into
~/.leash/leash/jvm/. Java 11+ is required to run generated programs.

artifacts/jvm-compiler/ holds the compiler itself compiled to JVM bytecode
as a reference artifact. It cannot be run as a compiler: the generated
CodeGen class exceeds the JVM's 64 KiB per-method code limit.
EOF
tar -C "$WORK" -czf "$DIST/leash-${VERSION}-${label}.tar.gz" "leash-${VERSION}-${label}"
echo "  built $DIST/leash-${VERSION}-${label}.tar.gz"

echo "[release] bundles in $DIST/"
ls -lh "$DIST"/leash-${VERSION}-*
