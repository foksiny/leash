<p align="center">
  <img src="icons/svg/leash-logo.svg" alt="Leash" width="460">
</p>

# Leash Programming Language

**Version 1.6.0** — Leash compiles itself.

Leash is a strongly-typed, modern compiled programming language built on LLVM. It features an intuitive syntax and native performance with a built-in garbage collector, package manager, and cross-platform support.

> **Full documentation available at [`docs/`](docs/index.html) &mdash; covers language reference, compiler CLI, standard library, package manager, concurrency, and advanced topics.**

## Self-Hosted since 1.0

The compiler shipped as `leash`/`leashc` is **written in Leash** and builds itself from a checked-in bootstrap seed with **no Python dependency**: the seed binary compiles the current sources, that binary then compiles itself, and the result is what gets installed — verified as a **bootstrap fixed point** (stage N emits IR byte-identical to stage N+1).

- `leashc` (alias `leash`) — the **self-hosted compiler**, the whole toolchain natively: `compile`, `run` (file or project), `check`, `dump`, `init`, `build`, `install`, `update`, `pkg` (the package manager), `self-host` (3-stage bootstrap + fixed-point check), and `dbg` (the interactive source-level debugger).

Output parity with the original reference compiler is enforced by a frozen corpus: **88/88 example programs compile and run with identical output**.

## Quick Start

### Prerequisites

| Dependency | Version |
|------------|---------|
| **C compiler** (`clang`/`gcc`) | any recent (links the compiled programs) |

### Install

```bash
git clone https://github.com/foksiny/leash && cd leash
./scripts/bootstrap.sh     # seeds from bootstrap/, builds bin/leashc (Python-free)
bin/leashc version
# leash 1.6.0 (self-hosted)
```

The bootstrap compiles `compiler/*.lsh` and installs the resulting native compiler at `bin/leashc`. Optionally put it on your `PATH`:

```bash
cp bin/leashc ~/.local/bin/leash
```

### Run a program

```bash
bin/leashc run hello.lsh
```

### Compile to a binary

```bash
bin/leashc compile hello.lsh to hello
./hello
```

### Scaffold a project

```bash
bin/leashc init my_project
cd my_project
bin/leashc build
bin/leashc run
```

### Rebuild the compiler from source

```bash
bin/leashc self-host   # 3-stage bootstrap + fixed-point check, installs bin/leashc
```

## Key Features

- **Self-hosted compiler** — the language's own compiler is written in the language, with a verified bootstrap fixed point and 88/88 output parity against the reference implementation
- **Strongly typed** with full type inference, generics (`class<T>`, multi-type `[int, float, ...]` parameters), operator overloading (`opdef`) and explicit bit-width integers/floats (`int<128>`, `uint<512>`, …)
- **LLVM-powered** compilation with optimization levels O0-O4, Os, Oz (O4 = O3 pipeline + aggressive inlining)
- **Precise garbage collection** — pointer-free vector buffers are never scanned (no false pins from integer payloads); unboxed inline element storage with reference semantics
- **Package manager** (`leash pkg`) with fully self-service publishing — registry updates are validated and merged by a bot, no human review; install from the index or any git URL; **reproducible project builds** with `leash.lock`
- **Concurrency model** with workers, `shared` and `fusion` variables — plus native **`async fnc`/`await`** returning `future<T>`
- **Cross-platform** compilation for Linux (x86-64, x86, ARM64), Windows, and macOS — all targets built by the native compiler (`--target win64`, `--target linux-arm`, …), with `wine`/`qemu` execution for cross binaries
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
| **Known Limitations** | [docs/limitations.html](docs/limitations.html) |

## Known Limitations

Leash 1.6.0 is a complete, self-hosted toolchain with no Python in the build or run path. The items below are the honest, currently-known limits of the platform &mdash; each is intentional and stable for 1.x.

| Limitation | Severity | Comment |
|------------|----------|---------|
| Windows `serve_tls` is experimental | 🟢 Low | SChannel backend; cross-compiled and exercised under Wine, but not yet battle-tested on real Windows hosts. |
| JVM `sql` is a mini engine, not SQLite | 🟢 Low | All stdlib modules now run on the JVM via bundled Java shims; the `sql` shim is a small interpreter with its own file format (not sqlite3 files). |
| HTTP/3 / QUIC | 🟢 Low | Not implemented, but nobody expects it in 1.x. |

See [docs/limitations.html](docs/limitations.html) for the full explanation of each item.

## Syntax Highlighting

Highlighting files for Vim, VS Code (with LSP), and Emacs are in [`syntax_highlighters/`](syntax_highlighters/).

## License

See [LICENSE](LICENSE).
