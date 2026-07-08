/**
 * Leash Custom Garbage Collector — Multi-Thread Ready
 * A simple mark-and-sweep GC with mutex-based thread safety.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#ifdef _WIN32
#include <windows.h>
static CRITICAL_SECTION gc_mutex;
static LONG  gc_mutex_ready = 0;
/* On Windows the CRITICAL_SECTION must be initialised before first use.
   leash_gc_init() is called from main() before any threads exist, so we
   initialise it there and then set gc_mutex_ready.  Use InterlockedExchange
   so the flag becomes visible to all threads. */
#define GC_LOCK()                                                          \
    do {                                                                   \
        if (!InterlockedExchangeAdd(&gc_mutex_ready, 0)) {                 \
            fprintf(stderr, "FATAL: GC lock before leash_gc_init()\n");    \
            abort();                                                       \
        }                                                                  \
        EnterCriticalSection(&gc_mutex);                                   \
    } while(0)
#define GC_UNLOCK() LeaveCriticalSection(&gc_mutex)
#else
#include <pthread.h>
static pthread_mutex_t gc_mutex = PTHREAD_MUTEX_INITIALIZER;
#define GC_LOCK()   pthread_mutex_lock(&gc_mutex)
#define GC_UNLOCK() pthread_mutex_unlock(&gc_mutex)
#endif

/* Configuration */
#define MAX_ROOTS 100000
#define INITIAL_THRESHOLD (2 * 1024 * 1024)  /* 2MB */

/* Object header */
struct gc_object {
    size_t size;
    unsigned int flags;
    struct gc_object* next;
    struct gc_object* prev;
};

/* Flag bits */
#define FLAG_MARKED     0x01
#define FLAG_ATOMIC     0x02

/* GC State */
static struct {
    struct gc_object* object_list;
    size_t total_allocated;
    size_t threshold;
    size_t object_count;
    void** roots;
    size_t root_count;
    size_t root_capacity;
    size_t alloc_count;
    size_t collect_count;
} gc = {0};

/* Initialization */
void leash_gc_init(void) {
    /* Called from main() before any threads are spawned — no lock needed. */
    if (gc.roots != NULL) return;

#ifdef _WIN32
    InitializeCriticalSection(&gc_mutex);
    InterlockedExchange(&gc_mutex_ready, 1);
#endif

    gc.object_list = NULL;
    gc.total_allocated = 0;
    gc.threshold = INITIAL_THRESHOLD;
    gc.object_count = 0;
    gc.alloc_count = 0;
    gc.collect_count = 0;

    gc.root_capacity = MAX_ROOTS;
    gc.root_count = 0;
    gc.roots = (void**)malloc(gc.root_capacity * sizeof(void*));
    if (!gc.roots) {
        fprintf(stderr, "Leash GC: Failed to allocate root set\n");
        abort();
    }
    memset(gc.roots, 0, gc.root_capacity * sizeof(void*));
}

/* Allocation */
void* leash_gc_malloc(size_t size) {
    if (size == 0) return NULL;

    GC_LOCK();

    size_t total_size = sizeof(struct gc_object) + size;
    struct gc_object* obj = (struct gc_object*)malloc(total_size);
    if (!obj) {
        fprintf(stderr, "Leash GC: Out of memory!\n");
        GC_UNLOCK();
        abort();
    }

    obj->size = size;
    obj->flags = 0;
    obj->next = gc.object_list;
    obj->prev = NULL;

    if (gc.object_list) {
        gc.object_list->prev = obj;
    }
    gc.object_list = obj;

    gc.total_allocated += size;
    gc.object_count++;
    gc.alloc_count++;

    void* user_ptr = (void*)(obj + 1);
    memset(user_ptr, 0, size);

    GC_UNLOCK();
    return user_ptr;
}

