# Changelog

All notable changes to the Leash compiler are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.4.0] - 2026-10-16

Minor release: HTTP/2 for the standard library — the `http` client and
`httpserver` both speak h2c (cleartext prior knowledge) and h2 over TLS
(ALPN negotiation with HTTP/1.1 fallback), plus a modernized HTTP toolbox
(protocol reporting, auth helpers, reply shortcuts). Native `@from`
functions on the JVM backend now fail loudly at call time instead of at
import, so mixed native/pure modules stay importable. The stdlib suite
grows to 101 tests.

### Added — HTTP/2 (RFC 9113 + HPACK RFC 7541)
- Shared HPACK codec (`installthis/*/src/lshhpack.h`, tables validated
  against the RFC 7541 Appendix C vectors): static table, full decode
  (indexed, incremental/no-index literals, Huffman, dynamic table with
  eviction) and compact encode (indexed + literal-no-index, Huffman when
  shorter).
- Client (`http`): `request_h2`/`request_h2_ex`, all verb shortcuts
  (`get_h2`/`post_h2`/`put_h2`/`patch_h2`/`delete_h2`/`head_h2`/
  `options_h2`), `download_h2`/`download_h2_ex`. Cleartext `http://`
  speaks h2c; `https://` offers ALPN and speaks h2 when selected, else
  finishes over HTTP/1.1. POSIX implements framing over TCP/TLS;
  Windows maps to WinHTTP with protocol reporting.
- Server (`httpserver`): h2c via client-preface detection on the same
  port (same handler contract, same keep-alive engine), and
  `serve_tls`/`serve_tls_ex(port, cert, key, handler)` — one TLS listener
  serving h2 (ALPN) and HTTP/1.1 concurrently. SIGPIPE is scoped-ignored
  while serving (a process-global refcount shared through the runtime, so
  client and server threads stay covered together) so vanishing peers
  cannot kill the host.
- `HttpResponse.proto` (`"http/1.1"`/`"h2"`), `is_h2()`,
  `content_type()`; `Http.options`, `basic_auth`/`bearer` header
  helpers, private Base64; `HttpServer.reply_json`/`reply_html`/
  `reply_text`.
- Raw C-heap (`malloc`/`free`/`strcpy`/...) and C stdio buffers
  (`scanf`/`sprintf`/...) are clean JVM compile errors; `printf(fmt…)`
  runs on the JVM via `String.format` (Locale.ROOT); `gc_collect()` is a
  JVM no-op.

### Changed
- `Http.version()` is `leash-http/1.1`, `HttpServer.version()` is
  `leash-httpd/1.2`; existing version asserts updated.
- Windows archives rebuilt (`win/liblshhttp.a`, `win/liblshhttpd.a`);
  Linux `liblshhttpd.a` now bundles OpenSSL like the client archive.
  macOS/ARM users rebuild via `src/build.sh` (documented in READMEs).
- `HttpResponse` gains the `proto` field (set it in exhaustive
  literals).
- Syntax highlighters (vim, emacs, vscode TextMate + language server):
  `async`/`await`/`nogc` keywords, `gc_collect`/`gc_stats`/`getenv`
  builtins, `print`/`println`/`panic`/`assert`, `vector`/`matrix` types.

### Fixed
- H2 buffer NUL termination (garbage bytes on POST bodies), `serve_ex`
  exit ordering vs handler shutdown, and the TLS listener startup race in
  tests (patient retries instead of instant ones).

## [1.3.3] - 2026-10-16

Minor release: ChaCha20 CSPRNG for all randomness, and the JVM backend
reaches 96/99 stdlib parity — unions, generic classes, `async`/`await`,
workers/`spawn`, and 128-bit integers now run on the JVM with native
output parity. The 3 remaining stdlib files need real native code
(sockets/TLS/SQLite via `@from`) and stay clean unsupported errors.

### Added — JVM backend
- Unions: `Object[]{tag, payload}` representation with native-exact
  semantics — auto variant selection, tag-checked reads (same abort
  message, skipped under `unsafe`), `.cur` smart reads, `show`/`typeof`
  support, and bit-exact `int64`<->`double` reinterpretation.
- Generic classes via monomorphized clones (template-name and type
  parameter resolution mirrors the native backend).
- `async fnc` / `await` / `future<T>` over `CompletableFuture` and a
  shared cached pool (per-function `Supplier` tasks); `spawn` /
  `worker fnc` reuse the same pool.
- `int<128>` / `uint<128>` via `BigInteger`: full arithmetic,
  comparisons, shifts, bitwise ops, compound assignment, `++`/`--`,
  `show`, conversions (unsigned sources keep magnitude).
- `getenv()` (`System.getenv`), `exit()` (`System.exit`), string
  subtraction (`a - b`, all-occurrences removal), `pointer<char>` as
  nullable string, `__toint_conv` conversion nodes.
- Cross-class runtime helpers are public (stdlib classes call
  main-class helpers without `IllegalAccessError`).
- Uninitialized strings default to `""` (native parity: `!= null`,
  concatenates empty); uninitialized struct/class locals are default
  constructed instead of null.

### Changed
- `rand` / `randf` / `choose` / `seed` now run on a ChaCha20-based
  CSPRNG (RFC 8439, 64-bit counter, zero nonce) on both the native C
  runtime and the JVM backend: integer ranges use unbiased rejection
  sampling, `randf` uses full 53-bit draws, unseeded draws pull OS
  entropy, and `seed(n)` selects a bit-identical stream on every
  backend. Seeded golden output updated (`tests/expected/rand.lsh.out`).
- `docs/compiler.html`: JVM capability table updated (async needs
  Java 8+; `@from` native libraries stay native-only).

### Fixed
- JVM `&&` with non-constant operands computed NOR (literals
  constant-folded, hiding the bug); short-circuit lowering rewritten.
- JVM `choose()` never ran (call site built `Object[]` against a
  `String[]` helper signature); helper now takes `Object[]` with a
  checked cast on retrieval.
- JVM string/`char` concatenation produced code points (`"a" + 'b'`
  gave `"a98"`); chars now materialize as single characters.
- JVM `s == null` failed (`tostring()` of nil); nil sides pass through
  to `Objects.equals`.
- JVM `LALOAD`/`DALOAD` stack accounting (2-slot results tracked as 1).
- JVM class-cast on `(vec<T>){...}` element copies (verifier-safe
  per-lane conversion; empty literals emit empty lists directly).
- Native `toint` with `int128`/`uint128` aliases now resolves to the
  wide type (plus full-precision decimal parse for >64-bit targets).
- `tests/stdlib/text_tmpl.lsh` escaped its `{{...}}` literals (it was
  failing on every toolchain; baseline re-recorded).
- `tests/stdlib/misc_log.lsh` closes the log file before reading it
  back (native buffering made the old assertion backend-dependent).

## [1.3.2] - 2026-10-15

Patch release: JVM backend complete (`--target jvm`), File I/O implemented
on JVM, `exec` builtin ported, WIDE local-index support, command
dispatch tightened. All stdlib + native gates still green.

### Added
- JVM File I/O: `File.open` (r/w/a modes), `read`, `write`, `writebytes`,
  `close`, `size`, `rename`, `delete`, with byte-faithful read/write
  (ISO-8859-1).
- JVM `exec` builtin: stdout capture and `code`/`run`/`wait` mode parity.
- WIDE-prefixed local slot load/store/index handling (more than 255 locals).
- docs: JVM target tables, `_PLATFORM == "jvm"`, changelog sync to 1.3.2.

### Changed
- `embedded` python CLI help/version and self-hosted banner report v1.3.2.
- Docs version badges now report v1.3.2.

### Fixed
- `int[]` array-literal and `int<64>` literals had regression under JVM.
- Self-hosted compiler no longer crashes on JvmGen fields after
  compilation: `CodeGen.make` fully initializes state.

## [1.2.2] - 2026-10-13

Patch release: worker-shared globals actually work, index stores exist,
and parse errors fail the build.

### Fixed — `shared` / `fusion` globals in the self-hosted compiler
- The `shared` and `fusion` modifiers were silently dropped (with a
  non-fatal parse error): every such global compiled as a plain
  global, so `fusion` spin-waits hung under optimization while the
  reference returned immediately. Both modifiers now parse (with
  optional `pub`/`priv`, accepted and ignored as for all stage1
  globals), `fusion` integer globals emit sequentially-consistent
  atomic loads/stores, and `x = x +/- k` (plus `+=`/`-=`/`++`/`--`)
  lowers to `atomicrmw` exactly like the reference. Non-integer,
  sub-byte, and non-byte-multiple widths keep plain access, also like
  the reference.
- The `shared` one-writer rule is enforced at typecheck time (two
  writing functions are an error pointing at `fusion`).

