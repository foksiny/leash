# Benchmarks — cross-language performance & memory

Tasks in `tasks/` (each has `.lsh`, `.cpp`, `.rs`; `fib`/`pi`/`primes`
also have `.c` and `.py`). Run with:

```bash
python3 scripts/benchmark.py --langs leash c cpp rust --runs 5 \
    --json benchmarking/results_memory.json
# arena builds instead of GC:
LEASH_BENCH_ARENA=1 python3 scripts/benchmark.py --langs leash --runs 5
```

Every run and every compile is capped at `LEASH_MEM_CAP_MB` MiB
(default 1024) — exceeding the cap kills the child (systemd cgroup
when it enforces, else a `/proc` VmRSS watchdog) and marks the cell
ERR instead of hanging the suite. The same guard runs the whole
test suite: `python3 scripts/mem_guard.py -- pytest tests/ -q`.

Tables: **compile time** (single shot), **run time** (median of N runs),
**peak RSS** (max over runs, via GNU `time -v`), **binary size**.
O-levels: Leash O0–O4, C/C++/Rust O0–O3. Toolchain on the reference
machine: g++ 13.3 (C/C++), clang 18.1 (Leash LLVM backend), rustc 1.98.
Note the asymmetry: Leash's C backend is **Clang** (it consumes LLVM IR;
GCC cannot), while the C/C++ baselines use **GCC** and Rust uses LLVM.
Compiler choice is part of the comparison — see “O1 vs O2” below.

Sources are checked in for every language so any claim can be audited
directly. JSON results are git-ignored (`*.json`); regenerate them
with the command above. The numbers in `CHANGELOG.md` `[1.7.0]` come
from `results_memory.json` (lean runtime, primes as `vec<char>`).

## GC vs arena (`--arena`, experimental)

`LEASH_BENCH_ARENA=1` makes the harness compile/run Leash with
`--arena` (the scoped bump-arena backend, `-DLEASH_ARENA`). Arena
builds are **correctness-gated**: every task must produce output
identical to GC (`0 errors`, same results) — the arena is not a
benchmark-only mode.

**Measurement methodology (recommended for fair GC-vs-arena comparisons):**
- Run **one mode at a time** and **never compile in parallel** with a
  running harness; clear the shared object cache first
  (`rm -rf ~/.leash/shcache`) because parallel compiles can corrupt it.
- **Interleave order** across repetitions (GC, arena, arena, GC, …) so
  thermal/frequency/load drift does not systematically favor one side.
- Record **wall time** (median/min), **child CPU/user+sys time**, and
  **max peak RSS** via GNU `time -v` (the same method as this harness —
  Python `os.wait4`/`ru_maxrss` is inflated by the interpreter across
  `fork`).
- For allocator-level truth, also record **arena block bytes** with
  `LEASH_ARENA_STATS=1 <exe>` (prints `peak N block bytes ...` to
  stderr). RSS is page-granular and can be noisy on small deltas; block
  bytes and output parity are the stable signals.

**Where arena tends to win (structural):**
- **Allocation/churn-heavy and short-lived-temporary workloads** (e.g.
  `allocstress`, per-iteration vector/string churn, allocation-free
  loops that skip framing) — whole-scope/loop release is O(blocks) with
  no per-object free, no scan, and no lock.
- **Hash- and string-heavy workloads at higher opt levels** — arena-only
  reserves/pre-sizing (O2+) and O3+ compact hash nodes/fused
  `prefix+int` keys reduce temporary-key churn and peak block bytes
  (e.g. measured `hash_lookup` peak block bytes improved from 1,873,960
  at O2 to 1,152,184 at O3; `matrix_mul`/`matrix_transpose` peaks also
  dropped at O3 in arena stats runs).
- **C-ABI / deterministic-lifetime use** — plain bump pointers, no
  stop-the-world pauses, root arena for data that must outlive scopes.

**Where arena can lose or show smaller/noisy gains:**
- **Growth-heavy kernels that retain superseded buffers until scope
  reset** — the bump arena reclaims growth waste only in specific cases
  (dedicated oversize blocks with no saved frame referencing them).
  Examples (GC → arena peak RSS, O2, historical measurements):
  `mem_copy` 9.7 → 10.1 MB (was 14.0 MB before reclaim),
  `vec_stress` 3.9 → 3.4 MB, `allocstress` 2.5 → 1.9 MB, while
  per-iteration string temporaries that outlive loop survivors keep
  `string_concat` around 2.4 → 2.9 MB.
- **Tiny/compute-bound tasks** (`fib`, `pi`, and in some samples
  `matrix_mul`, `sort_int`/`sort_string`) — framing is elided where
  allocation-free, so allocator work is minimal and wall/CPU/max-RSS
  differences can be run-to-run noise on a loaded host; prefer medians
  of many interleaved runs and compare block bytes.
- **Page-granularity effects** — a small live-data advantage may not
  reduce RSS if it does not cross a page/block boundary.

**Safety while benchmarking arena:** reclamation poisoning is **on by
default** (`LEASH_ARENA_POISON=0` disables the memset for peak-speed
runs once lifetimes are validated). Poison and strict frame checks can
change timings slightly; compare like-for-like configurations. See
`docs/advanced.html` for the full safety model and environment knobs.

## Fairness notes (per task)