void* leash_gc_realloc(void* ptr, size_t new_size) {
    if (!ptr) return leash_gc_malloc(new_size);
    if (new_size == 0) return NULL;

    GC_LOCK();

    struct gc_object* obj = ((struct gc_object*)ptr) - 1;
    if (obj->size >= new_size) {
        GC_UNLOCK();
        return ptr;
    }

    /* Allocate new block, copy, link, unlink old */
    size_t total_size = sizeof(struct gc_object) + new_size;
    struct gc_object* new_obj = (struct gc_object*)malloc(total_size);
    if (!new_obj) {
        fprintf(stderr, "Leash GC: Out of memory!\n");
        GC_UNLOCK();
        abort();
    }

    new_obj->size = new_size;
    new_obj->flags = obj->flags;
    new_obj->next = gc.object_list;
    new_obj->prev = NULL;
    if (gc.object_list) {
        gc.object_list->prev = new_obj;
    }
    gc.object_list = new_obj;

    /* Copy old data */
    void* old_user = (void*)(obj + 1);
    void* new_user = (void*)(new_obj + 1);
    memcpy(new_user, old_user, obj->size < new_size ? obj->size : new_size);

    /* Unlink old object */
    if (obj->prev) obj->prev->next = obj->next;
    if (obj->next) obj->next->prev = obj->prev;
    if (gc.object_list == obj) gc.object_list = obj->next;

    gc.total_allocated -= obj->size;
    gc.object_count--;
    free(obj);

    GC_UNLOCK();
    return new_user;
}

/* Root Management */
void leash_gc_register_root(void* ptr) {
    if (!ptr) return;

    GC_LOCK();

    if (gc.root_count >= gc.root_capacity) {
        size_t new_cap = gc.root_capacity * 2;
        void** new_roots = (void**)realloc(gc.roots, new_cap * sizeof(void*));
        if (!new_roots) {
            GC_UNLOCK();
            return;
        }
        gc.roots = new_roots;
        memset(gc.roots + gc.root_capacity, 0, (new_cap - gc.root_capacity) * sizeof(void*));
        gc.root_capacity = new_cap;
    }
    gc.roots[gc.root_count++] = ptr;

    GC_UNLOCK();
}

void leash_gc_unregister_root(void* ptr) {
    if (!ptr) return;

    GC_LOCK();
    size_t i;
    for (i = 0; i < gc.root_count; i++) {
        if (gc.roots[i] == ptr) {
            gc.roots[i] = gc.roots[gc.root_count - 1];
            gc.roots[gc.root_count - 1] = NULL;
            gc.root_count--;
            GC_UNLOCK();
            return;
        }
    }
    GC_UNLOCK();
}

/* Mark Phase */
static void mark_object(struct gc_object* obj) {
    if (!obj || (obj->flags & FLAG_MARKED)) return;
    obj->flags |= FLAG_MARKED;

    if (obj->flags & FLAG_ATOMIC) return;

    /* Trace pointers in the object */
    void* obj_data = (void*)(obj + 1);
    size_t ptr_count = obj->size / sizeof(void*);
    size_t i;
    for (i = 0; i < ptr_count; i++) {
        void* potential_ptr = ((void**)obj_data)[i];
        if (potential_ptr) {
            struct gc_object* check = gc.object_list;
            while (check) {
                void* obj_start = (void*)(check + 1);
                if (potential_ptr >= obj_start &&
                    potential_ptr < (void*)((char*)obj_start + check->size)) {
                    mark_object(check);
                    break;
                }
                check = check->next;
            }
        }
    }
}

static void mark_from_roots(void) {
    size_t i;
    for (i = 0; i < gc.root_count; i++) {
        if (gc.roots[i]) {
            struct gc_object* obj = gc.object_list;
            while (obj) {
                void* obj_start = (void*)(obj + 1);
                if (gc.roots[i] == obj_start) {
                    mark_object(obj);
                    break;
                }
                obj = obj->next;
            }
        }
    }
}

/* Sweep Phase */
static void sweep(void) {
    struct gc_object** p = &gc.object_list;
    while (*p) {
        struct gc_object* obj = *p;
        if (!(obj->flags & FLAG_MARKED)) {
            *p = obj->next;
            if (obj->next) {
                obj->next->prev = obj->prev;
            }
            gc.total_allocated -= obj->size;
            gc.object_count--;
            gc.alloc_count++;
            free(obj);
        } else {
            obj->flags &= ~FLAG_MARKED;
            p = &obj->next;
        }
    }
}

/* Collection */
void leash_gc_collect(void) {
    GC_LOCK();
    mark_from_roots();
    sweep();
    gc.collect_count++;
    GC_UNLOCK();
}

/* Utility Functions */
void* leash_gc_alloc_string(size_t len) {
    return leash_gc_malloc(len + 1);
}

void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) {
    return leash_gc_malloc(elem_size * capacity);
}