### Fixed — Index assignment (`v[i]`, `a[i]`, `m[k]`)
- Element stores were silently dropped (`v[0] = 42` kept `10`,
  `m["k"] = 42` read back `0`). New index-store paths mirror the
  reads: hash push with key boxing, bounds-checked vector slots,
  fixed/slice element stores with width coercion, and compound ops
  through the general compound emitter.
- String element writes are a typecheck error (reference E013 parity).
- Reference fix: hash updates never landed — `_hash_append_entry`
  blind-appended, so `get` kept returning the first entry (`m["k"] =
  43` after `42` still read `42`, and `m["k"] += 2` was a no-op).
  Push now updates the existing entry in place, matching the C
  runtime the self-hosted compiler already used correctly.

### Fixed — Prefix `++` / `--` and member inc/dec
- Prefix forms were dropped with a successful build; member `c.n++`
  stored `1`. Prefix now parses to `++p`/`--p` and lowers in codegen
  (new value for prefix, old for postfix, python parity), including
  atomic lowering on `fusion` globals. Statement `++`/`--` keeps its
  assign form, and member/global compound stores that only handled
  `+=`/`-=`/`*=` now route every compound op through the general
  emitter. Inc/dec on non-assignable targets is a clean typecheck
  error instead of silently dropped code.
- Reference fix: float `++`/`--` emitted `add double, 1` (invalid IR,
  internal crash). The one-constant now matches the operand type
  (`1.0` with `fadd`/`fsub` for floats).

### Fixed — Parse errors fail the build
- Recovery errors were ignored, so damaged programs "compiled".
  Any residual parse error now fails every emitting path (compile,
  run, libraries, dumps, modules included) with a summary line; the
  corpus has zero, so no valid program is affected. `--check` stays
  lenient — doc fragments and editor buffers are often incomplete,
  and the doc-test suite checks the declarations inside such
  fragments. (Python exits 1 on `check` of broken code as well; that
  narrow exit-code difference is accepted to keep fragment checking
  working.)

## [1.2.1] - 2026-10-13

Patch release: exact sub-64-bit integer semantics, slice stores across
element widths, and a clean diagnostic for entry-point-less programs.

### Fixed — `int<8>` / `int<16>` / `int<32>` kept their exact widths
- The self-hosted frontend erased signed sub-64 widths (`int<8>` →
  `int`, in the parser and in alias resolution) while `uint<8>` and
  friends kept theirs, and codegen mapped the survivors to `i64`.
  Narrow signed integers therefore never wrapped: `add8(100, 100)`
  printed `200` instead of `-56`, and `int<8>[4]` array elements read
  back untruncated. Both layers now preserve exact widths (python
  parity): same-width arithmetic wraps, mixed-width operands promote
  to the wider side, and `get_expr_type` reports the promoted type so
  downstream loads match the register width.
- `tostring()` on narrow integers now extends with the value's
  signedness instead of passing an `i8`/`i16` register to an `i64`
  slot (invalid IR).

### Fixed — Fixed-array / slice stores convert element-wise
- Storing a slice into a fixed array (or any slice slot) of a
  different element width reinterpreted the backing store: `a:
  int<64>[3] = {1, 2, 3}` read back packed pairs plus out-of-bounds
  garbage. New `adapt_slice` conversion (widen/narrow/float/int
  table mirroring the scalar coercions) runs at variable
  declarations, assignments, struct/class field stores, call
  arguments, returns, and global/static initializers. Untracked by
  design under `--autofree` (the product may escape; leaking is
  output-safe).
- `examples/arrays.lsh` now matches the reference byte-for-byte.

### Fixed — Clean error for programs without `main`
- Compiling a `main`-less file (e.g. `examples/alias_tst.lsh`) as an
  executable used to dump a raw linker error in both compilers. Both
  now report `error: no entry point: ...` (exit 1) and point at
  `to-dynamic`/`to-static`; `--check` and IR dumps stay exempt.

### Kept as-is (documented)
- `float<16>` / `float<32>` still compute at `double` precision in the
  self-hosted compiler (the reference uses `half`/`float` types, but
  no observable divergence was found: `1/3`, `0.1` accumulation, and
  `100000.0` overflow all print identically).

## [1.2.0] - 2026-10-13

Minor release: the self-hosted compiler builds native libraries and
frees memory without a collector — no Python fallback remains.

### Added — Native `to-dynamic` / `to-static` library outputs
- `leash compile program.lsh to-dynamic mylib` / `to-static mylib`
  (bare mode flags plus `to <name>`, same CLI shape as the reference)
  is implemented natively in the self-hosted compiler; the
  "use `leashp`" error is gone.
- Libraries emit position-independent objects (`-fPIC`, separate
  object-cache key), target-gated extensions (`.so`/`.a` on Linux,
  `.dylib`/`.a` on macOS, `.dll`/`.lib` on win64, never `.exe`), and
  omit the `@main` wrapper so consumers link their own `main`.
- New `leash_init()` entry point (once-guarded): runs the runtime
  prologue — GC init, scan regions, global/static initializers —
  without argv handling or signal handlers. C consumers call it once
  before any other library function.
- ELF `.so` files leave runtime symbols for the consumer to link
  (`gc.c` + stubs); PE DLLs link the runtime in (PE forbids undefined
  symbols). Deliberate divergences from the reference (whose
  `to-dynamic` link is broken and whose `.a` ships non-PIC objects):
  PIC objects, no `main` in archives, `leash_init()`.

### Added — Native `--autofree` / `-af`
- Per-function allocation tracking (1024-slot array + counter in the
  entry block, bounds-checked record, countdown free loop needing no
  PHI, first-exit-wins) across string, vector, hash, file, and exec
  creation sites; `compile`, `run`, `build` (config `autofree: true`),
  and global-install paths all thread the flag, and `-DNO_GC` stub
  runtime is selected automatically.
- Returned values are excluded from the sweep so the caller receives
  live memory (the reference's `_mark_as_return_value` exists but is
  never wired up; stage1 implements the intent).
- Allocations owned by heap objects (struct/class field defaults and
  explicit field stores of fresh containers) are never freed at scope
  exit; the sweep only releases true locals and temporaries.
- Runtime functions whose results the sweep frees (`file_read`,
  `file_readln`, `exec`) now return heap-owned empty strings instead
  of string literals on failure paths (`free()` on a literal aborts).
- Known limitation, shared with the reference: a tracked value that
  escapes inside another aggregate (tuple element, global assigned
  mid-function) is still freed at scope exit.

### Added — Tests
- `tests/test_native_libs.py`: symbol assertions (`add`, `greet`,
  `leash_init`, no `main`), PIC check, real C consumers linked and run
  for `.a`/`.so`, win64 `.lib`/`.dll` under wine (skips cleanly
  without the MinGW toolchain).
- `tests/test_autofree.py`: autofree/plain output parity for string
  churn with live returns, early and multi-value returns, classes with
  container fields (plain and generic), and `exec`.

## [1.1.0] - 2026-10-13

Minor release: global command installs, a self-updating toolchain,
and install reliability.

### Added — Global command installs (`global_install` + `leash rm`)
- Projects can ship command-line tools: a single-line
  `global_install: {name: src, ...}` map in `config.lshc`, installed
  with bare `leash install` (no path args) to host binaries in
  `~/.leash/bin/` (`.exe` on Windows, with a PATH hint when needed).
  Build settings mirror `build` (opt level, imports, clibs); all
  entries validate before anything compiles, and failures report
  per-entry.
- New `leash rm <name>...` removes installed binaries after an
  interactive confirmation (empty answer means yes), refuses
  non-identifier names, and reports missing entries without prompting.

### Fixed — Stale native compiler after reinstall
- `pip install -e .` silently kept the old `leash/native/leashc`: the
  install-time bootstrap still passed `-o <out>`, which the compilers
  no longer accept, so stage outputs landed at default paths, the link
  step failed, the exception was swallowed, and pip reported success
  over a stale binary (e.g. `leash -v` showing 1.0.0 after installing
  1.0.1). All bootstrap commands use `to <out>` now, half-built stage
  files are cleaned up on failure, and fresh builds are announced.
- The `leash` dispatcher now checks staleness itself: when the native
  binary predates the compiler sources it warns loudly and falls back
  to the Python implementation (with a rebuild pointer) instead of
  silently behaving like an older release. Wheels don't ship the
  sources and are fresh by construction.

### Fixed — Self-hosted `update` and `self-host`
- `leash update` (self-hosted) now finishes the job: after a successful
  pull it rebuilds the toolchain from the fresh tree, so the binaries on
  disk always match the sources. When the pull changes nothing it
  reports "Already up to date." without rebuilding (HEAD compared
  before/after — no output parsing).
