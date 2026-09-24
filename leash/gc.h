/**
 * Leash Custom Garbage Collector - Header
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
void* leash_gc_malloc_ex(size_t size, unsigned int flags);
void* leash_gc_realloc(void* ptr, size_t new_size);
void* leash_gc_aligned_alloc(size_t size, size_t alignment);

/* ===== Collection ===== */
void leash_gc_collect(void);

/* ===== Root Management ===== */
void leash_gc_register_root(void* ptr);
void leash_gc_unregister_root(void* ptr);
/* Allocate a GC object that is rooted before any collection can see it
   (used for runtime objects such as futures). */
void* leash_gc_malloc_rooted(size_t size);

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
void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity);

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
void* leash_tlab_alloc(size_t size);
void leash_gc_bitmap_init(size_t max_objects);
void leash_fast_memcpy(void* restrict dst, const void* restrict src, size_t n);
/* Tell the GC that the program spawned (or is about to spawn) a worker
   thread. Must be called BEFORE creating the thread. Until then the GC
   takes no locks on the allocation fast path. */
void leash_gc_thread_spawned(void);

/* ===== Statistics ===== */
size_t leash_gc_get_allocated(void);
size_t leash_gc_get_object_count(void);
void leash_gc_print_stats(void);

/* ===== Debugging ===== */
void leash_gc_verify(void);

/* ===== Flag Constants ===== */
#define LEASH_GC_FLAG_MARKED   0x01
#define LEASH_GC_FLAG_ATOMIC   0x02
#define LEASH_GC_FLAG_FINALIZE 0x04

#ifdef __cplusplus
}
#endif

#endif /* LEASH_GC_H */