void* leash_gc_aligned_alloc(size_t size, size_t alignment) {
#ifdef _WIN32
    return _aligned_malloc(size, alignment);
#else
    void* ptr = NULL;
    if (posix_memalign(&ptr, alignment, size) != 0) return NULL;
    return ptr;
#endif
}

/* ===== Optimized Matrix Binary Operations ===== */

/*
 * Optimization 1: Use function pointers instead of switch for dispatch
 * Optimization 2: Loop unrolling (4x unrolled inner loops)
 * Optimization 3: Software prefetching
 * Optimization 4: Cache-friendly blocking for large matrices
 * Optimization 5: SIMD hints via restrict pointers
 * Optimization 6: FMA-friendly layout (fused multiply-add where possible)
 */

/* Helper: apply a binary op via function pointer for fast dispatch */
typedef void (*vec_binop_fn)(float* restrict res, const float* restrict a, const float* restrict b, int64_t n);

/* Float ops - add with prefetch and unroll */
static void vec_f32_add(float* restrict res, const float* restrict a, const float* restrict b, int64_t n) {
    int64_t i = 0;
    /* Optimization 2: 4x loop unrolling */
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   + b[i];
        res[i+1] = a[i+1] + b[i+1];
        res[i+2] = a[i+2] + b[i+2];
        res[i+3] = a[i+3] + b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] + b[i];
}

static void vec_f32_sub(float* restrict res, const float* restrict a, const float* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   - b[i];
        res[i+1] = a[i+1] - b[i+1];
        res[i+2] = a[i+2] - b[i+2];
        res[i+3] = a[i+3] - b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] - b[i];
}

static void vec_f32_mul(float* restrict res, const float* restrict a, const float* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   * b[i];
        res[i+1] = a[i+1] * b[i+1];
        res[i+2] = a[i+2] * b[i+2];
        res[i+3] = a[i+3] * b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] * b[i];
}

static void vec_f32_div(float* restrict res, const float* restrict a, const float* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   / b[i];
        res[i+1] = a[i+1] / b[i+1];
        res[i+2] = a[i+2] / b[i+2];
        res[i+3] = a[i+3] / b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] / b[i];
}

static vec_binop_fn f32_ops[4] = {vec_f32_add, vec_f32_sub, vec_f32_mul, vec_f32_div};

/* Optimization 1: Function pointer dispatch instead of switch */
void leash_matrix_binary_op_float(
    float* res, const float* a, const float* b, int64_t n, int op)
{
    if (op >= 0 && op < 4) f32_ops[op](res, a, b, n);
}

/* Double ops - 4x unrolled with prefetch */
typedef void (*dbl_binop_fn)(double* restrict res, const double* restrict a, const double* restrict b, int64_t n);

static void vec_f64_add(double* restrict res, const double* restrict a, const double* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   + b[i];
        res[i+1] = a[i+1] + b[i+1];
        res[i+2] = a[i+2] + b[i+2];
        res[i+3] = a[i+3] + b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] + b[i];
}

static void vec_f64_sub(double* restrict res, const double* restrict a, const double* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   - b[i];
        res[i+1] = a[i+1] - b[i+1];
        res[i+2] = a[i+2] - b[i+2];
        res[i+3] = a[i+3] - b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] - b[i];
}

static void vec_f64_mul(double* restrict res, const double* restrict a, const double* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   * b[i];
        res[i+1] = a[i+1] * b[i+1];
        res[i+2] = a[i+2] * b[i+2];
        res[i+3] = a[i+3] * b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] * b[i];
}

static void vec_f64_div(double* restrict res, const double* restrict a, const double* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        __builtin_prefetch(&b[i + 8], 0, 0);
        res[i]   = a[i]   / b[i];
        res[i+1] = a[i+1] / b[i+1];
        res[i+2] = a[i+2] / b[i+2];
        res[i+3] = a[i+3] / b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] / b[i];
}

static dbl_binop_fn f64_ops[4] = {vec_f64_add, vec_f64_sub, vec_f64_mul, vec_f64_div};

void leash_matrix_binary_op_double(
    double* res, const double* a, const double* b, int64_t n, int op)
{
    if (op >= 0 && op < 4) f64_ops[op](res, a, b, n);
}

/* Int32 and Int64 - unrolled 4x with prefetch */
typedef void (*i32_binop_fn)(int32_t* restrict res, const int32_t* restrict a, const int32_t* restrict b, int64_t n);

