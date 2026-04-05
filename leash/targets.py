"""Target architecture configurations for cross-compilation."""

import platform
import os


class TargetConfig:
    """Configuration for a compilation target."""

    def __init__(
        self,
        name,
        llvm_triple,
        output_extension,
        linker=None,
        linker_flags=None,
        platform_name=None,
        is_js=False,
        is_html_js=False,
        description="",
    ):
        self.name = name
        self.llvm_triple = llvm_triple
        self.output_extension = output_extension
        self.linker = linker
        self.linker_flags = linker_flags or []
        self.platform_name = platform_name or name
        self.is_js = is_js
        self.is_html_js = is_html_js
        self.description = description

    def get_output_name(self, base_name):
        """Get the output filename for this target."""
        if self.is_html_js:
            return base_name + ".html"
        if self.is_js:
            return base_name + ".js"
        return base_name + self.output_extension

    def get_linker_cmd(self, obj_file, output_file, native_libs=None):
        """Get the linker command for this target."""
        native_libs = native_libs or []

        if self.is_js:
            return None  # No linking needed for JS

        if self.linker:
            cmd = [self.linker, obj_file, "-o", output_file]
            cmd.extend(self.linker_flags)
            cmd.extend(native_libs)
            return cmd

        # Fallback to system default
        cc = os.environ.get("CC", "gcc")
        cmd = [cc, obj_file, "-o", output_file]
        cmd.extend(self.linker_flags)
        cmd.extend(native_libs)
        return cmd

    def detect_cross_linker(self):
        """Try to detect an appropriate cross-compiler for this target."""
        cross_compilers = {
            "win64": ["x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-clang"],
            "linux32": ["i686-linux-gnu-gcc", "i686-pc-linux-gnu-gcc"],
            "linux64": None,  # Usually native on Linux
            "macos": ["o64-clang", "x86_64-apple-darwin20-clang"],
        }

        if self.name in cross_compilers and cross_compilers[self.name]:
            import subprocess

            for cc in cross_compilers[self.name]:
                try:
                    subprocess.run([cc, "--version"], capture_output=True, check=True)
                    return cc
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue

        return None


# Target configurations
TARGETS = {
    "linux64": TargetConfig(
        name="linux64",
        llvm_triple="x86_64-unknown-linux-gnu",
        output_extension="",
        linker_flags=["-no-pie", "-l:libgc.so.1"],
        platform_name="Linux",
        description="Linux x86_64",
    ),
    "linux32": TargetConfig(
        name="linux32",
        llvm_triple="i686-unknown-linux-gnu",
        output_extension="",
        linker_flags=["-no-pie", "-l:libgc.so.1"],
        platform_name="Linux",
        description="Linux x86 (32-bit)",
    ),
    "win64": TargetConfig(
        name="win64",
        llvm_triple="x86_64-pc-windows-msvc",
        output_extension=".exe",
        linker_flags=[],  # No libgc for Windows cross-compilation
        platform_name="Windows",
        description="Windows x86_64",
    ),
    "macos": TargetConfig(
        name="macos",
        llvm_triple="x86_64-apple-darwin",
        output_extension="",
        linker_flags=[],  # No libgc for macOS cross-compilation
        platform_name="macOS",
        description="macOS x86_64",
    ),
    "macos-arm": TargetConfig(
        name="macos-arm",
        llvm_triple="aarch64-apple-darwin",
        output_extension="",
        linker_flags=[],  # No libgc for macOS cross-compilation
        platform_name="macOS",
        description="macOS ARM64 (Apple Silicon)",
    ),
    "js": TargetConfig(
        name="js",
        llvm_triple=None,
        output_extension=".js",
        is_js=True,
        description="JavaScript (Node.js)",
    ),
    "html-js": TargetConfig(
        name="html-js",
        llvm_triple=None,
        output_extension=".html",
        is_js=True,
        is_html_js=True,
        description="JavaScript in HTML (Browser)",
    ),
}


def get_target(name):
    """Get a target configuration by name."""
    if name not in TARGETS:
        supported = ", ".join(TARGETS.keys())
        raise ValueError(f"Unknown target '{name}'. Supported targets: {supported}")
    return TARGETS[name]


def get_native_target():
    """Detect the native target."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return TARGETS["linux64"]
        elif machine in ("i386", "i686", "x86"):
            return TARGETS["linux32"]
    elif system == "windows":
        return TARGETS["win64"]
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            return TARGETS["macos-arm"]
        else:
            return TARGETS["macos"]

    # Default fallback
    return TARGETS["linux64"]


def list_targets():
    """Return a list of all supported targets with descriptions."""
    result = []
    for name, config in TARGETS.items():
        result.append((name, config.description))
    return result
