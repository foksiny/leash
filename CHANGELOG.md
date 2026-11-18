# Changelog

All notable changes to the Leash compiler are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.12.0] - 2026-11-18

Minor release: the `-O` scale finally delivers its promised performance, and the arena backend is hardened enough to compile the self-hosted compiler. **432 tests pass.**

### Added
- **Recursive-call unrolling at O3/O4.** A new IR pass emits `alwaysinline` clones of self-recursive functions and chains them, giving LLVM the inlining opportunities it refuses on a truly recursive call. O3 unrolls to depth 4 (body cap 96), O4 to depth 8 (body cap 80); neither O2 nor cross-target builds use the pass, so the IR stays small where LTO cannot exploit it.
- **Thin LTO at O3/O4.** Host-native builds now link with `-flto=thin -fno-plt` (and `-march=native -mtune=native` at O4), with `-O2` as the common base. Cross targets deliberately fall back to plain `-O2` because their toolchains may lack the matching LLVM plugin.

### Fixed
- **`-O2`, `-O3` and `-O4` are no longer silently pinned to `-O1`.** All three levels used the same backend flags, so `fib(50)` took ~44 s at every level while gcc `-O3` took ~15 s. The scale is now strictly ordered; `fib(50)` runs in ~30 s at O2 and completes in milliseconds at O3/O4 (fib(60) ≈ 136 ms at O4). Output parity is preserved for O2 vs O4.
- **Arena root stores of vectors of class instances.** The root-store helper treated aggregate elements as inline bodies, leaving their pointers dangling when the producing frame exited. A `vec<Expr>` assigned to an outer object now deep-roots every element through its type tree.
- **Arena field stores into outer objects.** Assigning a freshly built container to a field of a caller-owned object now root-copies the value. Alias-derived variables (`x = coll.get(i)` / `x = coll[i]`) are recognised as outer roots, and hash index stores into outer hashes promote the table so its nodes survive the callee's frame.
- **Arena compiler regression tests:** `compiler/main.lsh` now builds with `--arena`, and `leashc compile examples/hello.lsh --arena` produces a working program.

### Changed
- `tests/test_opt_scale.py` now asserts the emitted IR/flag structure (monotonic codegen levels, additive LTO/native flags, unroll only at O3/O4) instead of noisy wall-clock timings.

## [1.11.0] - 2026-11-17

Minor release: runtime numeric safety, automatic array defaults, and nil-safety analysis. **431 tests pass.**

### Added
- **Nil-flow audit and runtime nil-show guard.** `show` of a value that may be nil is now a compile-time error (`E_NIL_SHOW`): the audit tracks assignments of `nil`, is branch-sensitive for `if`/`else`, and is conservative for loops and interpolation. Reassigning a non-nil value before the show, or showing only inside the branch where the value is non-nil, compiles cleanly. Values whose nilness the compiler cannot track (e.g. a nil string from a container) are caught at runtime by `leash_sh_show_guard`, which aborts with a diagnostic instead of relying on libc's undefined `%s` handling.
- **Fixed-size arrays get their element defaults automatically.** `a: int[1000];` now allocates and zero-fills all 1000 elements in one allocation instead of producing an empty `{0, null}` slice. Numeric/POD arrays need a single zeroing allocation; `string[N]` is filled with empty strings and `vec<T>[N]` / `hash<K,V>[N]` with fresh empty containers through new bulk-fill helpers (`leash_sh_array_fill_ptr` / `_vec` / `_hash`). All three runtime variants (GC, arena, no-GC) compile the helpers.
- **Runtime numeric-safety regressions** (`tests/test_safety_conversions.py`) cover narrow string/float overflow, dynamic division by zero, dynamic `MIN / -1`, valid conversions, and fixed-array defaults.

### Fixed
- **Out-of-range `toint(int<N>, …)` no longer truncates silently.** Every string-to-integer conversion (both `toint(T, s)` and the typed conversion path) now uses the checked `leash_bigint_parse` for all widths, including narrow ones — `toint(int<8>, "300")` reports a runtime range error instead of returning 44.
- **Out-of-range float-to-int conversions no longer produce poison.** `toint(T, float)` and float→int casts call `leash_int_from_double_checked`, which rejects NaN, infinity and values outside the target's signed/unsigned range with a diagnostic instead of LLVM's undefined `fptosi` result (a common source of bogus indices/sizes and crashes).
- **Dynamic division and remainder are guarded at runtime.** A divisor that is not a compile-time constant is checked for zero, and signed `MIN_VALUE / -1` is rejected, with a diagnostic instead of an LLVM trap or undefined value. Static checks still catch constant cases at compile time.

## [1.10.2] - 2026-11-17

Patch release: the Windows installer no longer hangs.

### Fixed
- **`install.bat` no longer hangs in `xcopy`.** The standard-library copy used a destination ending in `\*`, which makes `xcopy` ask "F = file, D = directory?" and wait forever for input. Both `xcopy` calls now use `/I` (assume directory target) with directory-only destinations, and print progress before each copy.

## [1.10.1] - 2026-11-17

Patch release: the Windows release bundle is usable from a plain `cmd` prompt.

### Fixed
- **Windows bundle now works from a plain `cmd` prompt.** The self-hosted compiler shells out through `system()`/`popen()`, which use `cmd.exe` on Windows, but its command strings were POSIX (`realpath`, `test -L`, `rm -f`, `date +%s%N`, `/tmp/...`, `-pthread -lm -ldl`, `--hash-style=gnu`). The Windows bundle now ships tiny native shims (`scripts/win_shim.c`, built with MinGW-w64) for those utilities and a `clang.exe` wrapper that strips POSIX linker flags, rewrites response files, selects `leash/windows_stubs.c`, adds the Windows system libraries, and forwards to a real Windows clang (`clang.orig.exe`). The compiler uses `date.exe` and `.exe` temp names on a Windows host, detected at runtime with `echo %OS%` (`_PLATFORM` is always baked as `linux64`). Temp files live in the current directory instead of `/tmp`.
- **`install.bat` sets up the clang shim** and accepts `LEASH_CLANG=<path to clang.exe>` for the real compiler; it prints a clear message when no Windows clang is configured.
- **Host detection and temp paths are Windows-safe.** `echo %OS%` is used at runtime (`_PLATFORM` is always `linux64` in the self-hosted compiler), `date.exe` is called explicitly because `cmd` has a `date` builtin, carriage returns are trimmed from shell output, and `leashc run` uses a plain `.exe` temp name in the current directory.

### Known limitation (Wine)
- Under Wine, `leashc compile ... to out.exe` works end-to-end and produces a runnable PE, but `leashc run` fails at its final auto-exec of the temporary exe: Wine's `popen`/`cmd` does not resolve the temp binary from the compiler process even though the same command works from a standalone PE and the file exists. Use `leashc compile` followed by running the produced `.exe` under Wine. This is a Wine-specific process-spawn quirk, not a bundle issue.

## [1.10.0] - 2026-11-17

Minor release: the arena memory layout is hardened end-to-end and the
optimization scale is monotonic. `--arena` is now compile-or-fail (no
warning-only outcome), its header layout is fully validated, and the
compiler's object cache can no longer be poisoned by an interrupted build.

### Added
- **Arena: tagged allocation headers.** Every ordinary allocation now writes a magic tag into the spare high bits of its existing 8-byte size header. Interior pointers and foreign blocks are rejected structurally before any size word is trusted, at zero bytes per allocation. All header consumers (`realloc`, root blob/vec/hash stores, sole-block growth) share the same validation, replacing several inconsistent copies.
- **Arena: block-bounded string validation.** A single validator now proves that a string's advertised `total` actually fits inside the block holding its header before trusting `len` or computing `cap = total - 17`. Used by every string consumer (lookup, append, `realloc`, root stores/reclaim), so a forged or stale header can no longer drive an out-of-bounds read or copy. Failures produce distinct diagnostics for tag, bounds, length and terminator.
- **Arena: strict compile-time lifetime audit.** In `--arena` mode every lifetime diagnostic is now an `E_ARENA_LIFETIME` error (there is no warning-only outcome): the compile either emits a binary or fails. `-oe`/`--warnings-as-errors` is retained as a no-op for existing scripts.
- **Arena: adversarial layout stress harness.** `tests/test_arena_layout.py` builds `tests/test_arena_layout.c` against the runtime under ASan/UBSan and checks forged string headers, interior-pointer `realloc`, oversized `realloc` and slice byte-count overflow all fail safely.
- **README: compatibility promise for 1.x.** Documents what 1.x guarantees and that 2.0.0 will be the first breaking change, when `--arena` becomes the default layout.

