/**
 * Leash Custom Garbage Collector - Header
 *
 * Default: slab-based, non-moving, conservative mark-and-sweep
 * collector (16-byte cells, page directory, scanned stack + globals,
 * auto-collection gated on quiescence).
 *
 * Optional arena mode (-DLEASH_ARENA, --arena): a scoped bump arena —
 * thread-local blocks bump-allocated, freed wholesale on scope exit.
 * No scanning, no intrusive headers beyond an 8-byte size word, no
 * locks. See `leasharena_*` below and docs/advanced.html.
 */

#ifndef LEASH_GC_H
#define LEASH_GC_H

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ===== Initialization and Shutdown ===== */
void leash_gc_init(void);
void leash_gc_shutdown(void);

/* ===== Allocation ===== */
void* leash_gc_malloc(size_t size);
void* leash_gc_malloc_nz(size_t size);
/* 8-byte-aligned payload for headers/nodes whose fields never need more
 * than pointer alignment. The arena backend drops the 16-byte padding,
 * saving 8 bytes per small object; other backends delegate to malloc. */
void* leash_gc_malloc_compact(size_t size);
void* leash_gc_malloc_ex(size_t size, unsigned int flags);
void* leash_gc_realloc(void* ptr, size_t new_size);
void* leash_gc_aligned_alloc(size_t size, size_t alignment);
/* GC-tracked, aligned allocation with flags (e.g. LEASH_GC_FLAG_ATOMIC
   for pointer-free payloads such as numeric matrix buffers). */
void* leash_gc_aligned_alloc_ex(size_t size, size_t alignment, unsigned int flags);

/* ===== Collection ===== */
void leash_gc_collect(void);

/* ===== Root Management ===== */
void leash_gc_register_root(void* ptr);
void leash_gc_unregister_root(void* ptr);
/* Allocate a GC object that is rooted before any collection can see it
   (used for runtime objects such as futures). */
void* leash_gc_malloc_rooted(size_t size);

/* Register a scan region: every aligned word in [start, start+nbytes) is
   treated as a conservative candidate pointer at collection time. Used by
   generated code to register pointer-bearing globals precisely. */
void leash_gc_register_scan_region(void* start, size_t nbytes);

/* ===== Worker / quiescence accounting =====
 * The spawn stubs call begin() before creating a worker thread and the
 * worker trampoline calls end() when its function returns; the parallel
 * matrix dispatch brackets each operation the same way. Automatic and
 * explicit collection are refused while the counter is non-zero: worker
 * stacks hold unscanned live pointers while they run. */
void leash_gc_worker_begin(void);
void leash_gc_worker_end(void);

/* ===== Futures (native async/await) =====
 * A future is the join handle for an `async fnc` call. new() registers
 * the future as a GC root; complete() is called by the worker with the
 * boxed result; await() blocks until complete and returns the boxed
 * value, leaving the root in place so generated code can unroot it only
 * after it has loaded the result out. is_done() is a non-blocking poll. */
void* leash_future_new(void);
void  leash_future_complete(void* fut, void* value);
void* leash_future_await(void* fut);
int   leash_future_is_done(void* fut);

/* ===== String/Vector/Matrix Helpers ===== */
void* leash_gc_alloc_string(size_t len);
void* leash_gc_alloc_string_cap(size_t len, size_t cap);
void* leasharena_root_alloc_string_cap(size_t len, size_t cap);
void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity);
int64_t leash_str_bsize(const char* s);
void leash_str_share(const char* s);
char* leash_str_append(char* s, const char* piece);
char* leash_str_concat_ex(const char* s1, const char* s2);
char* leash_int_to_str_fast(int64_t v);
char* leash_uint_to_str_fast(uint64_t v);
char* leash_str_concat_int(const char* s, int64_t v, int is_uint);
char* leash_str_append_int(char* s, int64_t v, int is_uint);
char* leash_str_append_char(char* s, char c);
char* leash_sh_input(const char* prompt);
/* Arena: reclaim a superseded single-allocation block (growth waste).
 * Defined by the arena backend; other backends keep a stub that does
 * nothing (growth abandonment is reclaimed by their collectors). */
void leasharena_try_free_solo(void* ptr, size_t old_size_bytes);

/* ===== Optimized Matrix Binary Operations ===== */
void leash_matrix_binary_op_float(
    float* res, const float* a, const float* b, int64_t n, int op);
void leash_matrix_binary_op_double(
    double* res, const double* a, const double* b, int64_t n, int op);
void leash_matrix_binary_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op);
void leash_matrix_binary_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op);

/* ===== Parallel Matrix Operations (auto-splits across threads) ===== */
void leash_matrix_parallel_op_float(
    float* res, const float* a, const float* b, int64_t n, int op);
void leash_matrix_parallel_op_double(
    double* res, const double* a, const double* b, int64_t n, int op);
void leash_matrix_parallel_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op);
void leash_matrix_parallel_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op);

/* ===== Vector Batch Operations ===== */
void leash_vec_batch_pushb(void* vec_ptr, const void* elements, int64_t count, int64_t elem_size,
                           void* (*resize_fn)(void*, int64_t));