| Task | Leash | C++ | Rust | Allocation pattern |
|---|---|---|---|---|
| `vec_stress` 200k | `pushb` (no reserve API) | `v.reserve(200000)` | `Vec::with_capacity(200000)` | All pre-sized; `vec<int>` is `i32` (800 KB) vs Rust `i64` (1.6 MB) |
| `mem_copy` 1M | `extendv` (linear copy) | `dst.reserve(src.size())` | `extend_from_slice` | All linear; Rust `i64` doubles data |
| `sort_int` 50k | `pushb` | `v.reserve(n)` | `with_capacity(n)` | All reserved |
| `fp_math` 100k | `pushb` | `vector<double> v(100000)` | `collect()` | All sized |
| `matrix_mul` 128 | `pushb` | `vector<int> a(n*n)` | `vec![0i64; n*n]` | All sized |
| `hash_lookup` 20k | `h.push` | `unordered_map<string,int>` | `HashMap<String,i64>` | Unavoidable per-key allocs |
| `string_concat` 20k | `append` | `std::string` | `String` | All growing, no reserve (intentional) |
| `primes` 1M | `vec<char>` (1 B/bool) | `vector<bool>` (1 bit/bool) | `vec![bool]` (1 B/bool) | Leash matches C `char*`; C++ packs bits |
| `string_reverse` 100k | `append` | `std::string` no reserve | `String` no reserve | Both grow; Leash can `append_char` |
| `sort_string` 5k | `pushb` + string quicksort | `sort` (introsort) | `sort` (pdqsort) | Payload is heap strings; sorters differ |
| `vec_sort_float` 20k | manual quicksort | manual quicksort | manual quicksort | Hand-rolled same pivot logic |
| `matrix_transpose` 256 | `pushb` | sized vectors | `vec!` | Transpose copy |
| `alloc_churn_loop` 100k | quick `vec<int>` + `tostring` per iteration | `vector<int>` + `to_string` | `Vec<i64>` + `format!` | Deliberately churn-y |
| `struct_pack` 5k | manual quicksort, `vec<Node>` | manual quicksort, `vector<Node>` | manual quicksort, `Vec<Node>` | Field offsets exercised under mutation |

`std::vector` is 24 bytes (`start/finish/cap`, libstdc++) and
`LeashVec` is 32 bytes (`data/size/cap` + `i32 elem_size` + `i8 scan`).
The 8-byte difference is noise on 100 KB+ payloads and does **not**
explain the gaps: the differences are growth policy (`+50%`/`2×` vs
doubling), allocator (16 KB slabs + 16-byte cell header + size-class
rounding vs `malloc` exact), and GC retention (slabs unmapped only
when fully empty, freed only at collection). `vec_stress` originally
shipped **without** `reserve()` in the C++ file while `mem_copy`/
`sort_int` used it; it has since been fixed to `v.reserve(n)` /
`with_capacity`, after which Leash (3.95 MB) essentially ties C++
(3.91 MB) instead of beating it.

## O1 vs O2/O3 — investigation (why O4 is `-O1` + LTO)

Leash IR contains **no** `nuw`/`nsw` and **no** `fast`/`nnan`/`nsz`
(all plain `add`/`sub`/`mul`, `fadd`/`fmul`/`fdiv`; verified by
`grep -c`). LLVM *adds* `nsw` itself at O1+. The remaining O2/O3
slowdown is a **Clang vs GCC** property, reproduced without Leash:

```c
int fib_rec(int n){ if(n<=1) return n; return fib_rec(n-1)+fib_rec(n-2); }
// pure C, same source, n=40:
GCC   O1 458 ms -> O2 160 ms  (-65%)
Clang O1 245 ms -> O2 285 ms  (+16%)
```

GCC at O2 turns `fib_rec` into 309 instructions (heavy unroll/pee
LS) and wins big; Clang at O2 emits a tail-recursive loop with an
extra `icmp ult 4` + peel branch and loses. Rust (also LLVM) shows the
same sign as Leash (`fib` O1 217 ms → O2 261 ms, +20%), while GCC
C++ goes the other way (398 → 147 ms, -63%). So O4 = `-O1` + LTO /
`-march=native` is a workaround for **Clang's** fib/matrix behavior,
not a Leash IR defect — GCC cannot consume Leash's LLVM IR, so Leash
cannot use GCC's stronger fib optimizer.

The slowdown is **not universal**. From `results_memory.json`
(Leash, median run time):

| Task | O1 | O2 | O3 | O2 vs O1 |
|---|---|---|---|---|
| `fib` | 261 ms | 289 ms | 287 ms | **+11%** (Clang peel) |
| `matrix_mul` | 4.7 ms | 6.1 ms | 6.4 ms | +30% (noisy, ±1 ms) |
| `hash_lookup` | 16.0 ms | 12.9 ms | 13.7 ms | **-19%** (O2 wins) |
| `fp_math` | 5.3 ms | 5.0 ms | 5.1 ms | -6% (O2 wins) |
| `sort_int` | 6.3 ms | 5.8 ms | 9.8 ms | -8%, O3 +55% |

`-fno-inline-functions` recovers ~1/3 of the fib gap (306 → 294 ms)
but does not close it; `nounwind` on `fib_rec` is neutral (317 vs
314 ms). The extra peel branch (`icmp ult p_n,4` after the tail
call) is the likely cost, and no Leash-side flag removes it.

## Peak RSS methodology

Python `os.wait4` reports the child's `ru_maxrss`, which is floored
at the interpreter's high-water mark (~13 MB) because the child
inherits it across `fork` before `exec`. The harness therefore uses
GNU `time -v` (tiny C parent) for the true child peak. RSS is
flat across O0–O4 for every language; O-level is a run-time, not a
memory, knob. Hello-world baselines: C 1.27 MB / Leash 1.64 MB /
Rust 1.87 MB / C++ (iostream) 3.23 MB; task peaks are dominated by
payload + allocator slack, not by the baseline.
