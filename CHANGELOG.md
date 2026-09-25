# Changelog

All notable changes to the Leash compiler are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

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

[0.24.1]: https://github.com/foksiny/leash/releases/tag/v0.24.1
[0.24.0]: https://github.com/foksiny/leash/releases/tag/v0.24.0
[0.23.9]: https://github.com/foksiny/leash/releases/tag/v0.23.9
[0.23.8]: https://github.com/foksiny/leash/releases/tag/v0.23.8
[0.23.7]: https://github.com/foksiny/leash/releases/tag/v0.23.7
[0.23.6]: https://github.com/foksiny/leash/releases/tag/v0.23.6
[0.23.5]: https://github.com/foksiny/leash/releases/tag/v0.23.5