### Fixed
- **Arena: a forged/stale string header could read past its block.** `ar_str_read` validated the magic and a self-consistent `(total, len)` pair but never compared `total` to the block's capacity, so a header inside a 32 KB block could advertise a megabyte string and make `s[len]` fault (reproduced under ASan). `total` is now bounded by the owning block on every path.
- **Arena: `realloc` accepted interior pointers.** The generic header at `ptr-8` was an untagged size, so an interior pointer whose preceding bytes looked like a small integer passed `arena_check_payload`. The header is now tagged and its containment in the block checked.
- **Arena: `realloc` sizes near `SIZE_MAX` were not rejected.** Only allocation was size-capped; a huge `realloc` wrapped `(n+7)&~7` into a tiny/shrunk buffer while the header advertised the huge size. Growth now applies the same 48-bit ceiling.
- **Arena: unchecked byte arithmetic in vectors and slices.** `lv_alloc_data` and the three slice paths now guard `count * elem_size` against wraparound and the arena ceiling before allocating and `memcpy`ing.
- **Arena: sole-block growth now checks containment.** `arena_grow_sole_string` verifies the string header lies inside the block and that the old allocation fits before rebasing it.
- **Arena: spawn-transfer string lengths are ceiling-checked** before the worker copies them, so a corrupt prefix cannot advertise an unbounded length.
- **Compiler: object-cache writes are atomic.** Runtime and program objects are compiled to a temp path and published with `File.rename` only on success, so an interrupted clang can no longer leave a truncated `.o` in `~/.leash/shcache` that every later compile silently reuses (this produced a "file not recognized" link error during stress testing).

### Validation
- **Full suite: 421 tests pass**, including the new adversarial arena layout tests, the strict lifetime-audit tests, and the monotonic `-O` scale contract.
- **Arena-vs-GC corpus sweep:** 193 programs (96 examples, 101 stdlib smoke tests, minus programs without an entry point) were compiled and run in both modes. 185 produce identical output; 4 are cleanly rejected by the strict audit or have no entry point; 5 differ. Two of those (`examples/multithread.lsh`, `examples/vecmath.lsh`) are timing/RNG-dependent outputs, and three are **pre-existing arena lifetime bugs** reproduced with the 1.9.1 compiler and runtime: `tests/stdlib/collections_lru.lsh` and `tests/stdlib/text_regex.lsh` dereference a reclaimed frame pointer, and `tests/stdlib/llvm_ir.lsh` reallocs a stale string header. Arena remains experimental for these ownership patterns; use GC for them.
- **Benchmarks (18-task suite, O2, median of 5):** arena is 1.13x faster than GC by geometric mean (8.9x on `alloc_churn_loop`, 2.25x on `allocstress`, 1.6x on `primes`) but slower on most tiny compute-bound tasks; it uses less peak RSS on **all 18 tasks** (1.12x geomean). Against best-of-level C++/Rust, arena is close but behind overall (0.91x / 0.86x geomean), ahead on churn/hash/matrix tasks and shipping ~22 KB binaries vs ~4.5 MB for Rust. The inline arena allocator in generated IR was corrected to emit the same tagged header as the runtime — without this, vector buffers grown through `realloc` failed the new header check.

### Changed
- **Arena runtime code size kept flat in generated binaries.** Duplicated string-header validation was consolidated into one helper; the generated `hello --arena` binary is byte-identical in size to the previous release despite the added checks. The SEGV diagnostic is emitted with a single formatted call.
- **The `-O` scale is now monotonic by construction.** O1 uses the `-O1` backend, O2 adds the arena IR tier 2 (exact trip-count reserves) with the same backend, O3 adds LTO and IR tier 3, and O4 adds native code generation. No higher level disables an optimization enabled by a lower one, removing the old inversion where clang's `-O2`/`-O3` were slower than `-O1` for Leash's call-heavy IR. The runtime is compiled at `-O0`/`-O1`/`-O2`/`-O3`/`-O3 -flto` for O0..O4 instead of defaulting to `-O2` at every level. `tests/test_opt_scale.py` verifies the flag chain and IR tiers are additive. Wall-clock numbers on tiny programs can still tie or vary with noise; the optimization contract is what is guaranteed.

## [1.9.1] - 2026-11-17

### Added
- **Arena regression coverage under sanitizers.** `tests/test_arena_runtime.py`
  now builds the runtime harness with ASan/UBSan when the compiler supports
  it, so the shared-string and use-after-free regressions fail loudly in CI.
  The C harness gained cases for shared-string `realloc` (small and sole-block),
  self-append of a large string, near-`SIZE_MAX` allocations, tip-shrink tail
  zeroing, spawn-transfer strings with embedded NULs, typed root-copy container
  identity, and staged-hash-node alignment.

### Fixed
- **Arena: `realloc` of a shared string no longer aborts or dangles its other holders.** The string header is now checked before the generic allocation-size header (whose `ptr-8` word is the string length, with bit 63 set for a shared string), and a shared string's dedicated sole block is never reclaimed. Regression tests cover both a small shared string and a large sole-block string under ASan.
- **Arena: self-append of a large sole-block string no longer reads freed memory.** Growing the block with `realloc` can move it; when the appended piece aliases the string (`s = s + s`), the runtime now falls back to the alias-safe allocate-then-copy path instead of reading the piece through the freed pointer.
- **Arena: near-`SIZE_MAX` allocation requests are rejected.** The bump allocator's pointer arithmetic could wrap and return a tiny buffer with a negative-size memset; a 48-bit per-allocation ceiling now fails such requests deterministically. Non-power-of-two alignments are rejected too.
- **Arena: shrinking a tip allocation zeroes the entire reclaimed tail.** The old memset started at the unaligned `new_size`, leaving up to 7 bytes of stale data in the region the bump pointer moved back over.
- **Arena: worker-thread TLS destructor now marks the arena dead and clears its key**, matching the Windows path and preventing a later allocation from rebuilding an arena behind unwound frames.
- **Arena: typed root copies now preserve container identity and cycles.** `te_root_vec`/`te_root_hash` consult the same identity map as aggregates, so two fields holding the same vec/hash still alias after the copy and a container cycle terminates instead of recursing to the depth cap. The depth-cap fallback now stores `NULL` rather than a frame-local pointer into a root slot (a dangling pointer after the pop).
- **Arena: spawn transfers keep embedded NULs in string arguments.** The transfer copy carries its byte length in an 8-byte prefix; the worker's adopt step no longer truncates binary strings at the first `'\0'`.
- **Arena: staged hash nodes are 8-byte aligned.** The node+key stride is rounded up to 8 in every staging sizing and copy pass (and in the typed commit walker), removing undefined behaviour on strict-alignment targets.
- **Arena: miscellaneous hardening.** Re-storing a slot's own root copy no longer marks it shared (which stopped in-place reuse and grew the root arena each iteration); `escape_agg_typed` now pops its frame on a NULL slot; and root-copying a malformed hash with a non-positive bucket count no longer reads past its bucket array.

## [1.9.0] - 2026-11-16

### Changed
- **Arena: string metadata lookups cache the last block.** Repeated `bsize`,
  comparison, and COW-marking calls on strings from the same older block no
  longer walk the arena block list every time. The cache stores raw bounds
  and is cleared by every block-free path, so it stays safe after scope
  resets and block reallocation.
- **Arena: spawn transfers size their private block from the copied payload.**
  `xfer_ensure` no longer reserves 512 bytes for every hand-off with a short
  string argument; the first block is sized from the request (with a small
  alignment floor), reducing per-spawn heap use.

### Added
- **Restricted literal types (`def Name : type lit, ...;`).** A restricted
  type accepts only its exact declared literals at compile time (`0`, `1, 2`,
  `"ok", "error", nil`, and the same for char/bool/float). Assigning another
  value is rejected with the allowed values in the message, and a restricted
  variable cannot be implicitly widened to its underlying base type; copying
  into another variable of the same restricted type is allowed. The value is
  lowered to the literal's base type in codegen.

### Fixed
- **Arena: registered long-lived slots are validated before a frame is reclaimed.**
  Global, shared, and static-field pointer slots are registered with the
  runtime; before every scope pop or loop reset, the arena checks them for
  pointers into the frame about to be reclaimed and aborts with a clear
  diagnostic instead of leaving a dangling pointer. The check is on by
  default and can be disabled with `LEASH_ARENA_VERIFY=0` after lifetimes
  are proven. Library prologues now register function shared variables too,
  class/struct static-field stores use the typed root-store path, and
  boxed struct/class slots register their instance pointer instead of a
  guessed inline byte range. Generated inline frame pops call the same
  verifier as the runtime slow path, so the check is not limited to
  `leasharena_exit()` calls.
- **Arena: compile-time lifetime diagnostics.** An ergonomic, non-Rust-like
  lifetime audit runs in `--arena` builds. It copies/roots the values whose
  types are safe to move; direct `malloc`/`calloc`/`realloc`/`free` calls and
  raw-pointer returns are hard errors (`E_ARENA_LIFETIME`), while raw pointers
  stored into long-lived slots or passed to workers warn (`W_ARENA_LIFETIME`).
  `--warnings-as-errors`/`-oe` now promotes those warnings to errors; the
  previously unused flag is wired through the driver. Diagnostics use a
  structured format with the source location, a three-line snippet and
  caret, a `why` explanation, a concrete `help` suggestion, and an optional
  audit `trace`.