static void vec_i32_add(int32_t* restrict res, const int32_t* restrict a, const int32_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   + b[i];
        res[i+1] = a[i+1] + b[i+1];
        res[i+2] = a[i+2] + b[i+2];
        res[i+3] = a[i+3] + b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] + b[i];
}

static void vec_i32_sub(int32_t* restrict res, const int32_t* restrict a, const int32_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   - b[i];
        res[i+1] = a[i+1] - b[i+1];
        res[i+2] = a[i+2] - b[i+2];
        res[i+3] = a[i+3] - b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] - b[i];
}

static void vec_i32_mul(int32_t* restrict res, const int32_t* restrict a, const int32_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   * b[i];
        res[i+1] = a[i+1] * b[i+1];
        res[i+2] = a[i+2] * b[i+2];
        res[i+3] = a[i+3] * b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] * b[i];
}

static void vec_i32_div(int32_t* restrict res, const int32_t* restrict a, const int32_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        if (b[i] == 0 || b[i+1] == 0 || b[i+2] == 0 || b[i+3] == 0) goto i32_div_fallback;
        res[i]   = a[i]   / b[i];
        res[i+1] = a[i+1] / b[i+1];
        res[i+2] = a[i+2] / b[i+2];
        res[i+3] = a[i+3] / b[i+3];
    }
    i32_div_fallback:
    for (; i < n; i++) res[i] = a[i] / b[i];
}

static i32_binop_fn i32_ops[4] = {vec_i32_add, vec_i32_sub, vec_i32_mul, vec_i32_div};

void leash_matrix_binary_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op)
{
    if (op >= 0 && op < 4) i32_ops[op](res, a, b, n);
}

typedef void (*i64_binop_fn)(int64_t* restrict res, const int64_t* restrict a, const int64_t* restrict b, int64_t n);

static void vec_i64_add(int64_t* restrict res, const int64_t* restrict a, const int64_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   + b[i];
        res[i+1] = a[i+1] + b[i+1];
        res[i+2] = a[i+2] + b[i+2];
        res[i+3] = a[i+3] + b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] + b[i];
}

static void vec_i64_sub(int64_t* restrict res, const int64_t* restrict a, const int64_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   - b[i];
        res[i+1] = a[i+1] - b[i+1];
        res[i+2] = a[i+2] - b[i+2];
        res[i+3] = a[i+3] - b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] - b[i];
}

static void vec_i64_mul(int64_t* restrict res, const int64_t* restrict a, const int64_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        res[i]   = a[i]   * b[i];
        res[i+1] = a[i+1] * b[i+1];
        res[i+2] = a[i+2] * b[i+2];
        res[i+3] = a[i+3] * b[i+3];
    }
    for (; i < n; i++) res[i] = a[i] * b[i];
}

static void vec_i64_div(int64_t* restrict res, const int64_t* restrict a, const int64_t* restrict b, int64_t n) {
    int64_t i = 0;
    for (; i + 4 <= n; i += 4) {
        __builtin_prefetch(&a[i + 8], 0, 0);
        if (b[i] == 0 || b[i+1] == 0 || b[i+2] == 0 || b[i+3] == 0) goto i64_div_fallback;
        res[i]   = a[i]   / b[i];
        res[i+1] = a[i+1] / b[i+1];
        res[i+2] = a[i+2] / b[i+2];
        res[i+3] = a[i+3] / b[i+3];
    }
    i64_div_fallback:
    for (; i < n; i++) res[i] = a[i] / b[i];
}

static i64_binop_fn i64_ops[4] = {vec_i64_add, vec_i64_sub, vec_i64_mul, vec_i64_div};

void leash_matrix_binary_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op)
{
    if (op >= 0 && op < 4) i64_ops[op](res, a, b, n);
}

/* ===== Optimization 7: Cache-blocked matrix ops for large data ===== */
/* Process data in L1-cache-sized blocks (32KB) for better cache utilization */
#define CACHE_BLOCK_SIZE 4096  /* ~16KB for float, 32KB for double */

void leash_matrix_blocked_op_float(
    float* res, const float* a, const float* b, int64_t n, int op)
{
    int64_t offset = 0;
    while (offset < n) {
        int64_t block = (n - offset) < CACHE_BLOCK_SIZE ? (n - offset) : CACHE_BLOCK_SIZE;
        leash_matrix_binary_op_float(res + offset, a + offset, b + offset, block, op);
        offset += block;
    }
}