- The pull itself matches the Python toolchain's safety rules: the
  `origin` remote must be the Leash repository (anything else is
  refused, including a missing remote) and the pull is `--ff-only`, so
  a hostile or divergent remote can neither inject code nor create
  merges that trip local hooks.
- `leash self-host` installs atomically (temp + rename): replacing the
  running `bin/leashc` in place failed with ETXTBSY while still printing
  "Installed", leaving a stale binary behind a success message.
- The release version lives in one `leash_version()` helper instead of
  three hardcoded literals.

## [0.5.0] - 2026-10-13

### Added — Global commands for packages (`leashed`)
- Leashed packages support `global_install` in `leash-pkg.lshc`:
  publish maps tool sources to library-relative paths in
  `package.lshc`, installs build them to `~/.leash/bin`, and
  uninstalling the package removes its commands with it.

## [0.4.0] - 2026-10-13

### Fixed — Package manager integrity (`leashed`)
- Version requests are verified: a requested `name@X.Y.Z` must resolve
  to a real tag (a same-named branch no longer satisfies it), and the
  installed `package.lshc` version must equal the request — mismatches
  fail loudly instead of installing other bytes than asked for.
- `leash.lock` records a `rev` commit-SHA pin at install/lock time and
  verifies it on restore (fail-closed on moved tags); `update` never
  silently downgrades and honors explicit `@version` pins.
- Publishing re-checks ownership and version against freshly cloned
  registry data (concurrent publishes no longer overwrite each other),
  fails before pushing on conflict, never moves published tags
  (same-content retries excepted), and pushes with `--force-with-lease`.
- Publishing ships an allowlist (`src/`, manifests, README/LICENSE/
  CHANGELOG, the main file, `publish_include`) — secret-shaped files
  are skipped with a warning; credential-bearing repo URLs are rejected.
- Direct installs inside a project are recorded (lock + dependencies);
  `add` records the on-disk version; unknown CLI options print `info:`
  instead of being swallowed; registry display data is terminal-safe.

## [1.0.1] - 2026-10-13

Patch release: self-hosted compiler parity fixes (integer semantics,
`create`/class diagnostics, CLI surface), a win64 cross-compilation
test, and a `let`-syntax fix in the JSON docs.

### Added — Full CLI parity with the Python toolchain
- **Cross-compilation, natively**: all six targets
  (`linux64`, `linux32`, `linux-arm`, `win64`, `macos`, `macos-arm`) are
  built by the self-hosted compiler through clang driver flags
  (`-m32`, `--target=...`), with per-target runtime objects, Windows
  stubs + `.exe` output for win64, and `wine`/`qemu-aarch64` execution
  under `leash run` (verified end-to-end: a win64 binary built by `leash`
  runs under wine). Python routing is now only for `--autofree` and the
  `to-dynamic`/`to-static` library outputs.
- `-ngc` / `--no-garbage-collector`: plain malloc/free runtime
  (`-DNO_GC`), keyed into the runtime object cache.
- `--static` / `-static`: fully-static binaries (musl-clang/musl-gcc when
  present, `--target=...-musl -static` fallback — Python parity).
- `-ov` / `--optimization-verbosity` accepted (implies `--verbose`).
- `help <command>` per-command help topics; usage text now mirrors
  `leashp` (Global Options section, runp deprecation note).

### Changed — Install ships the true fixed-point binary
- `pip install .` now runs the **full 3-stage bootstrap**: Python builds
  stage 1, stage 1 builds stage 2, stage 2 builds stage 3 — and stage 3
  (compiled by the self-hosted compiler that was itself compiled by the
  self-hosted compiler: its own codegen quality, size and link flags) is
  what ships. The fixed point is verified during install and the pip
  binary emits IR identical to `bin/leashc`.

### Fixed — Out-of-i32-range integer literals are 64-bit
- An `int` literal past 2³¹−1 is a 64-bit constant in the Python
  reference codegen, so it silently widened any int-family operation it
  took part in. The self-hosted compiler wrapped it to i32 first, which
  agreed for `*`/`+`/`-` (wrapping is lossless there) but diverged for
  `/` `%` `<<` `>>` and every comparison. The whole path is widened now:
  int-family binops with a wide literal run at i64 (stores truncate at
  the `int` boundary), plus matching handling for unary `~`, int→float
  conversion, and string-concatenation formatting of the full int family
  (signed/unsigned per the Leash type, new `leash_uint_to_str` runtime
  helper — this also fixes `string + uint`/`int<N>` which never worked).