- **Arena: recursive aggregate escapes and root stores no longer allocate bookkeeping on every call.**
  The identity maps and graph arrays used by `leasharena_escape_agg_typed`
  and the typed root stores now come from the per-thread escape stage
  cache instead of `malloc`/`calloc`/`realloc`. After the first few calls
  these paths touch the heap allocator only when a temporary exceeds the
  cache's 256&nbsp;KB limit or all 16 slots are in use.
- **Arena: recursive structs/classes and cyclic aggregate graphs now compile and escape safely.**
  Struct POD analysis and value cloning are bounded for self-referential
  types, and `leasharena_escape_agg_typed` copies the reachable instance
  graph with an identity map. A cycle in the returned graph keeps pointing
  at the copied instances instead of recursing forever or retaining freed
  frame memory.
- **Arena: allocating functions are no longer misclassified as frameless.**
  Local variable types are collected before allocation analysis, so
  `s = s + c` on a local string is recognized as an allocation and the
  function keeps its arena frame and loop checkpoints.
- **Arena: hot string appends avoid repeated arena walks.**
  `leash_str_append_char` uses spare capacity regardless of which block
  owns the buffer, and `leash_str_append_span` grows a dedicated oversize
  block in place instead of copying to a second large buffer. Loop stores
  are promoted to the root arena only when an enclosing loop really
  checkpoints.
- **Arena: spawn arguments are rooted by type and unions keep their boxed size.**
  Containers and aggregate arguments are copied with the compiler's type
  tree so nested string/container fields survive the spawner's frame.
  A union is a boxed value, so escape and hand-off paths move the box and
  its payload bytes rather than treating the union as a pointer.
- **Arena: string pointers are now distinguishable from other pointers.**
  Arena strings carry a magic tag (`ARENA_STR_MAGIC`) in the high bits of
  their header word, so a non-string payload pointer can never be
  misread as a string (previously the length word was trusted blindly).
  The tag is preserved by in-place string growth
  (`leash_str_append_span`), so a grown string keeps its identity.
- **Arena: `leash_gc_realloc` checks ownership before reading headers.**
  The size header lives in front of the payload, so reading it first
  could touch memory the thread does not own (a foreign pointer would
  fault or produce a bogus size). Ownership is now checked first, and a
  foreign pointer aborts with a clear diagnostic instead of corrupting
  the heap.
- **Arena: `leash_gc_realloc` reallocates strings as strings.**
  Growing an arena string used to overwrite the string's length word and
  write past the end of the allocation (the bump pointer only reserves
  the string's total size). Strings now get a dedicated path that keeps
  the magic tag, the length, and the bytes.
- **Arena: shrinking a tip allocation zeroes the reclaimed tail**, so the
  next allocation over that range cannot observe stale bytes through the
  no-zero-fill fast path.
- **Arena: an emptied vector keeps its buffer.** `leash_sh_vec_ensure`
  only reallocated when `size > 0`, so clearing a vector abandoned its
  growth buffer; the buffer is now reused (and reallocated whenever one
  exists).
- **Arena: deep escape of a vector of structs no longer leaks or
  double-frees stage memory**, and `leasharena_root_copy_hash` no longer
  frees interior pointers of a hash table it only copies.
- **Arena: worker-thread arenas are released when the thread exits.**
  A thread-local destructor (`pthread_key` on POSIX, the Windows
  equivalent) now releases the thread's arena blocks and stage cache;
  previously every worker thread leaked its arena until process exit.
- **Vector element-wise arithmetic keeps the element width (all
  backends).** `vec OP vec` used to read and write 8-byte elements
  regardless of the vector's element size (an out-of-bounds read for
  4-byte float vectors) and returned a vector of the wrong type. `vec OP
  scalar` did not compile at all (invalid LLVM IR); the scalar is now
  broadcast over the elements in both operand orders. `examples/vecmath.lsh`
  now builds and runs under both GC and `--arena`.
- **Windows: programs that read console input now link.** The runtime's
  `leash_sh_input` was a weak definition, and a weak definition does not
  satisfy a strong reference in COFF/PE: every Windows build that called
  the input helper failed with "reference undefined to leash_sh_input"
  (the compiler itself is such a program). The definition is strong
  again — it lives once in the shared runtime region, so the old
  duplicate-definition workaround is no longer needed.
- **`--arena` compiles for Windows.** The escape staging path included
  `<alloca.h>`, which does not exist on Windows targets, so every arena
  build for `win64` failed to compile the runtime. The staging helper
  now uses `_alloca` on MSVC and `__builtin_alloca` everywhere else.
- **Windows: the per-thread state is really per-thread.** Clang's
  `x86_64-w64-mingw32` target expands `__declspec(x)` to
  `__attribute__((x))`, and `thread` is not a valid attribute there:
  clang warns "unknown attribute 'thread' ignored" and the variable
  silently degrades to a plain global shared by every thread. The
  arena state (`leasharena_tls_ar`) therefore lived in a different
  place than the one the generated IR addressed (the IR declares it as
  `external thread_local global`), so the inline fast paths and the
  runtime worked on two different arenas: the checkpoint stack was
  indexed at `-1` and the first frame exit of a worker thread died with
  a page fault at `0xffffffffffffffff`. The runtime now uses
  `__declspec(thread)` only under MSVC and `__thread` everywhere else.
  The same silent downgrade had turned the GC's per-thread "did I lock?"
  guard into a process-wide flag; it is fixed too.
- **Windows: `File.readb()` / `File.readlnb()` no longer crash.** The
  byte-slice helpers returned a 16-byte struct by value, and a struct
  return is not ABI-stable across the IR/C boundary: clang lowers the
  generated IR call to "returned in RAX:RDX" but lowers the C
  definition to "returned through a hidden pointer" on Windows x64. The
  caller therefore passed the file handle where the runtime expected
  the result slot, and the first `fseek` on the bogus handle died with
  an invalid-handle exception (`examples/files.lsh` crashed in the
  `writeb`/`readb` test). The slice now comes back through an
  out-pointer, which keeps the boundary to plain scalars and pointers
  on every target.
- **Strings are copy-on-write across aliases (GC and `--arena`).**
  Appending to a string used to mutate the buffer in place, so every
  other reference to that string observed the write: `b: string = a;
  a = a + "2";` changed `b` too, a string stored in a vector or hash
  changed when the source variable was appended to, and a callee that
  appended to a `string` parameter rewrote the caller's buffer. Every
  store now marks the value shared (`leash_str_share`, emitted for
  locals, globals, fields, container elements and all function/method
  parameters), and an append copies a shared buffer before growing it.
  `tests/test_codegen_fixes.py` locks in identical output for both
  backends.
- **Globals declared without an initializer are live empty containers.**
  `g: vec<int>;` and `g: hash<string, int>;` compiled to a
  `zeroinitializer` pointer, so the first `push`/`pushb` dereferenced
  null. Global initialization (`main` and `leash_init`) now allocates
  an empty container for such declarations.
- **Arena: a loop's hoisted reserve never reads an uninitialized
  container slot.** The O2+ trip-count reserve was emitted for any
  receiver whose *declared* type was a vector/hash, including one
  declared inside the loop body: its `alloca` slot is only written by
  the first iteration, so the reserve ran on garbage. A new
  declaration-ready set (`ar_decl_ready`) gates locals; parameters,
  `this` and globals are marked ready in the prologue.
- **Arena: an escaped hash owns its nodes after the commit.** The hash
  escape rebuilt the bucket chains from the staged node pool, but the
  stage block (and the pool in it) was released immediately after the
  commit, leaving the committed table with dangling nodes
  (heap-use-after-free on the first lookup, e.g. returning a
  `hash<string, Box>` from a function). Commit now copies every node
  (node image plus inline key) into the caller's arena/GC heap.
- **Arena: type-tree leaf fields no longer collapse onto the root.**
  The aggregate descriptor uses `-1` as the "no child" sentinel, but the
  field builder round-tripped it through `parse_int_str`, which does not
  parse a sign and returned `0`: a scalar field of a returning struct
  was then emitted as a heap reference to descriptor 0 (the containing
  type) instead of being skipped. The sentinel is carried explicitly
  now, and the duplicate `ar_te` typedef was removed (one descriptor ABI,
  `{ i8*, i32, i32, i32 }`).
- **Compiler: long expression chains no longer cost exponential type
  queries.** `get_expr_type` recursively typed both operands of a binary
  expression and asked for both types twice, so an n-term `+` chain cost
  O(2^n): the bootstrap seed effectively hung on a 48-term concat. The
  operands are typed once and the result reused.
- **Parser: type parsing no longer rewrites the token it reads.**
  `parse_type` took the identifier token's text by reference and the
  suffix parsers append to it (`Name[]`, `Name<N>`, sized ints). When the
  type was a cast lookahead, restoring the token position did not undo
  the text rewrite, so the following identifier could be corrupted (and
  the result was an accidentally shared buffer). The parser copies the
  token text now.
- **`leash self-host` quotes paths.** `mkdir`, `cd`, `cp` and the seed
  lookup interpolated a checkout path unquoted, so a repository under a
  directory with spaces (or shell metacharacters) failed. Paths are
  single-quoted now.