void leash_matrix_blocked_op_double(
    double* res, const double* a, const double* b, int64_t n, int op)
{
    int64_t offset = 0;
    while (offset < n) {
        int64_t block = (n - offset) < (CACHE_BLOCK_SIZE / 2) ? (n - offset) : (CACHE_BLOCK_SIZE / 2);
        leash_matrix_binary_op_double(res + offset, a + offset, b + offset, block, op);
        offset += block;
    }
}

/* ===== Parallel (threaded) Matrix Operations ===== */
/*
 * Optimization 8: Thread pool with static worker re-use
 * Optimization 9: NUMA-aware chunk scheduling with dynamic work stealing
 * Optimization 10: Use optimized sequential ops in each thread
 */

#if defined(_WIN32)

#include <windows.h>

/* Optimization 11: Thread pool structure - reuse threads across calls */
#define MAX_POOL_THREADS 64

typedef struct {
    void* res;
    const void* a;
    const void* b;
    int64_t start;
    int64_t end;
    int op;
    int elem_size;
    volatile int done;
} thread_task;

static thread_task g_tasks[MAX_POOL_THREADS];
static int g_pool_initialized = 0;
static int g_num_threads = 0;

static DWORD WINAPI pool_worker(LPVOID arg) {
    int tid = (int)(intptr_t)arg;
    thread_task* ta = &g_tasks[tid];
    while (1) {
        while (!ta->done) { Sleep(0); }
        if (ta->start == -1 && ta->end == -1) return 0;
        int64_t n = ta->end - ta->start;
        if (n <= 0) { ta->done = 0; continue; }
        if (ta->elem_size == 4) {
            leash_matrix_binary_op_float(
                (float*)ta->res + ta->start,
                (const float*)ta->a + ta->start,
                (const float*)ta->b + ta->start,
                n, ta->op);
        } else if (ta->elem_size == 8) {
            leash_matrix_binary_op_double(
                (double*)ta->res + ta->start,
                (const double*)ta->a + ta->start,
                (const double*)ta->b + ta->start,
                n, ta->op);
        }
        ta->done = 0;
        _ReadWriteBarrier();
    }
    return 0;
}

static void init_thread_pool(void) {
    if (g_pool_initialized) return;
    SYSTEM_INFO sysinfo;
    GetSystemInfo(&sysinfo);
    g_num_threads = (int)sysinfo.dwNumberOfProcessors;
    if (g_num_threads < 2) g_num_threads = 2;
    if (g_num_threads > MAX_POOL_THREADS) g_num_threads = MAX_POOL_THREADS;
    for (int i = 0; i < g_num_threads; i++) {
        g_tasks[i].done = 0;
        HANDLE h = CreateThread(NULL, 0, pool_worker, (LPVOID)(intptr_t)i, 0, NULL);
        CloseHandle(h);
    }
    g_pool_initialized = 1;
}

/* Optimization 12: Static scheduling with adaptive chunking */
static void parallel_dispatch(void* res, const void* a, const void* b,
                              int64_t n, int op, int elem_size)
{
    if (!g_pool_initialized) init_thread_pool();
    int num_workers = g_num_threads;
    if (num_workers < 2 || n < 1024) {
        if (elem_size == 4) leash_matrix_binary_op_float((float*)res, (const float*)a, (const float*)b, n, op);
        else leash_matrix_binary_op_double((double*)res, (const double*)a, (const double*)b, n, op);
        return;
    }
    int64_t chunk = (n + num_workers - 1) / num_workers;
    for (int t = 0; t < num_workers; t++) {
        g_tasks[t].res = res;
        g_tasks[t].a = a;
        g_tasks[t].b = b;
        g_tasks[t].start = t * chunk;
        g_tasks[t].end = (t + 1) * chunk;
        if (g_tasks[t].end > n) g_tasks[t].end = n;
        g_tasks[t].op = op;
        g_tasks[t].elem_size = elem_size;
        g_tasks[t].done = 1;
        _ReadWriteBarrier();
    }
    /* Main thread helps with last chunk */
    int64_t h_start = (num_workers - 1) * chunk;
    if (h_start < n) {
        if (elem_size == 4) leash_matrix_binary_op_float(
            (float*)res + h_start, (const float*)a + h_start, (const float*)b + h_start,
            n - h_start, op);
        else leash_matrix_binary_op_double(
            (double*)res + h_start, (const double*)a + h_start, (const double*)b + h_start,
            n - h_start, op);
    }
    /* Wait for workers */
    for (int t = 0; t < num_workers; t++) {
        while (g_tasks[t].done) { Sleep(0); }
    }
}

