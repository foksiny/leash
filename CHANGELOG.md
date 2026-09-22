# Changelog

All notable changes to the Leash compiler are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

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

[0.23.9]: https://github.com/foksiny/leash/releases/tag/v0.23.9
[0.23.8]: https://github.com/foksiny/leash/releases/tag/v0.23.8
[0.23.7]: https://github.com/foksiny/leash/releases/tag/v0.23.7
[0.23.6]: https://github.com/foksiny/leash/releases/tag/v0.23.6
[0.23.5]: https://github.com/foksiny/leash/releases/tag/v0.23.5