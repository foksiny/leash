# Leash Programming Language

**Version 1.0.0** — the first release where Leash compiles itself.

Leash is a strongly-typed, modern compiled programming language built on LLVM. It features an intuitive syntax and native performance with a built-in garbage collector, package manager, and cross-platform support.

> **Full documentation available at [`docs/`](docs/index.html) &mdash; covers language reference, compiler CLI, standard library, package manager, concurrency, and advanced topics.**

## Self-Hosted since 1.0

The compiler shipped as `leash` is **written in Leash**. Installing the package bootstraps it: the Python reference compiler builds the first self-hosted binary, that binary then compiles itself, and the result is what gets installed — verified as a **bootstrap fixed point** (stage N emits IR byte-identical to stage N+1).

- `leash` — the **self-hosted compiler**, the whole toolchain natively: `compile`, `run` (file or project), `check`, `dump`, `init`, `build`, `install`, `update`, `self-host` (3-stage bootstrap + fixed-point check), and `dbg` (the interactive source-level debugger).
- `leashp` — the pure Python compiler/toolchain, kept as the reference implementation and for cross-compilation targets other than `linux64`.
- `leashc` — an alias of `leash`.

Output parity with the reference compiler is enforced by a test harness: **88/88 example programs compile and run with identical output** on both compilers.

## Quick Start

### Prerequisites

| Dependency | Version |
|------------|---------|
| **Python 3** | 3.8+ (build stage 0 + `leashp`) |
| **llvmlite** | installed automatically by pip |
| **C compiler** (`clang`/`gcc`) | any recent (builds the self-hosted compiler) |

### Install

```bash
pip install .
leash version
# leash 1.0.0 (self-hosted)
```

The install compiles `compiler/*.lsh` twice (Python → stage 1 → itself) and ships the resulting native compiler inside the package. No repository checkout is needed afterwards — the binary locates its runtime from any working directory.

### Run a program

```bash
leash run hello.lsh
```

### Compile to a binary

```bash
leash compile hello.lsh -o hello
./hello
```

### Scaffold a project

```bash
leash init my_project
cd my_project
leash build
leash run
```

### Rebuild the compiler from source

```bash
git clone https://github.com/foksiny/leash && cd leash
pip install .
leash self-host   # 3-stage bootstrap + fixed-point check, installs bin/leashc
```

## Key Features

- **Self-hosted compiler** — the language's own compiler is written in the language, with a verified bootstrap fixed point and 88/88 output parity against the reference implementation
- **Strongly typed** with full type inference, generics (`class<T>`, multi-type `[int, float, ...]` parameters), operator overloading (`opdef`) and explicit bit-width integers/floats (`int<128>`, `uint<512>`, …)
- **LLVM-powered** compilation with optimization levels O0-O3, Os, Oz
- **Precise garbage collection** — pointer-free vector buffers are never scanned (no false pins from integer payloads); unboxed inline element storage with reference semantics
- **Package manager** (`leashed`) with fully self-service publishing — registry updates are validated and merged by a bot, no human review; install from the index or any git URL; **reproducible project builds** with `leash.lock`
- **Concurrency model** with workers, `shared` and `fusion` variables — plus native **`async fnc`/`await`** returning `future<T>`
- **Cross-platform** compilation for Linux (x86-64 and ARM64), Windows, and macOS (via `leashp` for non-native targets)
- **100+ standard-library packages** — vectors, matrices, hash tables, file I/O, string/text tools, math, crypto, SQL, HTTP, games, AI, testing helpers…
- **Native HTTP/HTTPS client** (`http`) and **multiplexed keep-alive HTTP/1.1 server** (`httpserver`) — full web stack with no external dependencies
- **Native source-level debugger** (`leash dbg`) — step/breakpoint debugging without GDB/LLDB, breakpoints from the CLI (`-b <line>`) or interactively
- **FFI** via `@from` directive for calling C/C++/Rust libraries (including Leash-language function-pointer callbacks)

## Documentation

| Topic | Location |
|-------|----------|
| **Installation & Setup** | [docs/getting-started.html](docs/getting-started.html) |
| **Language Reference** | [docs/language-guide.html](docs/language-guide.html) |
| **Compiler & CLI** | [docs/compiler.html](docs/compiler.html) |
| **Self-Hosted Compiler** | [docs/self-hosted.html](docs/self-hosted.html) |
| **Standard Library** | [docs/stdlib.html](docs/stdlib.html) |
| **Package Manager** | [docs/package-manager.html](docs/package-manager.html) |
| **Concurrency** | [docs/concurrency.html](docs/concurrency.html) |
| **Advanced Topics** (FFI, memory, error handling) | [docs/advanced.html](docs/advanced.html) |

## Syntax Highlighting

Highlighting files for Vim, VS Code (with LSP), and Emacs are in [`syntax_highlighters/`](syntax_highlighters/).

## License

See [LICENSE](LICENSE).
