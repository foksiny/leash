/**
 * Leash Custom Garbage Collector - Header
 */

#ifndef LEASH_GC_H
#define LEASH_GC_H

#include <stddef.h>
#include <stdbool.h>

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

/* ===== Collection ===== */
void leash_gc_collect(void);

/* ===== Root Management ===== */
void leash_gc_register_root(void* ptr);
void leash_gc_unregister_root(void* ptr);

/* ===== String/Vector Helpers ===== */
void* leash_gc_alloc_string(size_t len);
void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity);

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