- **Arena: a value stored into a global outlives the storing frame.**
  `g = v` (string, vector, hash) left the global pointing into the
  scope arena, which is rewound when the storing frame exits: the
  global then read freed memory. Globals now hold a *root-arena copy*
  of the value, and the slot reuses that copy in place when it is the
  only owner (string shared bit, vector/hash `shared` flag), so a loop
  that overwrites the global does not grow the root arena.
- **Arena: a value stored into an outer local from a loop body
  outlives the iteration.** The loop checkpoint rewinds the iteration's
  allocations, so the outer slot was left pointing into reclaimed
  memory. Such stores are now root-promoted into the root arena (the
  slot keeps the root copy), which makes the iteration checkpointable
  without losing the value.
- **Arena: a variable declared in a loop body but read after the loop
  is no longer reclaimed.** The GC keeps the last value alive (the
  variable outlives the iteration); the arena let the checkpoint reclaim
  it and the read was a use-after-free (a segfault in practice). An
  escape analysis (`ar_collect_escapes`) now detects the names that are
  declared in a loop body and referenced outside it, and root-promotes
  their stores.
- **Arena: `g = g + "x"` on a global reads the rooted value.** The
  self-append read the scope copy that the previous store had already
  replaced, so the global ended up with the wrong contents. The global
  now keeps the root copy and the append sees it.
- **Arena: the spawn hand-off no longer leaks the argument block.** When
  the worker cap was reached (or the thread could not be created), the
  private transfer block was dropped without being released: 20 000
  capped spawns leaked 15 MB of arena RSS (GC: 2.9 MB). All failure
  paths now release it.
