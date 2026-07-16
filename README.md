# Leash Programming Language

**Version 0.21.0b0 Beta**

Leash is a strongly-typed, modern compiled programming language built on LLVM. It features an intuitive syntax and native performance with a built-in garbage collector, package manager, and cross-platform support.

> **Full documentation available at [`docs/`](docs/index.html) &mdash; covers language reference, compiler CLI, standard library, package manager, concurrency, and advanced topics.**

## Quick Start

### Prerequisites

| Dependency | Version |
|------------|---------|
| **Python 3** | 3.8+ |
| **LLVM** (dev libs) | 11+ |
| **C compiler** (`gcc` / `clang`) | Any recent |

### Install

```bash
pip install -e .
leash --version
```

### Run a program

```bash
python3 -m leash.cli run hello.lsh
```

### Compile to a binary

```bash
python3 -m leash.cli compile hello.lsh
./hello
```

### Scaffold a project

```bash
leash init my_project
leash build
leash runp
```

## Key Features

- **Strongly typed** with full type inference and explicit bit-width integers/floats
- **LLVM-powered** compilation with optimization levels O0-O4, LTO, and PGO
- **Built-in garbage collector** with optional `nogc` manual mode
- **Package manager** (`leashed`) for publishing and installing libraries
- **Concurrency model** with workers, `shared` and `fusion` variables
- **Cross-platform** compilation for Linux, Windows, and macOS
- **Rich standard library** with vectors, matrices, hash tables, file I/O, math, and windowing
- **FFI** via `@from` directive for calling C/C++/Rust libraries

## Documentation

| Topic | Location |
|-------|----------|
| **Installation & Setup** | [docs/getting-started.html](docs/getting-started.html) |
| **Language Reference** | [docs/language-guide.html](docs/language-guide.html) |
| **Compiler & CLI** | [docs/compiler.html](docs/compiler.html) |
| **Standard Library** | [docs/stdlib.html](docs/stdlib.html) |
| **Package Manager** | [docs/package-manager.html](docs/package-manager.html) |
| **Concurrency** | [docs/concurrency.html](docs/concurrency.html) |
| **Advanced Topics** (FFI, memory, error handling) | [docs/advanced.html](docs/advanced.html) |

## Syntax Highlighting

Highlighting files for Vim, VS Code (with LSP), and Emacs are in [`syntax_highlighters/`](syntax_highlighters/).

## License

See [LICENSE](LICENSE).
