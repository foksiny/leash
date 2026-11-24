<p align="center">
  <img src="icons/svg/leash-logo.svg" alt="Leash" width="420">
</p>

<p align="center">
  <a href="https://github.com/foksiny/leash/releases"><img src="https://img.shields.io/badge/version-2.0.0-8b5cf6?style=flat-square" alt="Version 2.0.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square" alt="MIT License"></a>
  <a href="docs/index.html"><img src="https://img.shields.io/badge/docs-full%20reference-22d3ee?style=flat-square" alt="Documentation"></a>
  <a href="https://github.com/foksiny/leash"><img src="https://img.shields.io/badge/compiler-self--hosted-f97316?style=flat-square" alt="Self-hosted"></a>
</p>

# Leash Programming Language

**Version 2.0.0** — a strongly typed, compiled language with deterministic memory, a self-hosted compiler, and a real standard library.

Leash compiles to native code through LLVM. It gives you an expressive, statically checked language with generics, operator overloading, structured error handling and first-class concurrency — while keeping the memory model explicit: the **scoped arena is the default layout**, with `--gc` available for the classic mark-and-sweep collector.

> **New in 2.0.0:** arena is now the default memory layout; `getint` / `getfloat` / `getstruct`, `string.start()` / `string.end()`, and optional semicolons. See the [changelog](CHANGELOG.md#200---2026-11-22).

> **Full documentation:** [`docs/index.html`](docs/index.html) — language reference, compiler CLI, standard library, package manager, concurrency, and advanced topics.

## Why Leash?

- **Compiles itself.** The shipped `leash` / `leashc` binaries are written in Leash and rebuilt from a checked-in bootstrap seed with no Python dependency (verified three-stage fixed point, 89/89 output parity corpus).
- **Deterministic memory by default.** Thread-local bump allocation, whole-scope release, poison-on-reclaim, escape snapshots and a compile-time lifetime audit. No stop-the-world pauses on the hot path.
- **Familiar syntax, strong guarantees.** Full type inference, explicit bit widths (`int<128>`, `uint<512>`), generics, restricted literal types, nil-flow analysis, and range-checked numeric conversions.
- **Batteries included.** 100+ standard-library packages, a self-service package manager with reproducible `leash.lock` builds, native HTTP/HTTPS client and server, SQL, crypto, and cross-platform compilation.
- **Tooling that teaches.** Structured diagnostics with source snippets, why-it-happened explanations and fix hints; syntax highlighting and LSP completion for Vim, Emacs and VS Code; a native source-level debugger.

Leash was made with the help of AI, but its design and ideas come from a human. The AI implemented the language, toolchain and documentation; the human and AI stress-tested it together, fixing quirks as they were found. Features are kept stable and reliable rather than being shipped half-finished.

## Quick start

### Requirements

| Tool | Why |
|------|-----|
| C compiler (`clang` or `gcc`) | Links generated programs and the runtime |
| Git | Only for `leash pkg publish`, `leash pkg install` and `leash update` |

### Install from source

```bash
git clone https://github.com/foksiny/leash && cd leash
./scripts/bootstrap.sh     # Python-free: seed -> stage1 -> stage2 -> stage3
bin/leashc version
# leash 2.0.0 (self-hosted)
```

Prebuilt release bundles are attached to every [GitHub release](https://github.com/foksiny/leash/releases) for Linux x86-64, Linux x86, Linux ARM64, Windows x86-64 and the JVM target. Each bundle includes the compiler, runtime, standard library, docs and an installer:

```bash
tar -xzf leash-2.0.0-linux-x86_64.tar.gz && cd leash-2.0.0-linux-x86_64
./install.sh
export PATH="$HOME/.leash/bin:$PATH"
```

For a global install from a checkout:

```bash
bin/leashc install compiler
export PATH="$HOME/.leash/bin:$PATH"   # put this in ~/.bashrc (use $HOME, not "~")
```

The compiler finds its runtime next to the executable, under `~/.leash/leash`, or wherever `LEASH_RUNTIME` points.

### Write and run a program

```leash
fnc main() : void {
  name := get("What is your name? ")
  show("Hello, {name}!")
}
```

```bash
bin/leashc run hello.lsh            # arena by default
bin/leashc run hello.lsh --gc       # classic collector
bin/leashc compile hello.lsh to hello
./hello
```

### Projects and rebuilding

```bash
bin/leashc init my_project
cd my_project
bin/leashc build
bin/leashc run

bin/leashc self-host   # 3-stage bootstrap + fixed-point check
```

## Feature tour

### Language

| Feature | Example / where to read |
|---------|-------------------------|
| Type inference & generics | `x := 42`, `class Box<T>`, `fnc id<T>(x: T) : T` — [language guide](docs/language-guide.html#generics) |
| Bit-width numerics | `int<8>`, `uint<512>`, checked conversions, guarded division — [language guide](docs/language-guide.html#numeric-types) |
| Restricted literal types | `def status : type "ok", "error", nil;` — [language guide](docs/language-guide.html#restricted-literal-types) |
| Optional semicolons | `a := 1` followed by a newline ends the statement — [language guide](docs/language-guide.html#variables) |
| Strings | Unicode-aware `size`, UTF-8 `bsize`, `replace`, new `start(n)` / `end(n)` — [language guide](docs/language-guide.html#string-methods) |
| Console input | `get`, `keyget`, new `getint`, `getfloat`, `getstruct(Type, prompt)` — [language guide](docs/language-guide.html#input-handling) |
| Structs, classes, unions | Value structs, reference classes, tagged unions with variant safety — [language guide](docs/language-guide.html#structs-and-classes) |
| Errors | `error` types, `throw`, `works`/`otherwise`, structured diagnostics — [advanced](docs/advanced.html#error-handling) |
| Unsafe & FFI | `@from` native libraries, `unsafe fnc`, raw pointer rules — [advanced](docs/advanced.html#ffi) |

### Runtime & memory

| Mode | Flag | Notes |
|------|------|-------|
| **Scoped arena (default)** | *(none)* / `--arena` | Bump blocks, scope reclamation, poison by default, escape snapshots, strict lifetime audit |
| Garbage collected | `--gc` | Precise mark-and-sweep; recommended for raw-pointer-heavy FFI code |
| No GC | `-ngc` | Plain `malloc` / `free` |
| Auto-free | `-af` / `--autofree` | `malloc` plus automatic free at function exit |

Native programs default to the arena; the JVM target always uses its own GC. C-ABI library outputs (`to-static` / `to-dynamic`) default to GC so external linkers only need the classic runtime.

### Toolchain

- **LLVM pipeline** with a monotonic `-O0`…`-O4`, `-Os`, `-Oz` scale (LTO and recursive-call unrolling at O3+).
- **Cross compilation** for Linux x86-64/x86/ARM64, Windows x86-64 and macOS from one compiler.
- **Package manager** (`leash pkg`) with self-service publishing, git sources, semver resolution and `leash.lock`.
- **Debugger** (`leash dbg`) with breakpoints and source-level stepping.
- **Editor support** — [Vim](syntax_highlighters/leash.vim), [Emacs](syntax_highlighters/leash-mode.el) and [VS Code with LSP](syntax_highlighters/vscode/).

## Documentation map

| Topic | Location |
|-------|----------|
| Overview & feature tour | [docs/index.html](docs/index.html) |
| Installation & first program | [docs/getting-started.html](docs/getting-started.html) |
| Language reference | [docs/language-guide.html](docs/language-guide.html) |
| Compiler CLI & optimization levels | [docs/compiler.html](docs/compiler.html) |
| Self-hosted compiler & bootstrap | [docs/self-hosted.html](docs/self-hosted.html) |
| Standard library catalogue | [docs/stdlib.html](docs/stdlib.html) |
| Package manager | [docs/package-manager.html](docs/package-manager.html) |
| Concurrency model | [docs/concurrency.html](docs/concurrency.html) |
| Advanced topics (FFI, memory, errors) | [docs/advanced.html](docs/advanced.html) |
| Known limitations | [docs/limitations.html](docs/limitations.html) |

## Known limitations

Leash 2.0.0 is a complete, self-hosted toolchain. The limits below are intentional and scoped; each has a documented workaround.

| Limitation | Severity | Comment |
|------------|----------|---------|
| Arena is the default native layout | 🟡 Medium | Frame-based lifetimes with no borrow checker. Returns/spawn escape by copy or into the root arena; union payloads are rooted. Raw pointers keep provenance through locals: proven-safe returns, globals, stores and worker arguments are accepted, and only unprovable pointers are rejected. Escaping addresses are root-allocated (or immortal in workers) with their values rooted, so scalars, strings, vectors and structs stay valid after the producing frame pops. Reclaimed memory is poisoned by default and misuse aborts. See [docs/advanced.html](docs/advanced.html). |
| Windows `serve_tls` is experimental | 🟢 Low | SChannel backend; cross-compiled and exercised under Wine, but not yet battle-tested on real Windows hosts. |
| JVM `sql` is a mini engine, not SQLite | 🟢 Low | The shim is a small interpreter with its own file format, not `sqlite3` files. |
| HTTP/3 / QUIC | 🟢 Low | Not implemented, and not planned for 2.x. |

See [docs/limitations.html](docs/limitations.html) for the full explanation.

## Compatibility promise

Within 2.x we guarantee:

- Existing 2.x programs keep compiling — no new required syntax.
- `--gc` stays available so programs that rely on GC timing keep working.
- No feature is removed within 2.x.

We do **not** guarantee bit-identical behaviour for patterns that were undefined or defective, or that a bug fix preserves observable buggy behaviour. The 2.0.0 major bump marks the arena becoming the default layout: programs relying on GC timing, finalization order or GC-specific memory growth should be compiled with `--gc`.

## Contributing

Bug reports and focused pull requests are welcome at [github.com/foksiny/leash](https://github.com/foksiny/leash). When reporting a compiler diagnostic, include the source snippet, the command line and the full diagnostic — the compiler is designed to explain itself.

## License

See [LICENSE](LICENSE).
