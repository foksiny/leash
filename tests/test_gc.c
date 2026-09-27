/* Standalone harness for the Leash garbage collector (leash/gc.c).
 *
 * Exercises the allocator, the mark/sweep cycle, root and scan-region
 * registration, conservative stack retention, realloc, aligned allocation,
 * the matrix thread pool and futures — without going through codegen.
 *
 * Build & run:  python3 tests/test_gc.py
 * (or) gcc tests/test_gc.c leash/gc.c -O1 -Wall -Wextra -o test_gc -pthread
 */

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../leash/gc.h"

/* Scan-region target: a global holding a heap pointer. Not on the stack, so
   it survives collection ONLY because it was registered. */
static void* g_slot;

/* Allocates `target` bytes of garbage with varied size classes (so reuse of
   the sizes under test is likely) and pushes the heap past the auto-collect
   threshold several times. */
static void churn(size_t target) {
    size_t got = 0;
    while (got < target) {
        size_t sz = 32 + (got * 7919) % 3048;
        void* p = leash_gc_malloc(sz);
        if (!p) break;
        got += sz;
    }
}

static void test_basic_alloc(void) {
    char* s = (char*)leash_gc_alloc_string(128);
    assert(s != NULL);
    for (int i = 0; i <= 128; i++) assert(s[i] == 0);
    for (int i = 0; i < 128; i++) s[i] = (char)('a' + i % 26);
    assert(s[128] == '\0');

    void* v = leash_gc_alloc_vector_data(8, 100);
    assert(v != NULL);
    memset(v, 0x5A, 800);
    assert(((unsigned char*)v)[799] == 0x5A);

    /* small + large payload paths */
    void* small = leash_gc_malloc(64);
    void* large = leash_gc_malloc(100000);
    assert(small && large);
    memset(large, 0x11, 100000);
    assert(((unsigned char*)large)[99999] == 0x11);
}

static void test_dead_objects_are_collected(void) {
    churn(8 * 1024 * 1024); /* bring the heap to a known-good state */
    leash_gc_collect();
    size_t baseline = leash_gc_get_object_count();

    for (int i = 0; i < 2000; i++) (void)leash_gc_malloc(96);
    assert(leash_gc_get_object_count() >= baseline + 2000);

    leash_gc_collect();
    size_t after = leash_gc_get_object_count();
    /* Conservative stack scanning can retain a handful of stale slots, but
       the vast majority of the 2000 unrooted objects must be gone. */
    if (after >= baseline + 1900) {
        fprintf(stderr, "FAIL: 2000 garbage objects survived collect "
                        "(baseline=%zu after=%zu)\n", baseline, after);
        abort();
    }
    leash_gc_verify();
}

static void test_root_survival(void) {
    unsigned char* rooted = (unsigned char*)leash_gc_malloc(4096);
    assert(rooted);
    memset(rooted, 0xAB, 4096);
    leash_gc_register_root(rooted);

    churn(8 * 1024 * 1024); /* several automatic collections */
    assert(rooted[4095] == 0xAB);
    assert(rooted[0] == 0xAB);
    for (int i = 0; i < 4096; i++) assert(rooted[i] == 0xAB);

    leash_gc_unregister_root(rooted);
}

static void test_scan_region_survival(void) {
    unsigned char* obj = (unsigned char*)leash_gc_malloc(3000);
    assert(obj);
    memset(obj, 0xCD, 3000);
    g_slot = obj;
    leash_gc_register_scan_region(&g_slot, sizeof(g_slot));

    churn(8 * 1024 * 1024);
    /* Would read freed/reused memory (pattern clobbered) if the region
       registration did not keep the object alive. */
    if (g_slot != obj) {
        fprintf(stderr, "FAIL: scan-region object moved/freed\n");
        abort();
    }
    for (int i = 0; i < 3000; i++) {
        if (obj[i] != 0xCD) {
            fprintf(stderr, "FAIL: scan-region object reused (byte %d)\n", i);
            abort();
        }
    }
    g_slot = NULL;
    leash_gc_collect();
}

static void test_stack_retention(void) {
    unsigned char* local = (unsigned char*)leash_gc_malloc(2048);
    assert(local);
    memset(local, 0x77, 2048);
    churn(8 * 1024 * 1024);
    /* `local` is only reachable from this frame: the conservative stack
       scan (plus setjmp register flush) must keep it alive. */
    for (int i = 0; i < 2048; i++) {
        if (local[i] != 0x77) {
            fprintf(stderr, "FAIL: stack-local object collected (byte %d)\n", i);
            abort();
        }
    }
}

static void test_interior_pointer_retention(void) {
    unsigned char* base = (unsigned char*)leash_gc_malloc(4096);
    assert(base);
    memset(base, 0x33, 4096);
    unsigned char* interior = base + 2000; /* non-head pointer */
    churn(4 * 1024 * 1024);
    if (interior[-2000] != 0x33 || interior[1000] != 0x33) {
        fprintf(stderr, "FAIL: object retained via interior pointer lost\n");
        abort();
    }
}