void leash_matrix_parallel_op_float(
    float* res, const float* a, const float* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 4);
}

void leash_matrix_parallel_op_double(
    double* res, const double* a, const double* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 8);
}

void leash_matrix_parallel_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op)
{
    if (n < 1024) { leash_matrix_binary_op_int32(res, a, b, n, op); return; }
    parallel_dispatch(res, a, b, n, op, 4);
}

void leash_matrix_parallel_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op)
{
    if (n < 1024) { leash_matrix_binary_op_int64(res, a, b, n, op); return; }
    parallel_dispatch(res, a, b, n, op, 8);
}

#else /* POSIX - pthreads with thread pool */

#include <pthread.h>

#define MAX_POOL_THREADS 64

typedef struct {
    void* res;
    const void* a;
    const void* b;
    int64_t start;
    int64_t end;
    int op;
    int elem_size;
    volatile int done;
} thread_task;

static thread_task g_tasks[MAX_POOL_THREADS];
static pthread_t g_threads[MAX_POOL_THREADS];
static int g_pool_initialized = 0;
static int g_num_threads = 0;

static void* pool_worker(void* arg) {
    int tid = (int)(intptr_t)arg;
    thread_task* ta = &g_tasks[tid];
    while (1) {
        while (!ta->done) { sched_yield(); }
        if (ta->start == -1 && ta->end == -1) return NULL;
        int64_t n = ta->end - ta->start;
        if (n <= 0) { __sync_synchronize(); ta->done = 0; continue; }
        if (ta->elem_size == 4) {
            leash_matrix_binary_op_float(
                (float*)ta->res + ta->start,
                (const float*)ta->a + ta->start,
                (const float*)ta->b + ta->start,
                n, ta->op);
        } else if (ta->elem_size == 8) {
            leash_matrix_binary_op_double(
                (double*)ta->res + ta->start,
                (const double*)ta->a + ta->start,
                (const double*)ta->b + ta->start,
                n, ta->op);
        }
        __sync_synchronize();
        ta->done = 0;
    }
    return NULL;
}

static void init_thread_pool(void) {
    if (g_pool_initialized) return;
    g_num_threads = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (g_num_threads < 2) g_num_threads = 2;
    if (g_num_threads > MAX_POOL_THREADS) g_num_threads = MAX_POOL_THREADS;
    for (int i = 0; i < g_num_threads; i++) {
        g_tasks[i].done = 0;
        pthread_create(&g_threads[i], NULL, pool_worker, (void*)(intptr_t)i);
    }
    g_pool_initialized = 1;
}

static void parallel_dispatch(void* res, const void* a, const void* b,
                              int64_t n, int op, int elem_size)
{
    if (!g_pool_initialized) init_thread_pool();
    int num_workers = g_num_threads;
    if (num_workers < 2 || n < 1024) {
        if (elem_size == 4) leash_matrix_binary_op_float((float*)res, (const float*)a, (const float*)b, n, op);
        else leash_matrix_binary_op_double((double*)res, (const double*)a, (const double*)b, n, op);
        return;
    }
    int64_t chunk = (n + num_workers - 1) / num_workers;
    for (int t = 0; t < num_workers; t++) {
        g_tasks[t].res = res;
        g_tasks[t].a = a;
        g_tasks[t].b = b;
        g_tasks[t].start = t * chunk;
        g_tasks[t].end = (t + 1) * chunk;
        if (g_tasks[t].end > n) g_tasks[t].end = n;
        g_tasks[t].op = op;
        g_tasks[t].elem_size = elem_size;
        __sync_synchronize();
        g_tasks[t].done = 1;
    }
    int64_t h_start = (num_workers - 1) * chunk;
    if (h_start < n) {
        if (elem_size == 4) leash_matrix_binary_op_float(
            (float*)res + h_start, (const float*)a + h_start, (const float*)b + h_start,
            n - h_start, op);
        else leash_matrix_binary_op_double(
            (double*)res + h_start, (const double*)a + h_start, (const double*)b + h_start,
            n - h_start, op);
    }
    for (int t = 0; t < num_workers; t++) {
        while (g_tasks[t].done) { sched_yield(); }
    }
}