void leash_vec_bulk_copy(void* restrict dst, const void* restrict src, int64_t n, int64_t elem_size);
void leash_vec_reverse(void* data, int64_t size, int64_t elem_size);
void leash_vec_sort_i32(int32_t* data, int64_t size);
void leash_vec_sort_i64(int64_t* data, int64_t size);
void leash_vec_sort_f32(float* data, int64_t size);
void leash_vec_sort_f64(double* data, int64_t size);

/* ===== Cache-Blocked Matrix Ops ===== */
void leash_matrix_blocked_op_float(
    float* res, const float* a, const float* b, int64_t n, int op);
void leash_matrix_blocked_op_double(
    double* res, const double* a, const double* b, int64_t n, int op);

/* ===== GC Extensions ===== */
void leash_fast_memcpy(void* restrict dst, const void* restrict src, size_t n);
/* Tell the GC that the program spawned (or is about to spawn) a worker
   thread. Must be called BEFORE creating the thread. Until then the GC
   takes no locks on the allocation fast path. */
void leash_gc_thread_spawned(void);
void leash_gc_thread_attach(void);

/* ===== Statistics ===== */
size_t leash_gc_get_allocated(void);
size_t leash_gc_get_object_count(void);
void leash_gc_print_stats(void);

/* ===== Debugging ===== */
void leash_gc_verify(void);

/* ===== Scoped arena (experimental, -DLEASH_ARENA / --arena) =====
 * Thread-local bump allocator. Generated code pushes a frame on
 * function entry (`leasharena_enter`) and pops it on exit
 * (`leasharena_exit`), freeing everything since the checkpoint at
 * once. There is no per-object free, no scanning and no lock.
 * Pointers are plain C pointers valid until the frame that allocated
 * them exits; code that must outlive that frame escapes via the
 * `leasharena_escape_*` helpers (copy into the caller's frame) or
 * `leasharena_root_alloc` (program-lifetime arena, for C-ABI / FFI
 * data that must stay valid across calls). */
void leasharena_enter(void);
void leasharena_exit(void);
/* Slow paths for inline fast paths in generated code (stack
 * growth / block release). The fast paths handle the common
 * no-new-blocks case entirely in IR. */
void leasharena_enter_slow(void);
void leasharena_exit_slow(void);
void leasharena_exit_n(int64_t nframes);
/* Copy a heap value about to be returned out of nframes scopes into
 * the caller's frame. slot points at the generated-code holding the
 * pointer (updated in place); nframes = loop frames + 1. */
void leasharena_escape_string(void** slot, int64_t nframes);
void leasharena_escape_vec(void** slot, int64_t nframes);
void leasharena_escape_hash(void** slot, int64_t nframes);
typedef struct { int64_t offset; int32_t kind; int32_t _pad; } ar_field_desc;
/* Deep-escape a vec whose elements are pointers to structs containing
 * heap fields (string=1, vec=2, hash=3 at given offsets). pointee_size
 * is the struct's byte size. Stages all elements+fields, pops nframes,
 * commits into the new frame, and rewrites element pointers. */
void leasharena_escape_vec_typed(void** slot, int64_t nframes, int64_t pointee_size, int32_t nfields, const ar_field_desc* fields);
void leasharena_escape_blob(void** slot, int64_t size, int64_t nframes);
void leasharena_escape_slice(void* agg, int64_t elem_size, int64_t nframes);
/* Tuple returns (2+ heap components) cannot use the single-call
 * escape helpers: the first helper pops the scopes, which would free
 * the remaining components before they are staged. Tuple code
 * stages every component to malloc first, pops once, then commits. */
void* leasharena_stage_string(void* s);
void leasharena_commit_string(void** slot, void* stage);
void* leasharena_stage_vec(void* vec);
void leasharena_commit_vec(void** slot, void* stage);
void* leasharena_stage_hash(void* hash);
void leasharena_commit_hash(void** slot, void* stage);
void* leasharena_stage_blob(void* ptr, int64_t size);
void leasharena_commit_blob(void** slot, void* stage, int64_t size);
/* Root copies for spawn args: same shapes, allocated in the
 * program-lifetime arena instead of the caller's frame. No pop. */
void leasharena_root_copy_string(void** slot);
void leasharena_root_copy_vec(void** slot);
void leasharena_root_copy_hash(void** slot);
void leasharena_root_copy_blob(void** slot, int64_t size);
void leasharena_root_copy_slice(void* agg, int64_t elem_size);
/* Loop checkpoints for the per-iteration reset path. enter returns
 * the frame index (stable across balanced inner functions); reset
 * frees temporaries since entry and keeps the frame for the next
 * iteration; exit pops it. */
int64_t leasharena_loop_enter(void);
void leasharena_loop_reset(int64_t frame);
void leasharena_loop_reset_slow(int64_t frame);
void leasharena_loop_exit(int64_t frame);
/* Program-lifetime allocation: never freed by scope exits. Use for
 * C-ABI / FFI data, futures, and globals that must stay valid. */
void* leasharena_root_alloc(size_t size);
void* leasharena_root_alloc_aligned(size_t size, size_t alignment);

/* ===== Flag Constants ===== */
#define LEASH_GC_FLAG_MARKED 0x01
#define LEASH_GC_FLAG_ATOMIC 0x02

#ifdef __cplusplus
}
#endif

#endif /* LEASH_GC_H */