### Changed — Borrowed keywords are rejected, not tolerated
- A statement may not begin with `let`, `var`, `const`, `print`,
  `println`, `func`, `function`, `elif`, `elseif`, `include`, `using`,
  `interface`, `match`, `byte`, `long`, `double` or `boolean` — the
  self-hosted compiler now prints the reference's exact `error: Found
  unsupported keyword 'X'` diagnostic (excerpt, caret and tip) and exits
  1 instead of compiling. The old `let`-as-no-op-prefix tolerance is
  gone, along with the doc snippet in `docs/stdlib.html` that used the
  non-existent syntax.

### Fixed — `create Class(...)` allocation and class diagnostics
- `create` on a class without a same-named constructor fell through
  codegen to `call void @Class()` and died in clang with `expected
  value token`. Allocation now mirrors the reference exactly (exact-size
  heap instance, vtable slot, `Class_Class(this, ...)` call with
  argument coercion and trailing ctor-default fill; field defaults do
  not apply and `new(...)` never runs).
- A bare `Class(...)` call is rejected as an undefined function (it
  never constructed in the reference); unknown/generic classes and
  constructor arity/argument-type mismatches report the reference's
  exact messages; `create` results are not addressable (`Invalid
  l-value: CreateExpr` for method/member receivers and assignment
  targets, `Expected identifier after 'del'` for `del create`);
  `->` on a non-pointer value is rejected with the reference's error
  plus its follow-up diagnostic; unknown-type declarations keep
  checking the initializer.

### Changed — `-o` removed, unknown options reported
- `-o` never set the output in the Python reference (silently ignored)
  but the self-hosted compiler honored it, so the same command built to
  different paths. `to <path>` is now the only output setter on both;
  all in-repo callers (self-host stages, `ir_diff.py`, tests, docs,
  README) are migrated. Passing `-o` prints
  `info: the '-o' option is not supported; set the output file with
  'to <path>'` and the build continues; any other unknown option prints
  `info: ignoring unknown option '<arg>'` instead of being swallowed.
  Message text is byte-identical between compilers.

### Added — Tests
- `tests/test_cross_win64.py`: builds one program (ints, strings,
  floats, class `create`+constructor, vecs) three ways
  (self-hosted→win64, self-hosted→native, python→win64), checks the
  win64 artifact is a real PE32+ AMD64 binary, runs it under wine, and
  asserts byte-identical output to the reference build.
- `tests/test_cli_opts.py`: `-o`/unknown-flag INFO on both compilers
  plus INFO-text parity.
- `tests/test_self_hosted.py`: `create` with and without constructor.

## [1.0.0] - 2026-10-08

First stable release. The compiler is now self-hosted: the shipped `leashc`
binary is produced by the compiler it contains (bootstrap fixed point), and
installation builds it from source.

### Added — 1.0 installation flow (self-hosted by default)
- `pip install .` bootstraps the toolchain at install time: the Python
  compiler builds `leashc` stage 1 from `compiler/*.lsh`, then stage 1
  compiles itself — the binary shipped in the wheel is literally the
  compiler's own output (bootstrap fixed point, `scripts/bootstrap.sh`
  verifies Stage 2 IR == Stage 3 IR).
- Command naming: **`leash` is now the self-hosted compiler** and it
  implements the *entire* toolchain natively — `compile`, `run` (file and
  project modes, with the `--- Executed at ...`` banner, temp binaries and
  exit-code propagation), `check`, `dump`, `<file.lsh>` shorthand (now
  honoring `-o` and all compile flags), `init`, `build`, `install`,
  `update`, `self-host` (3-stage bootstrap + fixed-point check), and `dbg`
  (the interactive source-level debugger: statement instrumentation via
  `__leash_dbg_stmt`, breakpoints, stepping — powered by statement
  line/column tracking in the parser and a new `run` exec mode that
  inherits the terminal and returns the exit code). `leashp` is the pure
  Python compiler/toolchain; `leashc` remains an alias of `leash`. Python
  is only used for non-linux64 `--target` builds, when the native binary
  is unavailable, or with `LEASH_FORCE_PYTHON=1`.
- The compiler binary locates its runtime (`gc.c`, link stubs) from any
  working directory: CWD, `$LEASH_RUNTIME`, or relative to `argv[0]`
  (pip layout: `leash/native/leashc` next to `leash/gc.c`).
- `pyproject.toml` (build isolation now has llvmlite for stage 0),
  `MANIFEST.in`, `leash/native.py` dispatcher; version 1.0.0 everywhere
  (`leash version` / `leashp`).

### Added — Output-parity completion (88/88 examples)
- Every example in `tests/expected/` now compiles and runs with identical
  output on the self-hosted compiler: generics (`class<T>`, `def T : template`,
  multi-type `[int, float, ...]` params with monomorphization), `opdef`
  operator/method overloading with `error`/`throw`, `is`/`isnt`, matrices,
  `async`/`await` futures, File API, and full `int<N>`/`uint<N>` big
  integers (1..512-bit, exact decimal formatting/parsing, `toint(int<N>, s)`).
- `showb` is buffered and flushed once at exit (Python parity); structural
  `show(vec)` prints `[a, b, ...]`.

### Changed — Vector runtime redesign (speed + memory parity)
- `vec<T>` elements are now **unboxed inline values** (raw `i64`/`double`/
  `i1`/`i8`/`iN` in the data array; reference-typed elements stay 8-byte
  handles). Wide ints (`vec<int<128>>` etc.) no longer heap-box per element.
- **Precise GC tracing**: element buffers of pointer-free types are
  allocated `FLAG_ATOMIC`, so the collector never scans them — no false
  pins from integer payloads and no marking cost. Buffers are also
  lazily allocated (an empty vector costs one 40-byte header).
- Hot paths are inlined at the IR level: `pushb`/`get`/`set`/`popb`/
  indexing compile to direct loads/stores with Python-parity bounds
  checks (negative-index wrap + `Runtime error: ...` aborts); only the
  rare growth case calls into the runtime.
- Measured: identical-semantics `vec<int<64>>` push+get at **parity**
  with the Python compiler's binaries (0.38s vs 0.37s for 30M+30M ops);
  scalar and string workloads at parity; binary sizes at parity
  (`-no-pie -Wl,--gc-sections -Wl,--strip-all ...` link flags now match).

### Fixed
- `&&`/`||` now short-circuit (was: eager `and i1`/`or i1`), which broke
  the 3-stage bootstrap.
- Token enum collisions (`LT/LTE/GT/GTE` vs `ISINOP/DO/UNLESS/ALSOU`).
- Array literals feeding narrower vectors (`(vec<uint<2>>){1,0,...}`)
  now convert element widths instead of misreading the buffer.
- Generated IR referenced `@stdin` directly, which does not link on
  Windows (`stdin` is not a symbol under mingw); input now goes through
  the new `leash_sh_stdin()` runtime helper. `leash --target win64`
  compiles and runs (wine) — previously a hard link error.
- Failed clang invocations now surface the actual compiler error
  (previously swallowed: only "clang link failed" was printed).

### Added — Self-hosted Leash compiler (`compiler/*.lsh`)
- Compiler written in Leash (tokens/ast/lexer/parser/typechecker/codegen/optimize/driver/main)
  that compiles Leash source to LLVM IR and links with `leash/gc.c` +
  `leash/cross_compile_stubs.c` via clang.
- `leash self-host` CLI command and `scripts/bootstrap.sh` run the 3-stage
  bootstrap (python -> stage1 -> stage2 -> stage3) and verify the fixed-point:
  Stage 2 IR == Stage 3 IR. `bin/leashc` installed thereafter.
- `scripts/ir_diff.py` — parity harness comparing self-hosted program output
  against the Python compiler's expected outputs (tests/expected/*.out),
  with ulimit address-space guardrails on every child so runaway binaries
  die with OOM instead of eating host memory.
- `tests/test_self_hosted.py` (10 tests) and `docs/self-hosted.html`.

### Added — Self-hosted compiler feature parity (this pass)
- CLI: `-O<level>/--opt`, `--check`, `dump`/`check` commands, `--emit-llvm`,
  `-l<lib>` link flags, `-oi/--other-imports`, `--verbose` phase tracing,
  `--warnings-as-errors` (flag), `--target linux64`.
- AST constant folding (compiler/optimize.lsh): int/float/string/bool binops,
  unary fold, constant-if pruning, `tostring(literal)` fold.
- Syntax: `a: int` params, `-> ret` arrows, `fnc main {}` w/o parens,
  Java-style constructors (`pub Person(...)`), `create Type(...)`, `imut`
  type modifiers, reference types `&T`, tuple types `(T1, T2)` with
  multi-value returns and destructuring `a, b: int, int = f();`,
  do-while loops, `as` casts with proper precedence, `<>` containment
  operator, `use X alias Y`-style imports tolerated.
- Builtins: `get()`, `keyget()`, `normescape()`, `lstr()`, `cstr()` (slice
  semantics), `tostring(float)` via `leash_float_to_str`, global variables,
  `self`/`self::Class`/`self::Parent`, enum values + `.name` tables,
  hash literals `{"k": v}`, hash methods `getKey/delete/keys/values`,
  string `-` (remove), `str.replace()`, vec `isin`, string-interpolated
  show kwargs (`end=`), `{expr}` string interpolation with the Python
  parser's sub-expression EOF guard.
- Type system: type aliases (`def T : type X`), `float<N>`/`int<N>`
  normalization, param/local name collision dedupe, `vec<T>`/`hash<K,V>`
  INDEX inference, tuple element inference.

### Added — Class vtables & inheritance (this pass)
- Every class instance carries an `i8*` vtable slot at field 0; class type
  definitions, all field loads/stores (member get/assign, struct-init
  defaults and provided fields) and the heap allocation are offset by one.
  `ClassInfo.method_order` (parent's non-static slots first, then own new
  methods) drives `@C_vtable` emission; instance-method calls dispatch
  through the receiver's runtime vtable (bitcast to the static side's
  signature), while static methods and non-class receivers keep direct
  calls — `examples/advanced_class.lsh` now reaches output parity.
- Inheritance: parent instance fields are prepended to each child's layout
  in the typechecker, method lookup walks the parent chain to the defining
  class (`this` bitcast to it), inherited `ClassInfo`/`is_method_static`
  queries, `self`/`self::Class`/`self::Parent` statics, and a Java-style
  `@main` wrapper calling a class-static `main()` when no top-level main
  exists (`examples/javasyntax.lsh`, `examples/self_advanced.lsh`).
- Vec `extend`/`insertv` (incl. boxed element copy via
  `leash_sh_vec_extend`/`_insertv`), `<>` array containment dispatch for
  i64/i8/f64/string needles (`leash_sh_arr_isin_*`),
  `safecast T(x)` / plain `(T)x` cast parsing with `sizeof(int<64>)`
  width preservation, and `tofloat`/`toint` string conversion via
  `atof`/`atoll` (`examples/totype.lsh`).

### Fixed — Struct value semantics (this pass)
- Struct-typed values now copy on every python-side value boundary via the
  new `leash_sh_clone_obj` runtime helper (byte copy + recursive copy of
  nested struct-typed pointer fields): value parameters are cloned in the
  callee prologue (`&T` refs stay by-reference), `VAR_DECL`/plain
  assignment/struct-field assignment/struct-init field stores clone the
  RHS, and struct-typed `return` clones the result. `examples/structlval.lsh`
  now reaches output parity — the self-hosted compiler has zero output
  mismatches left on the parity corpus; the remainder are parse-level
  feature gaps.
- `scripts/ir_diff.py`: honours the `LEASHC` env var to point the harness
  at a specific stage binary (e.g. `bin/leashc_stage1`) while validating a
  change before bootstrap.

### Added — Unsafe / pointer / byte builtins (this pass)
- Modifiers: `inline`, `nogc`, `async` function flags and the `unsafe`
  block/function flag (parse + codegen gating: unsafe skips runtime checks
  and marks struct GEPs `inbounds`), `->` pointer-member access `p->field`
  (new `PointerMemberAccess` AST node, gep `{0, idx}` with class field
  offset), and `*T` pointer types with pointer arithmetic (`*T ± int` →
  `gep`, `&`-of-struct returning the heap pointer value).
- Reference semantics: scalar `&T` params/locals unwrap to the referenced
  cell on assignment (compound and `*p = v` deref stores included), `&`-ref
  arguments pass the lvalue cell through call boundaries, `show` prints
  pointers as `%p` and `*char` as `%s`, and per-function `var_ptrs`/
  `var_types` reset in `gen_function` so params/locals no longer leak
  between top-level functions.
- Byte builtins: `inttobytes`/`floattobytes` (→ `char[]` slice via
  `leash_gc_malloc` + `memmove`, copy clamped by `sizeof`), `bytestoint`/
  `bytestofloat` (compile-time `sizeof`-derived width, `iN` load + `zext`),
  `cstr()` slice→`*char` coercion (`extractvalue 1`), and `parse_int_text`
  for `0b`/`0o`/`0x` literals in the self-hosted parser.
- `scripts/ir_diff.py` normalizes `0x…` addresses in outputs; the
  `int<…>`/`float<…>` show aliases print as `int`/`float`.

### Fixed
- `leash/ast_optimize.py`: constant-folding double-rescanned every BinaryOp/
  UnaryOp node (O(2^depth) on deep string-concat chains), hanging compilation
  of `compiler/main.lsh`. The attr loop already folds children once; the
  extra `_deep_fold` recursion per Binary/Unary node is removed.
- `leash/gc.c`: new self-hosted runtime helpers — `leash_sh_str_sub`,
  `leash_sh_str_replace`, `leash_sh_normescape`, `leash_sh_slice_to_str`,
  `leash_sh_vec_isin`, `leash_sh_arr_isin_i64`, `leash_sh_str_isin`,
  `leash_sh_hash_getkey/delete/keys/values`.
- Self-hosted compiler: struct-literal disambiguation in condition contexts
  (`no_struct_init`), nested-generic `>>` splitting, `exec()` expressions,
  static class fields/methods, per-method typechecking, duplicate alloca
  dedupe (incl. param shadowing), static-call emission, char->string concat,
  float/int mixed ops + `fneg`, exact float literal text, `int[]`/`Type[N]`
  slices, ARRAY_LIT generation, member compound assignment (`p.x += n`),
  uninitialized pointer/aggregate default init, and the builtin table's
  `cstr` type (was `*char`, breaking concat conversions).

## [0.25.0] - 2026-09-27

### Summary — ~90 audit fixes across the whole compiler
A full read-and-verify audit of the five compiler subsystems (lexer/parser,
type checker, codegen, C runtime/GC, package manager/CLI) produced ~90
findings. All of them are fixed in this release, each with regression
coverage. The overarching theme: silent corruption, silent success and
silent exfiltration all became loud errors or well-defined behavior.

### Fixed — Type checker (soundness)
- Argument/field/method type mismatches are now errors (were warnings);
  literal values must fit the declared sign/width (`int<8> = 129` is
  rejected); missing struct fields without defaults are rejected.
- `works { ... } otherwise err { ... }` no longer swallows compile-time
  type errors inside the works block: static errors are re-reported
  instead of being misreported as runtime failures.
- Functions returning a value must return on all paths (an `if/else`
  where both arms return satisfies the check).
- Operators between incompatible types (`struct + int`, `int == string`)
  are hard errors; the unsafe-pointer-cast checker (`as`/`(T)x`) now
  recognizes `int<N>` spellings and real pointer sources.
- `spawn` only accepts `worker fnc` functions.
- Block scoping: variables declared in `if`/loop bodies no longer leak
  into the enclosing scope.
- `opdef` extension methods on user structs work (implicit `this`
  receiver, arguments checked); `this` inside opdef bodies resolves.
- Shift amounts are checked against the operand's real (resolved)
  width — including aliases and `char` — and negative amounts are
  errors; `x % 0` is caught statically like `x / 0`.
- Union type-punning is rejected for every escape hatch: globals,
  struct fields, and aliased unions (previously only direct locals).
- `del` poisons every alias of the deleted instance (`p := o; del o; p.v`
  is an error); read-only array borrows can no longer be handed to a
  mutable `&T` parameter; `imut` containers can no longer be mutated
  through element/field writes.
- Negative array indices wrap from the end by design; constant indices
  beyond the wrap range are compile-time errors.
- `if`/`while`/`for` conditions and `&&`/`||` operands must be `bool`.
- Member access on primitives/arrays/strings (`5.foo`) is a proper
  error instead of a downstream crash; default parameter values,
  unknown parameter/return types, array sizes, and builtin argument
  counts are all validated (no more internal-error tracebacks).
- New error codes: `LEASH-E015` (literal out of range), `LEASH-E016`
  (struct init missing field), `LEASH-E017` (shared one-writer rule).

### Fixed — Code generation (miscompiles & memory safety)
- `tostring`/concatenation buffers for floats grew from 64 bytes to 400
  with bounded `snprintf` (`"x = " + 1.5e300` no longer smashes the heap).
- Signedness is derived from the Leash types everywhere instead of
  defaulting to signed: widening (`uint<32> -> int<64>`), int<->float
  conversions (`uitofp`/`fptoui`), char/bool binary ops
  (`char(200) >> 1` is 100, not -28), union/matrix scalar helpers.
- Subscript indices widen straight to i64 with the source's signedness —
  a 2^32+3 index no longer truncates to a valid-looking 3; pointer
  arithmetic sign-extends negative offsets (`p + (-1)` reads the
  previous element instead of a wild address).
- Loop variables are restored after loops (`foreach i, v in ...; show(i)`
  prints the outer `i`, not the loop index); constant propagation
  invalidates loop-rebinding variables; `_ = expr` is a true discard.
- Runtime guards for shift amounts and `INT_MIN / -1` (plus static
  detection when constants were folded by the optimizer); `nuw`/`nsw`
  flags were removed from wrapping user arithmetic (they made overflow
  poison at -O1+).
- Union payloads are stored aligned (int<128> variants), `tounion(...)`
  produces a fully-tagged union value usable everywhere, and `vec.isin`
  compares struct elements field-wise and strings by content instead of
  comparing addresses (which never matched).
- `tostring(true)` prints `"true"`; `tostring(uint)`/`tostring(char)`
  no longer print negative numbers.
- `rand(min, max)` computes in 64-bit and validates `max >= min`;
  `exec(..., "code")` survives a failed `popen` and returns `""`;
  long exec commands abort instead of silently running a truncated
  command.
- The `_emit_cast` fallback now raises a proper diagnostic instead of
  returning an ill-typed value that crashed llvmlite.

### Fixed — C runtime & garbage collector
- Unions are pointer-bearing to the collector: union-typed globals are
  registered as scan regions and `vec<union>`/`matrix<union>` buffers
  are never marked atomic (a string held in a union used to be
  collectable while reachable).
- The `showb` output buffer is guarded by a runtime spin lock; growth
  uses a real branch (an LLVM `select` evaluated both the malloc and
  realloc arms, leaking one allocation per growth) with an OOM trap;
  `main` now waits for workers BEFORE flushing, so buffered worker
  output is no longer lost.
- `File.read`/`readb`/replace reject non-seekable streams (`ftell < 0`)
  instead of allocating 0/(size_t)-1 bytes; `readb` on an empty file
  returns a valid empty buffer; `exec` and `get` check every allocation
  result.
- GC hardening: double frees of small/large cells are ignored
  (`CELL_IN_USE` guard); futures are no longer double-rooted (every
  `async`/`await` used to leak permanently); stack-bound capture
  records its owning thread and invalidates on failure (a stale
  cross-thread top could SIGSEGV the marker); `leash_gc_thread_attach()`
  lets FFI-created threads opt into the collector deterministically;
  vector helpers validate sizes (`qsort` with a negative size walked out
  of bounds; the bulk-copy guard aborted every 1-byte-element copy);
  debug-init races in the Windows/cross stubs use init-once primitives.
- The GC harness passes cleanly under AddressSanitizer (the conservative
  stack scanner is excluded, Boehm-style).

### Fixed — Package manager & CLI security
- `leashed publish` validates the configured `repo` with the same strict
  URL rules as installs (a tampered `leash-pkg.lshc` could previously
  force-push to arbitrary local/`file://`/`ext::` remotes), rejects
  symlinked projects and `main:` paths that escape the project
  directory, and never dereferences tracked symlinks into the published
  repository.
- `assert_no_symlinks` now also rejects a symlinked package ROOT
  (`library -> ~/.ssh` used to pass and be copied wholesale).
- Project builds contain `main:`/`imports:` inside the project, object
  writes refuse to follow planted symlinks (`out/app.o -> ~/.bashrc`
  no longer truncates the target), `@from` native libraries must
  realpath inside the module directory (symlink escapes are errors),
  and the object cache runs the security scan before being consulted.
- Registry/package hardening: non-string index fields fail cleanly;
  package metadata can no longer inject source into import stubs via
  newlines; `git://` installs are rejected (unauthenticated transport);
  `write_pkg_config` preserves unknown keys and escapes values;
  the runtime stub cache moved from the (possibly shared) install dir
  to a private `~/.leash/rtcache`; temp binaries are 0600 instead of
  world-readable; `update_leash` verifies the origin remote and pulls
  with `--ff-only`.
- CLI ergonomics: unknown `--target`/unreadable input report friendly
  errors instead of tracebacks; `leash run --target x file.lsh` (options
  before the file) no longer silently switches to project mode;
  `leash install` refuses symlinked sources.

### Added — Concurrency (`shared`/`fusion` semantics enforced)
- The `shared` one-writer rule is now enforced at compile time
  (`LEASH-E017` when two functions write the same `shared` global).
- Scalar integer `fusion` globals compile to atomic (seq_cst) loads and
  stores, and the counter idiom `x = x + n` lowers to a single atomic
  read-modify-write — the generated IR previously contained zero atomic
  instructions, contradicting the documented semantics.
- Docs updated accordingly (conditions must be bool; negative index
  wrap rules; FFI threads and automatic collection).

### Tests
- New suites/growth: `test_frontend.py` (51), `test_typechecker.py`
  (75), `test_codegen_fixes.py` (32), `test_leashed.py` security
  additions, `test_security.py` symlink/containment cases. Full stack:
  240 pytest, 88 example tests, 99 stdlib tests, 227 unittest, GC
  harness (plain + ASan) — all green.


## [0.24.3] - 2026-09-26

### Added — The garbage collector actually collects (Phases 0–2 overhaul)
- **Slab allocator.** `leash_gc_malloc` now hands out cells from 64 KB
  page-aligned slabs carved into 95 size classes with per-class free lists,
  instead of a glibc `malloc` + 32 B header per object. Small allocations
  are a few instructions on the main thread (no lock while the program is
  single-threaded); empty slabs are returned to the OS on sweep.
- **Automatic collection.** The runtime now triggers mark-and-sweep when
  bytes allocated since the last cycle exceed `max(2 MB, 2 × live bytes)`.
  Collection runs only at *quiescence* (no active pool workers, no
  in-flight parallel op, no foreign threads) so mutator threads are never
  stopped mid-operation. `LEASH_GC_AUTO=0` (or `f`/`F`) disables it,
  `LEASH_GC_STATS=1` prints stats at exit.
- **Roots.** The compiler registers globals, class static fields, and the
  `showb` buffer precisely at startup (`gc_register_scan_region`); the
  collector additionally scans the collecting thread's C stack
  conservatively (stack bounds captured per thread, `[sp, stack_top)`,
  refusal falls back to a one-time warning + auto-collect disabled).
- **Builtins**: `gc_collect()` forces a full cycle, `gc_stats()` prints the
  stats line (both are no-ops under `-ngc`).
- GC behavior tests: `tests/test_gc.c` (12 cases) + `tests/test_gc.py`,
  plus `examples/gc_roots.lsh` and `examples/gc_churn.lsh`.
- Allocation-churn benchmark task `benchmarking/tasks/allocstress.lsh`
  (400 rounds of short-lived strings/vectors/hashes with a small live set).
- Benchmark harness now measures **peak RSS** (via `os.wait4` →
  `ru_maxrss`), prints a `PEAK RSS (max over runs)` table, and records
  `peak_rss_kb` per run in `--json` output.

### Changed
- Mark phase is iterative (explicit worklist, no recursion) over an
  open-addressed page directory; large objects live in an address-sorted
  array found by binary search. Freelist heads are discarded without
  deref on purge and rebuilt by walking the surviving slabs.
- Threads take the GC mutex on entry (`leash_gc_thread_spawned()` before
  creation, `gc_thread_worker` TLS + `leash_gc_worker_begin/end` brackets
  for pool/matrix workers); foreign threads that touch the GC flip it to
  locked mode and are tracked until quiescence.
- Matrix buffers, vector batches, `leash_fast_memcpy`, bigint and future
  helpers are compiled outside the `NO_GC` guard again, restoring
  `-ngc` link compatibility.
- Bump version to `0.24.3 Beta`.

### Fixed
- `_get_type_size` now computes real ABI layout (alignment + struct
  padding) instead of summing field sizes — llvmlite's `get_abi_size`
  requires a binding type and always threw for IR types. Undersized
  allocations (e.g. `RxNode`: 95 B requested, ≥120 B real) were masked by
  glibc slack under the old GC and corrupted the slab allocator.
  `sizeof` on an opaque struct now raises a proper `LeashError`.
- Memory: on the `allocstress` benchmark, peak RSS drops from **58.9 MB**
  (old GC, never collects) to **15.1 MB** (new GC, auto-collect) with
  comparable run times; compute benchmarks show no real regression
  (verified by direct A/B — suite-level deltas were machine noise).

## [0.24.2] - 2026-09-24

### Added — Non-blocking HTTP server write path (0.24.1's "next step")
- The last synchronous corner of the `httpserver` serving engine is gone:
  **responses are now written non-blockingly**. Each connection carries a
  pending-output buffer (`out` + write offset); when the kernel buffer is
  full (`EAGAIN`/`WSAEWOULDBLOCK`), the remaining bytes stay buffered and
  the event loop drains them on **POLLOUT** (POSIX `poll` events /
  Winsock `select` write set) while other connections keep being served.
  A slow reader can no longer stall the loop for the 30 s I/O budget —
  reading requests *and* writing responses are now fully multiplexed.
- In the common case (response fits the kernel buffer) the write still
  completes synchronously, so latency and request ordering are unchanged.
- Pipelined responses queue in order in the per-connection output buffer;
  `Connection: close` and peer-EOF close decisions are remembered
  (`out_close`) and honored exactly when the write fully drains.
- A response still draining after its 30 s write deadline is reaped by the
  idle sweep (a stalled reader cannot outlive it), and `shutdown()` /
  `max_requests` exhaustion still complete the in-flight response first
  (a bounded best-effort drain before the connections close).

### Added — Server-wide buffered-bytes ceiling (global memory bound)
- Buffered bytes are now bounded **per connection AND server-wide**:
  a new compile-time cap `LSHD_MAX_GLOBAL_BYTES` (64 MiB default) counts
  request accumulators plus pending response bytes across all live
  connections. Per-connection caps alone allowed 128 × 16 MiB ≈ 2 GiB.
- When the ceiling is reached the server stops pulling bytes from client
  sockets — they wait in the kernel until live requests are consumed and
  memory frees up, so nothing is lost and no connection is dropped for it.
  Every buffered byte is accounted exactly once (buffered → consumed /
  flushed / dropped), so the counter cannot drift.

### Changed — Worker-thread cap refinements (async/await side)
- The worker cap is now **runtime-configurable** through the
  `LEASH_MAX_WORKERS` environment variable (clamped to [1, 4096]; the
  default stays 64), and the worker-handle table grows to the resolved
  cap on first use instead of being a fixed 64-entry array.
- Exceeding the cap during an `async fnc` call is now **silent**: the
  runtime gained `leash_try_spawn_worker` (used by the generated async
  wrapper) which returns instead of printing
  `error: Maximum number of worker threads (64) reached` — running the
  task inline is expected, correct (just non-parallel) fallback behavior,
  so it must not pollute stderr. Plain `spawn` keeps the loud report,
  because a silently dropped task there IS an error.
- `leash_wait_for_workers()` cycles cleanly: repeated spawn/wait rounds
  reuse the same table (no growth leak).

### Changed
- Bump version to `0.24.2 Beta`.

## [0.24.1] - 2026-09-24

### Added — HTTP server keep-alive (persistent connections + pipelining)
- `httpserver`'s serving engine was rebuilt from "one connection at a time,
  `Connection: close`" into a **multiplexed event loop**: a single
  `poll()` (POSIX) / `select()` (Winsock) slice now watches the listen
  socket and every open client connection at once. The Leash handler still
  runs on the thread that called `serve()` — so GC visibility rules are
  unchanged — but client sockets stay open across requests.
- **HTTP/1.1 keep-alive is the default** (per RFC 9110): a connection is
  kept unless the client sends `Connection: close` or speaks HTTP/1.0
  without `Connection: keep-alive`. **Pipelining** works naturally —
  several requests pre-stacked in one TCP segment are answered in order
  from the per-connection byte buffer. A slow client no longer wedges the
  whole server: other connections keep being served while a partial
  request waits for its remaining bytes.
- Connection hygiene: up to 128 concurrent client connections; idle
  keep-alive connections close after 60 s of silence, a connection parked
  mid-request after the 30 s I/O budget; the per-connection accumulator is
  capped (64 KiB head + 16 MiB body). The listener itself is non-blocking,
  and `shutdown()` still unblocks the loop immediately (the in-flight
  response completes, everything else closes).
- All v1.0 protocol hardening is preserved: 400/413/414/431/501 mapping,
  strict Content-Length parsing (duplicate or negative → 400), any
  `Transfer-Encoding` → 501, HEAD body suppression, malformed-request
  replies without invoking the handler.
- The parser got stricter:
  header lines must be `name: value` (whitespace before the colon is
  rejected), the version must be `HTTP/1.x`, control bytes in the head are
  refused, and a head that mixes CRLF lines with a bare-LF terminator is
  rejected (a uniformly LF head is still fine). `add_header()` /
  `set_header()` drop any header carrying a control character, so echoing
  request data into a header cannot split the response. A head that
  terminates with bare LF is still accepted, but never when its lines are
  CRLF — that mix is what a smuggling chain needs.
- Version bump on the wire: `Server: leash-httpd/1.1`.

### Known limitations (beta)
- Responses are written synchronously from the serving thread: a client
  that stops reading stalls the write for up to the 30 s I/O budget, and
  other connections are not polled during that wait. Reading requests is
  fully multiplexed; only the write path is not. Non-blocking output
  buffering is the next step.
- Memory ceilings are per connection, not global: roughly 2x
  (64 KiB head + 16 MiB body) per connection with up to 128 connections.
  Lower the compile-time caps, or use a reverse proxy, for untrusted
  deployments.

### Added — Native async/await
- New language feature: **`async fnc`** and **`await`**, built on the
  existing worker-thread runtime (no new scheduler, no runtime rewrite).
  - `async fnc work(a int) : int { ... }` — calling `work(...)` returns
    immediately with a **`future<T>`** handle; the body runs on a worker
    thread. Plain calls (no `await`) are legal fire-and-forget.
  - `await f` / `await work(...)` — blocks the calling thread until the
    result is ready and yields `T`. `future<void>` tasks are joined with
    `await f;` as a statement. Futures can be held, passed around, and
    awaited later; several futures can be in flight at once (real
    parallelism — 3 concurrent CPU-bound tasks finish in the wall time of
    one).
  - `future<T>` is a first-class type: `f: future<int> = work(1);` works
    in variables, fields and across modules (`pub async fnc` exported from
    `use`d modules, platform conditionals included).
  - Nesting composes: an async fn calling another async fn awaits its child
    future like any other — recursion spawns child tasks.
  - Diagnostics: `await` on a non-future is `LEASH-E014`; `spawn` on an
    async fn is rejected ("it already runs on a worker"); `async fnc main`,
    `async` on a struct method, and `async` on a generic function are
    rejected. Awaiting a value whose type cannot be determined is reported
    instead of unboxing whatever the pointer happens to point at.
  - When more tasks are created than the runtime's worker-thread cap (64),
    the extra tasks run inline on the calling thread instead of leaving a
    future nobody will ever complete — correctness first, parallelism for
    the tasks that fit.
- Under the hood, the compiler expands one `async fnc` into three
  functions: the user body (unchanged, type-checked against `T`), a worker
  thunk that unboxes the packed arguments, runs the body and completes the
  future with the GC-boxed result, and a public wrapper that allocates the
  future, packs the arguments and spawns the worker. The future state
  (mutex + condvar + done + boxed value) lives in the GC heap, rooted from
  creation until `await`, so worker results cannot be swept mid-flight.
  Works in GC, `--no-garbage-collector` and `--autofree` modes.

### Fixed
- The worker-thread table in the cross-compiled runtimes
  (`leash_spawn_worker`) is now mutex-guarded: a worker running an async
  function can spawn children, and the previous counter/array update raced
  (a lost handle at best, an out-of-bounds write at worst).
- `future<T>` behind a type alias (`def Job : type future<int>;`) lowered as
  `i32` instead of the handle pointer, so awaiting such a variable failed
  to compile. Aliases now resolve to the same `i8*` handle.
- `await` now takes its result type from the typechecker instead of
  re-deriving it from the operand expression, so contexts that ask for the
  Leash type of an `await` (casts, comparisons, formatting) see `T` rather
  than a hardcoded `int`.
- `await f + 1;` used to stop parsing after the unary operand; the
  standalone-await statement now runs the full operator tail, matching how
  `await` behaves inside a larger expression.
- `async` no longer silently drops sibling modifiers: `unsafe async fnc`,
  `nogc async fnc` and `inline async fnc` now keep them.
- The future state is allocated already rooted (`leash_gc_malloc_rooted`),
  and the root is released only after generated code has loaded the result
  out of the box, closing the window in which a concurrent
  `leash_gc_collect()` from another thread could sweep a handle or its
  result.
- `async` on a struct method or on a generic function is now rejected with
  a clear message instead of compiling to a broken thunk.

### Changed
- Bump version to `0.24.1 Beta`.

## [0.24.0] - 2026-09-23

### Added — Native HTTP Server (`httpserver` stdlib package, completes the web stack)
- New `httpserver` stdlib package: a native HTTP/1.1 server shim
  (`installthis/httpserver/src/lshhttpd.c`) bound through `@from` — no
  external dependencies, both backends native (BSD sockets on POSIX,
  Winsock2 on Windows). The web stack is now symmetric: `http` (client)
  + `httpserver` (server).
- Leash-side API: `HttpServer.serve(port, &handler)`,
  `serve_ex(port, max_requests, &handler)`, `shutdown()` (callable from
  inside a handler or from another thread). Handlers get
  `(method, path, query, headers, body)` and return the HTTP status;
  the response is built with `HttpServer.reply(...)`, `set_body()`,
  `add_header()`, `set_content_type()`. Helpers:
  `parse_query(query)` → `HttpQuery` (`get`/`has`/`count`),
  `path_matches(path, prefix)`, `reply_raw(status, body)`.
  Requests are served sequentially on the serving thread (each response
  carries `Connection: close`, so keep-alive clients reconnect);
  request fields are handed to the handler as GC-managed strings, so
  handlers may store them freely. Malformed requests get a 400, chunked
  bodies a 501, `HEAD` runs the handler but suppresses the body, and an
  invalid port returns a clean negative error code instead of crashing.
- Prebuilt `liblshhttpd.a` archives ship for linux64 (`http/x86_64` and
  `httpserver/linux`) and win64 (`httpserver/win`, built with MinGW);
  macOS and Linux-ARM64 rebuild with `TARGET_DIR=macos|arm64 sh build.sh`.

### Added — `leash.lock` lockfile (reproducible builds)
- `leashed` now maintains a project-level `leash.lock` (JSON) recording the
  exact `name → {version, repo, tag}` of every installed dependency —
  reproducible installs even though the object cache already skips work.
- `leashed lock` resolves the project's `dependencies` field against the
  registry, installs them, and writes the lockfile. Inside a project,
  plain `leashed install <name>` automatically prefers the pinned
  version when a lockfile entry exists, and `leashed install --locked`
  fails instead of resolving an unpinned version (CI reproducibility).
- Bare `leashed install` inside a project restores the exact versions
  pinned in `leash.lock` (before, a fresh clone had to resolve latest).
  `leashed add`/`update`/`uninstall` keep the lockfile in sync; deleting
  it is safe (it simply re-resolves on the next `lock`).

### Added — Ecosystem: standard library grows to 100+ packages
- The bundled standard library grows from 47 to
  **100+ importable packages** (from `installthis/`), organized by area:
  - `utils/`: `pad`, `indent`, `units`, `roman`, `join`, `id`, `format`
  - `text/`: `slug`, `wrap`, `caseconv`, `plural`, `tmpl`, `count`,
    `freq`, `ngrams`, `lorem`, `leet`
  - `collections/`: `multiset`, `bitset`, `vecops`, `lru`
  - `mathx/`: `range`, `interp`, `geometry`, `vec2`, `vec3`
  - `algo/`: `combo`, `series`
  - `crypto/`: `adler32`, `fnv`
  - `encoding/`: `base32`, `escape`, `html`
  - `sys/`: `env`, `exit`, `info`
  - `misc/`: `table`, `progress`, `hexdump`
  - `net/`: `ip`, `email`, `mac`
  - `data/`: `ini`, `env`, `querystring`
  - `games/`: `dice`, `cards`, `board`
  - `ai/`: `markov`, `knn`, `perceptron`
  - `finance/`: `money`, `interest`
  - `geo/`: `coords`
  - `time/`: `duration`
  - `testing/`: `asserts`
- Every new package ships with a recorded stdlib smoke test
  (`tests/stdlib/`). Total: 97 stdlib tests, 85 example tests.

### Added — Cross-compile for Linux ARM64 (`--target linux-arm`)
- New target `linux-arm` (AArch64): triple `aarch64-unknown-linux-gnu` with
  the same linker flag set as linux64/linux32. Native builds on
  Raspberry Pi / ARM servers work out of the box; from x86 hosts the
  compiler detects `aarch64-linux-gnu-gcc` (and `-musl` for
  `--static`). On non-ARM hosts, `leash run --target linux-arm`
  transparently executes through `qemu-aarch64` when available;
  `--static -static` builds work with a musl ARM toolchain.
- Register-extends `platform_extensions`, `--static` support, WSL
  fallback (Windows → WSL with an aarch64 toolchain), and `_PLATFORM`
  gets `"linux-arm"` — bundled native packages (`http`, `sql`,
  `lshraylib`, `httpserver`) gained an `arm64/` variant slot.

### Added — Native debugger (`leash dbg`)
- New `leash dbg [<file.lsh>]` launches programs instrumented at the
  statement level: every statement is preceded by a runtime hook
  (`__leash_dbg_stmt`) linked from the platform stubs. Breakpoints from
  the command line (`--break N`, repeatable), `--run` starts in
  continue-until-breakpoint mode instead of stepping at `main`.
- Interactive commands at the `(dbg)` prompt: `s`/`n`/Enter step,
  `c` continue, `b <line>` / `d <line>` set/clear breakpoints,
  `l` list breakpoints, `h` help, `q` quit. Each stop shows
  `func:line` plus the source line. Non-interactive stdin is handled
  gracefully, so CI can drive it (e.g. `printf 'b 5\nc\n' | leash dbg app.lsh`).
- Debug builds default to `-O0` so statements map 1:1 to source lines;
  `-O3` etc. remain available when wanted. GDB/LLDB still work on the
  same binary.

### Fixed
- Function-pointer arguments: a `fnc(...)` value flowing through a local
  variable or parameter was passed to callees as a pointer-to-cell
  instead of the function pointer itself — crashing FFI callbacks
  (this is what powers the new `httpserver` handler). Direct `&func`
  arguments were unaffected; the fix excludes function-pointer targets
  from the by-reference argument marshalling path.

### Changed
- Bump version to `0.24.0 Beta`.
- `leash lshéd` → `leashed` bumped to v0.3.0 (lockfile support).

## [0.23.9] - 2026-09-22

### Performance — generated-code runtime
- Fixed the LLVM code model: target machines were created without
  `codemodel`, making llvmlite pick the *JIT* (large) code model. Every
  inter-function call was emitted as `movabs $addr; call *%reg` (indirect) in a
  private `.ltext` section. With the proper AOT (small) model, programs emit
  direct `call rel32` and now match `clang -O2` on call-heavy code
  (fib(42): 0.95s → 0.78s; clang -O2 reference: 0.81s, gcc: 0.40s via its
  two-chain recursion unroll).
- GC allocation fast path: the GC no longer takes its mutex on every
  allocation while the program is single-threaded. Threads switch the GC to
  locked mode *before* they are created (`leash_gc_thread_spawned()`, called
  from `leash_spawn_worker()` and the matrix thread pool); foreign threads
  (e.g. FFI callbacks) that reach the GC are detected via the main-thread id
  and switch it to locked mode permanently (thread-safety preserved).
- GC mark phase rewritten from O(objects² × words) to O(N log N): objects are
  snapshotted into a payload-sorted index once per collection and each
  candidate pointer is resolved by binary search instead of a full list walk.
  `leash_gc_collect()` on 120k objects: 14.3 s → 12.7 ms (~1,125x), identical
  live-set semantics.

### Performance — compiler
- New object-file cache (`~/.leash/objcache/`): repeated compiles of unchanged
  sources skip the entire parse → typecheck → AST-optimize → codegen → LLVM
  pipeline and link the previously emitted object. The cache key covers the
  compiler version, a stamp of the compiler's own sources, target, opt/GC
  flags and sha256 of the main source plus every imported module — editing
  the source or any import (transitively) invalidates it. 3600-line file:
  0.74 s cold, 0.21 s on recompile (5x vs the pre-change 1.09 s cold).
  Disable with `LEASH_NO_OBJ_CACHE=1`. `leash check` is never cached.
- Lexer: the tokenizer hot loop now dispatches on `mo.lastindex` (indexed
  kind table, keyword fast path first) instead of `lastgroup`/`group(name)`
  string lookups; the CHAR literal's stray capture group was made
  non-capturing so every alternative owns exactly one numbered group.
  Token streams verified byte-identical across all 191 repo source files.
- Codegen: `_get_leash_type_name` — one of the hottest compiler functions —
  no longer re-executes a 17-name `from .ast_nodes import ...` on every call
  (25k calls per compile); the names are imported at module level.
- Codegen: `_resolve_type_name` results are memoized (invalidating
  automatically when new type aliases are registered).
- The security scan iterates top-level items only (NativeImport nodes are
  only ever top-level after conditional flattening) instead of walking the
  whole AST.
- Runtime-stub cache: a bare compiler name (`gcc`) is normalized to its
  resolved path so it shares one cache set with `/usr/bin/gcc`, avoiding a
  spurious ~0.65 s recompile of the runtime on first use of each variant.

### Security
- `leashed` now validates every git URL before cloning. Git's `ext::`/`fd::`
  transports execute arbitrary local commands, and a URL starting with `-`
  (e.g. `--upload-pack=...`) is parsed by git as a command-line option — both
  are remote code execution vectors reachable through a poisoned registry
  entry or a crafted install argument. Only `https://`, `ssh://`, `git://` and
  scp-style `git@host:path` URLs are accepted; registry-provided URLs must be
  https.
- `leashed` refuses to install packages whose cloned tree contains symlinks.
  A symlink such as `library/data -> /home/user/.ssh` was dereferenced by
  `shutil.copytree`, leaking files into the install directory.
- The registry index download is capped (32 MiB) and shape-checked; corrupt
  index entries now fail with a clear error instead of crashing or being used.
- `leashed` no longer lets a tampered `package.lshc` 'main' value influence
  the generated import stub (e.g. `main: "../../evil"`).
- The registry validation bot no longer auto-merges a PR whose `index.json`
  is unparseable or deleted — both previously passed validation silently.
- The registry bot now validates the `versions` map metadata: a poisoned
  `versions[x]["repo"]` URL (ext:: transport, third-party repo) or a mismatched
  `tag` is rejected. `leashed install name@version` clones that URL verbatim,
  so this was a client-side code-execution chain that bypassed the top-level
  repo check.
- New compiler security pass (`E_SECURITY`/`W_SECURITY`): `@from` native
  library paths must stay inside the module directory (absolute paths and
  `..` segments are now hard errors — they could silently link an arbitrary
  binary into the output), and native libraries linked from *imported* modules
  are surfaced as explicit warnings since they run with the program's full
  privileges.
- The `exec` builtin now formats commands with `snprintf` instead of
  `sprintf`, fixing a heap buffer overflow in generated programs for any
  command longer than the fixed 256/1024-byte staging buffers.

### Fixed
- `check_file` no longer raises `NameError` if the type checker fails to
  initialize.
- Test harnesses normalize the multi-line ornamentation emitted by warnings
  (`-->`, source excerpt, `= tip:`), so warnings no longer break recorded
  baseline comparisons.

### Changed
- Bump version to `0.23.9 Beta`.

## [0.23.8] - 2026-09-08

### Fixed
- Integer constant folding for `/` and `%` now respects operand width, matching
  the guard already applied to `+`, `-` and `*`. Previously `-2147483648 / -1`
  was folded to the 64-bit constant `2147483648` instead of remaining a 32-bit
  runtime `sdiv`, so the folded result no longer diverges from runtime
  semantics on 32-bit operands.
- `leash update` now runs its `git pull` in the Leash *installation* directory
  instead of the caller's current working directory, preventing an unintended
  fetch/merge into whatever unrelated git repository the user happens to be in.

### Security
- `build_project` now rejects a malformed `out_name` from `config.lshc` that
  would escape the project's `out/` directory (e.g. `..` segments, absolute
  paths, or embedded separators), so a tampered config cannot write the object
  file and binary to arbitrary locations.

### Changed
- Bump version to `0.23.8 Beta`.

## [0.23.7] - 2026-09-08

### Fixed
- Constant-folding miscompiles: div/mod sign semantics and i32 overflow.
- Fixed the `%0` compiler crash.

### Changed
- Bump version to `0.23.7 Beta`.

## [0.23.6] - 2026-09-08

### Added
- Full vector math (add, sub, mul, div) with scalar broadcasting.

### Changed
- Bump version to `0.23.6 Beta`.

## [0.23.5] - 2026-09-08

### Added
- Dynamic union `show`/`showb`.

### Fixed
- Robust `.cur` / value-store handling.
- Unsigned integer display.

### Changed
- Bump version to `0.23.5 Beta`.

[0.24.3]: https://github.com/foksiny/leash/releases/tag/v0.24.3
[0.24.2]: https://github.com/foksiny/leash/releases/tag/v0.24.2
[0.24.1]: https://github.com/foksiny/leash/releases/tag/v0.24.1
[0.24.0]: https://github.com/foksiny/leash/releases/tag/v0.24.0
[0.23.9]: https://github.com/foksiny/leash/releases/tag/v0.23.9
[0.23.8]: https://github.com/foksiny/leash/releases/tag/v0.23.8
[0.23.7]: https://github.com/foksiny/leash/releases/tag/v0.23.7
[0.23.6]: https://github.com/foksiny/leash/releases/tag/v0.23.6
[0.23.5]: https://github.com/foksiny/leash/releases/tag/v0.23.5