- **Arena: a non-string spawn argument produced invalid LLVM IR.** The
  container/aggregate hand-off emitted `load i8*, i8** %slot, 8` instead
  of `load i8*, i8** %slot, align 8`, so every spawn with a vector,
  hash, struct or slice argument failed to compile ("expected metadata
  or 'align'"). Only string arguments had been exercised.
- **Arena: a spawn with a struct or class argument computed the size of
  the pointer instead of the pointee.** The hand-off emitted
  `getelementptr %struct.P* null, i32 1` (the pointer type as the GEP
  base), which is not valid LLVM: the size of the pointee needs the
  aggregate type as the base. Every spawn with a struct or class
  argument failed to compile.
- **Arena: a spawn with a union argument treated the union as a
  pointer.** A union is a boxed value (`{ tag, payload }`), so the
  pointer hand-off produced an invalid bitcast. The union is now copied
  into the transfer block by value, which is what keeps it alive past
  the spawner's frame.
- **Unions can be built from a literal again (both backends).** `U { i:
  3 }` emitted a GEP on the undefined `%struct.U` type and stored a
  pointer into a `%union.U` slot, so every union literal failed to
  compile ("base element of getelementptr must be sized"). The literal
  now builds the union box (tag + payload) and hands the value on.
- **A union can be stored in a hash (both backends).** Boxing a union
  value for a container bitcast an aggregate to a pointer, which is not
  valid LLVM. The value is now copied into a heap box that the
  container points at, and read back out of it.

### Added
- **Arena: root slots for globals and outer locals.** The compiler emits
  a typed root store for every heap value that outlives its frame
  (`leasharena_root_store_string`/`_vec_typed`/`_hash_typed`/
  `_blob`): the value is copied into the program-lifetime root arena
  field by field (never a bulk copy of pointer elements), and the
  previous root copy is reused in place when nothing else can observe
  it. The root arena grows geometrically and releases a block once it
  is the sole owner of its content, so a program that stores into the
  same global in a loop keeps a constant footprint (measured: 89 KB of
  root payload for 100 000 stores, GC RSS 2 240 KB vs arena 1 988 KB).
- **Arena: a mutable container stored into a global re-points the
  source local.** `g = v; g.pushb(4)` now sees the same vector through
  both names (the source local is updated to the root copy). Strings
  are deliberately *not* re-pointed: they are immutable/copy-on-write,
  so the original keeps its own value.
- **Arena: escape copies only the live payload.** A return that escapes
  the frame copied the whole allocation, including the unused tail of
  a grown vector or the slack of a hash table. The escape path now
  copies the live size only.
- **Arena: no zero-fill in arena mode.** The arena never scans or traces
  its memory, so the runtime no longer memsets the region it hands out;
  every skipped fill has a verified "fully initialised before read"
  contract. The reclaimed-region poison fill is unchanged (it is the
  debugger aid, not the allocator).
- **Arena: the arena stats report the scope and the root arena
  separately.** `LEASH_ARENA_STATS=1` prints the scope arena's peak and
  current block bytes and the root arena's peak/current bytes and
  payload, so a program that grows the permanent footprint is visible
  instead of being hidden behind the scope numbers.
- **Arena: a compact private transfer for `spawn`.** The hand-off uses
  one private malloc (a `leash_xfer` header, the argument block and a
  64-byte copy area) instead of a per-argument arena. Only string
  arguments travel through the transfer (`leasharena_xfer_copy_string`);
  vectors, hashes, structs and slices keep their root copies. The
  worker prologue adopts the strings into its own frame
  (`leasharena_adopt_string`) and releases the transfer immediately
  after unpacking, so the transfer lives only for the hand-off
  (measured: 20 000 spawns, arena RSS 2 048-2 164 KB vs GC 2 684-2 696 KB).
- **Arena: returning a string literal does not copy it.** A literal is
  read-only program data that outlives every frame, so the return only
  pops the frames now (measured: 100 000 global string stores, arena
  0.0048 s vs 0.0082 s before).
- **Arena: rooted growth for caller-owned vectors and hashes.** A vector
  or hash owned outside the current frame (a parameter or a global) must
  keep its growth buffer alive after the growing frame exits. The
  compiler now emits the rooted runtime entry points
  (`leash_sh_vec_reserve_rooted`, `leash_sh_vec_extend_rooted`,
  `leash_sh_vec_insertv_rooted`) and `leash_sh_hash_make_rooted` (which
  relocates a hash table into the root arena and marks it rooted so
  later inserts allocate there too) for those targets.
- **Arena: regression harness for the runtime fixes.**
  `tests/test_arena_runtime.c` / `tests/test_arena_runtime.py` cover the
  string identity tag, rooted vector/hash survival across a frame pop,
  tip growth that ignores strings, and the rejection of foreign-pointer
  realloc. `tests/test_arena.py` gained Leash-level parity cases for
  growing a caller-owned vector/hash from a framed callee (`extendv`,
  `insertv`, hash inserts), for vector element-wise arithmetic with
  scalar broadcasting, and for nested-container escape returns
  (`vec<vec<...>>`, `hash<string, vec|hash|struct>`) plus
  uninitialized global containers. `tests/test_codegen_fixes.py` adds
  the string-alias copy-on-write and long-concat-chain regressions, and
  `tests/test_cross_win64.py` builds and runs the arena backend for
  win64.
- **Arena: regression tests for the root-arena lifetime model.**
  `tests/test_arena.py` gained a `TestArenaRootLifetime` class that
  compares arena output against the GC output for the cases that used
  to dangle: a global that holds a vector/hash/string, the aliasing of
  a global vector with its source local, `g = g + "x"` on a global, an
  outer local written from a loop body, and a variable declared in a
  loop body but read after the loop. It also checks that the root arena
  does not grow with the number of stores into the same global, and
  that a spawn with a vector argument produces the same output in both
  backends, that every spawn argument kind (string, vector, hash,
  struct, class, union, slice) survives the hand-off, and that a union
  keeps its value through a literal, a container, a return and a
  global.

### Changed
- **Arena escapes stage on the C stack, not the heap.** Small escape
  payloads are staged in the escape helper's own frame (`alloca`), and
  larger ones come from a small per-thread stage cache (16 slots) that
  recycles released stage blocks. The escape path no longer allocates
  and frees a bounce buffer per value: a struct-returning benchmark went
  from 405 mallocs / 404 frees to 7 / 7, and a 500-string benchmark now
  performs 3 mallocs in total.
- **Seed lookup ranks versions numerically.** The fallback that picks a
  checked-in seed when the exact-version file is absent used text
  order, so `leash-1.10.0-target-x86_64` sorted before
  `leash-1.9.9-...` and could select an older seed that does not
  understand the current sources. `scripts/bootstrap.sh`,
  `leash self-host` and the registry validator try the current version
  first and rank any fallback by numeric (major, minor, patch) order.
- **Release drift after 1.8.4.** The VS Code extension still carried
  version 1.8.3, and the checked-in bootstrap seed set was still the
  1.8.3 one while the compiler reported 1.8.4. The extension version
  now tracks the compiler again, every seed lookup
  (`scripts/bootstrap.sh`, `leash self-host`, the registry validator)
  derives the seed name from the compiler version instead of hardcoding
  it, and the checked-in seeds were regenerated as
  `bootstrap/leash-1.8.4-target-*` from the repaired compiler. All five
  targets (x86_64, x86, arm, win64, jvm) were rebuilt; the native seeds
  pass `compile --check`, and a fresh x86_64 seed bootstraps the
  current sources to a fixed point.

### Documentation
- Documented rooted vector/hash growth, the stack-staged escape path and
  its stage cache, worker-arena release, the hardened `realloc`
  contract, and the vector-arithmetic/broadcast behavior across
  `README.md`, `docs/advanced.html`, and `docs/compiler.html`.
- Documented the arena lifetime model in `README.md`,
  `docs/advanced.html` and `docs/limitations.html`: a value that
  outlives its frame (a global, an outer local written from a loop
  body) is copied into the program-lifetime root arena, the root arena
  is never rewound, the root slot reuses its copy in place while it is
  the only owner, and a `spawn` hand-off uses one compact private
  transfer that the worker releases after unpacking. The one accepted
  difference from the GC is documented too: after `g = v; g = X`, the
  arena reuses the global's root copy in place, so `v` observes `X`'s
  contents, while the GC keeps `v` pointing at the old vector.

- Documented restricted literal types, the arena lifetime audit
  (`W_ARENA_LIFETIME`/`E_ARENA_LIFETIME`, structured diagnostics, `-oe`),
  the recursive graph copy, typed spawn roots, union box sizing, arena
  string tagging and COW rules, the `LEASH_ARENA_*` environment flags,
  and the 1.9.0 benchmark results across `README.md` and every page in
  `docs/`.
- Updated the Vim, Emacs, and VS Code highlighters for the 1.9.0 release,
  including restricted literal type declarations, `type` keyword hover
  documentation, and a committed ESLint config so `npm run lint` works
  from a fresh checkout. Version strings in the VS Code extension and
  all docs/README badges now track the compiler.
- Bumped the compiler, package manifest, extension, docs, and README to
  1.9.0, regenerated all five cross-target bootstrap seeds, and removed
  the stale 1.8.4 seed set.

## [1.8.4] - 2026-11-12

### Added
- **Arena safety hardening (`--arena`, experimental).** Reclaimed scope
  and loop-iteration memory is now **poisoned with `0xDD` by default**,
  so stale reads into a dead frame become obvious garbage instead of
  silently valid-looking data. Disable the poison memset with
  `LEASH_ARENA_POISON=0` (the legacy `LEASH_ARENA_POISON=1` is still
  accepted) when lifetimes are already validated and peak speed matters.
- **Arena diagnostics.** Frame/loop misuse now aborts with a clear
  message instead of silently ignoring impossible states: scope
  underflow on unmatched exit, `leasharena_exit_n` beyond current depth,
  and invalid or out-of-range loop-frame indices on reset/exit.
- **Arena instrumentation.** `LEASH_ARENA_STATS=1` prints arena peak
  block bytes, current live blocks/payload and rooted bytes to stderr on
  shutdown for per-program measurement.
- **Arena-only optimizations at higher opt levels.**
  - **O2+**: exact trip-count vector/collection reserves and arena-only
    hash pre-sizing (`leash_sh_hash_reserve`) fire only when the opt
    level is >= 2 (`-DLEASH_ARENA_O2`), keeping O0/O1 conservative.
  - **O3+ (`-DLEASH_ARENA_O3`)**: nested counted-loop push analysis and
    hoisted reserves (never above a loop-local receiver), compact 24-byte
    hash nodes, and fused hash keys that build `"<prefix>"+int` in a
    stack buffer for push/get/index-set (`leash_sh_hash_push_int` /
    `get_int`), avoiding per-iteration temporary key strings.

### Changed
- **Arena peak memory and opt-level scaling.** Measured arena block peaks
  (`LEASH_ARENA_STATS`, O2 vs O3) improve or stay flat and are monotonic
  where applicable — notably `hash_lookup` 1,873,960 → 1,152,184 block
  bytes at O3 and reduced peaks for `matrix_mul` / `matrix_transpose` at
  O3 from compact hash nodes and fused keys.
- **Arena performance profile (18-task suite, interleaved GC-vs-arena,
  cache cleared, alternating order).** Arena shows broad wins and
  identical outputs across allocation/churn- and hash/string-heavy tasks;
  allocator peak bytes and structural wins are consistent. Timing and
  max-RSS deltas on tiny/compute-bound cases (e.g. `fib`, `pi`, and in
  some samples `matrix_mul`, `sort_int`/`sort_string`) can be run-to-run
  noise on a loaded host, while growth-heavy kernels remain bounded by
  live/superseded data retained until scope reset and by page
  granularity.

### Fixed
- **Arena: deep escape of nested heap fields in returned structs/classes.**
  Returning a struct/class containing nested struct fields with heap
  members (e.g. `Inner { a: string }` inside `Outer`) now deep-stages all
  heap fields recursively from the live dying frame **before** the scope
  pop and deep-commits them into the caller's frame **after**, so nested
  string/vec/hash/struct fields never read reclaimed (now-poisoned)
  memory. This fixes a use-after-scope that could segfault under
  poison-on-default and was previously exposed as `nested_struct_return`
  corruption.

### Documentation
- Documented arena behavior end-to-end: safety model and env vars
  (`LEASH_ARENA_POISON`, `LEASH_ARENA_STATS`), opt-level/arena-exclusive
  optimizations, lifetime/escape rules, C-ABI/root-arena notes, and where
  arena wins vs loses (with methodology caveats) across README, docs, and
  benchmarking notes.

## [1.8.3] - 2026-11-09

### Fixed
- **Arena: stores of frame-local allocations into owned aggregates.**
  A struct clone allocated inside a helper frame and stored into a
  param-passed vec (e.g. a recursive quicksort swap) used to dangle
  once the helper returned. Struct clones are now rooted
  (`leash_gc_malloc_rooted` in arena mode) so any stored clone
  outlives every store target; the loop analysis also treats any
  fresh allocation stored into an outer heap object as outer
  mutation (read-only calls with scalar args stay eligible). This
  was corrupting `vec<Node>` quicksort under `--arena` while GC
  (mark-sweep) was unaffected.
- **Test-run safety: memory caps in the benchmark harness.**
  `scripts/benchmark.py` now caps every *run and compile* at
  `LEASH_MEM_CAP_MB` MiB (default 1024; `--mem-cap`; env
  `LEASH_MEM_CAP_MB`), enforced via systemd cgroup when it actually
  kills (verified on-host — plain scope creation without enforcement
  falls back to a `/proc` VmRSS watchdog with SIGKILL). Exceeding
  runs record `memory cap exceeded` and mark the cell ERR so a
  runaway can no longer eat the host. `scripts/mem_guard.py` applies
  the same watchdog to whole-suite invocations
  (`python3 scripts/mem_guard.py -- pytest tests/ -q`; guarded full
  suite peaks at 488 MB).

### Added
- Six new benchmarks (`benchmarking/tasks/`, Leash + C++ + Rust each):
  `string_reverse` (100k char build + reverse), `sort_string`
  (5k random strings, LCG-seeded), `vec_sort_float` (20k manual
  quicksort), `matrix_transpose` (256² strided copy), `alloc_churn_loop`
  (100k× `vec<T>` + `tostring` churn), and `struct_pack` (5k `Node`
  quicksort with hand-rolled rank computation). Arena output matches
  GC on all six (struct_pack after the clone fix).

## [1.8.2] - 2026-11-09

### Changed
- **Arena loops that allocate nothing skip frames entirely.** Loop
  instrumentation now requires three conditions: the loop actually
  allocates (a loop that allocates nothing must not pay per-iteration
  reset costs), no outer survivors point into its temporaries, and
  the proven allocation sites are narrowed to growth methods
  (`push`/`pushb`/`extend`/`insert`) so read-only `get`/`set`/`size`
  no longer disqualify. Allocation-free loops (scalar matrix and
  sieve loops) now run at GC speed or better: `primes` 15 ms → 6 ms
  (beats GC 9 ms), `matrix_mul` 12 ms → 4 ms (beats GC 4 ms),
  `sort_int` unchanged. `benchmarks` can now also measure arena
  builds via `scripts/benchmark.py` with `LEASH_BENCH_ARENA=1`.
- **`tostring(int)` inside self-append chains is fused.**
  `s = s + "x" + tostring(i) + "-"` no longer allocates a temporary
  per `tostring` call; the decimal lands in the accumulator via
  `leash_str_append_int` (this fixes an i32→i64 widening mismatch in
  the existing int-append path). String accumulation growth
  additionally extends in place when the buffer is the bump tip, so
  `string_concat` ends at 2.9 MB (GC 2.5 MB, Rust 2.2 MB) at GC-beating
  speed.

## [1.8.1] - 2026-11-09

### Changed
- **Arena growth reclaim.** Oversize growth buffers now free the
  superseded allocation's block immediately instead of holding it
  until scope exit (dedicated single-occupant blocks, gated on no
  saved frame referencing the block). Vector growth routes through
  realloc (tip-extension plus block reclaim — doubling in arena mode
  instead of GC's +50% policy), and string appends and hash bucket
  growth do the same. Measured peak RSS (GC → arena, O2):
  `mem_copy` 9.6 → **10.1 MB** (was 14.0 before reclaim),
  `vec_stress` 3.9 → **3.4 MB** (now leaner than GC),
  `hash_lookup` 4.6 → **4.3 MB**, `allocstress` 2.5 → **1.9 MB**,
  `fib` parity. `string_concat` is still 2.4 → 2.9 MB: per-iteration
  string temporaries necessarily outlive their loop's survivors and
  stay in the function's frame.

### Fixed
- **Arena frameless returns inside loops.** Scalar functions proven
  allocation-free skip framing; their loop frames are now excluded
  too, so a return issued from inside an instrumented loop cannot
  pop the caller's frame.

## [1.8.0] - 2026-11-09

### Added
- **Experimental `--arena` memory layout (native-only).** A scoped
  bump arena selectable with `leash compile <file> --arena` (or
  `arena: true` in project config). GC remains the default; arena is
  the opt-in second backend (`-DLEASH_ARENA` runtime), planned as the
  default in a future release. There is no GC, no borrow checker, and
  no per-object free:
  - Thread-local 32 KB blocks bump-allocated (align + bump + memset),
    pushed on function entry and restored on exit — release is
    O(blocks), not per allocation. The exit for the common
    no-new-blocks case and the entry push are emitted inline in the IR
    (no runtime call); only stack growth and block release take the
    slow paths, so recursion-heavy code pays ~nothing for framing.
  - Allocation-free functions (no heap-typed locals, no heap-producing
    operations; e.g. `fib_rec`) are proven at codegen time and skip
    framing entirely — `fib(40)` runs at GC parity (0.30 s).
  - Loop checkpoints give the same property per iteration: a loop
    whose body stores no heap value outside itself and mutates no
    outer vec/hash frees its temporaries at the back edge instead of
    at function exit. Loops with surviving values fall back to
    function lifetime (still correct).
  - Escape rule, no borrow checker: a value that must survive its
    allocating frame is copied, not tracked. Returned strings, vecs,
    hashes, structs/classes (shallow body plus a deep copy of every
    heap-typed field), slices and multi-heap tuples stage to malloc,
    pop to the caller's frame, then commit there. `spawn` argument
    blocks and future boxes go to the program-lifetime root arena, so
    workers read persistent copies. Raw `*T` pointers and union
    payloads are caller-owned, same as under `--autofree`'s aggregate
    limitation — see `docs/advanced.html`.
  - C ABI: pointers are plain bump memory with no collector metadata
    (strings keep the O(1) `bsize` prefix) and no stop-the-world
    pauses; `to-dynamic`/`to-static` libraries link the same runtime
    and persistent data should use the root arena declared in
    `leash/gc.h` (`leasharena_root_alloc`, `leasharena_escape_*`).
  - Measured on `benchmarking/tasks/` (O2, peak RSS, GC → arena):
    `allocstress` 2.5 MB/0.05 s → **1.9 MB/0.03 s**, `fib` parity,
    `hash_lookup` 4.7 → 4.6 MB, `vec_stress` 3.9 → 3.9 MB; growth-
    heavy `mem_copy` (1M `i32`, 9.7 → 14.0 MB) and `string_concat`
    (2.4 → 3.3 MB) retain superseded growth buffers until scope exit
    (GC collects them) — the bump allocator's inherent cost. Vectors
    grow by doubling (plus tip-extension), so growth copying is
    cheaper; a second trial reran the full suite unchanged.
    356 tests pass (342 + 14 new `tests/test_arena.py` parity cases).

## [1.7.0] - 2026-11-08

### Added
- **`.bsize` as the canonical UTF-8 byte-length member.** `.__bytesize` is
  kept as a **silent** deprecated alias; `s.bsize` (property) and
  `s.bsize()` (method) return the same byte count on both native and
  JVM targets. **No deprecation warning is emitted** — `__bytesize`
  parses, type-checks and compiles exactly like `bsize`, so
  `--warnings-as-errors`/`warn_err` builds are unaffected. (The word
  “deprecated” in prose means “prefer the new spelling”, not “will
  warn”.)
- **4 new layout-stress benchmarks** (`vec_stress`, `string_concat`,
  `hash_lookup`, `matrix_mul`) in `benchmarking/tasks/` with Leash,
  C++ and Rust versions, runnable via `scripts/benchmark.py`.
- **Total: 12 cross-language benchmarks** once the four additional tasks
  (`sort_int`, `string_search`, `mem_copy`, `fp_math`) are included,
  plus the allocator-churn task `allocstress`. The harness now reports
  compile time, median run time, **peak RSS** (max over runs) and
  **binary size** per language/level.

### Changed
- **Memory layout optimizations (faster + smaller).**
  - Strings now return byte length in O(1) via the GC cell header
    (`leash_str_bsize`) instead of an O(n) `strlen` scan; the same
    path speeds up `string.size` code-point counting (bounded scan)
    and `string` concatenation lengths. Small strings stay in the
    GC slab with per-size-class reuse and literal deduplication
    (header-cached fat-string + slab SSO).
  - `LeashVec` header shrunk from 40B to 32B (`i64 elem_size/scan` ->
    `i32 elem_size` + `i8 scan`); `size/cap` stay `i64` so old
    and new compilers keep identical field offsets (bootstrap-safe).
    Full 24B packing (i32 size/cap) is staged for a follow-up
    two-stage bootstrap.
    **ABI note:** this is a layout change. Field offsets 0/8/16
    (`data`/`size`/`cap`) are unchanged, but `sizeof` is 32 not 40 and
    `elem_size`/`scan` are no longer `i64` — C/FFI code that embeds
    `LeashVec` by value, reads `elem_size` as `i64`, or mixes objects
    across runtime versions must rebuild against the 1.7.0 `gc.c`.
    `LeashVec` is otherwise opaque (`gc.h` does not export the
    struct; vector access goes through `leash_sh_vec_*`), so normal
    Leash + `@from` code that only holds `i8*` handles is unaffected.
  - The standard library (`installthis/`) now uses canonical
    `.bsize`; the compiler and docs reference both names.
- **Minimum peak RSS (lean runtime).** Measured peak resident memory
  (macOS/Linux `ru_maxrss` via GNU `time -v`, 5-run max) across the 12
  tasks vs C, C++ and Rust is now near `2× live + 256 KB` by design:
  - GC auto-collect threshold `max(2 MB, 2× live)` → `live + 256 KB`
    (`INITIAL_THRESHOLD`), slabs 64 KB → 16 KB (one live cell pins at
    most 16 KB), and the RSS probe uses a separate counter so it never
    resets GC accounting. On `allocstress` peak RSS drops from
    **57.9 MB to 2.6 MB**; `mem_copy` (1M ints) drops from 14.0 MB to
    **9.6 MB** (now leaner than C++ 10.9 MB and Rust 17.1 MB);
    `hash_lookup` (20k) from 5.7 MB to **4.8 MB** (beats C++ 4.9 MB);
    `primes` (1M) from 10.0 MB to **3.6 MB** (ties C++).
  - Hash tables start at 64 buckets (was 8192) and grow at 75% load;
    strings use exact concat caps and `2×` (small) / `+25%` (large)
    append growth (was `+50%`/`2×`); vectors grow `2×` (small) /
    `+50%` (large) (was `2×` everywhere). Large probes: 10M-int vec
    130 MB → **77 MB** (beats Rust 80 MB), 200k-entry hash
    45 MB → **28 MB**, 7 MB string 17.9 MB → **16.4 MB**.
  - Leash beats C++ on 7 of 9 heap tasks (ties `vec_stress` and
    `primes` after the `reserve()` fairness fix below) and wins
    `mem_copy` outright;
    Rust stays leanest on most tasks but ships 4.3 MB binaries vs
    ~22 KB for Leash/C/C++. See `benchmarking/README.md` (methods,
    per-task fairness table, O1-vs-O2 investigation) and
    `benchmarking/results_memory.json` (regenerable, git-ignored).
- **O4 is `-O1` + LTO/native, not an O3 pipeline.** The driver now
  emits `-O1 -march=native -mtune=native -flto (-inline-threshold=125)`
  for native targets; `-O2/-O3` were slower than `-O1` for Leash
  (fib 255 ms at O1 vs 336 ms at O2). Docs and README updated to match.
  **Investigation (see `benchmarking/README.md`):** Leash IR has no
  `nuw`/`nsw`/`fast` (LLVM adds `nsw` itself); the fib gap reproduces
  in pure C (`gcc` O1 458→O2 160 ms, `clang` O1 245→O2 285 ms) and in
  Rust (also LLVM), so it is a Clang-vs-GCC optimizer property that
  Leash cannot fix by IR tweaks (`nounwind` neutral, `-fno-inline`
  recovers ⅓). O2 *wins* elsewhere (`hash_lookup` -19%).
- Version bumped to 1.7.0; the cross-target bootstrap seeds are now
  `bootstrap/leash-1.7.0-target-*` (`x86_64`, `x86`, `arm`, `win64`,
  `java`).

### Fixed
- **Benchmark RSS was measuring Python, not the benchmark.** `os.wait4`
  reports the forked child's high-water mark, which includes the
  ~13 MB interpreter image before `exec`; every task read ~14.5 MB.
  `scripts/benchmark.py` now measures peak RSS with GNU `time -v`
  (tiny C parent) and records binary sizes. Old `results_*.json` RSS
  columns are invalid; run times stand.
- **`--autofree` / `--no-gc` (`NO_GC` stub) duplicated
  `leash_sh_input` and freed interior pointers.** The stub defined it
  twice (stub + shared region), a hard redefinition error; and stub
  strings gained the 8-byte GC prefix while autofree still `free()`d
  the data pointer (interior free → segfault in `tostring`/string
  loops). The stub now returns plain `malloc` bases with no prefix and
  every shared prefix store goes through `LEASH_STR_SET_PREFIX`
  (no-op in stub mode). `pytest tests/` 342 passed.
- `benchmarking/tasks/primes.lsh` used `vec<int>` (4B/bool, 10 MB)
  where C/Rust use 1B/bool; switched to `vec<char>` (3.6 MB, same
  output 78498).
- **Benchmark fairness: `vec_stress` C++/Rust now reserve once.**
  `benchmarking/tasks/vec_stress.cpp` used a naive `push_back` loop
  (geometric growth to 262k, 4.2 MB) while `mem_copy`/`sort_int` used
  `reserve()`; it now uses `v.reserve(200000)` and the Rust twin
  `Vec::with_capacity(200000)`, matching the other vector tasks.
  C++ peak RSS drops 4.2 → 3.91 MB, tying Leash (3.95 MB) instead
  of losing; the earlier “beats C++ 8 of 9” headline is corrected to
  “7 of 9, plus 2 ties”. All `.lsh`/`.cpp`/`.rs` sources were already
  checked in (auditable); `benchmarking/README.md` now documents
  methods, toolchain, growth policies and the header analysis
  (`LeashVec` 32B vs `std::vector` 24B is noise on 100 KB+ payloads).

## [1.6.2] - 2026-11-03

### Fixed
- **`string.size` now counts Unicode code points, not UTF-8 bytes.**
  `"é".size` is `1`, `"€".size` is `1`, `"😀".size` is `1`, and
  `"héllo wörld".size` is `11`. Leash strings are stored as UTF-8 byte
  sequences, so `.size` used to return the encoded byte length (2, 3, 4
  and 13 for those examples): fine for byte-wise algorithms, wrong for
  every user-visible length. Indexing (`s[i]`) and
  `foreach ... in<string>` still walk bytes, exactly as before.
- A new member, `.__bytesize`, returns the UTF-8 byte length. It is the
  internal byte-oriented counterpart of `.size`: the compiler, the
  native runtime and the bundled standard library use it wherever a byte
  count or offset is required (lexing, slicing, hashing, files, buffers,
  scanners), so their behaviour is unchanged. `.size` is now the
  code-point count on every target; the JVM backend implements the same
  rule with a generated `__utf8_size` helper over the byte-per-char
  strings it emits.
- `string.size()` (the method form) now returns the same `int` as the
  property form. It previously emitted an `i64` where the backend
  expected `i32` and failed at the LLVM stage.
- The standard library keeps its old behaviour wherever it manipulates
  bytes, so `installthis/` now calls `.__bytesize` in ~300 places
  (lexing, slicing, hashing, files, buffers, scanners). Three helpers
  deliberately measure *content* rather than bytes and keep `.size`, so
  they now count code points: `utils::pad::Pad` (column padding),
  `misc::table::Table` (column widths), and `text::count::Count.chars`.
- Version bumped to 1.6.2; the cross-target bootstrap seeds are now
  `bootstrap/leash-1.6.2-target-*` (`x86_64`, `x86`, `arm`, `win64`,
  `java`).

### Documentation
- The language guide documents the new `.size` semantics and
  `.__bytesize`, and the stdlib docs note that `Utf.length` matches
  `.size` for well-formed UTF-8.

## [1.6.1] - 2026-10-21

### Added
- **Shared variables.** A function can declare shared variables in its
  signature, after the return type and before the body:
  `fnc add(a int, b int) : void | result: int { ... }`. A shared
  variable is one piece of program-wide storage with a declared type,
  and only the functions that name it in their signature may read or
  write it — two functions declaring the same name share the same
  storage, which is how a helper hands a result back without threading
  it through a return value. Several shared variables are separated by
  commas (`| x: int, y: string`); they start zero-initialized (a
  `string` starts as `""`, a `vec`/`hash` as an empty container), and
  they work identically on every target: the native backends emit them
  as program globals (with their own mangled slot, so they can never
  collide with a global of the same name) and the JVM backend as static
  fields of the main class. Pointer-bearing shared variables are
  registered as GC scan regions like any other global. Touching a
  shared variable from a function that does not declare it, declaring
  the same name with two different types, reusing the name of a program
  global, an unknown type, or an `imut` shared variable are compile
  errors. New example: `examples/shared_vars.lsh` (also part of the
  output-parity corpus).
  Function shared variables are *not* the concurrency globals: a
  top-level `shared` global is module-wide and enforces a one-writer
  rule for worker threads, a `fusion` global is atomic, while a
  function shared variable is scoped to the functions that declare it
  and carries no thread-safety rules. The language guide and the
  concurrency page now contrast the two.

### Changed
- Version bumped to 1.6.1; the cross-target bootstrap seeds are now
  `bootstrap/leash-1.6.1-target-*` (`x86_64`, `x86`, `arm`, `win64`,
  `java`).
- The output-parity corpus grew to 89 programs (the new shared-variables
  example joined it).
- **The VS Code extension version now tracks the Leash version** (1.6.1).
  It had drifted: `package.json` said 1.0.0, `package-lock.json` said
  0.23.9, and the extension README pointed at `leash-0.23.9.vsix`. All
  three now carry 1.6.1, and a new test (`tests/test_version_sync.py`)
  fails the build if any version string in the repository drifts from
  the compiler's.
- The package manager's messages are prefixed `[leash pkg]` instead of
  `[leashed]`, matching the command users actually type.

### Removed
- The `runp` command. It had been a deprecated alias for `run` without
  a file since project mode arrived; it is now gone. `leash runp` prints
  an error pointing at `leash run`, and the `--help` note says the
  command has been removed rather than deprecated.

### Documentation
- The docs were audited for inconsistencies and corrected: the CLI
  reference was missing `leash pkg`, `leash self-host`, `leash version`
  and `leash help`; the bootstrap pipeline still credited Python with
  building stage 1 (a checked-in seed does that now); the index and the
  self-hosted page repeated the retired-Python story; the README called
  the `httpserver` HTTP/1.1-only while the server also speaks HTTP/2;
  the README's prerequisites table omitted Git; the corpus' provenance
  is now stated honestly (entries added after the Python reference was
  retired come from the current compiler); the `registry/README.md`
  still instructed users to run the removed `leashed` command; and the
  language guide now cross-links global variables, shared variables and
  the concurrency globals in both directions.

## [1.6.0] - 2026-10-18

### Removed
- The last Python in the repository. The package-manager reference
  `leash/leashed.py` (and `leash/__init__.py` / `leash/native/`) is gone,
  so `leash/` now contains only the compiler's C runtime (`gc.c`, `gc.h`,
  `cross_compile_stubs.c`, `windows_stubs.c`). The `leash pkg` port in
  `compiler/leashed.lsh` is the sole implementation.
- `tests/test_leashed.py` (which unit-tested the Python reference) is
  replaced by `tests/test_registry_validator.py` (registry CI checks) and
  golden-value assertions in `tests/test_pkg.py` captured from the
  reference before deletion.

### Added
- **`serve_tls` now works on Windows.** The httpserver shim gained a
  system-SChannel TLS backend (`secur32` + `crypt32`, no external
  dependency): PEM certificate loading plus RSA key import (PKCS#8
  `BEGIN PRIVATE KEY` or PKCS#1 `BEGIN RSA PRIVATE KEY`) through a
  per-process key container, a non-blocking handshake driven by the same
  poll slice, ALPN advertisement, and record encrypt/decrypt wired into
  the existing HTTP/1.1 engine. The Windows listener speaks HTTP/1.1
  over TLS and is marked **experimental**: it is cross-compiled with
  MinGW-w64 and exercised end-to-end under Wine, but has not yet been
  battle-tested on real Windows hosts. The win64 link line now pulls
  `ws2_32 secur32 crypt32 ncrypt`.
- **`http`, `httpserver` and `sql` now work on the JVM target** through
  bundled Java shims (`leash/jvm/LeashHttp.java`, `LeashHttpServer.java`,
  `LshHttpHandler.java`, `LeashSql.java`; source shipped, prebuilt
  `.class` files committed like the `liblsh*.a` archives). The compiler
  lowers the `@from` calls into the shims and copies their classes next
  to the output. The client rides `java.net.http` with identical API,
  error codes, redirect rules and `strerror` text (h2 over TLS via ALPN;
  h2c unavailable — cleartext `*_h2` reports `http/1.1`). The server
  rides `jdk.httpserver` with the same handler contract, `reply_*`
  helpers, `shutdown()` semantics and `LSHD_E_*` codes, speaking
  HTTP/1.1 only. Both require a Java 11+ runtime; `sql` has no such
  floor, and other JVM programs keep the Java 5 class-file floor.
- **JVM `sql` is a small SQL interpreter, not SQLite.** The `lsql_*`
  contract is faithful — sqlite3 result codes (OK/ROW/DONE), column
  type numbering, prepare-time column metadata, `reset()` keeping
  bindings, `changes()`/`last_insert_rowid()`, `INTEGER PRIMARY KEY`
  rowid aliasing, transactions, and SQLite's type-affinity rules for
  storage and comparisons — over the core surface (CREATE TABLE /
  INSERT / SELECT with WHERE/ORDER BY/COUNT(*) / UPDATE / DELETE /
  DROP TABLE / transactions). The stdlib sql test program produces
  byte-identical output on the JVM and natively. Database files use the
  engine's own `LEASHSQL1` format, not sqlite3 files (each engine
  cleanly rejects the other's files); joins, subqueries, indexes and
  GROUP BY are not available on the JVM engine.
- **First-class function values on the JVM target**: `fnc(...)` types
  lower to single-method interfaces (the handler shape to the shim's
  `LshHttpHandler`, other signatures to generated `<Main>_LshFn<n>`
  classes, namespaced by the program's main class),
  `&fname` lowers to a generated adapter class, and indirect calls
  through locals and parameters invoke them — previously any function
  value failed to compile on the JVM.
- The JVM build no longer warns about `@from` native libraries: the JVM
  target links no archive, so the transitive native-library security
  warning was noise (path/symlink checks still run).
- A **Known Limitations** page (`docs/limitations.html`, linked from the
  docs sidebar and the README) that states the platform's deliberate
  limits with a severity rating: the Windows `serve_tls` listener being
  experimental, the JVM `sql` engine being a small interpreter with its
  own file format rather than SQLite, and the absence of HTTP/3 / QUIC.

### Changed
- Version bumped to 1.6.0; the cross-target bootstrap seeds are now
  `bootstrap/leash-1.6.0-target-*` (`x86_64`, `x86`, `arm`, `win64`,
  `java`).
- `installthis/httpserver/src/build.sh` learned `TARGET_DIR=win` (MinGW-w64
  cross-compile), so the Windows `liblshhttpd.a` can be rebuilt from Linux
  as well as on Windows.

### Fixed
- **`leash install` now installs a working toolchain, not just binaries.**
  The global install also copies the runtime (`leash/gc.c`, `gc.h`, the
  cross-compile/Windows stubs and the bundled JVM shim classes) to
  `~/.leash/leash` and the bundled standard library to `~/.leash/libs` —
  the exact layout the compiler already searches relative to its own
  executable — so `leash run` works from any directory afterwards.
  Previously an installed `leash`/`leashc` died with "cannot find the
  Leash runtime (leash/gc.c)" everywhere outside a repository checkout.
- **Executable lookup for a bare `argv[0]` no longer leans on the shell's
  `command -v`.** The compiler scans `PATH` itself and expands a leading
  `~` via `HOME`. Shells like dash do not expand `~` in PATH entries, so
  with `export PATH="~/.leash/bin:$PATH"` (quotes keep the tilde literal;
  bash still finds the binary) every runtime and JVM-shim lookup inside
  the compiler failed. The "not on your PATH" check applies the same
  expansion, so it no longer nags when the tilde form works.
- **`leash install <dir>` no longer crashes on a bare `argv[0]`.** It
  resolved itself with `realpath leash`, which resolves lexically to
  `<cwd>/leash` even when no such file exists, then handed that phantom
  path to the child shell (`sh: 1: <cwd>/leash: not found`). The child
  install also runs with inherited stdio now, so its output and errors
  are no longer silently drained by the exit-code capture.
- **Re-installing from a checkout refreshes the installed runtime**: the
  installer prefers the sibling runtime of the project it installs from
  (`compiler/../leash`) over the already-installed one, and skips the
  copy when source and destination are the same directory instead of
  failing with "are the same file".

## [1.5.1] - 2026-10-18

### Changed
- The Python reference compiler (`leash/cli.py` and the 12 other
  `leash/*.py` modules) has been removed. The self-hosted compiler is
  now the sole toolchain: it bootstraps itself from the checked-in
  `bootstrap/leash-1.5.1-target-*` seed binaries with no Python
  dependency (`./scripts/bootstrap.sh`, `leash self-host`). `pip
  install .` / `leashp` are no longer provided; use `bin/leashc` (or
  `leash`/`leashc` on PATH) instead.
- The package manager's reference oracle remains in
  `leash/leashed.py` (used by `tests/test_pkg.py` / `tests/test_leashed.py`),
  now driven by the self-hosted compiler instead of the Python toolchain.
- Lexer/parser now match the Python reference's diagnostics byte-for-byte
  (lexer error positions, CRLF line counting, bit-width validation,
  interpolation error surfacing, trailing commas in all list contexts,
  strict escape/number handling), closing the last frontend parity gaps.
- `tests/test_frontend.py` ported to run against `bin/leashc`.

### Removed
- `setup.py`, `pyproject.toml`, `MANIFEST.in` (Python packaging) and the
  `leashp` console entry point.

Minor release: the package manager is now built into the self-hosted
compiler as `leash pkg` — a full port of the Python reference tool
(`leashed` 0.5.0), so create/publish/install/lock workflows run natively
with no Python in the loop. String ordering (`<`, `<=`, `>`, `>=`) is
lexicographic in all three backends (native, JVM, Python reference),
fixing a pointer-order comparison bug. The README gets the new logo and
the VS Code extension ships light/dark file icons for `.lsh` files.

### Added — `leash pkg` (port of leashed 0.5.0)
- Full command set: `init`, `publish`, `install`, `uninstall`, `list`,
  `info`, `update`, `add`, `lock`, `search`, plus `--help`/`--version`
  (`leash pkg --version` → `leash pkg v0.5.0`).
- Same protocols and safety checks as the reference: `leash-pkg.lshc`
  config round-trip, `leash.lock` (v1: version/repo/tag/40-hex rev pins;
  restores abort on moved tags), semver ordering with prereleases,
  registry `index.json` validation, publish gates (publisher identity,
  ownership, strictly increasing versions), fork + PR registration with
  bot auto-merge, `--locked` strict CI restores, and the same env
  overrides (`LEASHED_REGISTRY_REPO`/`LEASHED_REGISTRY_URL`/
  `LEASHED_REGISTRY_GIT`).
- Pure logic lives in `compiler/leashed.lsh` as local functions,
  differential-tested against `leash/leashed.py` in `tests/test_pkg.py`;
  global-command installs reuse `CompilerDriver.compile_file` in-process.

### Fixed
- String `<`, `<=`, `>`, `>=` compared pointers natively and on the JVM
  instead of lexicographic order; all three backends now use
  `strcmp`/`String.compareTo`, and the typechecker accepts ordering
  operators on strings (regressions:
  `test_string_ordering_lexicographic`, `test_string_ordering_parity`).
- Mixed string concatenation (`scalar + string` / `string + scalar`, with
  char/int/uint/float/bool/char[] operands) now infers `string` in the
  self-hosted typechecker, matching the reference. Previously
  `char + string` was typed `char`, so chained concatenations collapsed
  to `int` and unsound assignments could pass silently. The native
  backend also formats float (`%f`) and bool ("true"/"false") concat
  operands instead of emitting invalid IR (regressions:
  `test_mixed_string_concat`, `test_string_concat_not_char`,
  `test_mixed_string_concat_parity`).

### Changed
- README shows the language logo; the VS Code extension wires
  `icons/leash-file-icon-{light,dark}.svg` through
  `contributes.languages[].icon`.
- Docs: package-manager page and getting-started/index now present
  `leash pkg` (the Python `leashed` entry point remains as the
  standalone reference build). Version bumped to 1.5.0 everywhere.

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

[1.12.0]: https://github.com/foksiny/leash/releases/tag/v1.12.0
[1.11.0]: https://github.com/foksiny/leash/releases/tag/v1.11.0
[1.10.2]: https://github.com/foksiny/leash/releases/tag/v1.10.2
[1.10.1]: https://github.com/foksiny/leash/releases/tag/v1.10.1
[1.10.0]: https://github.com/foksiny/leash/releases/tag/v1.10.0
[1.9.1]: https://github.com/foksiny/leash/releases/tag/v1.9.1
[0.24.3]: https://github.com/foksiny/leash/releases/tag/v0.24.3
[0.24.2]: https://github.com/foksiny/leash/releases/tag/v0.24.2
[0.24.1]: https://github.com/foksiny/leash/releases/tag/v0.24.1
[0.24.0]: https://github.com/foksiny/leash/releases/tag/v0.24.0
[0.23.9]: https://github.com/foksiny/leash/releases/tag/v0.23.9
[0.23.8]: https://github.com/foksiny/leash/releases/tag/v0.23.8
[0.23.7]: https://github.com/foksiny/leash/releases/tag/v0.23.7
[0.23.6]: https://github.com/foksiny/leash/releases/tag/v0.23.6
[0.23.5]: https://github.com/foksiny/leash/releases/tag/v0.23.5