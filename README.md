# Leash Programming Language

**Version 0.21.0b0 Beta**

Leash is a strongly-typed, modern compiled programming language built on top of LLVM. It features an intuitive syntax and is designed to handle common tasks with native performance.

> **New to Leash?** See the [Compiler Pipeline](#compiler-pipeline) for an overview of how source code becomes an executable.

## Table of Contents
- [Installation](#installation)
- [Running Leash](#running-leash)
  - [Leash Update (`update`)](#leash-update-update)
- [Checking for Errors](#checking-for-errors)
  - [Verbose Diagnostics (`--verbose` / `-vb`)](#verbose-diagnostics---verbose---vb)
- [Compilation Targets](#compilation-targets)
  - [Native Targets (linux64, win64, macos)](#native-targets-linux64-win64-macos)
- [Defining Variables](#defining-variables)
- [Immutable Variables](#immutable-variables)
- [Data Types](#data-types)
- [Operators](#operators)
  - [Is-In Operator](#is-in-operator-)
- [Functions](#functions)
  - [Optional Parentheses](#optional-parentheses)
  - [Default Arguments](#default-arguments)
  - [Named Arguments](#named-arguments)
- [Global Variables](#global-variables)
- [Control Flow](#control-flow)
  - [Branching](#branching)
  - [Loops](#loops)
  - [Switch-Case](#switch-case)
  - [The `self` Keyword](#the-self-keyword)
- [Input Handling](#input-handling)
- [Random Numbers](#random-numbers)
- [Time and Delays](#time-and-delays)
- [Built-in Compile-Time Variables](#built-in-compile-time-variables)
- [Conditional Compilation (Top-Level If)](#conditional-compilation-top-level-if)
- [Executing Shell Commands](#executing-shell-commands)
- [Arrays](#arrays)
- [Hash Tables](#hash-tables)
- [Structs](#structs)
- [Pointers](#pointers)
  - [Function Pointers](#function-pointers)
- [Unions](#unions)
- [Enums](#enums)
- [Type Aliases](#type-aliases)
- [Macros](#macros)
- [Generic Types](#generic-types)
- [Multi-Type Functions](#multi-type-functions)
- [Operator Definitions (`opdef`)](#operator-definitions-opdef)
- [Multi-Return Functions](#multi-return-functions)
- [Type Casting](#type-casting)
- [The `as` Keyword](#the-as-keyword)
- [Type Conversions](#type-conversions)
- [The `sizeof()` Operator](#the-sizeof-operator)
- [The `typeof()` Operator](#the-typeof-operator)
- [Strings](#strings)
- [Classes](#classes)
  - [Class Inheritance (Subclasses)](#class-inheritance-subclasses)
  - [Polymorphism and Dynamic Dispatch](#polymorphism-and-dynamic-dispatch)
  - [Class-Based Entry Points (Java-Friendly Syntax)](#class-based-entry-points-java-friendly-syntax)
- [File I/O](#file-io)
- [Memory Management](#memory-management)
- [Error Handling & Safety](#error-handling--safety)
  - [Works-Otherwise Error Handling](#works-otherwise-error-handling)
  - [Unsafe Functions](#unsafe-functions)
- [Native Library Imports (FFI)](#native-library-imports-ffi)
- [Library Imports](#library-imports)
- [Program Termination](#program-termination)
- [Leashed Package Manager](#leashed-package-manager)
  - [Installation](#installation-1)
  - [Creating a Package (`leashed init`)](#creating-a-package-leashed-init)
  - [Publishing a Package (`leashed publish`)](#publishing-a-package-leashed-publish)
  - [Installing Libraries (`leashed install`)](#installing-libraries-leashed-install)
  - [Adding Libraries to a Project (`leashed add`)](#adding-libraries-to-a-project-leashed-add)
  - [Searching for Libraries (`leashed search`)](#searching-for-libraries-leashed-search)
  - [How Publishing Works Under the Hood](#how-publishing-works-under-the-hood)
- [Library Installation](#library-installation)
- [Concurrency](#concurrency)
  - [Shared Variables (`shared`)](#shared-variables-shared)
  - [Fusion Variables (`fusion`)](#fusion-variables-fusion)
  - [Worker Functions (`worker fnc`)](#worker-functions-worker-fnc)
  - [Spawning Workers (`spawn`)](#spawning-workers-spawn)
  - [The `thisworker` Built-in](#the-thisworker-built-in)
  - [Lifecycle & Cleanup](#lifecycle--cleanup)
  - [Thread Safety Notes](#thread-safety-notes)
- [Syntax Highlighting](#syntax-highlighting)

## Installation

### Prerequisites

Before using Leash, you need the following installed on your system:

| Dependency | Required for | Version |
|------------|-------------|---------|
| **Python 3** | Running the Leash compiler | 3.8+ |
| **LLVM** | Code generation via `llvmlite` | 11+ (development libraries) |
| **C compiler** (`gcc` or `clang`) | Linking compiled Leash programs with the GC runtime | Any recent version |

### Installing Dependencies

#### Linux (Debian/Ubuntu)

```bash
# Install LLVM development libraries and a C compiler
sudo apt install llvm-dev gcc

# Optional: cross-compilation to Windows (MinGW)
sudo apt install gcc-mingw-w64-x86-64
```

#### Linux (Fedora/RHEL)

```bash
sudo dnf install llvm-devel gcc
```

#### Linux (Arch)

```bash
sudo pacman -S llvm gcc
```

#### Windows

```bash
# Option 1: Using MinGW-w64 (recommended)
# Download from https://www.mingw-w64.org/ or use winget:
winget install mingw

# Option 2: Using LLVM + clang
# Download from https://releases.llvm.org/

# Install Python 3 from https://python.org
```

### Install Leash

Once the prerequisites are installed, install Leash and its Python dependencies:

```bash
# Clone or navigate to the Leash project directory, then:
pip install -e .
```

This installs:
- `leash` — the Leash compiler CLI (`leash compile`, `leash run`, etc.)
- `leashed` — the Leash package manager

Verify the installation:

```bash
leash --version
leashed --version
```

### Optional Dependencies

| Dependency | Used by | Notes |
|------------|---------|-------|
| **Git** | `leashed publish`, `leashed install` | Required for package management |
| **GitHub CLI (`gh`)** | `leashed publish` | Required to create repos and PRs |
| **Wine** | `leash run --target win64` on Linux | Run Windows binaries on Linux |

```bash
# Linux
sudo apt install git gh wine

# Windows
winget install Git.Git GitHub.cli
```

## Running Leash

You can run leash files (`.lsh`) directly using the `run` command, or compile them to an executable using the `compile` command. Leash also supports project-level workflows with `init`, `build`, and `runp`.

### Running Directly
To execute a Leash program without creating an output binary executable (and optionally pass command line arguments):
```bash
python3 -m leash.cli run program.lsh [arguments...]
```

### Compiling
Compiling will generate a native executable for your system.

```bash
# By default, compiles to a binary with the same name as the file (e.g., 'program')
python3 -m leash.cli compile program.lsh
./program

# You can also use the 'to' keyword to specify a custom output binary name
python3 -m leash.cli compile program.lsh to my_custom_binary
./my_custom_binary

# Compile to a dynamic library (.so)
python3 -m leash.cli compile program.lsh to-dynamic mylib

# Compile to a static library (.a)
python3 -m leash.cli compile program.lsh to-static mylib

# Link with external libraries (-l<name> links lib<name>.so or -l<name>.so)
python3 -m leash.cli compile program.lsh -lm   # links libm.so (math library)
python3 -m leash.cli run program.lsh -lcurl   # links libcurl.so
```

### Dumping LLVM IR
You can dump the generated LLVM IR to a `.ll` file instead of compiling to an executable. This is useful for debugging or inspecting the generated code.

```bash
# Dump to a .ll file with the same name as the input (e.g., 'program.ll')
python3 -m leash.cli dump program.lsh

# Specify a custom output name
python3 -m leash.cli dump program.lsh to myoutput    # creates myoutput.ll
python3 -m leash.cli dump program.lsh to myoutput.ll  # creates myoutput.ll
```

### Project Init (`init`)

The `init` command scaffolds a new Leash project with the standard directory structure:

```bash
# Initialize in current directory
leash init

# Initialize in a specific directory
leash init my_project
```

This creates:

```
my_project/
├── src/
│   └── main.lsh          # Entry point
├── imports/               # Local import directory
├── out/                   # Compiled output directory
└── config.lshc            # Project configuration
```

The generated `main.lsh`:
```leash
fnc main |> show("Hello, World!");
```

The generated `config.lshc`:
```leashconfig
main: "src/main.lsh"
clibs: {}
imports: "imports/"
opt_level: "O3"
```

### Project Build (`build`)

The `build` command reads `config.lshc` from the current directory and compiles the project:

```bash
leash build
```

This uses the project configuration to:
- Find the main entry file (`main` key)
- Resolve the imports directory (`imports` key)
- Link C libraries (`clibs` key)
- Apply the optimization level (`opt_level` key)

Additional import directories can be specified:

```bash
leash build --other-imports path/to/imports
leash build -oi path/to/imports
```

### Project Run (`runp`)

The `runp` command compiles and runs a project in one step, using `config.lshc`:

```bash
# Compile and run
leash runp

# Pass arguments to the program (after --)
leash runp -- arg1 arg2 arg3

# With additional import directories
leash runp --other-imports path/to/imports -- arg1 arg2
```

The output binary is temporary and cleaned up after execution, similar to `leash run`.

### Leash Update (`update`)

The `update` command checks for new releases on the Leash GitHub repository and pulls the latest changes:

```bash
leash update
```

This will:
1. Fetch the latest release tag from `github.com/foksiny/leash` via the GitHub API
2. Run `git pull` to update your local clone to the latest commit

Requires Git to be installed and the Leash repo to have been cloned from GitHub.

### Additional Import Directories (`--other-imports / -oi`)

The `--other-imports` (or `-oi`) flag adds extra directories to the module search path for `use ...;` statements. This is available on `compile`, `run`, `dump`, `build`, and `runp`:

```bash
# Compile with additional import paths
leash compile program.lsh --other-imports mylibs/
leash run program.lsh -oi mylibs/ -oi other_libs/

# These directories are searched before ~/.leash/libs/
```

This is useful when working outside a project setup or when you need to import from non-standard locations.

## Optimization Levels

Leash supports LLVM optimization levels via the `--opt` (or `-O`) flag. Use it with `compile`, `run`, or `dump` to control the aggressiveness of the optimization pipeline.

```bash
# No optimization (default)
python3 -m leash.cli compile program.lsh --opt 0

# Basic optimizations
python3 -m leash.cli compile program.lsh --opt 1

# Standard optimizations
python3 -m leash.cli compile program.lsh --opt 2

# Aggressive optimizations
python3 -m leash.cli run program.lsh --opt 3

# Maximum optimizations (tail recursion opt, extra LLVM passes)
python3 -m leash.cli compile program.lsh --opt 4

# Optimize for size
python3 -m leash.cli compile program.lsh --opt s

# Optimize for minimum size (additional size reductions)
python3 -m leash.cli compile program.lsh --opt z
```

### Optimization Passes by Level

| Level | Description | Key Passes |
|-------|-------------|------------|
| **`-O0`** (default) | No optimization | Fastest compilation, unoptimized output |
| **`-O1`** | Basic | Instruction combining, dead code elimination, simplify CFG |
| **`-O2`** | Standard | Above + SROA, jump threading, reassociation, global opt, loop simplification |
| **`-O3`** | Aggressive | Above + loop unrolling, loop strength reduction, always inline, merge functions, aggressive DCE, argument promotion, IPSCCP, SLP vectorizer, loop distribution, versioning, interchange, predication, unswitching, called-value propagation, float-to-int promotion, speculative execution |
| **`-O4`** | Maximum | Above + tail recursion optimization (AST), **aggressive inlining** (threshold 600 vs default 225), **dual-pass pipeline** (run standard O3 pass‑manager twice with aggressive cleanup iterations between and after) |
| **`-Os`** | Size | `-O2` + global dead code elimination + extra CFG simplification |

> **Note:** Optimization occurs at **three layers**: Leash AST (frontend), LLVM IR (middle-end), and C runtime (link-time). The `-O` flag controls all three simultaneously.

### Optimization Verbosity (`--optimization-verbosity` / `-ov`)

The `-ov` flag enables detailed logging of optimization passes as they run. This is useful for understanding what the optimizer is doing, debugging performance issues, or learning about the optimization pipeline.

```bash
python3 -m leash.cli compile program.lsh -ov
python3 -m leash.cli run program.lsh --opt 3 -ov
```

When enabled, the compiler prints messages for each phase:
- **`[AST Opt]`** — AST-level pipeline start/end and phase descriptions
- **`[CF]`** — Constant folding of specific expressions
- **`[CP]`** — Constant propagation inlining compile-time constants
- **`[DBE]`** — Dead branch elimination (removing if/else with constant conditions)
- **`[UCE]`** — Unreachable code elimination (statements after `return`)
- **`[DCE]`** — Dead code elimination (unused top-level definitions)
- **`[TRO]`** — Tail recursion optimization (applied at `-O4`)
- **`[LLVM Opt]`** — LLVM pass pipeline execution details

This flag is independent of `--verbose`/`-vb` — it only shows optimization-related messages, not masterclass diagnostics.

---

## Compiler Pipeline

Leash transforms source code into a native executable through a multi-stage pipeline. Each stage feeds its output into the next:

```
                          ┌─────────────────────────────────────┐
   .lsh file  ──────────▶ │         1. Lexer (Lexer)            │
                          │  characters → tokens                │
                          └──────────────┬──────────────────────┘
                                         │ tokens
                                         ▼
                          ┌─────────────────────────────────────┐
                          │         2. Parser (Parser)          │
                          │  tokens → AST (Abstract Syntax Tree)│
                          └──────────────┬──────────────────────┘
                                         │ raw AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │    3. Import Resolution             │
                          │  resolve_imports()                  │
                          │  inlines `use` references           │
                          └──────────────┬──────────────────────┘
                                         │ resolved AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │  4. Conditional Compilation         │
                          │  resolve_conditionals()             │
                          │  evaluates `if _PLATFORM == ...`    │
                          └──────────────┬──────────────────────┘
                                         │ target-specific AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │      5. Macro Expansion             │
                          │  expand_macros()                    │
                          │  expands `def : macro` blocks       │
                          └──────────────┬──────────────────────┘
                                         │ expanded AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │      6. Type Checker                │
                          │  TypeChecker.check()                │
                          │  validates types, reports warnings  │
                          └──────────────┬──────────────────────┘
                                         │ checked AST + warnings
                                         ▼
                          ┌─────────────────────────────────────┐
                          │      7. Low-Level Checker           │
                          │  LowLevelChecker.check()            │
                          │  validates memory layout, fields    │
                          └──────────────┬──────────────────────┘
                                         │ verified AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │      8. AST Optimization            │
                          │  optimize_ast()                     │
                          │  constant folding, DCE, CP, TRO...  │
                          └──────────────┬──────────────────────┘
                                         │ optimized AST
                                         ▼
                          ┌─────────────────────────────────────┐
                          │      9. Code Generation (CodeGen)   │
                          │  generate_code()                    │
                          │  AST → LLVM IR in-memory module     │
                          └──────────────┬──────────────────────┘
                                         │ LLVM IR module
                                         ▼
                          ┌─────────────────────────────────────┐
                          │    10. Alloca Hoisting              │
                          │  hoist_allocas()                    │
                          │  moves all alloca to entry block    │
                          └──────────────┬──────────────────────┘
                                         │ cleaned LLVM IR
                                         ▼
                          ┌─────────────────────────────────────┐
                          │   11. Re-parse & Verify             │
                          │  llvm.parse_assembly() + .verify()  │
                          │  ensures IR is well-formed          │
                          └──────────────┬──────────────────────┘
                                         │ verified module
                                         ▼
                          ┌─────────────────────────────────────┐
                          │   12. LLVM Optimization             │
                          │  optimize_module()                  │
                          │  runs LLVM pass pipeline (-O0..-O4) │
                          └──────────────┬──────────────────────┘
                                         │ optimized module
                                         ▼
                          ┌─────────────────────────────────────┐
                          │   13. Object Emission               │
                          │  TargetMachine.emit_object()        │
                          │  LLVM IR → .o machine code          │
                          └──────────────┬──────────────────────┘
                                         │ .o object file
                                         ▼
                          ┌─────────────────────────────────────┐
                          │   14. C Runtime Compilation         │
                          │  _get_runtime_stubs()               │
                          │  compiles gc.c + platform stubs     │
                          └──────────────┬──────────────────────┘
                                         │ runtime .o files
                                         ▼
                          ┌─────────────────────────────────────┐
                          │   15. Linking (gcc/clang)           │
                          │  _link_native()                     │
                          │  links .o + GC + libs → executable  │
                          └──────────────┬──────────────────────┘
                                         │
                                         ▼
                                   .exe / binary
```

### Stage Details

| # | Stage | Input | Output | File(s) | Description |
|---|-------|-------|--------|---------|-------------|
| 1 | **Lexer** | Source text (`str`) | Token list | `leash/lexer.py` | Breaks source into tokens: keywords (`fnc`, `def`, `if`, ...), operators, identifiers, literals, whitespace-tracking. |
| 2 | **Parser** | Token list | AST root node | `leash/parser_l.py` | Recursive-descent parser. Builds a typed AST with nodes for functions, classes, control flow, expressions, etc. |
| 3 | **Import Resolution** | AST | Expanded AST | `leash/cli.py` | Resolves `use module::Item;` statements by locating and parsing referenced modules, inlining their public definitions. |
| 4 | **Conditional Compilation** | AST | Filtered AST | `leash/cli.py` | Evaluates top-level `if _condition { ... }` blocks. Prunes branches for non-matching targets (e.g., Win32-only code on Linux). |
| 5 | **Macro Expansion** | AST | Expanded AST | `leash/cli.py` | Expands `defmacro` definitions in-place, substituting captured arguments into template bodies. |
| 6 | **Type Checker** | AST | Checked AST + warnings | `leash/typechecker.py` | Infers and validates types for all expressions, checks assignments, reports unused variables, unreachable code, redundant ops, etc. |
| 7 | **Low-Level Checker** | AST | Verified AST | `leash/cli.py` | Validates struct/class field layout, union tag consistency, and other low-level invariants. |
| 8 | **AST Optimization** | AST | Optimized AST | `leash/ast_optimize.py` | Semantics-preserving frontend passes: constant folding, dead branch elimination, unreachable code elimination, DCE, constant propagation, foreach unrolling, pushb fusion, tail recursion (`-O4`). |
| 9 | **Code Generation** | AST | LLVM IR module | `leash/codegen.py` | Walks the optimized AST and emits LLVM IR instructions, functions, globals, and debug metadata into an in-memory `llvmlite` module. Generates `main` wrapper for class-based entry points. |
| 10 | **Alloca Hoisting** | LLVM module | Cleaned module | `leash/hoist_allocas.py` | Moves all `alloca` instructions to the entry block of each function, improving LLVM's optimization opportunities. |
| 11 | **Re-parse & Verify** | IR string | Verified module | `llvmlite` | Serializes the module back to IR text, re-parses it, and calls `verify()` to catch any codegen-level issues. |
| 12 | **LLVM Optimization** | Module | Optimized module | `leash/optimize.py` | Runs the LLVM pass manager with function- and module-level passes controlled by `-O0` through `-O4` (and `-Os`/`-Oz`). |
| 13 | **Object Emission** | Module | `.o` file | `llvmlite` / TargetMachine | Lowers LLVM IR to native machine code for the target triple. |
| 14 | **Runtime Compilation** | `gc.c` + stubs | `.o` files | `leash/gc.c`, `windows_stubs.c`, `cross_compile_stubs.c` | Compiles the Boehm-style garbage collector and platform-specific stubs with the detected C compiler. Results are cached to avoid repeated recompilation. |
| 15 | **Linking** | `.o` files | executable | `gcc` / `clang` | Links the program object, GC runtime, `-l` libraries, and system libraries into the final native binary. Auto-detects missing system libraries by parsing linker errors. |

---

## Optimization Techniques in Depth

Beyond the `-O` flag, Leash leverages a deep, multi-layered optimization stack that spans the **frontend (Leash source code)**, **middle-end (LLVM IR)**, and **link-time (LTO)**. Each layer has a specific purpose and synergizes with the others to produce fast, compact binaries.

### Optimization Landscape

| Level | Stage | Scope | Latency Impact | Use Case |
|-------|-------|-------|----------------|----------|
| **Source-Level** | Leash → AST | Entire file | Compile time | Constant folding, inlining decisions |
| **Module-Level (IR)** | LLVM IR → LLVM IR | Single module | Compile time | Dead code, loops unrolling, vectorization |
| **Link-Time (LTO)** | Object → Native | Whole program | Link time | Dead stripping, IPO, devirtualization |

### 1. Constant Folding & Propagation
Replaces expressions with known constant results at compile time:

```leash
fnc main() : void {
    a: int = 2 + 3;        // folded to a = 5
    b: int = a * 4;        // folded to b = 20
    show(b);               // no runtime math
}
```

### 2. Dead Code Elimination (DCE)
Removes unused functions, variables, and basic blocks. In Leash, the compiler also detects:

- **Unreachable code** after `return`, `exit()`, or `throw`
- **Unreferenced global variables** (when possible)
- **Dead branches** of `if`/`switch` with constant conditions

### 3. Inline Expansion
Leash supports both automatic and explicit inlining. The `inline` keyword provides a strong hint:

```leash
inline fnc add(a int, b int) : int {
    return a + b;
}

fnc main() : void {
    show(add(10, 20));  // body may be inserted directly here
}
```

At `-O3`, the optimizer performs **aggressive inlining** of small functions even without the `inline` keyword.

### 4. Loop Optimizations

| Technique | Effect | Trigger |
|-----------|--------|---------|
| **Loop unrolling** | Reduces loop overhead, enables better scheduling | `-O3` |
| **Strength reduction** | Replaces expensive ops (mul, div) with adds/shifts | `-O3` |
| **Loop invariant code motion (LICM)** | Hoists invariant calculations out of the loop | `-O2+` |
| **Loop deletion** | Removes empty or side-effect-free loops | `-O2+` |

```leash
fnc sum(n int) : int {
    total: int = 0;
    // At -O3, this loop may be fully unrolled for small constant n
    for i: int = 0; i < n; i = i + 1 {
        total = total + i;
    }
    return total;
}
```

### 5. Scalar Replacement of Aggregates (SROA)
Breaks up `struct` / `class` stack allocations into individual scalar variables when possible, eliminating `alloca` and `memcpy` calls:

```leash
def Point : struct { x: int; y: int; };

fnc move(p Point) : Point {
    return Point { x: p.x + 1, y: p.y + 2 };
    // SROA may replace the struct with two scalar SSA values
}
```

### 6. Link-Time Optimization (LTO)
LTO enables whole-program analysis after linking, enabling optimizations across module boundaries:

```bash
# IR-based LTO (ThinLTO) – best balance of speed and optimization
python3 -m leash.cli compile program.lsh --opt 2 --lto thin

# Full LTO – maximum cross-module optimization at link time
python3 -m leash.cli compile program.lsh --opt 3 --lto full
```

#### LTO Benefits
- **Dead function elimination** across modules
- **Cross-module inlining** (e.g., library functions)
- **Devirtualization** of virtual calls when the target is known
- **Constant propagation** across translation units

### 7. Profile-Guided Optimization (PGO)
PGO uses runtime profiling data to optimize hot paths:

```bash
# Step 1: Compile with instrumentation
python3 -m leash.cli compile program.lsh --opt 2 --pgo-generate

# Step 2: Run to collect profile data
./program

# Step 3: Recompile with profile data
python3 -m leash.cli compile program.lsh --opt 3 --pgo-use
```

PGO enables:
- **Hot/cold code splitting** – frequently-executed code is packed together
- **Accurate branch prediction** – branches weighted by actual execution frequency
- **Inlining decisions** – functions in hot paths are inlined first
- **Loop unrolling** – unroll only hot loops based on trip counts

### 8. Auto-Vectorization
At `-O2` and above, the LLVM vectorizer automatically converts scalar loops into SIMD operations:

```leash
fnc dot_product(a float[1024], b float[1024]) : float {
    sum: float = 0.0;
    for i: int = 0; i < 1024; i = i + 1 {
        sum = sum + a[i] * b[i];
    }
    return sum;
    // Auto-vectorized to AVX2 or AVX-512 by LLVM at -O2+
}
```

Use `-O3` or the `-ffast-math` equivalent (future flag) to enable relaxed floating-point rules and broader vectorization opportunities.

### 9. Tail Call Optimization (TCO)
At `-O2` and above, the optimizer converts tail-recursive calls into simple `jmp` instructions, turning recursion into looping:

```leash
fnc factorial(n int, acc int = 1) : int {
    if n <= 1 { return acc; }
    return factorial(n - 1, n * acc);  // tail position
}
```

### 10. Memory Prefetching
For predictable memory access patterns, LLVM inserts prefetch hints at `-O3`, which can significantly improve cache performance for large array/vector workloads.

### 11. Defer Optimization
The `defer` statement is inlined during codegen, and the optimizer can eliminate deferred calls when it proves they have no side effects or when the path is unreachable.

### Putting It All Together

```bash
# Maximum optimization for production builds
python3 -m leash.cli compile program.lsh --opt 3 --lto thin

# Lowest binary size
python3 -m leash.cli compile program.lsh --opt s --lto full

# Development build with checks but no optimization
python3 -m leash.cli compile program.lsh --check --opt 0

# Profile-guided optimization workflow
python3 -m leash.cli compile program.lsh --opt 2 --pgo-generate
./program
python3 -m leash.cli compile program.lsh --opt 3 --pgo-use
```

## Checking for Errors

Leash provides thorough static analysis to catch errors and potential issues before your code runs.

### The `check` Command

Use the `check` command to analyze a file without compiling it. This runs the full type checker with enhanced safety analysis:

```bash
python3 -m leash.cli check program.lsh
```

The checker reports:
- **Type errors** — incompatible assignments, undefined variables, unknown types
- **Duplicate switch cases** — unreachable case blocks
- **Shadowed globals** — local variables that hide global ones
- **Missing switch defaults** — switch statements without a `default` block
- **Large stack arrays** — arrays over 10,000 elements that risk stack overflow
- **Self-assignments** — assignments like `x = x` that have no effect
- **Unused variables and parameters** — declared but never referenced
- **Empty blocks** — empty if/else/loop/case bodies
- **Unreachable code** — statements after `return`
- **Always-true/false conditions** — `if true` or `if false` branches
- **Redundant operations** — `+ 0`, `* 1`, `x == x`, etc.

### The `--check` Flag

You can also run the same thorough checks during compilation or execution by adding the `--check` flag:

```bash
# Check while compiling
python3 -m leash.cli compile program.lsh --check

# Check while running
python3 -m leash.cli run program.lsh --check
```

### Warnings as Errors

For strict builds (e.g., CI/CD), treat all warnings as errors:

```bash
python3 -m leash.cli compile program.lsh --warnings-as-errors
```

### Verbose Diagnostics (`--verbose` / `-vb`)

For in-depth educational guidance, append the `--verbose` or `-vb` flag to any Leash command. This prompts the compiler to print highly detailed, step-by-step masterclass explanations, diagnostics, and fully validated Leash code tips for any encountered errors or warnings.

```bash
# Get masterclass explanations during static analysis
python3 -m leash.cli check program.lsh --verbose

# Get masterclass explanations during compilation or execution
python3 -m leash.cli compile program.lsh --verbose
python3 -m leash.cli run program.lsh -vb
```

### Runtime Safety

Leash also embeds runtime safety checks into compiled programs:

- **Division by zero** — caught at runtime with a descriptive error message
- **Vector bounds checking** — `get()`, `set()`, `popb()`, `popf()` validate indices and empty vectors
- **Null file handle checks** — file operations on unopened or closed files produce clear errors
- **Union tag mismatch** — accessing the wrong union variant halts with a runtime error

All errors include error codes (e.g., `LEASH-E004`) for easy reference and suppression.

### Unsafe Functions

Leash provides an `unsafe` keyword that disables runtime safety checks and static safety warnings within a function. This is useful for low-level operations like bit manipulation, type punning through unions, or performance-critical code where you want to bypass the overhead of safety checks.

```leash
def Memory : union {
    i: int<64>;
    f: float<64>;
};

unsafe fnc fabs(x float<64>) : float<64> {
    mem: Memory = x;
    mem.i = mem.i & 0x7FFFFFFFFFFFFFFF;  // Clear sign bit
    return mem.f;
}

fnc main() : void {
    show(fabs(-5.0));   // 5.000000
    show(fabs(100.0));  // 100.000000
}
```

When a function is marked `unsafe`, the following safety checks are disabled:

- **Division by zero** — no runtime check is emitted
- **Union tag mismatch** — accessing inactive variants does not error
- **Null pointer dereference** — null checks are skipped
- **Vector bounds checking** — index validation is bypassed
- **Static division-by-zero** — compile-time warning is suppressed
- **Static array bounds** — compile-time index checks are suppressed
- **Negative array index** — compile-time check is suppressed

`unsafe` also works on class methods:

```leash
def pMath : class {
    static pub unsafe fnc fabs(x float<64>) : float<64> {
        mem: Memory = x;
        mem.i = mem.i & 0x7FFFFFFFFFFFFFFF;
        return mem.f;
    }
}
```

> **Warning:** Use `unsafe` only when you truly need to bypass safety checks. Unsafe code can cause undefined behavior, crashes, or memory corruption if used incorrectly.

### The `nogc` Modifier

Leash provides a `nogc` modifier for functions that need manual memory management instead of the garbage collector:

```leash
unsafe nogc fnc main : int {
    buf: *char = (*char)malloc(256);
    strcpy(buf, "Manual memory");
    printf("%s\n", buf);
    free(buf);
    return 0;
}
```

When a function is marked `nogc`:

- **GC init is skipped** — `leash_gc_init()` is not called for `nogc main()`, reducing startup overhead
- **C heap allocation** — `malloc`, `calloc`, `realloc`, and `free` call the standard C library directly
- **Low-level checker integration** — calling a `nogc` function from a GC-managed function produces a warning (use `unsafe` to suppress)
- **Combined with `unsafe`** — `unsafe nogc` is the typical pattern for low-level code

> **Note:** The `--no-garbage-collector` / `-ngc` CLI flag applies `nogc` globally to the entire program.

### Native Library Compilation

Leash can compile programs into shared (`.so`) or static (`.a`) libraries for use by other programs or languages:

| Output Type | Flag | Output File |
|-------------|------|-------------|
| Executable | `to <name>` | `<name>` |
| Dynamic Library | `to-dynamic <name>` | `lib<name>.so` |
| Static Library | `to-static <name>` | `lib<name>.a` |

```bash
# Compile as dynamic library
leash compile math.lsh to-dynamic math
# Creates: libmath.so

# Compile as static library
leash compile utils.lsh to-static utils
# Creates: libutils.a
```

## Compilation Targets

Leash supports multiple compilation targets, allowing you to compile your code for different platforms. Use the `--target` flag to specify the target architecture:

```bash
# Compile for your current system (default)
python3 -m leash.cli compile program.lsh

# Compile for Windows 64-bit (cross-compile)
python3 -m leash.cli compile program.lsh --target win64

# Compile for macOS (cross-compile)
python3 -m leash.cli compile program.lsh --target macos
```

### Native Targets (linux64, win64, macos)

The default target (`linux64`) compiles to a native Linux 64-bit executable using LLVM and GCC. Cross-compilation to Windows (`win64`) and macOS (`macos`) is also supported when the appropriate cross-compilation toolchains are installed.

Native targets produce standalone binaries with full access to the system's C library, garbage collector, and OS APIs.

## Native Library Imports (FFI)

Leash supports importing functions from native libraries (`.a` or `.so` files) using the `@from` directive. This enables Foreign Function Interface (FFI) with C, C++, Rust, and other languages that compile to static or shared libraries.

```leash
@from("mylib.so") {
    fnc add(a int, b int) : int;
    fnc multiply(a int, b int) : int;
};

fnc main() : void {
    show(add(10, 20));        // 30
    show(multiply(5, 4));     // 20
}
```

### How It Works

The `@from` directive tells the compiler:
1. To declare functions, variables, structs, unions, enums, and type aliases as external
2. To link against the specified library during compilation

You can declare functions, variables, structs, unions, enums, and type aliases from native libraries:

```leash
@from("mylib.so") {
    fnc add(a int, b int) : int;
    my_global: int;
    def Point : struct {
        x: int;
        y: int;
    };
    def ErrorCode : union {
        code: int;
        message: string;
    };
    def Color : enum { RED, GREEN, BLUE };
    def MyInt : type int;
};
```

You can use `.a` (static) or `.so` (dynamic) library files:

```leash
// Link against a static library
@from("libmath.a") {
    fnc sin(x float) : float;
    fnc cos(x float) : float;
    PI: float;
};

// Link against a shared library
@from("/usr/lib/libcurl.so") {
    fnc curl_init() : int;
    fnc curl_setopt(handle int, option int, value string) : int;
};
```

### Creating Native Libraries

To use C functions in Leash, compile them to a library first:

```c
// math_utils.c
int add(int a, int b) {
    return a + b;
}
```

```bash
# Compile to static library
gcc -c math_utils.c -o math_utils.o
ar rcs libmath.a math_utils.o

# Or compile to shared library
gcc -shared -fPIC math_utils.c -o libmath.so
```

### Limitations

- Type signatures must match exactly between Leash and the native library
- The library must be available at link time
- Initializers are not supported for variable declarations (e.g., `x: int = 10;` is not allowed)
- Only basic struct/union/enum fields are supported (no nested anonymous structs)

## Library Imports

Leash supports importing definitions from other modules using the `use` statement. The syntax is:

```leash
use module_name::ItemName;
```

You can import multiple items from the same module:

```leash
use hash::Hash;
use colors::Color;
```

Or import all public items from a module using the wildcard `*`:

```leash
use mylib::*;
```

### Private Imports

You can use `priv use` to import items from a module without re-exporting them. This is useful when creating library modules that depend on other libraries internally:

```leash
priv use mylib::*;  # Import all items, including private ones, but don't re-export them
pub fnc helper() : int {
    return lib_priv();  # Can use private items from mylib
}
```

### Nested Paths

If your libraries are organized in subdirectories, you can use nested paths:

```leash
use math::operations::add;
use utils::string_helpers::capitalize;
```

This looks for files at `math/operations.lsh` or `utils/string_helpers.lsh` in your search paths.

When you import a module, all its public definitions (structs, classes, enums, functions, templates, etc.) become available in your file. The compiler searches for modules in two places:

1. The **local directory** of the file containing the `use` statement (including subdirectories)
2. The **global libraries directory** (`~/.leash/libs/`) - this is where you can install libraries to be available from anywhere

### Example

Given a file `hash.lsh`:

```leash
priv def T1 : template;
priv def T2 : template;

def Hash : class<T1, T2> {
    priv names:  vec<T1>;
    priv values: vec<T2>;
    pub  size:   int;

    static pub fnc new() : Hash {
        return Hash {};
    }

    pub fnc add(name T1, value T2) : int { ... }
    pub fnc get(name T1) : T2 { ... }
}
```

You can use it in your program:

```leash
use hash::Hash;

fnc main() : void {
    h: Hash<string, string> = Hash.new();
    h.add("key", "value");
    show(h.get("key"));
}
```

## Program Termination

The built-in `exit(exit_code)` function terminates the program immediately with the given exit code. The argument must be an integer.

```leash
fnc main() : void {
    if error_condition {
        exit(1);
    }
    exit(0);
}
```

## Library Installation

Leash provides a global library installation system to make your libraries available from any project. Use the `install` command to copy library files or directories into the global libs directory (`~/.leash/libs/`).

### Installing a Single File

```bash
leash install path/to/mylib.lsh
```

This copies `mylib.lsh` to `~/.leash/libs/mylib.lsh`. After installation, any program can import it using `use mylib::*;` or `use mylib::SpecificItem;`.

### Installing a Directory

You can also install an entire directory of library files:

```bash
leash install path/to/mylib_folder/
```

The **contents** of the directory (files and subdirectories) will be copied directly into `~/.leash/libs/`. The top-level folder name is not preserved.

For example, if you have:

```
mylib_folder/
├── hash.lsh
└── utils/
    └── string_helpers.lsh
```

After installation, `~/.leash/libs/` will contain:

```
~/.leash/libs/
├── hash.lsh
└── utils/
    └── string_helpers.lsh
```

You can then import using nested paths:

```leash
use hash::Hash;
use utils::string_helpers::SomeFunction;
```

### Notes

- If a file or directory with the same name already exists in the global libs directory, the installation will fail. Remove the existing library first if you want to replace it.
- The `install` command only copies files; it does not compile them.
- Once installed, libraries are automatically available to all your Leash programs via the `use` statement.

---

## Standard Libraries

Leash ships with a set of standard libraries in the `installthis/` directory. Install them globally with `leash install installthis/` and then import via `use`.

### Types (`types.lsh`)

Type aliases for explicit bit-width types:

| Alias | Underlying Type |
|-------|----------------|
| `double` | `float<64>` |
| `float16` | `float<16>` |
| `float64` | `float<64>` |
| `float128` | `float<128>` |
| `uint8` | `uint<8>` |
| `uint16` | `uint<16>` |
| `uint64` | `uint<64>` |
| `uint128` | `uint<128>` |
| `uint256` | `uint<256>` |
| `uint512` | `uint<512>` |
| `int8` | `int<8>` |
| `int16` | `int<16>` |
| `int64` | `int<64>` |
| `int128` | `int<128>` |
| `int256` | `int<256>` |
| `int512` | `int<512>` |

### Tuple (`tuple.lsh`)

A generic `Tuple<T>` class for storing a collection of values.

| Method | Description |
|--------|-------------|
| `Tuple.new(vals T[])` | Create a new Tuple from an array |
| `.get(idx int)` | Get the element at index `idx` |
| `.isin(val T)` | Check if a value exists |
| `.size` | Number of elements |

```leash
use tuple::Tuple;

fnc main() {
    t: Tuple<int> = Tuple.new({1, 2, 3});
    show(t.get(1));   // 2
    show(t.isin(3));  // true
}
```

### Hot Reloader (`hotreload.lsh`)

`Reloader` watches a Leash source file for changes and automatically re-runs it.

```leash
use hotreload::Reloader;

fnc main() {
    r: Reloader = Reloader.new("main.lsh");
    r.start();
}
```

### Math Utilities (`utils/math.lsh`)

Constants: `PI`, `E`, `HALF_PI`, `LN10`, `LN2`

| Method | Description |
|--------|-------------|
| `.floor(x)` | Round down |
| `.ceil(x)` | Round up |
| `.fmod(x, y)` | Floating-point modulo |
| `.sqrt(x)` | Square root |
| `.exp(x)` | Exponential (e^x) |
| `.log(x)` | Natural logarithm |
| `.sin(x)` | Sine |
| `.cos(x)` | Cosine |
| `.tan(x)` | Tangent |
| `.pow(base, exp)` | Power |
| `.asin(x)` | Arc sine |
| `.acos(x)` | Arc cosine |
| `.atan(x)` | Arc tangent |
| `.sinh(x)` | Hyperbolic sine |
| `.cosh(x)` | Hyperbolic cosine |
| `.log10(x)` | Base-10 logarithm |
| `.log2(x)` | Base-2 logarithm |
| `.fabs(x)` | Absolute value (unsafe) |

```leash
use math::Math;

fnc main() {
    show(Math.sin(Math.PI / 2));  // 1.0
    show(Math.sqrt(144));         // 12.0
}
```

### 12. AST-Level Optimizations (Frontend)

Leash runs **semantics-preserving AST passes** before LLVM IR generation. These passes are always safe and never change program behavior.

| Pass | File | Effect |
|------|------|--------|
| **Constant folding & propagation** | `ast_optimize.py` | Replaces constant expressions with their results at compile time |
| **Dead branch elimination** | `ast_optimize.py` | Removes `if`/`else` branches with constant-false conditions |
| **Unreachable code elimination** | `ast_optimize.py` | Removes statements after `return`, `exit()`, or `throw` |
| **Dead code elimination** | `ast_optimize.py` | Removes unused top-level functions, globals, and type definitions |
| **Foreach small-loop unrolling hint** | `ast_optimize.py` | Annotates arrays with ≤8 elements for the LLVM loop unroller |
| **Pushb fusion (future)** | `ast_optimize.py` | Detects consecutive `.pushb()` calls on the same vector (reserved for batch-insertion expansion) |
| **Redundant store elimination (future)** | `ast_optimize.py` | Detects dead stores to vector/matrix elements that are immediately overwritten |
| **Size call caching** | `ast_optimize.py` | Annotates functions where `.size()` is called 3+ times on the same collection, enabling codegen to hoist the load |
| **Empty collection skip (future)** | `ast_optimize.py` | Detects `.popb()`, `.popf()`, `.remove()` on empty vectors for early-out codegen |

### 13. LLVM IR Codegen Optimizations

At the **codegen layer** (`codegen.py`), Leash emits optimal LLVM IR directly:

| Optimization | Details |
|-------------|---------|
| **`nuw`/`nsw` flags** | All vector/matrix index arithmetic (`add`, `sub`, `mul`) is emitted with `nuw` (no unsigned wrap) and `nsw` (no signed wrap) flags, enabling LLVM to fold, reorder, and eliminate redundant bounds checks |
| **`fast` flag** | All floating-point operations (`fadd`, `fsub`, `fmul`, `fdiv`, `fcmp`) on `float` and `double` are emitted with `fast` semantics, enabling reassociation, reciprocal-math, and contract fusion |
| **Branch weight metadata** | Foreach loop back-edges carry `set_weights([99, 1])` metadata, telling LLVM the loop body is hot (99:1 taken rate), which improves register allocation and code layout |
| **GC-tracked vector allocation** | Vector backing buffers use `leash_gc_malloc` (not `_aligned_alloc` directly), ensuring the GC sees vector data as reachable roots |

### 14. C Runtime Optimizations (`gc.c` / `gc.h`)

When `compile` or `run` finishes, the Leash binary is linked against a **pre-compiled C runtime** (`-O3`). The following optimizations live in the runtime:

| # | Optimization | Detail |
|---|-------------|--------|
| 1 | **Function-pointer matrix dispatch** | Element-wise binary ops on `float`, `double`, `int32`, `int64` use a single indirect call through a function-pointer table instead of switch-per-element, halving branch mispredictions |
| 2 | **4× loop unrolling** | All C-level matrix loops use explicit 4× unrolling with `__builtin_prefetch(data[i+8])` |
| 3 | **Thread pool** | Per-call thread creation is replaced by a **reusable thread pool** (`init_thread_pool`/`parallel_dispatch`) supporting both Win32 and POSIX threads. Pool is lazily initialized and persists for the program lifetime. Parallelism kicks in at ≥1024 elements. |
| 4 | **Cache-blocked operations** | `leash_matrix_blocked_op_float` / `_double` process tiles that fit in L1 cache (`block_size ≈ 64`), improving cache reuse on large matrices |
| 5 | **Vector batch utilities** | `leash_vec_bulk_copy`, `leash_vec_reverse`, `leash_vec_sort_i32`/`_i64`/`_f32`/`_f64` provide O(n log n) sorting and bulk operations directly in the runtime |
| 6 | **Thread-local allocation (TLAB)** | `leash_tlab_alloc` provides a bump-pointer arena per thread — no atomic CAS for hot allocations. Falls back to `leash_gc_malloc` when the TLAB is exhausted. |
| 7 | **GC bitmap** | `leash_gc_bitmap_init` stores a compact bitmask of allocated GC blocks, speeding up the mark phase by skipping free regions |
| 8 | **Fast memcpy** | `leash_fast_memcpy` (a thin wrapper around `memcpy` with __restrict__ hints) is used for all vector/matrix element moves |

### 15. LLVM Pass Pipeline Extras (`optimize.py`)

Beyond the standard `-O` passes, Leash explicitly enables several LLVM passes that are not always on by default:

| Pass | Purpose |
|------|---------|
| `loop_vectorize_pass` | Inner-loop vectorisation (SSE/AVX/NEON) |
| `slp_vectorize_pass` | Straight-line (basic-block) vectorisation |
| `loop_distribute_pass` | Split large loops into smaller ones to improve cache behaviour |
| `loop_versioning_pass` | Create fast/slow paths based on runtime checks (e.g. aliasing) |
| `loop_interchange_pass` | Swap loop nests to improve memory access patterns |
| `loop_predication_pass` | Hoist loop-invariant condition checks out of loops |
| `loop_unswitch_pass` | Duplicate loops to hoist invariant branches out |
| `called_value_propagation_pass` | Propagate known function-pointer targets through calls |
| `float2int_pass` | Convert floating-point ops to integer when safe |
| `speculative_execution_pass` | Speculatively execute likely paths to shorten critical paths |

### String Utilities (`utils/str.lsh`)

| Method | Description |
|--------|-------------|
| `.split(str, delimiter)` | Split by a character |
| `.splits(str, delimiters)` | Split by multiple characters |
| `.starts(a, b)` | Check prefix |
| `.ends(a, b)` | Check suffix |
| `.upper(str)` | Convert to uppercase |
| `.lower(str)` | Convert to lowercase |
| `.reversed(str)` | Reverse the string |
| `.digit(txt)` | Check if all digits |
| `.alpha(txt)` | Check if all alphabetic |
| `.alnum(txt)` | Check if alphanumeric |
| `.ident(txt)` | Check if valid identifier |

```leash
use str::Str;

fnc main() {
    parts: vec<string> = Str.split("hello world", ' ');
    show(Str.upper("hello"));  // "HELLO"
}
```

### Window Library (`utils::window`)

> **Note:** The low-level `lshraylib` binding (direct raylib FFI) is **unstable and buggy** on Leash — many raylib functions cause crashes, memory corruption, or undefined behavior when called through the FFI layer. It is only kept for backwards compatibility.

The `utils::window` library is a **new, in-development replacement** that wraps raylib in a Leash-native API using only stable, well-tested functions. It provides a safe, idiomatic Leash interface for windowing, rendering, and input.

**Usage:**
```leash
use utils::window::*;

wind: LshWindow;
pos: Vector2;
speed: int = 5;

fnc start() {
    pos = Vector2 { x: wind.width / 2, y: wind.height / 2 };
}

fnc update() {
    sizes := Vector2 { x: 50, y: 50 };
    Draw.draw_rect(pos, sizes, WHITE);

    if Input.key_down(KEY_W) {
        pos.y -= speed;
    } also Input.key_down(KEY_S) {
        pos.y += speed;
    }

    if Input.key_down(KEY_A) {
        pos.x -= speed;
    } also Input.key_down(KEY_D) {
        pos.x += speed;
    }

    result := check_border_rect(wind, pos, sizes);

    normalized := norm_rect(sizes);

    if result.x == -1 {
        pos.x = normalized.x;
    } also result.x == 1 {
        pos.x = wind.width - normalized.x;
    }

    if result.y == -1 {
        pos.y = normalized.y;
    } also result.y == 1 {
        pos.y = wind.height - normalized.y;
    }
}

fnc endd() {
    ignore;
}

fnc main {
    wind = create_window(800, 600, "My Game");
    close_key(KEY_ESCAPE);

    init_window(wind, &start, &update, &endd);
}
```

**Key Structures:**

| Type | Description |
|------|-------------|
| `LshWindow` | Window configuration (width, height, title, fps, background color) |
| `Vector2` | 2D vector with `x: float<32>`, `y: float<32>` |
| `Color` | RGBA color with `r`, `g`, `b`, `a: uint<8>` |

**Key Functions:**

| Function | Description |
|----------|-------------|
| `create_window(w, h, title, fps=60, bgc)` | Create a window config struct |
| `init_window(&window, &start, &update, &end)` | Run the window event loop |
| `close_key(key)` | Set the key that closes the window |
| `close_window()` | Close the window manually |
| `toggle_fullscreen()` | Toggle fullscreen mode |
| `set_title(&window, title)` | Change the window title |
| `norm_rect(sizes)` | Normalize a rect to its center point |
| `check_border_rect(window, pos, sizes)` | Check if a rect is colliding with the window border |

**Drawing API (`Draw` class):**

| Method | Description |
|--------|-------------|
| `Draw.draw_rect(pos, sizes, color)` | Draw a centered rectangle |
| `Draw.draw_circle(pos, radius, color)` | Draw a circle |
| `Draw.draw_line(start, end, color)` | Draw a line |
| `Draw.draw_pixel(pos, color)` | Draw a single pixel |
| `Draw.draw_ellipse(pos, radius, color)` | Draw an ellipse |
| `Draw.draw_ellipse_lines(pos, radius, color)` | Draw an ellipse outline |

**Input API (`Input` class):**

| Method | Description |
|--------|-------------|
| `Input.key_down(key)` | Is a key currently held down? |
| `Input.key_up(key)` | Is a key up? |
| `Input.key_pressed(key)` | Was the key just pressed this frame? |
| `Input.key_released(key)` | Was the key just released this frame? |

Pre-defined key constants are available: `KEY_W`, `KEY_A`, `KEY_S`, `KEY_D`, `KEY_SPACE`, `KEY_ESCAPE`, `KEY_ENTER`, `KEY_UP`, `KEY_DOWN`, `KEY_LEFT`, `KEY_RIGHT`, etc.

**Color macros:** `WHITE()`, `BLACK()`, `RED()`, `GREEN()`, `BLUE()`, `YELLOW()`, `ORANGE()`, `PURPLE()`, `RAYWHITE()`, `GRAY()`, and more.


## Leashed Package Manager

`leashed` is the official package manager for Leash. It lets you create, publish, share, and install Leash libraries through a central registry at `github.com/foksiny/leash-packages`. Every library lives in its own GitHub repository; the registry is an `index.json` that maps library names to repo URLs.

### Installation

`leashed` is installed alongside the Leash compiler:

```bash
pip install -e .
leashed --version
# leashed v0.1.0
```

### Creating a Package (`leashed init`)

Scaffold a new library project:

```bash
leashed init mylib
cd mylib
```

This creates:

```
mylib/
├── src/
│   └── main.lsh       # Library entry point
├── leash-pkg.lshc     # Package configuration
└── .gitignore
```

The generated `leash-pkg.lshc`:

```leashconfig
name: "mylib"
version: "0.1.0"
author: "your-github-username"
description: "The mylib library"
main: "src/main.lsh"
```

Edit these fields to match your library. If you already have a GitHub repo for your library, add a `repo` field:

```leashconfig
repo: "https://github.com/you/mylib.git"
```

### Publishing a Package (`leashed publish`)

From inside your project directory, publish your library:

```bash
leashed publish
```

This does the following automatically:

1. **Verifies** your source code with `leash check`
2. **Compiles** your library to a static `.lib` / `.a` file
3. If no `repo` is set in config, **creates a GitHub repo** under your account via `gh repo create`
4. **Pushes** the source and compiled library to your repo
5. **Registers** the library in the central registry:
   - If you're the registry owner — pushes directly to the `index.json`
   - If you're a contributor — forks the registry, updates the index, and **creates a pull request**

Requirements:
- [GitHub CLI (`gh`)](https://cli.github.com) installed and authenticated (`gh auth login`)
- Git installed

### Installing Libraries (`leashed install`)

Install a library globally so any Leash program can use it:

```bash
leashed install mylib
```

This:
1. Fetches the registry `index.json` from `raw.githubusercontent.com` (no clone needed)
2. Looks up the library name and finds its repo URL
3. Shallow-clones only the library's repository
4. Copies the files to `~/.leash/libs/<name>/`
5. Creates a stub `<name>.lsh` so `use mylib::*;` resolves correctly

After installation, import it in any Leash program:

```leash
use mylib::*;

fnc main() {
    show(greet("World"));
}
```

### Adding Libraries to a Project (`leashed add`)

Add a library as a dependency of the current project:

```bash
leashed add mylib
```

This:
1. Installs the library globally if not already installed
2. Appends `mylib@version` to the `dependencies` field in `leash-pkg.lshc`
3. Inserts `use mylib::*;` into your main source file

### Searching for Libraries (`leashed search`)

Search the registry for libraries:

```bash
leashed search math
```

This fetches the registry `index.json` and filters entries by name and description. No cloning required.

### How Publishing Works Under the Hood

Each library has its own GitHub repository. The central registry (`foksiny/leash-packages`) is just an `index.json` file:

```json
{
  "libraries": {
    "mylib": {
      "repo": "https://github.com/you/mylib.git",
      "description": "The mylib library",
      "author": "you",
      "version": "0.1.0"
    }
  }
}
```

**For the registry owner (`foksiny`)**: `leashed publish` pushes directly to the registry repo with `--force`, so re-publishing just overwrites the index entry.

**For everyone else**: `leashed publish` forks the registry, updates `index.json`, and creates a pull request. A maintainer reviews and merges it.

Security checks:
- **Package name validation** — only `[a-zA-Z_][a-zA-Z0-9_-]*` names are allowed
- **Source verification** — code must pass `leash check` before publishing
- **Publisher identity** — when updating an existing registry entry, only the original author can modify it
- **Compilation check** — the library must compile to a static library before it's published

## Defining Variables

In Leash, variables must declare their type upon initialization.

```leash
a: int = 10;
b: string = "Hello World";
```

### Auto-Type Inference (`:=`)

Leash supports automatic type inference using the `:=` operator. Instead of explicitly declaring the type, the compiler automatically infers the type from the value on the right-hand side:

```leash
fnc main() : void {
    a := 10;           // infers type 'int'
    b := 20;           // infers type 'int'
    name := "Leash";    // infers type 'string'
    
    show(a, " ", b, " ", name);  // 10 20 Leash
}
```

The `:=` syntax is syntactic sugar that enables the compiler to automatically determine the variable's type based on its initializer value. This is useful for:

- **Conciseness**: Less boilerplate when the type is obvious
- **Flexibility**: The type automatically matches the value's type
- **Readability**: Avoids repeating type names when they're clear from context

> **Note:** Using `:=` requires an initializer expression. The type cannot be inferred without a value.

### Default Initialization

If you declare a variable without an assignment, Leash automatically initializes it to a default value (zero for numbers, empty strings, empty vectors/arrays, and a heap-allocated instance for class types):

```leash
n: int;       // 0
s: string;    // ""
v: vec<int>;  // empty vector
p: Person;    // heap-allocated Person with default constructor
```

For class types, the compiler calls the default constructor (all parameters must have default values, or the class must have no constructor). If the constructor has required parameters, a compile-time error is raised.

## Immutable Variables

Leash supports immutable (constant) variables using the `imut` qualifier. Once assigned, an `imut` variable cannot be reassigned.

```leash
a: imut int = 10;
// a = 20; // ERROR: Cannot assign to immutable variable 'a'
show(a);
```

### Immutable Function Parameters

Function parameters can be marked `imut` to prevent modification inside the function body:

```leash
fnc add(a imut int, b imut int) : int {
    // a = 5; // ERROR: Cannot assign to immutable parameter 'a'
    return a + b;
}
```

### Infectious Immutability

Functions can return `imut` types. When an `imut` return value is assigned to a variable, that variable **automatically becomes immutable** — even if it wasn't originally declared as `imut`:

```leash
fnc imut_add(a imut int, b imut int) : imut int {
    return a + b;
}

fnc main() : void {
    c: int = imut_add(10, 20);
    // c = 50; // ERROR: 'c' is now immutable because it received an imut return value
}
```

This makes `imut` a powerful tool for enforcing data safety across function boundaries.

## Data Types

Leash supports rich generic and specific length types natively! 

### Primitive Types
- `int` and `uint`
- `float`
- `bool` (e.g., `true` / `false`)
- `string` and `char` (e.g., `'a'`)
- `void` (for null or empty values)

### Number Literals

Leash supports multiple number literal formats for integers and floats:

```leash
// Decimal (default)
a: int = 42;
b: float = 3.14;

// Hexadecimal (0x prefix)
c: int = 0xFF;          // 255
d: int = 0xDEADBEEF;    // 3735928559
e: float = 0x1.5p3;     // 10.5 (hex float with binary exponent)

// Binary (0b prefix)
f: int = 0b1010;        // 10
g: int = 0b11110000;    // 240

// Octal (0o prefix)
h: int = 0o755;         // 493
i: int = 0o17;          // 15

// Scientific notation
j: float = 1e10;        // 10000000000.0
k: float = 2.5E-3;      // 0.0025
l: float = .5;          // 0.5 (leading dot)
```

### Explicit Integer & Float Sizes
Leash integers and floats can specify an explicit bit width between `<` and `>` brackets to optimize memory and calculations. Leash supports arbitrary bit-widths for precise data representation:

- **Integers**: `int<1>` to `int<512>` (and `uint<1>` to `uint<512>`)
- **Floats**: `float<16>` to `float<128>`

```leash
flag: uint<1>;           // 1-bit integer (boolean-like)
half: float<16> = 1.0;   // 16-bit float
pixel: uint<8> = 255;    // 8-bit integer
bigInt: int<512>;        // 512-bit wide integer
```

Integers use native LLVM support for arbitrary bit-widths, while floats are intelligently mapped to the closest standard hardware format (e.g., 32-bit or 64-bit) for maximum compatibility and performance.

## Operators

Leash supports a full suite of arithmetic, comparison, bitwise, and logical operators.

### Arithmetic Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `+` | Addition | `a + b` |
| `-` | Subtraction | `a - b` |
| `*` | Multiplication | `a * b` |
| `/` | Division | `a / b` |
| `%` | Modulo | `a % b` |

### Compound Assignment Operators
| Operator | Description | Equivalent To |
|----------|-------------|---------------|
| `+=` | Add and assign | `a = a + b` |
| `-=` | Subtract and assign | `a = a - b` |
| `*=` | Multiply and assign | `a = a * b` |
| `/=` | Divide and assign | `a = a / b` |
| `%=` | Modulo and assign | `a = a % b` |
| `<<=` | Left shift and assign | `a = a << b` |
| `>>=` | Right shift and assign | `a = a >> b` |
| `&=` | Bitwise AND and assign | `a = a & b` |
| `|=` | Bitwise OR and assign | `a = a | b` |
| `^=` | Bitwise XOR and assign | `a = a ^ b` |

### Increment and Decrement Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `++` | Increment by 1 | `a++` or `++a` |
| `--` | Decrement by 1 | `a--` or `--a` |

Both prefix (`++a`) and postfix (`a++`) forms are supported. They are syntactic sugar that expands to `a = a + 1` or `a = a - 1`.

### Comparison Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `==` | Equal to | `a == b` |
| `!=` | Not equal to | `a != b` |
| `<` | Less than | `a < b` |
| `>` | Greater than | `a > b` |
| `<=` | Less than or equal | `a <= b` |
| `>=` | Greater than or equal | `a >= b` |
| `<>` | Is-in (checks if left operand exists in right array) | `val <> arr` |

### Is-In Operator (`<>`)

The `<>` operator checks if a value exists within an array. It returns `true` if the value is found, `false` otherwise:

```leash
fnc main() : void {
    nums: int[5] = {1, 2, 3, 4, 5};

    if 3 <> nums {
        show("3 is in the array");
    }

    if 10 <> nums == false {
        show("10 is not in the array");
    }

    // Works with strings too
    words: string[3] = {"hello", "world", "leash"};
    if "world" <> words {
        show("Found 'world'!");
    }
}
```

For vectors, use the `.isin()` method instead (see [Vectors](#vectors)).

### `is` and `isnt` Operators

Leash supports `is` and `isnt` operators for checking both **types** and **values**. These operators provide a powerful way to perform runtime type checks and value comparisons.

#### Type Checking

Use `is` to check if a variable is of a specific type at runtime:

```leash
fnc main() : void {
    a: int = 10;
    p: Person = Person { name: "John Doe", age: 23 };

    if a is int {
        show("It's int!");
    }

    if p is Person {
        show(p.name, " ", p.age);
    }
}
```

The `isnt` operator performs the negated type check:

```leash
if myvar isnt string {
    show("myvar is NOT a string");
}
```

#### Value Comparisons

Both `is` and `isnt` also work with **values**, including:

- **Primitive values**: integers, floats, booleans, characters
- **Strings**: compared by content (not pointer)
- **Arrays**: deep comparison of array contents
- **Vectors**: deep comparison of vector elements

```leash
fnc main() : void {
    mylist: int[5] = {1, 2, 3, 4, 5};

    // Compare with array literal
    if mylist is {1, 2, 3, 4, 5} {
        foreach i, v in<array> mylist {
            show("ARRAY ", i, ": ", v);
        }
    }

    // Compare with another variable
    mv2: vec<int> = (vec<int>){1, 2, 3, 4, 5};
    myvec: vec<int> = (vec<int>){1, 2, 3, 4, 5};

    if myvec is mv2 {
        foreach i, v in<vector> myvec {
            show("VEC ", i, ": ", v);
        }
    }

    // Use 'isnt' for negated comparisons
    if mylist isnt {1, 2, 3, 4, 5, 6, 7, 8, 9, 10} {
        show("mylist isn't '{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}'");
    }
}
```

#### Supported Comparisons

| Type | `is`/`isnt` Support | Notes |
|------|----------------------|-------|
| **Primitive types** (`int`, `float`, `bool`, `char`) | ✓ | Type checks and value comparisons |
| **Strings** (`string`) | ✓ | Deep comparison (content-based) |
| **Arrays** (`type[size]`) | ✓ | Deep comparison of all elements |
| **Vectors** (`vec<T>`) | ✓ | Deep comparison of all elements |
| **Structs** | ✓ (type only) | Type checking only |
| **Unions** | ✓ (type only) | Type checking only |
| **Enums** | ✓ (type only) | Type checking only |
| **Classes** | ✓ (type only) | Type checking with inheritance support |

> **Note:** Value comparisons for arrays and vectors perform **deep comparison** (checking each element), not just pointer equality. This makes `is` very powerful for comparing complex data structures.

### Bitwise Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `&` | Bitwise AND | `a & b` |
| `\|` | Bitwise OR | `a \| b` |
| `^` | Bitwise XOR | `a ^ b` |
| `~` | Bitwise NOT | `~a` |
| `<<` | Left shift | `a << b` |
| `>>` | Right shift | `a >> b` |

### Logical Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `&&` | Logical AND | `a && b` |
| `\|\|` | Logical OR | `a \|\| b` |
| `!` | Logical NOT | `!a` |

*Note: Logical `&&` and `||` use short-circuit evaluation — the right operand is only evaluated if necessary.*

### Ternary Operator

Leash supports the ternary conditional operator `? :` for concise conditional expressions:

```leash
fnc main() : void {
    a: int = 10 == 10 ? 10 : 20;
    show(a); // 10

    b: string = "Hello" != "World!" ? "Hello" : "World!";
    show(b); // "Hello"
}
```

The ternary operator evaluates the condition and returns the value of the matching branch. Both branches must have compatible types.

```leash
fnc main() : void {
    x: int = 5;
    result: string = x > 3 ? "big" : "small";
    show(result); // "big"
}
```

```leash
fnc main() : void {
    a: int = 5;
    b: int = 3;

    show(a + b);    // 8
    show(a % b);    // 2
    show(a & b);    // 1
    show(a << 1);   // 10
    show(a > b);    // 1 (true)
    show(!0);       // 1 (true)
}
```

## Functions

Functions are defined with the `fnc` keyword, followed by the argument list and its evaluated return type. The entrypoint to any compiled leash application is `main() : void`.

```leash
fnc add(a int, b int) : int {
    return a + b;
}

// One-line function using the pipe operator (|>)
fnc multiply(a int, b int) : int |> return a * b;

// Functions without a return type default to 'void'
fnc greet(name string) |> show("Hello, ", name);

fnc main() : void {
    result: int = add(10, 20);
    show("Result: ", result);
    greet("Leash");
}
```

### Optional Parentheses

When a function takes no arguments, the parentheses `()` can be omitted in both definitions and calls:

```leash
// Definition without parentheses
fnc greet {
    show("Hello!");
}

fnc getValue : int {
    return 42;
}

fnc main {
    // Call without parentheses
    greet;

    // Also works with return values
    x: int = getValue;
    show(x);

    // Parentheses are still valid too
    show(getValue());
}
```

### One-Line Functions

For simple, single-statement functions, you can use the `|>` (pipe) operator instead of curly braces `{ }`. 

```leash
fnc square(x int) : int |> return x * x;
```

If a function has no return type specified, it defaults to `void`:

```leash
fnc log(msg string) |> show("[LOG]: ", msg);
```

### Default Arguments

Leash supports default argument values in functions. Parameters with default values are optional at the call site — if not provided, the default value is used.

```leash
fnc add(a int, b int, c int = 0) : int {
    return a + b + c;
}

fnc main() : void {
    show(add(10, 20));       // 30 (uses c=0)
    show(add(10, 20, 30));   // 60
}
```

### Named Arguments

When calling a function, you can specify arguments by name using the `name=value` syntax. This is especially useful when:
- A function has many parameters with default values
- You want to skip some optional parameters while specifying others

```leash
fnc math(a int = 1, b int = 2, typ int = 0) : int {
    if typ == 0 { return a + b; }
    if typ == 1 { return a - b; }
    if typ == 2 { return a * b; }
    if typ == 3 { return a / b; }
    return 0;
}

fnc main() : void {
    show(math());                 // 3 (all defaults)
    show(math(10, 20));         // 30 (a=10, b=20)
    show(math(b=20, typ=2));    // 40 (a=1 default, b=20, typ=2)
    show(math(20, 10, 1));      // 10 (positional)
}
```

You can mix positional and named arguments. Named arguments are also supported for parameters without default values.

### Rule: Required Arguments First

Arguments without default values must come **before** arguments with default values:

```leash
// Valid
fnc valid(a int, b int = 1, c int = 2) : int { ... }

// Invalid - will cause a compile error
fnc invalid(a int = 1, b int) : int { ... }
```

### Command Line Arguments

To accept command line arguments, you can declare `main` with an `args string[]` parameter:

```leash
fnc main(args string[]) : void {
    show("Received ", args.size, " arguments!");
    foreach i, arg in<array> args {
        show(i, ": ", arg);
    }
}
```

*Note: The `show()` function is built-in and makes printing multiple values to console very easy!*

#### The `end` Parameter

By default, `show()` appends a newline (`\n`) after printing all arguments. You can override this with the `end` keyword argument:

```leash
fnc main() : void {
    show("Hello", end="");      // No trailing newline
    show("World", end=", ");    // Trailing comma+space instead of newline
    show("Done");               // Still appends a newline by default
}
// Output: HelloWorld, Done
```

This is especially useful for building output incrementally without unwanted newlines.

### Buffered Output (`showb`)

Leash also provides a `showb()` (show buffer) function for more precise control over console output. Unlike `show()`, which automatically adds a newline and spaces between arguments, `showb()` prints its arguments exactly as they are, without any automatic separators or trailing newlines.

```leash
fnc main() : void {
    showb("Hello ");
    showb("World");
    showb("!\n"); // Newline must be added manually
}
```

`showb()` is particularly powerful when working with vectors and nested vectors, as it can "unpack" and print their contents recursively in a compact format:

```leash
fnc main() : void {
    grid: vec<vec<uint<1>>>;
    grid.pushb((vec<uint<1>>){1, 0, 1});
    grid.pushb((vec<uint<1>>){0, 1, 0});

    foreach _, row in<vector> grid {
        showb(row);   // Prints all elements of the inner vector
        showb("\n");  // Manual newline for each row
    }
}
// Output:
// 101
// 010
```

### Inline Functions

Functions can be marked with the `inline` keyword to suggest the compiler should inline them (insert the function body at the call site instead of generating a function call). This can improve performance for small, frequently-called functions.

```leash
inline fnc add(a int, b int) : int {
    return a + b;
}

fnc main() : void {
    show(add(10, 20));  // Inlined at compile time
    
    r: int = add(20, 30);
    show(r);
}
```

### Deferred Execution

The `defer` keyword schedules a function call to be executed automatically when the current scope exits. This is useful for resource cleanup (like closing files) and ensures cleanup happens even if the function returns early or throws an error.

```leash
fnc read_file(fname string) : string {
    f: File = File.open(fname, "r");
    
    defer f.close();  // Called when read_file returns
    
    result: string = f.read();
    return result;
}

fnc main() : void {
    content: string = read_file("data.txt");
    show(content);
}
```

Deferred calls are executed in **reverse order** (last defer runs first) when the scope exits, which is the standard behavior for defer statements.

### Lambdas

Leash supports anonymous functions (lambdas) that can be assigned to variables and passed around:

```leash
fnc main() : void {
    add: fnc(int, int) : int = fnc(a int, b int) : int {
        return a + b;
    };
    
    sub: fnc(int, int) : int = fnc(a int, b int) : int {
        return a - b;
    };
    
    show(add(1, 2));  // 3
    show(sub(2, 1));  // 1
}
```

Lambda type syntax is `fnc(param_types) : return_type`. Lambdas can be stored in variables of function pointer type and called like regular functions.

### Nested Functions

Leash supports defining functions inside other functions. Nested functions work like regular functions — they can accept parameters, return values, and call other functions — but are scoped to the enclosing function. They are useful for organizing helper logic without polluting the global namespace.

```leash
fnc main() : void {
    fnc add(a int, b int) : int {
        return a + b;
    }

    result: int = add(10, 20);
    show("10 + 20 = ", result);
}
```

Nested functions use the same `fnc` syntax as global functions and are called by their simple name. They can access their own parameters and global variables, but do not capture variables from the enclosing scope (no closure semantics).

## Operator Definitions (`opdef`)

Operator Definitions let you extend existing types with new methods and overload operators. They are defined using the `opdef` keyword and work with built-in types (`string`, `vec`, etc.) and user-defined types (classes, structs) alike.

### Extension Methods

```leash
opdef string.join(a string, b string) : string {
    return a + b;
}

opdef string.repeat(s string, n int) : string {
    result: string = "";
    for i: int = 0; i < n; i = i + 1 {
        result = result + s;
    }
    return result;
}

fnc main() {
    show("Hello, ".join("world!"));  // Hello, world!
    show("Hey".repeat(3));           // HeyHeyHey
}
```

### Operator Overloads

For generic types like `vec<T>`, use `thisop.typ` to refer to the inner type parameter in the body:

```leash
opdef vec+(vec1 vec, vec2 vec) : vec {
    result: vec<thisop.typ>;
    foreach _, v in<vector> vec1 {
        result.pushb(v);
    }
    foreach _, v in<vector> vec2 {
        result.pushb(v);
    }
    return result;
}

fnc main() {
    vec1: vec<int> = (vec<int>){1, 2, 3};
    vec2: vec<int> = (vec<int>){4, 5, 6};
    result: vec<int> = vec1 + vec2;  // (5, 7, 9) — correctly typed per element
}
```

### Syntax

```
opdef <type>.<method>(<args>) : <return_type> { <body> }
opdef <type><operator>(<args>) : <return_type> { <body> }
```

- `<type>` — the type being extended (`string`, `vec`, or a user-defined type)
- `<method>` — name for an extension method call (e.g., `.join(...)`)
- `<operator>` — symbol for an operator overload (`+`, `-`, `*`, `/`, `%`, etc.)
- `thisop.typ` — resolves to the inner type parameter of a generic type (e.g., inside a `vec` opdef, `thisop.typ` is `int` when the operand is `vec<int>`)

## Global Variables

Leash supports module-level variable declarations. These variables are accessible from any function within the same module. By default, top-level variables are public. You can also explicitly mark them as `pub` or `priv`.

```leash
// Public global variable (default)
a: int = 10;

// Explicitly public
pub b: string = "hello";

// Private global variable (only accessible within this module)
priv secret: int = 42;

fnc main() : void {
    show(a);      // 10
    show(b);      // hello
    show(secret); // 42 (if in same module)
}
```

Global variables can be of any type, including structs, classes, vectors, arrays, unions, and type aliases. They can also have initializers, which are evaluated at program startup (before `main` runs). If a global variable has no initializer, it is zero-initialized by default.

### Visibility

- `pub`: The variable is publicly accessible from other modules (future feature). Currently, all modules are compiled together, so this has no effect but is allowed for forward compatibility.
- `priv`: The variable is only accessible within the defining module. This is useful for hiding implementation details when modules are introduced.

Note: The `pub`/`priv` modifiers are optional; omitting them defaults to `pub`.

## Control Flow

### Multi-Line Comments

Leash supports multi-line comments using `/* ... */`:

```leash
fnc main() : void {
    /* This is a multi-line comment
       that spans multiple lines
       and can contain any content */
    show("Hello");
}
```

### Branching
Use `if`, `also` (acts like `else if`), and `else`:

```leash
if a < b {
    show("a is less than b");
} also a > b {
    show("a is greater than b");
} else {
    show("a is equal to b");
}
```

#### The `unless` Keyword

Leash supports `unless`, which is like `if` but inverts the condition (executes the then-block when the condition is **false** instead of **true**):

```leash
unless some_error_occurred {
    show("All is well");
} else {
    show("Error happened");
}
```

You can also use `alsou` with `unless`:

```leash
unless result == nil {
    show("Got a result");
} alsou is_pending {
    show("Still waiting...");
} else {
    show("No result available");
}
```

- `unless` - executes then-block when condition is **false**
- `alsou` - like `also` but inverts that branch's condition

### Loops

Leash comes packed with many loops built-in (`for`, `while`, `do-while`, and `loop`):

```leash
// while
i: int = 0;
while i < 10 {
    i = i + 1;
}

// for
for j: int = 0; j < 10; j = j + 1 {
    show(j);
}

// do-while
k: int = 0;
do {
    k = k + 1;
} while k < 10;

#### Infinite Loop (`loop`)

Leash provides an infinite `loop` keyword that runs forever until `stop` is used:

```leash
fnc main() : void {
    i: int = 1;

    loop {
        show(i); // outputs: 1, 2, 3, 4, ...
        i = i + 1;

        if i > 10 {
            stop; // exit the loop when i is greater than 10
        }
    }
}
```

The `loop` keyword creates an infinite loop that runs until a `stop` statement exits it. It's useful when you need a loop with a condition that's checked in the middle or at the end of the loop body rather than at the beginning.

#### `stop` and `continue`

Inside loops, you can use `stop` to exit the loop early (similar to `break` in other languages) and `continue` to skip to the next iteration immediately.

```leash
fnc main() : void {
    i: int = 0;
    while true {
        show(i);
        if i >= 10 {
            stop; // exit the loop
        }
        continue; // skip the rest of this iteration and jump to condition/update
        show("This will not be printed.");
    }
}
```

- `stop;` terminates the innermost loop and transfers control to the code after the loop.
- `continue;` skips the remaining statements in the current iteration and jumps to the loop's continuation point (the condition check for `while`/`do-while`, or the update step for `for`).

Both `stop` and `continue` can be used in `while`, `for`, `do-while`, `loop`, and `foreach` loops (including `foreach` over arrays, strings, and vectors). They are not supported in `foreach` over structs because that loop is unrolled at compile time.

#### `empty`

The `empty` statement is a no-op that does nothing. It can be used anywhere a statement is expected, such as in if bodies, loops, or as a placeholder:

```leash
fnc example() {
    if some_condition {
        empty; // does nothing, but explicitly shows intent
    }
    while true {
        empty; // placeholder for future implementation
    }
}
```

#### `ignore`

The `ignore` statement immediately exits the current function and returns the default value for the function's return type. In `void` functions, it returns nothing. In functions with a return type, it returns zero (for numeric types), null (for pointers), or an empty value.

```leash
fnc earlyExit() {
    if some_error_condition {
        ignore; // exit immediately, returning default value
    }
    // This code is skipped if ignore was executed
    show("This won't show");
}

fnc getValue() int {
    if not_ready {
        ignore; // returns 0 for int
    }
    return 42;
}
```

Note: `ignore` executes any deferred calls (`defer`) before returning, just like a regular `return` statement.

### Switch-Case

Leash supports `switch-case` statements for multi-way branching. Unlike C-style switch statements, each case block is wrapped in `{ }` braces, so there is no fall-through — each case automatically breaks after its body executes.

```leash
def Color : enum {
    RED,
    GREEN,
    BLUE
};

fnc main() : void {
    c: Color = Color::RED;

    switch c {
        case Color::RED {
            show("it's RED!");
        } case Color::GREEN {
            show("it's GREEN!");
        } case Color::BLUE {
            show("it's BLUE!");
        } default {
            show("it's something else!");
        }
    }
}
```

Switch cases support any type, including:
- Integers (`int`, `uint`, `int<64>`, etc.)
- Floats (`float`, `float<32>`, etc.)
- Booleans (`bool`)
- Strings (`string`)
- Enums

The `default` block is optional and handles any value that doesn't match a case.

```leash
fnc main() : void {
    x: int = 42;

    switch x {
        case 1 {
            show("one");
        } case 2 {
            show("two");
        } default {
            show("not one or two");
        }
    }
}
```

### The `self` Keyword

The `self` keyword is a special expression that evaluates to the name of the current code context as a `string`. It is highly useful for logging, debugging, and generic error messages.

#### Contextual Behavior

| Context | `self` Value | Example |
|---------|--------------|---------|
| **Function** | Function name | `fnc add(a, b) { show(self); }` prints `"add"` |
| **Class Method** | Method name | `pub fnc greet() { show(self); }` prints `"greet"` |
| **Lambda** | Literal string | `show(self);` prints `"<lambda>"` |
| **Error Def** | Error name | `error MyErr() -> self;` returns `"MyErr"` |
| **Class** | Class name | `def User : class { n: string = self; }` sets `n` to `"User"` |

#### Advanced Metadata (`::` syntax)

When used inside a class or method, `self` supports static member access to retrieve related class names:

- `self::Class`: Always returns the name of the current class.
- `self::Parent`: Returns the name of the parent class (available only in subclasses).

```leash
def Animal : class {
    pub fnc identify() {
        show("Context: ", self);       // "identify"
        show("Class:   ", self::Class); // "Animal"
    }
}

def Dog : class(Animal) {
    pub fnc info() {
        show("Method: ", self);         // "info"
        show("Class:  ", self::Class);   // "Dog"
        show("Parent: ", self::Parent); // "Animal"
    }
}
```

#### Why use `self`?
Instead of hardcoding names in strings (which can break when you rename a function), `self` ensures your logs and error messages always stay in sync with your code:

```leash
error DatabaseError(msg string) -> "[" + self + "]: " + msg;

fnc connect() {
    if failed {
        throw DatabaseError("Connection timeout"); // "[DatabaseError]: Connection timeout"
    }
}
```

## Input Handling

Leash provides two built-in functions for reading user input from the console.

### Reading a Line (`get()`)

The `get()` function reads an entire line of text (until Enter is pressed) and returns it as a `string`.

```leash
fnc main() : void {
    // You can provide an optional string prompt
    name: string = get("What is your name? ");
    show("Hello, ", name, "!");

    // Or call it without arguments
    show("Enter something else: ");
    val: string = get();
    show("You entered: ", val);
}
```

The `get()` function automatically allocates memory for the input string and ensures it is managed properly, so you don't have to worry about buffer overflows or manual `free()` calls.

### Reading a Single Key (`keyget()`)

The `keyget()` function reads a single key press **immediately** without waiting for the Enter key. It returns the pressed key as a `char`.

```leash
fnc main() : void {
    show("Press any key to continue...", end="");
    key: char = keyget();

    show("Key pressed: '", key, "'");
}
```

This is useful for:
- **Menu-driven applications** where users press a single key to select an option
- **Game controls** requiring immediate input without pressing Enter
- **Prompt-and-continue** patterns ("Press any key to continue...")

Like `get()`, `keyget()` works on all supported platforms (Windows, Linux, macOS).

## Random Numbers

Leash provides built-in functions for generating random numbers and making random choices:

- `rand(min, max)` - Returns a random integer between `min` and `max` (inclusive)
- `randf(min, max)` - Returns a random floating-point number between `min` and `max`
- `seed(value)` - Sets the random number generator seed. If not called explicitly, the RNG is automatically seeded with the current time.
- `choose(str1, str2, ...)` - Randomly selects and returns one of the provided string arguments

Example:

```leash
fnc main() : void {
    seed(42);  // Optional: set a specific seed for deterministic results

    // Random integer between -10 and 10
    num: int = rand(-10, 10);

    // Random float between 0 and 2
    f: float = randf(0, 2);

    // Randomly choose a name
    name: string = choose("Alice", "Bob", "Charlie");

    show(name, " got ", num, " and ", f);
}
```

## Time and Delays

Leash includes functions for measuring elapsed time and introducing delays:

- `wait(seconds)` - Pauses program execution for the specified number of seconds (can be a float for sub-second precision)
- `timepass()` - Returns the elapsed time in seconds (as a float) since the program started

Example:

```leash
fnc main() : void {
    show("Starting...");
    wait(1.5);  // Wait 1.5 seconds

    elapsed: float = timepass();
    show("Elapsed time: ", elapsed, " seconds");
}
```

## Built-in Compile-Time Variables

Leash provides special built-in variables that are automatically available in every program:

- `_FILEPATH` - The full path to the current source file being compiled
- `_FILENAME` - The name of the current source file (without path)
- `_PLATFORM` - The compilation target platform (e.g., `"linux64"`, `"win64"`, `"macos"`, `"macos-arm"`)

```leash
fnc main() : void {
    show("File path: ", _FILEPATH);
    show("File name: ", _FILENAME);
    show("Platform: ", _PLATFORM);
}
```

This is useful for debugging, logging, conditional compilation based on platform, or including file information in your program's output.

## Conditional Compilation (Top-Level If)

Leash supports conditional compilation using `if` statements at the top-level (outside any function). This allows you to include or exclude code based on compile-time conditions, such as the target platform.

### How It Works

When the compiler encounters a top-level `if`, it evaluates the condition at **compile time**. Only the branch whose condition evaluates to `true` is included in the final program. The other branches are completely ignored (no code is generated, and they are not type-checked).

This is particularly useful for platform-specific code:

```leash
if _PLATFORM == "linux64" {
    fnc main() : void {
        show("Running on Linux");
        // Linux-specific code here
    }
} also _PLATFORM == "win64" {
    fnc main() : void {
        show("Running on Windows");
        // Windows-specific code here
    }
} else {
    fnc main() : void {
        show("Running on an unsupported platform");
    }
}
```

The `also` keyword acts like `else if`, allowing you to chain multiple conditions. The `else` branch is optional and runs if none of the previous conditions were true.

### Supported Compile-Time Expressions

The condition can use:

- **Builtin variables**: `_PLATFORM`, `_FILEPATH`, `_FILENAME`
- **String literals**: `"linux64"`, `"win64"`, `"macos"`, `"macos-arm"`, etc.
- **Boolean literals**: `true`, `false`
- **Comparison operators**: `==`, `!=`
- **Logical operators**: `&&` (AND), `||` (OR), `!` (NOT)

Examples:

```leash
// Simple equality check
if _PLATFORM == "linux64" {
    // Linux-specific code
}

// Combining conditions
if _PLATFORM == "linux64" || _PLATFORM == "macos" {
    // Unix-like specific code
}

// Negation
if _PLATFORM != "win64" {
    // Non-Windows code
}
```

### Nesting

Top-level `if` statements can be nested inside each other, allowing complex conditional compilation:

```leash
if _PLATFORM == "linux64" {
    if true {  // constant foldable
        fnc main() : void {
            show("Nested condition");
        }
    }
}
```

### Limitations

- **Compile-time evaluation only**: The condition must be evaluable at compile time. It cannot use runtime variables or function calls.
- **Branch items must be valid top-level items**: Inside each branch, you can define structs, classes, functions, global variables, etc., just like at the normal top level.
- **No `else if`**: The syntax uses `also` instead of `else if`.

### Notes

- The condition is evaluated using the target platform you specify with `--target`. For example, if you compile with `--target win64`, then `_PLATFORM` evaluates to `"win64"`.
- This feature is similar to C/C++ preprocessor `#if` but happens at the AST level, so the unused branches are never type-checked or codegen'd.

## Executing Shell Commands

Leash provides the built-in `exec()` function to execute shell commands and capture their output. It accepts a command string and an optional mode.

```leash
fnc main() : void {
    // Execute a command - returns the output as a string
    result: string = exec("echo Hello World", nil);
    show(result);  // Prints: Hello World

    // Different modes:
    // nil - Execute command and return output (prints to stdout)
    // "wait" - Wait for command to finish and return output
    // "silent" - Execute command, suppress output, return empty string
    // "code" - Return the exit code as a string (no output)
}
```

### Modes

| Mode | Description | Returns |
|------|-------------|---------|
| `nil` | Execute command, return output (default) | Command output string |
| `"wait"` | Wait for command to finish, return output | Command output string |
| `"silent"` | Execute command, suppress all output | Empty string `""` |
| `"code"` | Return the exit code of the command | Exit code as string |

### Examples

```leash
fnc main() : void {
    // nil mode - captures output
    out: string = exec("ls -la", nil);
    show(out);

    // silent mode - runs command without showing output
    exec("echo This is hidden", "silent");

    // code mode - get exit code
    code: string = exec("ls /nonexistent", "code");
    show("Exit code: ", code);  // Prints: Exit code: 2
}
```

## Arrays

Data is sequentially packed into memory and can be evaluated or constructed easily:
```leash
// Construct inline lists with fixed size
a: int<64>[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};

// Query the size of an array dynamically
len: int = a.size;

// Iterate across items automatically using `in<array>`
foreach index, value in<array> a {
    show(index, ": ", value);
}
```

### Dynamic Array Sizes

Leash supports using variables, member access, and even math expressions to specify array sizes at runtime:

```leash
fnc main() : void {
    // Using a variable
    n: int = 5;
    arr1: int[n] = {1, 2, 3, 4, 5};

    // Using member access (e.g., string size)
    str: string = "Hello";
    chars: char[str.size] = cstr(str);

    // Using math expressions
    x: int = 2;
    y: int = 3;
    arr2: int[x + y + 1] = {1, 2, 3, 4, 5, 6};

    // Even using function calls
    arr3: int[getSize()] = {1, 2, 3};
}
```

All arrays in Leash are stored as runtime-sized structures (`{ i64 size, ptr data }`), so the size is evaluated at runtime and the array can hold any number of elements up to that size.

## Vectors

Vectors are dynamic arrays that can grow or shrink in size. They are declared using the `vec<T>` syntax.

```leash
v: vec<string>;
v.pushb("first");  // push to back
v.pushb("second");
v.pushf("start");  // push to front

show("Size: ", v.size);   // .size is a property (no parentheses)
show("First: ", v.get(0));

v.set(1, "middle");
v.insert(1, "extra");

foreach i, s in<vector> v {
    show(i, ": ", s);
}

v.clear();
```

### Expanding Vectors

You can use `.extend()` to append an entire array or slice to a vector, or `.extendv()` to append another vector:

```leash
v: vec<int>;
v.pushb(1);

// Extend with an array literal or slice
v.extend({2, 3, 4});

other: vec<int>;
other.pushb(5);
other.pushb(6);

// Extend with another vector
v.extendv(other);

show(v.size); // 6
```

### Inserting Vectors and Arrays

You can use `.insertv()` to insert all elements of another vector at a given position, or `.inserta()` to insert an array or slice:

```leash
v: vec<int>;
v.pushb(1);
v.pushb(2);
v.pushb(3);

other: vec<int>;
other.pushb(4);
other.pushb(2);

// Insert all elements of another vec<T> at position 1
v.insertv(1, other);  // v is now [1, 4, 2, 2, 3]

v2: vec<string>;
v2.pushb("a");
v2.pushb("b");
v2.pushb("c");

// Insert an array literal or slice at position 1
v2.inserta(1, {"x", "y"});  // v2 is now ["a", "x", "y", "b", "c"]
```

### Vector Properties

| Property | Description |
|----------|-------------|
| `.size` | Return the current number of elements (as an `int`) |

### Vector Methods

| Method | Description |
|--------|-------------|
| `.pushb(val)` | Push element to the back |
| `.popb()` | Remove and return the last element |
| `.pushf(val)` | Push element to the front |
| `.popf()` | Remove and return the first element |
| `.size()` | Return the current number of elements (same as `.size` property) |
| `.get(idx)` | Get the element at the specified index |
| `.set(idx, val)` | Set the element at the specified index |
| `.insert(idx, val)` | Insert an element at the specified index |
| `.remove(idx)` | Remove the element at the specified index |
| `.extend(arr)` | Append all elements from an array or slice to the vector |
| `.extendv(other)`| Append all elements from another vector to this vector |
| `.insertv(pos, other)` | Insert all elements from another vector at position `pos` |
| `.inserta(pos, arr)` | Insert all elements from an array or slice at position `pos` |
| `.clear()` | Remove all elements from the vector |
| `.isin(val)` | Return `true` if the value exists in the vector, `false` otherwise |

## Matrices

Matrices are dynamically-sized, heap-allocated flat arrays that support element-wise math operations with automatic parallelisation. They are declared using the `matrix<T>` syntax.

```leash
m1: matrix<float> = {1.3, 2.1, 5.3};
m2: matrix<float> = {2.4, 9.3, 4.2};

result: matrix<float> = m1 + m2;

foreach i, v in<matrix> result {
    show(i, ": ", v);
}
```

### Memory Layout

```
{ T* data, int64 size, int64 capacity }
```

All data pointers are 64-byte aligned for SIMD/cache-line friendliness.

### Optimisations

| # | Optimisation | Detail |
|---|-------------|--------|
| 1 | **Function-pointer dispatch** | `float`, `double`, `int32`, `int64` binary ops use a single indirect call through a function-pointer table instead of a per-element switch, halving branch mispredictions in the hot loop |
| 2 | **4× loop unrolling + prefetch** | All C-level matrix loops are explicitly 4× unrolled with `__builtin_prefetch(data[i+8])` scheduling two cache lines ahead |
| 3 | **Reusable thread pool** | Lazily-initialised thread pool (`init_thread_pool` / `parallel_dispatch`) replaces per-call thread creation. Parallelism activates at ≥1024 elements and splits work evenly across all cores (Win32/pthreads). |
| 4 | **Cache-blocked ops** | `leash_matrix_blocked_op_float` / `_double` process the array in tiles of ≈64 elements that fit in L1 cache, dramatically reducing cache misses on matrices of 10⁵+ elements |
| 5 | **Aligned allocation** | 64-byte aligned heap buffers via `leash_gc_malloc` (GC-tracked), plus explicit `__builtin_assume_aligned` hints in the hot paths |
| 6 | **`nuw`/`nsw` IR flags** | All index arithmetic (`add`, `sub`, `mul`) in codegen carries `nuw` + `nsw`, letting LLVM fold, reorder, and eliminate bounds checks |
| 7 | **`fast` IR flags on fp** | All `fadd`/`fsub`/`fmul`/`fdiv`/`fcmp` in matrix binary ops carry the `fast` flag, enabling reassociation, reciprocal-math, and fused multiply-add |

### Matrix Methods

| Method | Description |
|--------|-------------|
| `.pushb(val)` | Append element at the end |
| `.pushf(val)` | Prepend element at the front |
| `.popb()` | Remove and return the last element |
| `.popf()` | Remove and return the first element |
| `.insert(idx)` / `.insert(idx, val)` | Insert at flat index |
| `.remove(idx)` | Remove element at flat index (negative wraps) |
| `.get(idx...)` | Index into the flat array (negative wraps) |
| `.set(idx..., val)` | Write val at flat index |
| `.size()` | Total number of elements |
| `.clear()` | Reset to empty (capacity preserved) |
| `.isin(val)` | Search; returns `bool` |
| `.shape()` | Returns `vec<int>` of dimension sizes |

### Arithmetic

| Op | Description |
|----|-------------|
| `+` | Element-wise add (parallel on `float`/`double`/`int32`/`int64`) |
| `-` | Element-wise sub |
| `*` | Element-wise mul |
| `/` | Element-wise div |
| `==` / `!=` | Element-wise comparison |

All binary ops check size equality at runtime.

## Hash Tables

Hash tables are key-value data structures that provide efficient lookup by key. They are declared using the `hash<K, V>` syntax where `K` is the key type and `V` is the value type.

```leash
fnc main() : void {
    // Create a hash table with string keys and integer values
    persons: hash<string, int> = {"John Doe": 23, "Jane Doe": 24};

    // Access values by key using bracket notation
    show(persons["John Doe"]);  // 23

    // Get the key associated with a value
    key: string = persons.getKey(23);
    show(key);  // "John Doe"

    // Check if a key exists
    if persons.isin("John Doe") {
        show("John exists!");
    }

    // Get all keys
    keys: vec<string> = persons.keys();

    // Get all values
    values: vec<int> = persons.values();

    // Get the number of entries
    show("Size: ", persons.size);

    // Add or update entries
    persons.push("Bob Smith", 25);

    // Delete an entry
    persons.delete("Jane Doe");
}
```

### Hash Table Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `getKey(value)` | Returns the key associated with the given value | `K` |
| `keys()` | Returns a vector of all keys | `vec<K>` |
| `values()` | Returns a vector of all values | `vec<V>` |
| `isin(key)` | Returns `true` if the key exists, `false` otherwise | `bool` |
| `delete(key)` | Removes the key-value pair from the hash table | `void` |
| `push(key, value)` | Adds a new key-value pair or updates an existing key | `void` |

### Hash Table Properties

| Property | Description | Returns |
|----------|-------------|---------|
| `size` | Returns the number of key-value pairs | `int` |

### Hash Table Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `hash[key]` | Returns the value for the given key | `persons["John Doe"]` |
| `hash[key] = value` | Updates the value for the given key | `persons["John Doe"] = 30` |

Hash tables support any value types, including pointers, classes, structs, unions, and even other hash tables.

## Structs

Structs group different variables together. You can declare structs at the top-level using the `def` keyword:

```leash
def Point : struct {
    x: int;
    y: int;
};

fnc main() : void {
    p: Point = Point {x: 10, y: 20};
    
    // Member access:
    p.x = 15;
    
    // Leash supports iterating directly across members of a struct natively!
    foreach name, value in<struct> p {
        show(name, ": ", value); // prints x: 15, y: 20
    }
}
```

### Struct Field Default Values

Struct fields can have default values that are used when the struct is created without specifying all fields:

```leash
def Person : struct {
    name: string = "John Doe";
    age:  int    = 23;
};

fnc main() : void {
    // Uses default values
    p1: Person = Person {};
    show(p1.name, " ", p1.age);  // John Doe 23

    // Override specific fields
    p2: Person = Person { name: "Jane" };
    show(p2.name, " ", p2.age);  // Jane 23

    // Override all fields
    p3: Person = Person { name: "Bob", age: 30 };
    show(p3.name, " ", p3.age);  // Bob 30
}
```

When creating a struct instance, you can:
- Use `{}` to use all default values
- Use `{ field: value }` to specify specific fields (using `:` not `=`)
- Omitted fields use their default values

### Struct Functions

Structs can also have functions attached to them using `fnc ... -> StructType` syntax. The function receives an implicit `this` pointer to the struct instance — no heap allocation or vtable dispatch is involved.

```leash
def Person : struct {
    name: string;
    age: int;
};

fnc getName() : string -> Person {
    return this.name;
}

fnc getAge() : int -> Person {
    return this.age;
}

fnc main() {
    p: Person = Person { name: "John Doe", age: 23 };
    show(p.getName(), " ", p.getAge());  // John Doe 23
}
```

Struct functions are called on a variable instance (e.g., `p.getName()`) and can access fields and other struct functions via `this`.

## Pointers

Leash supports pointers for low-level memory operations and efficient parameter passing. Pointers use the `*` prefix for raw pointers and `&` for safe references.

### Function Pointers

Leash supports function pointers, allowing you to pass functions as arguments, store them in variables, and call them indirectly.

```leash
fnc add(a int, b int) : int {
    return a + b;
}

def MathFunc : type fnc(int, int) : int;

fnc call_math(a int, b int, f fnc(int, int) : int) : int {
    return f(a, b);
}

fnc main() : void {
    add_p: fnc(int, int) : int = &add;

    show(add_p(1, 2));

    add2: MathFunc = &add;

    show(add2(1, 2));

    show(call_math(1, 2, &add));
}
```

### Function Pointer Type Syntax

| Syntax | Description |
|--------|-------------|
| `fnc(param_types) : return_type` | Function pointer type |
| `def Alias : type fnc(params) : ret;` | Type alias for function pointer |

### Key Features:
- **Address-of operator**: Use `&function_name` to get a function pointer
- **Type aliases**: Create named function pointer types using `def Name : type fnc(...) : ret;`
- **Function parameters**: Pass functions to other functions as arguments
- **Direct calls**: Call function pointers like regular functions `fn_ptr(arg1, arg2)`

### Basic Pointer Operations

```leash
a: int = 10;
p: *int = &a;       // p holds the address of a

show("p: ", p);     // prints the pointer address
show("*p: ", *p);   // dereferences p, prints 10

*p = 20;            // modifies a through the pointer
show("a: ", a);     // prints 20
```

### Pointer Arithmetic

Pointer arithmetic is supported on `*char` pointers for working with raw character data:

```leash
c_str: *char = cstr("Hello");
show(*(c_str + 1)); // prints 'e' (second character)
show(*(c_str + 4)); // prints 'o' (fifth character)
```

### Safe References (`&`)

Leash recommends using `&` (references) instead of `*` (raw pointers) for function parameters when possible. References provide the same efficiency with better safety guarantees:

```leash
// Using a reference - recommended for simple modifications
fnc increment(v &int) : void {
    v = v + 1;
}

// Using a raw pointer - for low-level operations
fnc decrement(v *int) : void {
    *v = *v - 1;
}

fnc main() : void {
    a: int = 10;
    
    increment(a);    // passes address of 'a' automatically
    show("a: ", a);  // prints 11
    
    p: *int = &a;
    decrement(p);    // passes the pointer directly
    show("a: ", a);  // prints 10
}
```

### Pointer Member Access (`->`)

Use the `->` operator to access struct members through a pointer:

```leash
def Point : struct {
    x: int;
    y: int;
};

fnc print_point(p *Point) : void {
    show("x: ", p->x);  // arrow operator for pointer access
    show("y: ", p->y);
}

fnc main() : void {
    pt: Point = Point {x: 10, y: 20};
    print_point(&pt);
}
```

### Pointer Types

| Syntax | Description | Example |
|--------|-------------|---------|
| `*T` | Raw pointer to type T | `*int`, `*Point`, `*char` |
| `&T` | Safe reference to type T | `&int`, `&Point` |
| `pointer<T>` | Generic pointer syntax (canonicalizes to `*T`) | `pointer<int>`, `pointer<Point>` |

*Note: Leash uses the Boehm Garbage Collector for memory management, so pointers to GC-allocated objects remain valid throughout the program's lifetime.*

### Generic Pointer Syntax (`pointer<T>`)

Leash supports a generic pointer syntax `pointer<T>` as an alternative to the `*T` syntax. This is particularly useful for readability in complex generic types and template metaprogramming:

```leash
// These are equivalent:
def Node : struct {
    value: int;
    next: *Node;           // raw pointer syntax
};

def Node2 : struct {
    value: int;
    next: pointer<Node2>;  // generic pointer syntax
};

// Useful in generic contexts:
def LinkedList<T> : struct {
    head: pointer<Node<T>>;  // cleaner than *Node<T>
    size: int;
};

// Also works with type aliases:
def int_ptr : type pointer<int>;  // same as *int

fnc main() : void {
    p: pointer<int> = &10;
    show(*p);  // 10
}
```

The `pointer<T>` syntax is **canonicalized to `*T`** at parse time, so both forms are completely interchangeable and have identical behavior.

## Unions

Unions allow a variable to store different types of data at different times. Leash implements **Tagged Unions**, meaning the language tracks which type is currently active at runtime for safety.

```leash
def Value : union {
    i: int;
    f: float;
    s: string;
};

fnc main() : void {
    v: Value = 42;         // Auto-detects 'i' variant
    show("v: ", v.cur);    // Smart-prints active variant
    
    v = "hello";           // Changes active variant to 's'
    show("v: ", v.cur);
    
    // Explicit variant access:
    v.f = 3.14;
    show("v.f: ", v.f);
}
```

### The `tounion()` Function

The `tounion(UnionType, value)` built-in explicitly wraps a value into a union type, typically used with type-inferred variable declarations:

```leash
def Value : union {
    i: int;
    f: float;
    b: bool;
    s: string;
    c: char
};

fnc main {
    v := tounion(Value, 45);
    show("v: ", v, ", typeof(v): ", typeof(v));  // v: 45, typeof(v): Value
}
```

The compiler verifies that the union type exists and that the value's type matches one of its variants. It raises a compile-time error if no variant accepts the given type.

*Note: Accessing an inactive variant (e.g., calling `v.i` when `v.f` is active) will trigger a **Runtime Safety Error** to prevent crashes or memory corruption.*

## Enums

Enums allow you to define a set of named constants. In Leash, enums are represented as integers under the hood but provide a `.name` property to access their string representation.

### Basic Enums

```leash
def Color : enum {
    RED,
    GREEN,
    BLUE
};

fnc main() : void {
    c: Color = Color::RED;
    show("c = ", c);         // Prints the integer value (0)
    show("c.name = ", c.name); // Prints the member name ("RED")

    // Enums are compatible with int
    val: int = c;
    
    colors: Color[3] = {Color::RED, Color::GREEN, Color::BLUE};
    foreach i, v in<array> colors {
        show(i, ": ", v.name);
    }
}
```

### Enums with Custom Values

Leash supports enums with custom values. Each member can have a type annotation and a custom value. This allows enums to carry rich data types like strings, specific integers, floats, and booleans.

```leash
// Enum with string values
def Names : enum {
    PERSON1: string = "John Doe",
    PERSON2: string = "Jane Doe",
    UNKNOWN: string = "Unknown"
};

// Enum with numeric values
def ErrorCodes : enum {
    OK: int = 0,
    NOT_FOUND: int = 404,
    SERVER_ERROR: int = 500
};

// Enum with float values
def Constants : enum {
    PI: float = 3.14159,
    E: float = 2.71828
};

fnc main() : void {
    // Access enum members with custom values
    // Use the member's type (not the enum type) for variables
    name: string = Names::PERSON1;
    show(name);  // Prints: John Doe
    
    // Numeric values work too
    code: int = ErrorCodes::NOT_FOUND;
    show(code);  // Prints: 404
    
    // Direct access
    show(Names::UNKNOWN);        // Prints: Unknown
    show(ErrorCodes::SERVER_ERROR); // Prints: 500
    show(Constants::PI);           // Prints: 3.14159
}
```

**Key Points:**
- When accessing enum members with custom values, use the member's type (e.g., `string`) not the enum type (e.g., `Names`) for variable declarations
- Supported value types: `string`, `int`, `uint`, `float`, `bool`, and explicit bit-width types (e.g., `int<64>`)
- Members without custom values default to sequential integer values (0, 1, 2, ...)
- Custom values are evaluated at compile time for literal values

## Type Aliases

You can define custom names for existing types to improve readability or create abstraction layers:

```leash
def MyInt : type int;
def Pixel : type uint<8>;

a: MyInt = 10;
p: Pixel = 255;
```

## Macros

Macros allow you to define reusable code snippets that are expanded at compile time through textual substitution. They are useful for creating concise, readable abbreviations for commonly used expressions.

### Defining Macros

Macros are defined using the `def` keyword with the `macro` keyword, followed by a parameter list and a body:

```leash
def MAX : macro(a, b) |> a < b ? b : a;
```

The body can use the pipe operator (`|>`) for a single-expression macro, or curly braces (`{ }`) for multi-line macros:

```leash
// Single-line macro using |>
def MAX : macro(a, b) |> a < b ? b : a;

// Multi-line macro using { }
def CLAMP : macro(val, lo, hi) {
    if val < lo { return lo; }
    if val > hi { return hi; }
    return val;
};
```

### Using Macros

Macro calls look like regular function calls. The compiler expands them inline at compile time by substituting the arguments into the body:

```leash
def MAX : macro(a, b) |> a < b ? b : a;

fnc main() : void {
    show(MAX(10, 20));   // Expanded to: 10 < 20 ? 20 : 10 → 20
    show(MAX(34, 1));    // Expanded to: 34 < 1 ? 1 : 34 → 34
}
```

### How Macros Work

Macros perform **textual substitution** at compile time. When the compiler encounters a macro call, it replaces the call with the macro's body, substituting each argument for the corresponding parameter. This means:

- **No function call overhead** — the expression is inlined directly at the call site
- **Arguments are evaluated per-use** — if an argument appears multiple times in the body, it is re-evaluated each time
- **No type constraints** — macro parameters can accept any expression type

### Zero-Parameter Macros

Macros with no parameters can omit the parentheses when calling them. Both `NAME` and `NAME()` are accepted:

```leash
def PI : macro() |> 3.14159;

fnc main() : void {
    show(PI);     // 3.14159
    show(PI());   // 3.14159 — equivalent
}
```

This is particularly useful for constants defined as macros (e.g., key codes, color values from libraries), where the parentheses can feel redundant.

### Visibility

Macros support `pub` (default) and `priv` visibility modifiers:

```leash
pub def MAX : macro(a, b) |> a < b ? b : a;     // Accessible from other modules
priv def INTERNAL : macro(x) |> x * 2;           // Only accessible within this module
```

### Tips

- Use `|>` for single-expression macros to keep them concise.
- Use `{ }` for multi-statement macros that need more complex logic.
- Macro parameter names are plain identifiers (no types), since they are substituted textually.

## Generic Types

Leash supports generic programming through templates. Templates allow you to define functions and classes that work with multiple types, providing type safety and code reuse.

### Defining Template Parameters

Use the `template` keyword to declare a type parameter:

```leash
def T : template;
```

Template parameters are conventionally named with a single uppercase letter (e.g., `T`, `U`, `V`) or with descriptive names like `KeyType`, `ValueType`.

### Generic Functions

You can use template parameters as types in function signatures:

```leash
def T : template;

fnc identity(x T) : T {
    return x;
}

fnc main() : void {
    show(identity<int>(42));        // 42
    show(identity<string>("hi"));  // "hi"
}
```

When calling a generic function, you must specify the concrete type inside angle brackets after the function name: `identity<int>(42)`.

### Generic Classes

Classes can also have multiple template parameters:

```leash
def K : template;
def V : template;

def Pair : class<K, V> {
    first: K;
    second: V;

    pub fnc get_first() : K {
        return this.first;
    }

    pub fnc get_second() : V {
        return this.second;
    }
}

fnc main() : void {
    p: Pair<int, string> = Pair<int, string> {first: 1, second: "one"};
    show(p.get_first());   // 1
    show(p.get_second()); // "one"
}
```

### Calling Static Methods on Generic Classes

You can call static methods on generic classes by specifying the type arguments directly after the class name:

```leash
def T : template;

def VecMath : class<T> {
    static pub fnc sum(a vec<T>, b vec<T>) : vec<T> {
        if a.size != b.size {
            return nil;
        }
        result: vec<T>;
        foreach i, v in<vector> a {
            result.pushb(v + b.get(i));
        }
        return result;
    }
}

fnc main() : void {
    a: vec<int>;
    b: vec<int>;
    a.extend({10, 20, 30});
    b.extend({1, 2, 3});

    c: vec<int> = VecMath<int>.sum(a, b);
    show(c.get(0));  // 11
    show(c.get(1));  // 22
    show(c.get(2));  // 33
}
```

The syntax `ClassName<Type>.method(args)` instantiates the generic class with the specified type and calls the static method.

### Using `nil` with Generic Types

The `nil` value can be used with any type, including generic type parameters. This is useful for optional return values:

```leash
def T : template;

fnc find(key string, map Hash<string, T>) : T {
    // ... search logic
    return nil; // nil is valid for any T
}
```

Generic types can be used anywhere a regular type is expected, including as function parameters, return types, struct fields, and class members.

## Multi-Type Functions

Leash supports functions that can accept and return multiple types using the **multi-type syntax**. This allows you to write generic-like functions without explicitly declaring template parameters.

### Syntax

Use square brackets `[type1, type2, ...]` to specify multiple allowed types for parameters and return values:

```leash
fnc add(a [int, float], b [int, float]) : [int, float] {
    return a + b;
}

fnc main() : void {
    i: int = add(10, 20);
    f: float = add(10.5, 20.5);
    show(i, " ", f);  // prints: 30 31.0
}
```

### How It Works

When you call a multi-type function, Leash automatically instantiates a specialized version based on the concrete argument types:

- `add(10, 20)` creates an `int` version of the function
- `add(10.5, 20.5)` creates a `float` version of the function

Each instantiation is type-checked and compiled separately, ensuring type safety while maintaining the flexibility of generic functions.

### Use Cases

Multi-type functions are useful when:

- You want simple overloading without explicit template syntax
- A function should work with multiple related types (e.g., `int` and `float`)
- You need the compiler to automatically generate the right function based on usage

This is similar to how C++ templates work, but with Leash's type checker automatically handling the specialization.

## Multi-Return Functions

Leash supports functions that return multiple values using **multi-return syntax**. This allows a function to return a tuple of values without needing to define a struct.

### Syntax

Use parenthesized type lists `(type1, type2, ...)` in the function signature and comma-separated expressions in the `return` statement:

```leash
fnc add_sub(a int, b int) : (int, int) {
    return a + b, a - b;
}

fnc main() : void {
    sum, diff: int, int = add_sub(10, 4);
    show(sum);   // 14
    show(diff);  // 6
}
```

### How It Works

Under the hood, multi-return values are passed as an LLVM struct. The compiler automatically:

1. Constructs a struct from the returned values
2. Extracts each element when assigning to variables
3. Type-checks that the number and types of values match the declaration

### Multi-Variable Declaration

Use `name1, name2 : type1, type2 = expr` to declare multiple variables from a multi-return call:

```leash
fnc divmod(a int, b int) : (int, int) {
    return a / b, a % b;
}

fnc main() : void {
    quotient, remainder: int, int = divmod(17, 5);
    show(quotient);   // 3
    show(remainder);  // 2
}
```

### Multi-Assignment

Use `name1, name2 = expr` to assign to existing variables:

```leash
fnc swap(a int, b int) : (int, int) {
    return b, a;
}

fnc main() : void {
    x: int = 10;
    y: int = 20;
    x, y = swap(x, y);
    show(x);  // 20
    show(y);  // 10
}
```

### Three or More Return Values

Multi-return works with any number of values:

```leash
fnc stats(a int, b int, c int) : (int, int, int) {
    return a + b + c, a * b * c, a - b - c;
}

fnc main() : void {
    sum, prod, diff: int, int, int = stats(2, 3, 4);
    show(sum);   // 9
    show(prod);  // 24
    show(diff);  // -5
}
```

### Mixed Types

Each return position can have a different type:

```leash
fnc compute(x int, y float) : (int, float) {
    return x * 2, y + 1.5;
}

fnc main() : void {
    i, f: int, float = compute(5, 3.14);
    show(i);  // 10
    show(f);  // 4.64
}
```

### Type Safety

The compiler enforces that:

- The number of return values matches the declared return type
- Each return value's type is compatible with the corresponding declared type
- Multi-variable declarations have matching names and types
- Multi-assignment targets existing, mutable variables with compatible types

```leash
// Error: function expects 3 return values but got 2
fnc bad() : (int, int, int) {
    return 1, 2;  // compile error
}

// Error: type mismatch
fnc main() : void {
    a, b: string, int = add_sub(10, 4);  // 'string' vs 'int' error
}
```

### Works with `defer`

Deferred calls execute before the multi-return value is returned, ensuring proper cleanup:

```leash
fnc process(f File) : (int, string) {
    defer f.close();
    data: string = f.read();
    return data.size, data;
}
```

## Type Casting

Leash supports explicit type casting using the `(type)expression` syntax to convert between incompatible types.

```leash
f: float = 3.99;
i: int = (int)f;    // Evaluates to 3 (truncation)

c: char = 'A';
u: uint = (uint)c;  // Evaluates to 65 (ASCII)

// Pointer casts (e.g., bitcasting internal pointers)
arr: int<32>[] = {1, 2, 3};
arr64: int<64>[] = (int<64>[])arr;
```

## The `as` Keyword

The `as` keyword provides a clean, readable way to convert values between compatible types. Unlike the C-style cast `(type)expr`, `as` is designed for safe and fast type conversions with clear intent.

```leash
fnc main() : void {
    a: int = 10;
    
    show(a as float);           // 10.000000
    show((a as float<64>) + .5); // 10.500000
}
```

### Supported Conversions

| Conversion | Example | Description |
|------------|---------|-------------|
| Numeric → Numeric | `10 as float` | Integer to float, float to int, etc. |
| Bit-width changes | `x as int<64>` | Change integer/float precision |
| Class upcasting | `dog as Animal` | Child class to parent class |
| Class downcasting | `animal as Dog` | Parent class to child class |
| Pointer conversions | `ptr as *char` | Between pointer types |

### Unsafe Mode

Inside `unsafe` functions, `as` also allows pointer ↔ integer conversions for low-level operations:

```leash
unsafe fnc ptr_to_int(p *int) : int<64> {
    return p as int<64>;
}
```

### `as` vs `(type)expr` vs Conversion Functions

| Syntax | Use Case | Safety |
|--------|----------|--------|
| `expr as type` | Safe numeric/class conversions | Type-checked at compile time |
| `(type)expr` | Low-level casts, bit manipulation | Allows any cast (may be unsafe) |
| `toint(type, expr)` | String parsing, explicit conversion | Runtime parsing |
| `tofloat(type, expr)` | String parsing, explicit conversion | Runtime parsing |

Use `as` when you want clear, readable type conversions that the compiler can validate. Use `(type)expr` for low-level pointer casts or when you need to bypass type checking. Use `toint`/`tofloat` when parsing strings.

## Type Conversions

For dynamic conversions (such as parsing a string into a number or formatting a number as a string), Leash provides built-in conversion functions.

### String to Number
Use `toint(target_type, value)` and `tofloat(target_type, value)` to parse strings.

```leash
a: int = toint(int, "123");
b: float<32> = tofloat(float<32>, "3.14159");

// These functions also handle numeric-to-numeric conversions:
c: int = toint(int, 3.99); // Evaluates to 3
```

### Number to String
Use `tostring(value)` to format a numeric value into a string.

```leash
s1: string = tostring(123);    // "123"
s2: string = tostring(3.14);   // "3.140000"
```

Just like `get()`, `tostring()` returns a managed string that will be automatically cleaned up by the GC.

### Byte Conversions

Leash provides functions to convert between integers/floats and their byte representations. These are useful for binary data manipulation, serialization, and FFI operations.

- `inttobytes(size, value)` - Convert an integer to a byte array (`char[]`)
- `bytestoint(size, bytes)` - Convert a byte array back to an integer
- `floattobytes(size, value)` - Convert a float to a byte array (`char[]`)
- `bytestofloat(size, bytes)` - Convert a byte array back to a float

```leash
fnc main() {
    // Integer to bytes and back
    n: int<64> = 1982;
    bytes: char[8] = inttobytes(sizeof(int<64>), n);
    restored: int<64> = bytestoint(sizeof(int<64>), bytes);
    show("Restored int: ", restored);  // 1982

    // Float to bytes and back
    f: float<64> = 3.14;
    fbytes: char[8] = floattobytes(sizeof(float<64>), f);
    restored_f: float<64> = bytestofloat(sizeof(float<64>), fbytes);
    show("Restored float: ", restored_f);  // 3.140000
}
```

The `size` argument is typically `sizeof(type)` to ensure the correct byte count. For `inttobytes`/`bytestoint`, the size determines the integer bit width (e.g., `sizeof(int<64>)` = 8 bytes). For `floattobytes`/`bytestofloat`, Leash's default float is 64-bit (`float<64>`).

### Value to Union

Use `tounion(UnionType, value)` to explicitly wrap a value into a union type. This is useful with type-inferred variable declarations:

```leash
def Value : union {
    i: int;
    f: float;
    b: bool;
    s: string;
};

fnc main() {
    v := tounion(Value, 42);   // infers type 'Value'
    show(v, " ", typeof(v));   // 42 Value
}
```

The compiler validates that the value's type matches one of the union's variants at compile time.

## The `sizeof()` Operator

The built-in `sizeof()` operator returns the size in bytes of a type or the result of an expression. It supports **literally anything** in Leash, from primitives to complex classes.

```leash
fnc main() {
    show(sizeof(int));        // 4
    show(sizeof(float<64>));  // 8
    
    a: int<128>;
    show(sizeof(a));          // 16
    
    show(sizeof(string));     // 8 (pointer)
    
    // Works with structs and classes
    show(sizeof(Point));      // 8
    
    // Works with functions and lambdas (returns pointer size)
    show(sizeof(add));        // 8
    
    // Works with complex expressions
    show(sizeof(a + 10));     // 16
}
```

`sizeof()` is evaluated at compile time when possible, providing the precise memory footprint of your data structures and types.

## The `typeof()` Operator

The built-in `typeof()` operator returns the **type name** of any expression as a string at compile time. Useful for debugging, logging, or generic code that needs to inspect types.

```leash
fnc main {
    a := 10;
    b := "hello";
    c := {1, 2, 3};
    d := Point {x: 1, y: 2};

    show(typeof(a));       // "int"
    show(typeof(b));       // "string"
    show(typeof(c));       // "int[]"
    show(typeof(d));       // "Point"
    show(typeof(c.x));     // "int"

    lam: fnc(int) : int = fnc(x int) : int { return x + 1; };
    show(typeof(lam));     // "fnc(int) : int"

    m: matrix<float> = {1.0, 2.0, 3.0};
    show(typeof(m));       // "matrix<float>"
}
```

`typeof()` works with any expression — primitives, arrays, structs, enums, unions, lambdas, function pointers, vectors, matrices, and classes. The result is always a `string` known at compile time.

## Strings

String evaluation is supported natively in the language!
```leash
a: string = "hello";
b: string = "world";

// Concatenation
c: string = a + b; 

// Removal of exact substring (removes first occurrence)
removed: string = c - b; // evaluates to 'hello'

// Equality testing
if a != b {
    show("Not equal!");
}

// Character Indexing & Sizes
charVar: char = c[1];
strLen: int = c.size; // evaluates length!
```

### String Interpolation

Leash supports string interpolation inside double-quoted strings. Any Leash expression enclosed in `{` `}` is evaluated and converted to a string at runtime:

```leash
fnc main() : void {
    a: int = 10;
    b: int = 20;
    show("{a} + {b} = {a + b}");  // "10 + 20 = 30"
}
```

Interpolation works with any expression, including function calls:

```leash
fnc double(x int) : int { return x * 2; }

fnc main() : void {
    show("{double(5)}");  // "10"
}
```

To include a literal `{` or `}` in an interpolated string, escape it with `\{` and `\}`:

```leash
fnc main() : void {
    mylist: int[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    show("mylist isn't '\{1, 2, 3, 4, 5, 6, 7, 8, 9, 10\}'");
    // Output: mylist isn't '{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}'
}
```

Interpolation is supported for double-quoted strings only (single-quoted strings and triple-quoted strings do not support interpolation).

### Multi-Line Strings

Leash supports multi-line strings using triple quotes (`"""` or `'''`):

```leash
fnc main() : void {
    // Multi-line string with double quotes
    poem: string = """Roses are red,
    Violets are blue,
    Leash is awesome,
    And so are you!""";

    // Multi-line string with single quotes
    code: string = '''fnc main() : void {
    show("Hello World!");
}''';

    show(poem);
    show(code);
}
```

### String Methods

Leash strings support the following methods:

| Method | Description |
|--------|-------------|
| `.replace(old, new)` | Replace the first occurrence of `old` with `new` |
| `.size` | Return the length of the string (property) |

Example:

```leash
fnc main() : void {
    str: string = "Hello World!";
    
    show(str.replace("World", "Luna"));  // "Hello Luna!"
    
    newStr: string = str.replace("Hello", "Hi");
    show(newStr);  // "Hi World!"
    
    // Works with string literals too
    result: string = "Hello Bro".replace("Bro", "World!");
    show(result);  // "Hello World!"
}
```

### Leash Strings vs Char Arrays

Leash natively supports both automatically managed `string` types and lower-level `char[]` slices. You can easily convert and concatenate between them using the built-in `lstr()` and `cstr()` functions.

```leash
// cstr() converts a Leash string to a char[]
str: char[] = cstr("Hello World");
show(str);

// lstr() converts a char[] to an implicitly managed Leash string
s: string = lstr(str);

// Iteration over strings:
foreach i, c in<string> s {
    show(i, ": ", c);
}

// Standard string concatenation automatically handles mixed conversions:
s = s + '!';            // concatenate with character
s = s + cstr(" :D");    // concatenate with char[]
show(s);
```

### Escape Normalization (`normescape`)

The built-in `normescape()` converts escape sequences in a string to their actual character values. This is useful when you have a string literal containing escape sequences like `\n` and want them interpreted as the real characters (newline, tab, etc.).

```leash
fnc main {
    s: string = "Hello, world!\n haha \\";
    show(normescape(s));
    /* Output:
Hello, world!
 haha \ */
}
```

Supported escape conversions:

| Source | Result |
|--------|--------|
| `\n` | Newline (`0x0A`) |
| `\t` | Horizontal tab (`0x09`) |
| `\r` | Carriage return (`0x0D`) |
| `\0` | Null character (`0x00`) |
| `\a` | Bell/alert (`0x07`) |
| `\b` | Backspace (`0x08`) |
| `\f` | Form feed (`0x0C`) |
| `\v` | Vertical tab (`0x0B`) |
| `\\` | Backslash (`\`) |
| `\"` | Double quote (`"`) |
| `\'` | Single quote (`'`) |
| `\?` | Question mark (`?`) |
| `\xNN` | Byte from hex digits `NN` (1-2 hex digits, e.g., `\x1B` → escape, `\x41` → `A`) |
| Any other `\X` | The character `X` itself (e.g., `\q` becomes `q`) |

## Classes

Leash supports Object-Oriented Programming through classes. Classes combine data (fields) and behavior (methods) into a single unit.

```leash
def Person : class {
    // Fields with visibility modifiers
    pub name: string;
    priv age: int;

    // Static conversion/factory method
    static pub fnc new(name string, age int) : Person {
        return Person { name: name, age: age };
    }

    // Instance method using the 'this' keyword
    pub fnc greet() : string {
        return "Hello, my name is " + this.name + " and I am " + this.age + " years old!";
    }
}

fnc main() : void {
    // Call static method
    p: Person = Person.new("John", 30);
    
    // Call instance method
    show(p.greet());
    
    // Access public fields
    p.name = "Jane";
    show(p.name);
}
```

### Key Features:
- **Visibility**: Use `pub` (default) for members accessible from anywhere, and `priv` for members only accessible within the class methods.
- **Methods**: Functions defined inside a class. Non-static methods automatically receive an implicit `this` pointer to the current instance.
- **Static vs Instance**: Methods marked with the `static` keyword are called on the class name (e.g., `Person.new()`), while non-static methods are called on a variable instance (e.g., `p.greet()`).
- **The `this` Keyword**: Automatically available inside instance methods to access fields and other methods of the current object.

### Class Inheritance (Subclasses)

Leash supports class inheritance, allowing you to create subclasses that inherit fields and methods from a parent class. Subclasses can override parent methods to provide specialized behavior.

```leash
def Animal : class {
    pub name: string;

    static pub fnc new(name string) : Animal {
        return Animal { name: name };
    }

    pub fnc talk() : void {
        show("No sounds");
    }
}

// Define a subclass using class(Parent)
def Dog : class(Animal) {
    // Dog inherits 'name' field from Animal

    static pub fnc new(name string) : Dog(Animal) {
        return Dog { name: name };
    }

    // Override the parent's talk() method
    pub fnc talk() : void {
        show("Bark Bark!");
    }
}

def Cat : class(Animal) {
    static pub fnc new(name string) : Cat(Animal) {
        return Cat { name: name };
    }

    pub fnc talk() : void {
        show("Meow!");
    }
}
```

### Polymorphism and Dynamic Dispatch

Leash supports polymorphism - a child class can be used wherever its parent class is expected. Method calls are dispatched dynamically at runtime, so the correct overridden method is called based on the actual object type.

```leash
fnc main() : void {
    a: Animal = Animal.new("bob");
    d: Dog    = Dog.new("Jake");
    c: Cat    = Cat.new("Justaname");

    a.talk();  // "No sounds"
    d.talk();  // "Bark Bark!"
    c.talk();  // "Meow!"

    // Upcasting: Child can be assigned to parent variable
    a2: Animal = Dog.new("Jakeee");

    // Dynamic dispatch: calls Dog.talk() even though a2 is typed as Animal
    a2.talk(); // "Bark Bark!"

    // Downcasting: Explicit cast from parent to child
    d = (Dog)a;
}
```

### Inheritance Features:
- **Subclass Syntax**: Use `def Child : class(Parent)` to create a subclass
- **Field Inheritance**: Child classes automatically inherit all fields from the parent
- **Method Inheritance**: Child classes inherit all methods from the parent
- **Method Overriding**: Child classes can override parent methods by redefining them
- **Imut Methods**: Use `imut` before `fnc` to make a method non-overridable by subclasses
- **Upcasting**: Implicit conversion from child to parent type (e.g., `Animal a = Dog.new()`)
- **Downcasting**: Explicit cast from parent to child using `(ChildType)expr`
- **Dynamic Dispatch**: Method calls use vtables for runtime polymorphism

 ### Preventing Method Overriding with `imut`

If you want to make a method non-modifiable by subclasses, use `imut` before `fnc`:

```leash
def Animal : class {
    pub name: string;

    static pub fnc new(name string) : Animal {
        return Animal { name: name };
    }

    // imut makes this method non-overridable
    pub imut fnc talk() : void {
        show("No sounds");
    }
}

def Dog : class(Animal) {
    // ERROR: Cannot override imut method 'talk' from parent class 'Animal'
    pub fnc talk() : void {
        show("Bark Bark!"); // Compilation error!
    }
}
```

This is useful when you want to ensure certain methods always behave the same way across all subclasses, maintaining consistent behavior for critical operations.

### Creating Class Instances with `create`

Leash provides the `create` keyword to instantiate classes. When you use `create`, the compiler automatically calls the class constructor (a method with the same name as the class).

```leash
def Math : class {
    pub Math() {
        show("Hello! I've been created :)");
    }

    pub fnc sum(a int, b int) : int {
        return a + b;
    }

    pub fnc sub(a int, b int) : int {
        return a - b;
    }
}

fnc main() : void {
    // Create an instance using 'create'
    m: Math = create Math();

    show(m.sum(10, 20));  // 30
    show(m.sub(20, 5));   // 15
}
```

#### Constructor Syntax

Constructors are methods with the same name as the class. They can have parameters:

```leash
def Point : class {
    pub Point(x int, y int) {
        this.x = x;
        this.y = y;
        show("Point created at (", x, ", ", y, ")");
    }

    priv x: int;
    priv y: int;
}

fnc main() : void {
    p: Point = create Point(10, 20);
}
```

#### Auto-Initialization

When a variable is declared with a class type but no initializer, Leash automatically heap-allocates an instance and calls the default constructor (the constructor with all default arguments, or no constructor at all):

```leash
def Person : class {
    name: string;
    age:  int;
    pub Person(name string = "No Name", age int = 10) {
        this.name = name;
        this.age  = age;
    }
}

fnc main {
    p1: Person;                     // auto-init with default constructor
    p2: Person = create Person;     // explicit (same result)
    p3: Person = create Person();   // explicit with parens (same result)

    show(p1.name, " ", p1.age);  // No Name 10
}
```

The three forms are equivalent — all allocate a new `Person` on the heap and call the constructor with default values. If the constructor has required parameters (without defaults), the compiler reports an error at compile time.

### Deleting Class Instances with `del`

The `del` keyword deletes a class instance and frees its memory. When you use `del`, the compiler calls the destructor (a method named `DEL_ClassName`).

```leash
def Math : class {
    pub Math() {
        show("Hello! I've been created :)");
    }

    priv DEL_Math() {
        show("Oh no! I'm dead :(");
    }
}

fnc main() : void {
    m: Math = create Math();

    del m;  // Calls DEL_Math() and frees the instance
}
```

#### Destructor Syntax

Destructors are methods named `DEL_ClassName` (with `DEL_` prefix followed by the class name). They are called automatically when:
- The `del` keyword is used on a variable
- The instance is no longer referenced and GC collects it (though `del` provides explicit control)

### Class-Based Entry Points (Java-Friendly Syntax)

Leash supports Java-like entry points: instead of a top-level `fnc main()`, you can define a class named `Main` with a `static fnc main` method. The compiler detects this pattern and generates the appropriate `main` wrapper automatically.

```leash
def Main : class {
    static fnc main {
        show("Hello, world!");
    }
}
```

This is equivalent to writing:

```leash
fnc main() : void {
    show("Hello, world!");
}
```

**Rules:**
- The class must be named exactly `Main` (case-sensitive).
- It must contain a `static fnc main` method (no arguments, `void` return).
- The `Main` class can contain other members — only `static fnc main` is used as the entry point.
- If both a top-level `fnc main()` and a `Main` class with `static fnc main` exist, the top-level function takes precedence.

## File I/O

Leash provides a built-in `File` class for reading and writing files. The `File` class is a native class (not a primitive type) that wraps the C standard library's `FILE*` operations.

### Opening and Closing Files

Use `File.open()` to open a file and `.close()` to close it:

```leash
fnc main() : void {
    // Open a file for writing (creates or truncates)
    file: File = File.open("output.txt", "w");
    file.write("Hello, World!");
    file.close();

    // Open a file for reading
    reader: File = File.open("output.txt", "r");
    content: string = reader.read();
    show(content);  // "Hello, World!"
    reader.close();
}
```

### File Modes

| Mode | Description |
|------|-------------|
| `"r"` | Open for reading (file must exist) |
| `"w"` | Open for writing (creates or truncates) |
| `"a"` | Open for appending (creates if doesn't exist) |
| `"r+"` | Open for reading and writing (file must exist) |

### Static Methods

These methods are called on the `File` class directly:

| Method | Description | Returns |
|--------|-------------|---------|
| `File.open(filename, mode)` | Open a file with the specified mode | `File` (or `nil` on error) |
| `File.rename(oldname, newname)` | Rename a file | `int` (0 on success) |
| `File.delete(filename)` | Delete a file | `int` (0 on success) |

### Instance Methods

These methods are called on a `File` object:

| Method | Description | Returns |
|--------|-------------|---------|
| `.read()` | Read entire file content | `string` |
| `.write(text)` | Write text to file | `int` (0 on success) |
| `.close()` | Close the file | `int` (0 on success) |
| `.readln()` | Read one line (strips newline) | `string` |
| `.readb()` | Read entire file as bytes | `char[]` |
| `.writeb(bytes)` | Write bytes to file | `int` (0 on success) |
| `.readlnb()` | Read one line as bytes | `char[]` |
| `.replace(old, new)` | Replace first occurrence | `int` (1 if found, 0 otherwise) |
| `.replaceall(old, new)` | Replace all occurrences | `int` (count of replacements) |
| `.rewind()` | Reset file position to start | `void` |

### Reading Line by Line

```leash
fnc main() : void {
    file: File = File.open("data.txt", "w");
    file.write("Line 1\nLine 2\nLine 3");
    file.close();

    reader: File = File.open("data.txt", "r");
    
    line1: string = reader.readln();  // "Line 1"
    line2: string = reader.readln();  // "Line 2"
    line3: string = reader.readln();  // "Line 3"
    
    show(line1);
    show(line2);
    show(line3);
    
    reader.close();
}
```

### Byte Operations

For binary data, use `readb()`, `writeb()`, and `readlnb()`:

```leash
fnc main() : void {
    // Write binary data
    file: File = File.open("data.bin", "wb");
    data: char[] = cstr("Binary content");
    file.writeb(data);
    file.close();

    // Read binary data
    reader: File = File.open("data.bin", "rb");
    bytes: char[] = reader.readb();
    show("Read ", bytes.size, " bytes");
    reader.close();
}
```

### String Replacement in Files

The `replace()` and `replaceall()` methods allow in-place string replacement:

```leash
fnc main() : void {
    file: File = File.open("template.txt", "w");
    file.write("Hello {name}! Welcome to {place}.");
    file.close();

    // Open for reading and writing
    editor: File = File.open("template.txt", "r+");
    
    // Replace first occurrence
    editor.replace("{name}", "World");
    
    // Replace all remaining occurrences
    editor.replaceall("{place}", "Leash");
    
    editor.rewind();
    show(editor.read());  // "Hello World! Welcome to Leash."
    
    editor.close();
}
```

### Appending to Files

```leash
fnc main() : void {
    // Create initial file
    file: File = File.open("log.txt", "w");
    file.write("Log started\n");
    file.close();

    // Append more content
    logger: File = File.open("log.txt", "a");
    logger.write("Entry 1\n");
    logger.write("Entry 2\n");
    logger.close();

    // Read all content
    reader: File = File.open("log.txt", "r");
    show(reader.read());
    reader.close();
}
```

## Memory Management

Leash uses a **custom garbage collector** built specifically for the language. This mark-and-sweep GC with conservative root finding manages memory automatically - you never need to call `free()` or worry about memory leaks.

### How It Works

The custom GC uses a **mark-and-sweep algorithm**:

1. **Mark Phase**: Starting from "root" pointers (global variables, local variables on the stack, etc.), the GC traces all reachable objects
2. **Sweep Phase**: Any object not marked as reachable is freed automatically

### Features

- **Conservative Root Finding**: The GC can identify pointers to managed objects even without explicit registration
- **Explicit Root Management**: Use `leash_gc_register_root()` and `leash_gc_unregister_root()` for tricky cases
- **Automatic Collection**: Garbage collection triggers automatically when memory usage exceeds a threshold
- **Type-Aware**: Properly handles all Leash types:
  - Strings (allocated with atomic flag - no internal pointers)
  - Vectors (dynamic arrays with tracked data pointers)
  - Arrays and slices
  - Structs and classes (with inheritance/vtable support)
  - Unions (with active variant tracking)
- **Statistics**: Built-in GC stats available via `leash_gc_print_stats()`

### Memory Lifecycle

```leash
fnc main() : void {
    // Strings are GC-managed
    name : string = "Leash";
    
    // Vectors grow dynamically, GC handles reallocation
    v : vec<int>;
    for i : int = 0; i < 1000; i = i + 1 {
        v.pushb(i);  // GC manages vector buffer
    }
    
    // When variables go out of scope or become unreachable,
    // memory is automatically reclaimed on next GC cycle
}
```

### GC Integration

- The GC is **automatically initialized** when your program starts (in the `main()` function)
- All memory allocation in Leash goes through the custom GC (`leash_gc_malloc`, `leash_gc_realloc`)
- The GC runtime is compiled and linked with every Leash program automatically
- No external GC library (like Boehm) is needed

### Manual Collection (Advanced)

While the GC runs automatically, you can trigger collection manually:

```leash
// In rare cases, you might want to force collection
// (Normally not needed - the GC handles this automatically)
extern fnc leash_gc_collect() : void;  // Declare if using FFI

// Then call it
// leash_gc_collect();  // Not normally needed!
```

*Note: The custom GC is implemented in `leash/gc.c` and `leash/gc.h`.*

### Manual Memory Management (`nogc`)

For low-level systems programming, Leash supports **manual memory management** via the `nogc` function modifier and the `--no-garbage-collector` / `-ngc` global flag.

#### `nogc` Functions

Mark a function with `nogc` to use C's `malloc`/`free` instead of the GC allocator:

```leash
unsafe nogc fnc main : int {
    buf: *char = (*char)malloc(256);
    strcpy(buf, "Hello, raw heap!");
    printf("%s\n", buf);
    free(buf);
    return 0;
}
```

Key behaviors:
- Memory allocated with `malloc`/`calloc`/`realloc` inside `nogc` functions uses the **C heap** directly
- The GC is **not initialized** for `nogc` main functions (saves startup time and memory)
- `nogc` functions called from GC-managed code produce a low-level checker warning
- Use `unsafe nogc` together to suppress safety checks (recommended for raw pointer code)

#### `--no-garbage-collector` / `-ngc`

The `-ngc` flag disables the garbage collector **globally** for the entire program:

```bash
leash run myprogram.lsh --no-garbage-collector
leash compile myprogram.lsh -ngc
```

When `-ngc` is active:
- The GC runtime (`gc.c`) is **not linked** into the binary
- All internal allocations (strings, vectors, etc.) use C `malloc`/`free` instead of `leash_gc_malloc`
- `gc_init` is never called — no startup overhead
- You are responsible for **every** `free()` — memory leaks are possible

> **Warning:** `-ngc` is intended for embedded, real-time, or minimal-binary-size scenarios. Use with extreme care.

#### C Standard Library Stubs

When using `nogc` (or `-ngc`), the following C standard library functions are available as built-in calls (no `@from` import needed):

| Header    | Functions |
|-----------|-----------|
| `stdlib.h` | `malloc`, `calloc`, `realloc`, `free`, `atoi`, `atol`, `atoll`, `strtol`, `strtoll`, `strtoul`, `strtof`, `strtod`, `abs`, `labs`, `abort`, `atexit`, `getenv`, `qsort`, `bsearch` |
| `string.h` | `strlen`, `strcpy`, `strncpy`, `strcat`, `strncat`, `strcmp`, `strncmp`, `strcasecmp`, `strchr`, `strrchr`, `strstr`, `strtok`, `strdup`, `memcpy`, `memmove`, `memset`, `memcmp`, `memchr`, `strpbrk`, `strspn`, `strcspn` |
| `stdio.h`  | `printf`, `sprintf`, `snprintf`, `fprintf`, `scanf`, `fscanf`, `sscanf`, `fopen`, `fclose`, `fread`, `fwrite`, `fgets`, `fputs`, `fgetc`, `fputc`, `ungetc`, `fseek`, `ftell`, `rewind`, `rename`, `remove`, `fflush`, `feof`, `perror`, `puts`, `getchar`, `putchar`, `tmpfile`, `setbuf`, `setvbuf`, `popen`, `pclose`, `fileno` |
| `ctype.h`  | `isalpha`, `isdigit`, `isalnum`, `isxdigit`, `isspace`, `isupper`, `islower`, `toupper`, `tolower`, `iscntrl`, `isprint`, `ispunct`, `isgraph` |
| `math.h`   | `sqrt`, `cbrt`, `fabs`, `ceil`, `floor`, `round`, `trunc`, `exp`, `log`, `log10`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sinh`, `cosh`, `tanh`, `pow`, `atan2`, `fmod` |
| `time.h`   | `clock`, `difftime`, `time`, `mktime`, `localtime`, `gmtime`, `asctime`, `ctime` |

These functions can also be called from regular (GC-managed) code — the type checker accepts `string` where `*char` is expected by performing an automatic conversion.

## Error Handling & Safety

Leash prioritizes developer experience with helpful error reporting and safety features:

### Custom Error Definitions

Leash allows you to define custom errors with the `error` keyword. Errors can take arguments and have a message expression that is evaluated when the error is thrown.

```leash
error ExpectedArguments(amount int, got int) -> "Expected " + tostring(amount) + " or more arguments but only got " + tostring(got) + ".";

fnc main(args string[]) {
    if args.size < 2 {
        throw ExpectedArguments(2, args.size);
    }
}
```

When an error is thrown:
1. The message expression is evaluated.
2. The error message is printed to the console along with the file, line, and column where it occurred.
3. The program terminates with exit code 1 (unless caught in a `works` block).

Errors can be marked `pub` (default) or `priv` for module visibility:

```leash
priv error InternalError(msg string) -> "[INTERNAL]: " + msg;
```

- **Static Type Checker**: The compiler validates types before generating code, catching undefined variables, incompatible assignments, and member access errors.
- **Smart Error Tips**: When a syntax error occurs, Leash provides actionable tips (e.g., suggesting a missing semicolon or parenthetical).
- **Runtime Union Checks**: Accessing union members is checked at runtime to ensure the correct "tag" is active, avoiding memory-unsafe operations common in C.
- **Runtime Safety Checks**: Division by zero, vector bounds, and null pointer dereferences are caught at runtime with descriptive error messages.
- **Error Codes**: Every error and warning has a unique code (e.g., `LEASH-E004`) for easy reference.

See the [Checking for Errors](#checking-for-errors) section for details on the `check` command, `--check` flag, and `--warnings-as-errors` option.
- **Runtime Safety Checks**: Division by zero, vector bounds, and null pointer dereferences are caught at runtime with descriptive error messages.
- **Error Codes**: Every error and warning has a unique code (e.g., `LEASH-E004`) for easy reference.

See the [Checking for Errors](#checking-for-errors) section for details on the `check` command, `--check` flag, and `--warnings-as-errors` option.

### Works-Otherwise Error Handling

Leash provides a `works...otherwise` construct for catching and handling errors. Unlike traditional try-catch, it can catch **any** error (undefined variables, type errors, etc.):

```leash
fnc main() : void {
    works {
        a: int = unknown_var;  // This would normally cause an error
    } otherwise err {
        show("Error caught: ", err);  // Prints: "Error caught: Undefined variable: 'unknown_var'"
    }
    show("Program continues...");
}
```

**How it works:**
- The `works` block attempts to execute its statements
- If any error occurs (undefined variable, type mismatch, etc.), instead of crashing, control jumps to the `otherwise` block
- The error variable (`err` in the example) contains a string describing what went wrong
- After the `otherwise` block completes, the program continues normally

```leash
fnc main() : void {
    works {
        x: int = 10;
        show("Works block executed");
    } otherwise err {
        show("Caught error: ", err);
    }
    show("After works");  // This runs because no error occurred
}
```

This feature is useful for graceful error handling, fallback logic, and recovering from unexpected conditions.

## Concurrency

Leash supports multi-threaded programming through **workers** — special functions that run concurrently in separate OS threads. Workers communicate through two kinds of global variables:

### Shared Variables (`shared`)

A `shared` variable allows **one thread** to write while any number of threads read. This is the default safe pattern for producer-consumer scenarios:

```leash
shared result: int = 0;  // Only one writer, many readers
```

- Only a single worker may write to a `shared` variable at any time
- Any number of workers may read the current value
- No atomicity guarantees — readers may see stale values until the writer's store is visible

### Fusion Variables (`fusion`)

A `fusion` variable allows **multiple threads** to both read and write concurrently. Writes use atomic stores and reads use atomic loads, so every thread eventually sees the latest value:

```leash
fusion counter: int = 0;  // Multiple readers and writers
```

- Any worker may read or write
- Changes are eventually visible to all threads (atomic semantics)
- Use for counters, flags, and other values that many threads need to share

### Worker Functions (`worker fnc`)

Mark a function with `worker fnc` instead of `fnc` to make it runnable in its own thread:

```leash
worker fnc calculate(a int, b int) {
    while !thisworker.interrupted {
        result = a + b + result;
        show("calculate updated result to: ", result);
    }
}
```

- Workers are declared just like regular functions but with the `worker` keyword
- They accept parameters like normal functions
- They run in an infinite loop by default — use `thisworker.interrupted` to check if the program is shutting down

### Spawning Workers (`spawn`)

Use `spawn` to launch a worker function in a new thread:

```leash
fnc main() {
    spawn calculate(3, 5);
    spawn increment_counter();
    spawn show_result();
}
```

- Each `spawn` creates a new OS thread running the given worker function
- Arguments are passed by value (copied into a heap-allocated struct)
- All spawned threads run concurrently with `main` and with each other
- Workers stop when the program terminates or when `thisworker.interrupted` becomes true (e.g., on Ctrl+C)

### The `thisworker` Built-in

Inside a worker function, the `thisworker` keyword provides access to the current worker's state:

```leash
worker fnc my_worker() {
    while !thisworker.interrupted {
        // Do work
    }
}
```

| Member | Type | Description |
|--------|------|-------------|
| `interrupted` | `bool` | `true` when the program is shutting down (e.g., Ctrl+C) |

### Full Example

```leash
shared result: int = 0;
fusion counter: int = 0;

worker fnc calculate(a int, b int) {
    while !thisworker.interrupted {
        result = a + b + result;
        show("calculate updated result to: ", result);
    }
}

worker fnc increment_counter() {
    while !thisworker.interrupted {
        counter = counter + 1;
        show("counter incremented to: ", counter);
    }
}

worker fnc show_result() {
    while !thisworker.interrupted {
        show("result:", result, " counter:", counter);
    }
}

fnc main() {
    spawn calculate(3, 5);
    spawn increment_counter();
    spawn show_result();
}
```

### Lifecycle & Cleanup

- Workers are automatically started when `spawn` is called
- They run until the program exits or they detect `thisworker.interrupted`
- Pressing Ctrl+C sets the interrupted flag, allowing workers to exit their loops cleanly
- The runtime waits for all workers to finish before the program fully exits
- Memory allocated in workers is handled by Leash's garbage collector (thread-safe)

### Thread Safety Notes

- `shared` variables have no atomicity guarantees — use them when only one worker writes
- `fusion` variables use atomic loads/stores, making writes visible across threads
- The garbage collector is fully thread-safe — allocations from any worker are safe
- The `show()` function is thread-safe (output lines may interleave)
- There is no built-in mutex or lock — use `fusion` variables for coordination

## Syntax Highlighting

Leash provides native syntax highlighting for popular editors. You can find the files in the `syntax_highlighters/` directory.

### Vim / Neovim

1. Copy the `leash.vim` file to your `~/.vim/syntax/` (Vim) or `~/.config/nvim/syntax/` (Neovim) directory:
   ```bash
   mkdir -p ~/.vim/syntax
   cp syntax_highlighters/leash.vim ~/.vim/syntax/
   ```
2. Tell Vim to use it for `.lsh` files by adding this to your `.vimrc` or `init.vim`:
   ```vim
   au BufRead,BufNewFile *.lsh set filetype=leash
   ```

### VS Code (with LSP Support)

The VS Code extension provides syntax highlighting, real-time diagnostics, hover tooltips, go-to-definition, and auto-completion. It features a self-contained Node.js Language Server that uses the Leash compiler for diagnostics.

#### Prerequisites
- **Node.js** (for the extension itself)
- **Leash** compiler installed and in your PATH.

#### Method 1: Manual Installation (Ready to use)
1. Copy the `syntax_highlighters/vscode` directory to your VS Code extensions folder:
   - **Windows**: `%USERPROFILE%\.vscode\extensions\leash`
   - **macOS/Linux**: `~/.vscode/extensions/leash`
2. Restart VS Code.

#### Method 2: Development / From Source
1. Navigate to the extension directory:
   ```bash
   cd syntax_highlighters/vscode
   ```
2. Install Node.js dependencies and compile the extension:
   ```bash
   npm install
   npm run compile
   ```
3. Copy the compiled directory to your extensions folder (as described in Method 1).

#### Method 3: VSIX Package (Standard)
1. Install `vsce` globally:
   ```bash
   npm install -g @vscode/vsce
   ```
2. Build the package:
   ```bash
   cd syntax_highlighters/vscode
   npm run package
   ```
3. Install the generated `leash-0.21.0.vsix` in VS Code (Extensions view -> `...` -> `Install from VSIX...`).

### Emacs

1. Move `leash-mode.el` to your load path (e.g., `~/.emacs.d/lisp/`):
   ```bash
   mkdir -p ~/.emacs.d/lisp
   cp syntax_highlighters/leash-mode.el ~/.emacs.d/lisp/
   ```
2. Add the following to your `init.el` or `.emacs` file:
   ```elisp
   (add-to-list 'load-path "~/.emacs.d/lisp/")
   (require 'leash-mode)
   ```