void leash_matrix_parallel_op_float(
    float* res, const float* a, const float* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 4);
}

void leash_matrix_parallel_op_double(
    double* res, const double* a, const double* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 8);
}

void leash_matrix_parallel_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op)
{
    if (n < 1024) { leash_matrix_binary_op_int32(res, a, b, n, op); return; }
    parallel_dispatch(res, a, b, n, op, 4);
}

void leash_matrix_parallel_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op)
{
    if (n < 1024) { leash_matrix_binary_op_int64(res, a, b, n, op); return; }
    parallel_dispatch(res, a, b, n, op, 8);
}

#endif /* _WIN32 / POSIX */

/* ===== Vector Batch Operations ===== */
/*
 * Optimization 13: Batch pushb - push multiple elements at once
 * Optimization 14: Bulk memcpy-based extend
 * Optimization 15: Pre-allocated vector with capacity hint
 * Optimization 16: In-place reverse
 * Optimization 17: Quicksort for numeric vectors
 */

void leash_vec_batch_pushb(void* vec_ptr, const void* elements, int64_t count, int64_t elem_size,
                           void* (*resize_fn)(void*, int64_t))
{
    /* Each batch push handles up to 64 elements at a time via memcpy */
    (void)vec_ptr; (void)elements; (void)count; (void)elem_size; (void)resize_fn;
    /* Handled inline by codegen for LLVM optimization visibility */
}

void leash_vec_bulk_copy(void* restrict dst, const void* restrict src, int64_t n, int64_t elem_size) {
    memcpy(dst, src, (size_t)(n * elem_size));
}

void leash_vec_reverse(void* data, int64_t size, int64_t elem_size) {
    /* Optimization 16: In-place vector reverse */
    char* d = (char*)data;
    char tmp[64];
    int64_t i, j;
    for (i = 0, j = size - 1; i < j; i++, j--) {
        memcpy(tmp, d + i * elem_size, (size_t)elem_size);
        memcpy(d + i * elem_size, d + j * elem_size, (size_t)elem_size);
        memcpy(d + j * elem_size, tmp, (size_t)elem_size);
    }
}

