"""Target architecture configurations for cross-compilation."""

import platform
import os


def wsl_available():
    """Return True if Windows Subsystem for Linux (WSL) is installed and usable.

    WSL is required to build and run Linux targets from a Windows host when no
    Linux cross-compiler is installed.
    """
    if os.name != "nt":
        return False
    import shutil
    import subprocess

    if not shutil.which("wsl"):
        return False
    try:
        res = subprocess.run(["wsl", "-l", "-q"], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return True
        res = subprocess.run(["wsl", "--status"], capture_output=True, text=True, timeout=15)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


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
        description="",
        size_flags=None,
        size_only_flags=None,
    ):
        self.name = name
        self.llvm_triple = llvm_triple
        self.output_extension = output_extension
        self.linker = linker
        self.linker_flags = linker_flags or []
        self.platform_name = platform_name or name
        self.description = description
        # Always-on flags: link-time dead code elimination + symbol stripping
        self.size_flags = size_flags or []
        # Extra flags only applied for -Os/-Oz size-optimized builds
        self.size_only_flags = size_only_flags or []

    def get_output_name(self, base_name):
        """Get the output filename for this target."""
        if self.output_extension and not base_name.endswith(self.output_extension):
            return base_name + self.output_extension
        return base_name

    def get_linker_cmd(self, obj_file, output_file, native_libs=None):
        """Get the linker command for this target."""
        native_libs = native_libs or []

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

    def uses_wsl(self):
        """Return True if this target must be built/run through WSL on Windows."""
        return os.name == "nt" and self.name in ("linux64", "linux32")

    def detect_cross_linker(self):
        """Try to detect an appropriate cross-compiler for this target."""
        if self.name == "win64" and os.name == "nt":
            return None
        # Linux targets on a Windows host are built through WSL (there is no
        # usable native Linux cross-toolchain), so no cross-compiler name.
        if self.uses_wsl():
            return None

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
        linker_flags=["-no-pie"],
        platform_name="Linux",
        description="Linux x86_64",
        size_flags=[
            "-Wl,--gc-sections",
            "-Wl,--strip-all",
            "-Wl,--build-id=none",
            "-Wl,-O1",
        ],
        size_only_flags=["-Wl,--hash-style=gnu"],
    ),
    "linux32": TargetConfig(
        name="linux32",
        llvm_triple="i686-unknown-linux-gnu",
        output_extension="",
        linker_flags=["-no-pie"],
        platform_name="Linux",
        description="Linux x86 (32-bit)",
        size_flags=[
            "-Wl,--gc-sections",
            "-Wl,--strip-all",
            "-Wl,--build-id=none",
            "-Wl,-O1",
        ],
        size_only_flags=["-Wl,--hash-style=gnu"],
    ),
    "win64": TargetConfig(
        name="win64",
        llvm_triple="x86_64-pc-windows-msvc",
        output_extension=".exe",
        linker_flags=["-mconsole"],
        platform_name="Windows",
        description="Windows x86_64",
        size_flags=["-Wl,--gc-sections", "-Wl,--strip-all"],
    ),
    "macos": TargetConfig(
        name="macos",
        llvm_triple="x86_64-apple-darwin",
        output_extension="",
        linker_flags=[],  # No libgc for macOS cross-compilation
        platform_name="macOS",
        description="macOS x86_64",
        size_flags=["-Wl,-dead_strip", "-Wl,-S"],
    ),
    "macos-arm": TargetConfig(
        name="macos-arm",
        llvm_triple="aarch64-apple-darwin",
        output_extension="",
        linker_flags=[],  # No libgc for macOS cross-compilation
        platform_name="macOS",
        description="macOS ARM64 (Apple Silicon)",
        size_flags=["-Wl,-dead_strip", "-Wl,-S"],
    ),
}


def get_target(name):
    """Get a target configuration by name."""
    if name not in TARGETS:
        supported = ", ".join(TARGETS.keys())
        raise ValueError(f"Unknown target '{name}'. Supported targets: {supported}")
    return TARGETS[name]


def get_native_target_name():
    """Detect the native target name without constructing a full config."""
    return get_native_target().name


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


def needs_cross_compile(target_name):
    """Return True if `target_name` differs from the native host target."""
    return target_name != get_native_target_name()