static void test_auto_collection_bounds_heap(void) {
    /* 32MB of garbage: without automatic collection the object count would
       be in the tens of thousands; with it the heap must stay small. */
    churn(32 * 1024 * 1024);
    size_t count = leash_gc_get_object_count();
    if (count > 8000) {
        fprintf(stderr, "FAIL: heap grew to %zu objects without being bounded "
                        "(auto-collection did not run)\n", count);
        abort();
    }
}

static void test_realloc(void) {
    char* r = (char*)leash_gc_realloc(NULL, 64);
    assert(r);
    for (int i = 0; i < 64; i++) r[i] = (char)i;

    /* grow (must move, preserving contents) */
    r = (char*)leash_gc_realloc(r, 10000);
    assert(r);
    for (int i = 0; i < 64; i++) assert(r[i] == (char)i);

    /* shrink in place */
    char* same = (char*)leash_gc_realloc(r, 32);
    assert(same == r);
    for (int i = 0; i < 32; i++) assert(same[i] == (char)i);

    /* free */
    assert(leash_gc_realloc(same, 0) == NULL);

    /* large object grow/shrink */
    char* l = (char*)leash_gc_realloc(NULL, 20000);
    assert(l);
    memset(l, 0x44, 20000);
    l = (char*)leash_gc_realloc(l, 50000);
    assert(l);
    for (int i = 0; i < 20000; i++) assert(l[i] == 0x44);
    l = (char*)leash_gc_realloc(l, 10000);
    assert(l);
    assert(leash_gc_realloc(l, 0) == NULL);
}

static void test_aligned_alloc(void) {
    void* a16 = leash_gc_aligned_alloc(100, 16);
    void* a64 = leash_gc_aligned_alloc(1000, 64);
    void* big = leash_gc_aligned_alloc(30000, 128);
    void* a64x = leash_gc_aligned_alloc_ex(500, 64, LEASH_GC_FLAG_ATOMIC);
    assert(a16 && a64 && big && a64x);
    assert(((uintptr_t)a16 % 16) == 0);
    assert(((uintptr_t)a64 % 64) == 0);
    assert(((uintptr_t)big % 128) == 0);
    assert(((uintptr_t)a64x % 64) == 0);
    memset(big, 0x22, 30000);
    churn(4 * 1024 * 1024); /* aligned buffers must be collector-owned now */
    assert(((unsigned char*)big)[29999] == 0x22);
}

static void test_matrix_pool(void) {
    const int64_t n = 200000;
    float* a = (float*)leash_gc_aligned_alloc((size_t)n * 4, 64);
    float* b = (float*)leash_gc_aligned_alloc((size_t)n * 4, 64);
    float* r = (float*)leash_gc_aligned_alloc((size_t)n * 4, 64);
    assert(a && b && r);
    for (int64_t i = 0; i < n; i++) { a[i] = (float)i; b[i] = 2.0f; }
    /* op 0 = add; parallel path (n >= 1024, >1 thread) */
    leash_matrix_parallel_op_float(r, a, b, n, 0);
    for (int64_t i = 0; i < n; i++) {
        if (r[i] != (float)i + 2.0f) {
            fprintf(stderr, "FAIL: parallel float add wrong at %lld\n",
                    (long long)i);
            abort();
        }
    }
    /* sequential path (n < 1024) */
    float rs[8];
    float as[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    float bs[8] = {1, 1, 1, 1, 1, 1, 1, 1};
    leash_matrix_parallel_op_float(rs, as, bs, 8, 0);
    for (int i = 0; i < 8; i++) assert(rs[i] == as[i] + 1.0f);
    /* int path with is_int dispatch */
    int32_t ia[16], ib[16], ir[16];
    for (int i = 0; i < 16; i++) { ia[i] = i; ib[i] = 3; }
    leash_matrix_parallel_op_int32(ir, ia, ib, 16, 0);
    for (int i = 0; i < 16; i++) assert(ir[i] == i + 3);
}

static void test_futures(void) {
    void* fut = leash_future_new();
    assert(fut);
    assert(!leash_future_is_done(fut));
    void* val = leash_gc_malloc(16);
    memset(val, 0x99, 16);
    leash_future_complete(fut, val);
    assert(leash_future_is_done(fut));
    void* got = leash_future_await(fut);
    assert(got == val);
    assert(((unsigned char*)got)[15] == 0x99);
    leash_gc_unregister_root(fut);
}

static void test_worker_bracket(void) {
    leash_gc_worker_begin();
    void* p = leash_gc_malloc(64); /* allocation while "workers" active */
    assert(p);
    leash_gc_collect(); /* must be skipped (workers active), not crash */
    leash_gc_worker_end();
    leash_gc_collect(); /* quiescent again */
}

int main(void) {
    leash_gc_init();

    test_basic_alloc();
    test_dead_objects_are_collected();
    test_root_survival();
    test_scan_region_survival();
    test_stack_retention();
    test_interior_pointer_retention();
    test_auto_collection_bounds_heap();
    test_realloc();
    test_aligned_alloc();
    test_matrix_pool();
    test_futures();
    test_worker_bracket();

    leash_gc_print_stats();
    leash_gc_shutdown();
    printf("test_gc: all tests passed\n");
    return 0;
}