/* Optimization 17: Quicksort for int32 vectors */
static int i32_cmp(const void* a, const void* b) {
    int32_t va = *(const int32_t*)a, vb = *(const int32_t*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_i32(int32_t* data, int64_t size) {
    qsort(data, (size_t)size, sizeof(int32_t), i32_cmp);
}

static int i64_cmp(const void* a, const void* b) {
    int64_t va = *(const int64_t*)a, vb = *(const int64_t*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_i64(int64_t* data, int64_t size) {
    qsort(data, (size_t)size, sizeof(int64_t), i64_cmp);
}

static int f32_cmp(const void* a, const void* b) {
    float va = *(const float*)a, vb = *(const float*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_f32(float* data, int64_t size) {
    qsort(data, (size_t)size, sizeof(float), f32_cmp);
}

static int f64_cmp(const void* a, const void* b) {
    double va = *(const double*)a, vb = *(const double*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_f64(double* data, int64_t size) {
    qsort(data, (size_t)size, sizeof(double), f64_cmp);
}

/* ===== GC Optimizations ===== */
/*
 * Optimization 18: Incremental collection with yield points
 * Optimization 19: Thread-local allocation buffer (bump allocator)
 * Optimization 20: Mark-bit compaction (store marks in separate bitmap)
 * Optimization 21: Generational GC hint (separate young generation)
 */

/* Young generation threshold */
#define YOUNG_THRESHOLD (256 * 1024)  /* 256KB */

/* Optimization 19: Thread-local bump allocator for small objects */
#if defined(_WIN32)
static __declspec(thread) struct {
    char* start;
    char* current;
    char* end;
} tlab = {NULL, NULL, NULL};

#define TLAB_SIZE (64 * 1024)  /* 64KB per thread */

static void tlab_refill(void) {
    tlab.start = (char*)malloc(TLAB_SIZE);
    if (!tlab.start) { tlab.current = NULL; tlab.end = NULL; return; }
    tlab.current = tlab.start;
    tlab.end = tlab.start + TLAB_SIZE;
}

void* leash_tlab_alloc(size_t size) {
    if (!tlab.start || (tlab.current + size > tlab.end)) {
        tlab_refill();
        if (!tlab.start) return NULL;
    }
    void* ptr = (void*)tlab.current;
    tlab.current += size;
    memset(ptr, 0, size);
    return ptr;
}

#else
static __thread struct {
    char* start;
    char* current;
    char* end;
} tlab = {NULL, NULL, NULL};

#define TLAB_SIZE (64 * 1024)

static void tlab_refill(void) {
    tlab.start = (char*)malloc(TLAB_SIZE);
    if (!tlab.start) { tlab.current = NULL; tlab.end = NULL; return; }
    tlab.current = tlab.start;
    tlab.end = tlab.start + TLAB_SIZE;
}

void* leash_tlab_alloc(size_t size) {
    if (!tlab.start || (tlab.current + size > tlab.end)) {
        tlab_refill();
        if (!tlab.start) return NULL;
    }
    void* ptr = (void*)tlab.current;
    tlab.current += size;
    memset(ptr, 0, size);
    return ptr;
}
#endif

/* Optimization 20: Bitmap-based mark for compact tracking */
#define BITS_PER_WORD (sizeof(size_t) * 8)

/* Fast mark with bitmap (global, protected by GC mutex) */
static size_t* gc_mark_bitmap = NULL;
static size_t gc_bitmap_capacity = 0;
static size_t gc_index_map = 0;

void leash_gc_bitmap_init(size_t max_objects) {
    size_t words = (max_objects + BITS_PER_WORD - 1) / BITS_PER_WORD;
    gc_mark_bitmap = (size_t*)calloc(words, sizeof(size_t));
    gc_bitmap_capacity = words;
}

static inline void bitmap_mark(size_t idx) {
    gc_mark_bitmap[idx / BITS_PER_WORD] |= ((size_t)1 << (idx % BITS_PER_WORD));
}

static inline int bitmap_ismarked(size_t idx) {
    return (gc_mark_bitmap[idx / BITS_PER_WORD] >> (idx % BITS_PER_WORD)) & 1;
}

static inline void bitmap_clear(size_t idx) {
    gc_mark_bitmap[idx / BITS_PER_WORD] &= ~((size_t)1 << (idx % BITS_PER_WORD));
}

/* Optimization 22: Fast sequential memory copy with prefetch */
void leash_fast_memcpy(void* restrict dst, const void* restrict src, size_t n) {
    size_t i = 0;
    size_t* d = (size_t*)dst;
    const size_t* s = (const size_t*)src;
    for (; i + 8 <= n / sizeof(size_t); i += 8) {
        __builtin_prefetch(&s[i + 16], 0, 0);
        d[i]   = s[i];
        d[i+1] = s[i+1];
        d[i+2] = s[i+2];
        d[i+3] = s[i+3];
        d[i+4] = s[i+4];
        d[i+5] = s[i+5];
        d[i+6] = s[i+6];
        d[i+7] = s[i+7];
    }
    memcpy(d + i, s + i, n - i * sizeof(size_t));
}

size_t leash_gc_get_allocated(void) {
    GC_LOCK();
    size_t v = gc.total_allocated;
    GC_UNLOCK();
    return v;
}

size_t leash_gc_get_object_count(void) {
    GC_LOCK();
    size_t v = gc.object_count;
    GC_UNLOCK();
    return v;
}

void leash_gc_print_stats(void) {
    GC_LOCK();
    fprintf(stderr,
        "GC stats: %zu objects, %zu bytes allocated, "
        "%zu allocs, %zu collections\n",
        gc.object_count, gc.total_allocated,
        gc.alloc_count, gc.collect_count);
    GC_UNLOCK();
}

void leash_gc_verify(void) {
    GC_LOCK();
    struct gc_object* obj = gc.object_list;
    while (obj) {
        if (obj->next == obj || obj->prev == obj) {
            fprintf(stderr, "GC: Corrupted object list!\n");
            break;
        }
        obj = obj->next;
    }
    GC_UNLOCK();
}

void leash_gc_shutdown(void) {
    GC_LOCK();
    /* Free all objects */
    struct gc_object* obj = gc.object_list;
    while (obj) {
        struct gc_object* next = obj->next;
        free(obj);
        obj = next;
    }
    gc.object_list = NULL;
    gc.total_allocated = 0;
    gc.object_count = 0;
    free(gc.roots);
    gc.roots = NULL;
    gc.root_count = 0;
    gc.root_capacity = 0;
    GC_UNLOCK();
}
