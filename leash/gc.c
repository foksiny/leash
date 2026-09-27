/**
 * Leash Custom Garbage Collector — slab-based mark & sweep
 *
 * Design:
 *   - Small objects (payload <= 4080 bytes) are carved from 64KB slabs,
 *     one size class per slab, with a 16-byte cell header
 *     {size_t size; u32 flags} immediately before the payload. Freed
 *     cells recycle through per-class free lists (threaded through the
 *     cell payload); empty slabs are unmapped during sweep.
 *   - Large objects are individually malloc'd with the same 16-byte
 *     header at payload-16 (raw base at payload-32) and kept in an
 *     address-sorted array.
 *   - Pointer -> object lookup is O(1): slab pages are registered in an
 *     open-addressing page directory keyed by page address; large
 *     objects are found by binary search. Candidates outside the heap
 *     are rejected by a single hash probe + binary search miss.
 *   - Marking is iterative (explicit worklist, no recursion). Roots:
 *     the explicit object-root array (futures, spawn arg blocks),
 *     scan regions registered by generated code for pointer-bearing
 *     globals, and a conservative scan of the main thread's stack with
 *     callee-saved registers flushed via setjmp.
 *   - Automatic collection triggers from the allocation path when
 *     bytes-since-last-collection exceeds max(2MB, 2x live bytes),
 *     only while quiescent: no active worker threads, no parallel
 *     matrix op in flight, no foreign (FFI) threads detected. Workers
 *     register with leash_gc_worker_begin/end around their lifetime;
 *     their stacks hold unscanned live pointers while they run.
 *
 * When compiled with -DNO_GC (used by --no-gc and --autofree modes),
 * the GC entry points become thin wrappers around standard malloc/free,
 * making the binary independent of the GC runtime.
 */

/* Must be defined before any system header so posix_memalign is declared
   and pthread_getattr_np (stack bounds) is visible on glibc. */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE 1
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <setjmp.h>
#include <time.h>

#include "gc.h"

#if defined(_WIN32)
#include <windows.h>
#else
#include <unistd.h>
#include <pthread.h>
#include <sys/mman.h>
#endif

#ifdef NO_GC
/* ===== Stub mode: plain malloc/free, no GC tracking ===== */

void leash_gc_init(void) {}
void leash_gc_shutdown(void) {}

void* leash_gc_malloc(size_t size) {
    if (size == 0) return NULL;
    void* p = malloc(size);
    if (!p) { fprintf(stderr, "Leash: Out of memory!\n"); abort(); }
    memset(p, 0, size);
    return p;
}

void* leash_gc_malloc_ex(size_t size, unsigned int flags) {
    (void)flags; /* no GC scanning in stub mode, so the ATOMIC flag is unused */
    return leash_gc_malloc(size);
}

void* leash_gc_realloc(void* ptr, size_t new_size) {
    if (!ptr) return leash_gc_malloc(new_size);
    if (new_size == 0) { free(ptr); return NULL; }
    void* p = realloc(ptr, new_size);
    if (!p) { fprintf(stderr, "Leash: Out of memory!\n"); abort(); }
    return p;
}

void* leash_gc_aligned_alloc(size_t size, size_t alignment) {
    (void)alignment;
    return leash_gc_malloc(size);
}

void* leash_gc_aligned_alloc_ex(size_t size, size_t alignment, unsigned int flags) {
    (void)flags;
    return leash_gc_aligned_alloc(size, alignment);
}

void leash_gc_collect(void) {}
void leash_gc_register_root(void* ptr) { (void)ptr; }
void leash_gc_unregister_root(void* ptr) { (void)ptr; }
void leash_gc_register_scan_region(void* start, size_t nbytes) { (void)start; (void)nbytes; }
void* leash_gc_malloc_rooted(size_t size) { return leash_gc_malloc(size); }
void* leash_gc_alloc_string(size_t len) { return leash_gc_malloc(len + 1); }
void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) { return leash_gc_malloc(elem_size * capacity); }
void leash_gc_thread_spawned(void) {}
void leash_gc_worker_begin(void) {}
void leash_gc_worker_end(void) {}
size_t leash_gc_get_allocated(void) { return 0; }
size_t leash_gc_get_object_count(void) { return 0; }
void leash_gc_print_stats(void) {}
void leash_gc_verify(void) {}

/* ===== End stub mode ===== */
#else
/* Full GC implementation follows */

#ifdef _WIN32
static CRITICAL_SECTION gc_mutex;
static LONG  gc_mutex_ready = 0;
/* On Windows the CRITICAL_SECTION must be initialised before first use.
   leash_gc_init() is called from main() before any threads exist, so we
   initialise it there and then set gc_mutex_ready.  Use InterlockedExchange
   so the flag becomes visible to all threads. */
static DWORD gc_main_thread_id = 0;
static __declspec(thread) int gc_i_hold_lock = 0;
static __declspec(thread) int gc_thread_worker = 0;
#else
static pthread_mutex_t gc_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_t gc_main_thread;
static __thread int gc_i_hold_lock = 0;
static __thread int gc_thread_worker = 0;
#endif

/* Single-thread fast path: allocating with a mutex (uncontended lock +
   unlock) costs ~25-40ns on every allocation, which is a large fraction of
   small-object allocation time. While no worker threads exist there is no
   possible concurrent access, so the lock can be skipped entirely.

   How threads become known:
      - leash_spawn_worker() and the matrix thread pool call
        leash_gc_thread_spawned() BEFORE creating each thread (set-once flag,
        never cleared).
      - Threads that run Leash code also set the per-thread gc_thread_worker
        flag via leash_gc_worker_begin(); they are bracketed for their whole
        lifetime so automatic collection stays away while they run.
      - Any OTHER thread that still reaches the GC (e.g. a callback thread
        created inside an FFI library) is detected here via the main-thread id
        check and permanently switches the GC to locked mode AND disables
        automatic collection (its stack is not scanned). */
static volatile int gc_is_multithreaded = 0;
/* Set when a thread we did not sanction (FFI callback) touches the GC.
   Automatic and explicit collection are refused from then on: unknown
   threads hold live pointers on stacks we never scan. */
static volatile int gc_foreign_threads = 0;

/* GC is initialised via leash_gc_init once before any concurrent access.
   GC_UNLOCK must mirror exactly what GC_LOCK decided — the multithreaded flag
   can flip (0->1) while the main thread is inside a lock-free section, so
   "did I lock?" is tracked per-thread (__thread / __declspec(thread)). */
#ifdef _WIN32
#define GC_LOCK()                                                          \
    do {                                                                   \
        if (gc_is_multithreaded || !gc_is_main_thread()) {                 \
            if (!gc_is_main_thread()) {                                    \
                if (!gc_thread_worker) gc_foreign_threads = 1;             \
                if (!gc_is_multithreaded) leash_gc_thread_spawned();       \
            }                                                              \
            if (!InterlockedExchangeAdd(&gc_mutex_ready, 0)) {             \
                fprintf(stderr, "FATAL: GC lock before leash_gc_init()\n");\
                abort();                                                   \
            }                                                              \
            EnterCriticalSection(&gc_mutex);                               \
            gc_i_hold_lock = 1;                                            \
        }                                                                  \
    } while(0)
#define GC_UNLOCK()                                                        \
    do {                                                                   \
        if (gc_i_hold_lock) {                                              \
            gc_i_hold_lock = 0;                                            \
            LeaveCriticalSection(&gc_mutex);                               \
        }                                                                  \
    } while(0)
#else
#define GC_LOCK()                                                          \
    do {                                                                   \
        if (gc_is_multithreaded || !gc_is_main_thread()) {                 \
            if (!gc_is_main_thread()) {                                    \
                if (!gc_thread_worker) gc_foreign_threads = 1;             \
                if (!gc_is_multithreaded) leash_gc_thread_spawned();       \
            }                                                              \
            pthread_mutex_lock(&gc_mutex);                                 \
            gc_i_hold_lock = 1;                                            \
        }                                                                  \
    } while(0)
#define GC_UNLOCK()                                                        \
    do {                                                                   \
        if (gc_i_hold_lock) {                                              \
            gc_i_hold_lock = 0;                                            \
            pthread_mutex_unlock(&gc_mutex);                               \
        }                                                                  \
    } while(0)
#endif

static int gc_is_main_thread(void) {
#ifdef _WIN32
    return GetCurrentThreadId() == gc_main_thread_id;
#else
    return pthread_equal(pthread_self(), gc_main_thread);
#endif
}

void leash_gc_thread_spawned(void) {
#ifdef _WIN32
    InterlockedExchange(&gc_is_multithreaded, 1);
#else
    __sync_synchronize();
    gc_is_multithreaded = 1;
#endif
}

/* ===== Configuration ===== */
#define INITIAL_THRESHOLD (2 * 1024 * 1024)  /* 2MB floor between auto-collections */
#define SLAB_NOMINAL_SIZE (64 * 1024)        /* slab size, rounded to page size */
#define CELL_HDR ((size_t)16)                /* cell header before payload */
#define SMALL_MAX_PAYLOAD 4080               /* cell <= 4096 */
#define NUM_CLASSES 95
#define ROOTS_INIT_CAP 256
#define PAGE_DIR_TOMBSTONE ((uintptr_t)1)

/* Cell classes (cell size includes the 16-byte header):
     payload <= 496  -> cell = round16(payload+16), 32..512
     payload <= 1008 -> cell = round32(payload+16), 544..1024
     payload <= 4080 -> cell = round64(payload+16), 1088..4096
   class index maps each cell size back to a free-list slot. */
static uint32_t gc_cell_size_for(size_t payload) {
    size_t need = payload + CELL_HDR;
    if (need <= 512) return (uint32_t)((need + 15) & ~(size_t)15);
    if (need <= 1024) return (uint32_t)((need + 31) & ~(size_t)31);
    if (need <= 4096) return (uint32_t)((need + 63) & ~(size_t)63);
    return 0; /* large path */
}

static uint32_t gc_class_index(uint32_t cell) {
    if (cell <= 512) return (cell - 32) / 16;       /* 0..30 */
    if (cell <= 1024) return 31 + (cell - 544) / 32; /* 31..46 */
    return 47 + (cell - 1088) / 64;                  /* 47..94 */
}

/* ===== Object header (16 bytes, immediately before the payload) ===== */
struct gc_cell {
    size_t size;      /* requested payload size */
    uint32_t flags;   /* FLAG_MARKED | FLAG_ATOMIC | CELL_IN_USE */
    uint32_t _pad;
};

#define FLAG_MARKED   0x01U
#define FLAG_ATOMIC   0x02U
#define CELL_IN_USE   0x04U

/* ===== Slab (lives at the start of its own page-aligned mapping) ===== */
struct gc_slab {
    struct gc_slab* next;       /* all-slabs list (sweep) */
    struct gc_slab* class_next; /* per-class slab list */
    char* base;
    char* cells;
    char* bump;                 /* next never-handed-out cell */
    char* end;                  /* end of whole-cell region */
    size_t map_size;
    uint32_t cell_size;
    uint32_t live;              /* IN_USE cells */
    uint16_t class_idx;
    uint16_t _pad;
};

/* ===== Large object (payload > SMALL_MAX_PAYLOAD or alignment > 16) ===== */
struct gc_large {
    char* payload;
    size_t size;
    void* raw;                  /* malloc base at payload-32.. */
};

/* ===== Page directory: page address -> slab owner (tag bit clear) ===== */
struct gc_page_slot {
    uintptr_t key;    /* page address, 0 = empty, 1 = tombstone */
    uintptr_t owner;  /* struct gc_slab* (bit 0 clear — page aligned) */
};

struct gc_region { void* start; size_t nbytes; };

/* GC State */
static struct {
    /* slabs */
    struct gc_slab* slabs;
    struct gc_slab* class_slabs[NUM_CLASSES];
    struct gc_slab* class_bump[NUM_CLASSES];
    void* class_freelists[NUM_CLASSES];
    /* large objects, sorted by payload address */
    struct gc_large* larges;
    size_t large_count;
    size_t large_cap;
    /* page directory */
    struct gc_page_slot* page_dir;
    size_t page_dir_cap;
    size_t page_dir_count;
    unsigned page_dir_shift;
    /* roots */
    void** roots;
    size_t root_count;
    size_t root_capacity;
    /* scan regions (pointer-bearing globals) */
    struct gc_region* regions;
    size_t region_count;
    size_t region_capacity;
    /* mark worklist */
    struct gc_cell** worklist;
    size_t worklist_count;
    size_t worklist_cap;
    /* accounting */
    size_t total_allocated;
    size_t object_count;
    size_t alloc_count;
    size_t collect_count;
    size_t bytes_since_gc;
    size_t live_bytes;
    size_t threshold_floor;
    unsigned long long gc_time_ns;
    /* environment */
    char* stack_top;            /* conservative scan upper bound (NULL = unknown) */
    int auto_collect;
} gc = {0};

/* Quiescence counters (atomics; written without the GC lock) */
static volatile int gc_active_workers = 0;

static void gc_oom(const char* what) {
    fprintf(stderr, "Leash GC: Out of memory (%s)!\n", what);
    abort();
}

/* ===== Atomics ===== */
static int gc_atomic_load(volatile int* p) {
#ifdef _WIN32
    return (int)InterlockedCompareExchange((volatile LONG*)p, 0, 0);
#else
    return __sync_fetch_and_add(p, 0);
#endif
}

void leash_gc_worker_begin(void) {
    /* Marks this thread as sanctioned Leash code: it may touch the GC, and
       automatic collection stays away while the count is non-zero. */
    gc_thread_worker = 1;
#ifdef _WIN32
    InterlockedIncrement((volatile LONG*)&gc_active_workers);
#else
    __sync_fetch_and_add(&gc_active_workers, 1);
#endif
}

void leash_gc_worker_end(void) {
#ifdef _WIN32
    InterlockedDecrement((volatile LONG*)&gc_active_workers);
#else
    __sync_fetch_and_add(&gc_active_workers, -1);
#endif
}

/* ===== Page allocation (slab mappings) ===== */
static size_t gc_page_size = 4096;
static size_t gc_slab_map = SLAB_NOMINAL_SIZE;

static char* gc_page_alloc(size_t size) {
#ifdef _WIN32
    void* p = VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    return (char*)p;
#else
    void* p = mmap(NULL, size, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return NULL;
    return (char*)p;
#endif
}

static void gc_page_free(char* base, size_t size) {
#ifdef _WIN32
    (void)size;
    VirtualFree(base, 0, MEM_RELEASE);
#else
    munmap(base, size);
#endif
}

/* ===== Page directory ===== */
static inline size_t gc_page_hash(uintptr_t key) {
    uint64_t h = (uint64_t)key * 0x9E3779B97F4A7C15ULL;
    return (size_t)(h >> gc.page_dir_shift);
}

static void gc_page_dir_grow(void);

static void gc_page_dir_insert(uintptr_t key, uintptr_t owner) {
    if (gc.page_dir_cap == 0 || gc.page_dir_count * 10 >= gc.page_dir_cap * 7) {
        gc_page_dir_grow();
    }
    size_t cap = gc.page_dir_cap;
    size_t idx = gc_page_hash(key);
    size_t first_tomb = (size_t)-1;
    for (size_t probe = 0; probe < cap; probe++) {
        struct gc_page_slot* s = &gc.page_dir[(idx + probe) & (cap - 1)];
        if (s->key == 0) {
            struct gc_page_slot* dst = (first_tomb != (size_t)-1)
                ? &gc.page_dir[first_tomb] : s;
            dst->key = key;
            dst->owner = owner;
            if (first_tomb == (size_t)-1) gc.page_dir_count++;
            return;
        }
        if (s->key == PAGE_DIR_TOMBSTONE) {
            if (first_tomb == (size_t)-1) first_tomb = (idx + probe) & (cap - 1);
            continue;
        }
        if (s->key == key) {
            s->owner = owner;
            return;
        }
    }
    gc_page_dir_grow();
    gc_page_dir_insert(key, owner);
}

static void gc_page_dir_grow(void) {
    size_t new_cap = gc.page_dir_cap ? gc.page_dir_cap * 2 : 4096;
    struct gc_page_slot* nd = (struct gc_page_slot*)calloc(new_cap, sizeof(*nd));
    if (!nd) gc_oom("page directory");
    struct gc_page_slot* old = gc.page_dir;
    size_t old_cap = gc.page_dir_cap;
    gc.page_dir = nd;
    gc.page_dir_cap = new_cap;
    gc.page_dir_count = 0;
    unsigned shift = 0;
    while (((size_t)1 << shift) < new_cap) shift++;
    gc.page_dir_shift = 64 - shift;
    for (size_t i = 0; i < old_cap; i++) {
        if (old[i].key != 0 && old[i].key != PAGE_DIR_TOMBSTONE) {
            /* re-insert without recursion into grow */
            size_t idx = gc_page_hash(old[i].key);
            for (size_t probe = 0; probe < new_cap; probe++) {
                struct gc_page_slot* s = &gc.page_dir[(idx + probe) & (new_cap - 1)];
                if (s->key == 0) {
                    s->key = old[i].key;
                    s->owner = old[i].owner;
                    gc.page_dir_count++;
                    break;
                }
            }
        }
    }
    free(old);
}

static uintptr_t gc_page_dir_find(uintptr_t key) {
    size_t cap = gc.page_dir_cap;
    if (cap == 0) return 0;
    size_t idx = gc_page_hash(key);
    for (size_t probe = 0; probe < cap; probe++) {
        const struct gc_page_slot* s = &gc.page_dir[(idx + probe) & (cap - 1)];
        if (s->key == 0) return 0;
        if (s->key == key) return s->owner;
    }
    return 0;
}

static void gc_page_dir_tombstone(uintptr_t key) {
    size_t cap = gc.page_dir_cap;
    if (cap == 0) return;
    size_t idx = gc_page_hash(key);
    for (size_t probe = 0; probe < cap; probe++) {
        struct gc_page_slot* s = &gc.page_dir[(idx + probe) & (cap - 1)];
        if (s->key == 0) return;
        if (s->key == key) {
            s->key = PAGE_DIR_TOMBSTONE;
            s->owner = 0;
            return;
        }
    }
}

/* ===== Pointer -> object lookup (O(1) slab probe + binary search) ===== */
static struct gc_cell* gc_find_object(const void* p) {
    uintptr_t addr = (uintptr_t)p;
    if (addr == 0 || addr < CELL_HDR + 32) return NULL;

    uintptr_t page = addr & ~((uintptr_t)gc_page_size - 1);
    uintptr_t owner = gc_page_dir_find(page);
    if (owner != 0) {
        struct gc_slab* s = (struct gc_slab*)owner;
        if (addr < (uintptr_t)s->cells || addr >= (uintptr_t)s->end) return NULL;
        size_t off = addr - (uintptr_t)s->cells;
        uint32_t cs = s->cell_size;
        size_t idx = off / cs;
        char* cp = s->cells + idx * cs;
        if (addr < (uintptr_t)cp || addr >= (uintptr_t)s->end) return NULL;
        struct gc_cell* c = (struct gc_cell*)cp;
        uintptr_t pay = (uintptr_t)cp + CELL_HDR;
        if (addr < pay) return NULL;
        if (addr - pay >= c->size) return NULL;
        if (!(c->flags & CELL_IN_USE)) return NULL;
        return c;
    }

    /* large objects: address-sorted array */
    size_t lo = 0, hi = gc.large_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        uintptr_t start = (uintptr_t)gc.larges[mid].payload;
        if (addr < start) hi = mid;
        else lo = mid + 1;
    }
    if (lo == 0) return NULL;
    struct gc_large* L = &gc.larges[lo - 1];
    if (addr < (uintptr_t)L->payload) return NULL;
    struct gc_cell* c = (struct gc_cell*)(L->payload - CELL_HDR);
    if (addr - (uintptr_t)L->payload >= c->size) return NULL;
    if (!(c->flags & CELL_IN_USE)) return NULL;
    if (L->payload + c->size <= L->payload) return NULL;
    return c;
}

/* ===== Large object array ===== */
static void gc_large_insert(char* payload, size_t size, void* raw) {
    if (gc.large_count == gc.large_cap) {
        size_t cap = gc.large_cap ? gc.large_cap * 2 : 16;
        struct gc_large* nl = (struct gc_large*)realloc(gc.larges, cap * sizeof(*nl));
        if (!nl) gc_oom("large object table");
        gc.larges = nl;
        gc.large_cap = cap;
    }
    size_t lo = 0, hi = gc.large_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if ((uintptr_t)gc.larges[mid].payload < (uintptr_t)payload) lo = mid + 1;
        else hi = mid;
    }
    memmove(&gc.larges[lo + 1], &gc.larges[lo],
            (gc.large_count - lo) * sizeof(struct gc_large));
    gc.larges[lo].payload = payload;
    gc.larges[lo].size = size;
    gc.larges[lo].raw = raw;
    gc.large_count++;
}

static size_t gc_large_find_index(const char* payload) {
    size_t lo = 0, hi = gc.large_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if ((uintptr_t)gc.larges[mid].payload < (uintptr_t)payload) lo = mid + 1;
        else hi = mid;
    }
    return lo; /* caller checks match */
}

static void gc_large_remove(size_t idx) {
    memmove(&gc.larges[idx], &gc.larges[idx + 1],
            (gc.large_count - idx - 1) * sizeof(struct gc_large));
    gc.large_count--;
}

/* ===== Slab creation / purge ===== */
static struct gc_slab* gc_slab_create(uint32_t cell_size, uint32_t class_idx) {
    char* base = gc_page_alloc(gc_slab_map);
    if (!base) gc_oom("slab");
    size_t hdr = (sizeof(struct gc_slab) + 15) & ~(size_t)15;
    struct gc_slab* s = (struct gc_slab*)base;
    s->next = gc.slabs;
    gc.slabs = s;
    s->class_next = NULL;
    s->base = base;
    s->map_size = gc_slab_map;
    s->cell_size = cell_size;
    s->live = 0;
    s->class_idx = (uint16_t)class_idx;
    s->cells = base + hdr;
    s->bump = s->cells;
    s->end = s->cells + ((gc_slab_map - hdr) / cell_size) * cell_size;

    uintptr_t page_mask = ~((uintptr_t)gc_page_size - 1);
    uintptr_t pg = (uintptr_t)base & page_mask;
    uintptr_t pend = ((uintptr_t)(base + gc_slab_map) - 1) & page_mask;
    for (; pg <= pend; pg += gc_page_size) {
        gc_page_dir_insert(pg, (uintptr_t)s);
    }
    return s;
}

static void gc_slab_purge(struct gc_slab* s) {
    /* Tombstone page entries before the mapping goes away. */
    uintptr_t page_mask = ~((uintptr_t)gc_page_size - 1);
    uintptr_t pg = (uintptr_t)s->base & page_mask;
    uintptr_t pend = ((uintptr_t)(s->base + s->map_size) - 1) & page_mask;
    for (; pg <= pend; pg += gc_page_size) {
        gc_page_dir_tombstone(pg);
    }
    /* Unlink from lists (descriptor lives inside the mapping: unlink first). */
    struct gc_slab** p = &gc.slabs;
    while (*p && *p != s) p = &(*p)->next;
    if (*p) *p = s->next;
    struct gc_slab** cp = &gc.class_slabs[s->class_idx];
    while (*cp && *cp != s) cp = &(*cp)->class_next;
    if (*cp) *cp = s->class_next;
    if (gc.class_bump[s->class_idx] == s) gc.class_bump[s->class_idx] = NULL;
    char* base = s->base;
    size_t map = s->map_size;
    gc_page_free(base, map);
}

/* ===== Allocation ===== */
static void* gc_alloc_small(size_t size, uint32_t cell_size, unsigned flags) {
    uint32_t idx = gc_class_index(cell_size);

    /* Prefer recycled cells (bounded RSS) over fresh bump space. */
    void* payload = gc.class_freelists[idx];
    struct gc_cell* c;
    if (payload) {
        gc.class_freelists[idx] = *(void**)payload;
        c = (struct gc_cell*)((char*)payload - CELL_HDR);
        c->size = size;
        c->flags = CELL_IN_USE | (flags & FLAG_ATOMIC);
        memset(payload, 0, size);
        /* slab live count: find owner via page dir (cheap, one probe) */
        uintptr_t page = ((uintptr_t)c) & ~((uintptr_t)gc_page_size - 1);
        uintptr_t owner = gc_page_dir_find(page);
        if (owner) ((struct gc_slab*)owner)->live++;
        goto accounted;
    }

    struct gc_slab* s = gc.class_bump[idx];
    if (s && s->bump + cell_size > s->end) s = NULL;
    if (!s) {
        for (s = gc.class_slabs[idx]; s; s = s->class_next) {
            if (s->bump + cell_size <= s->end) break;
        }
    }
    if (!s) {
        s = gc_slab_create(cell_size, idx);
        s->class_next = gc.class_slabs[idx];
        gc.class_slabs[idx] = s;
    }
    gc.class_bump[idx] = s;
    c = (struct gc_cell*)s->bump;
    s->bump += cell_size;
    s->live++;
    c->size = size;
    c->flags = CELL_IN_USE | (flags & FLAG_ATOMIC);
    /* mmap'd slab: fresh cells are already zeroed — no memset. */
    payload = (char*)c + CELL_HDR;

accounted:
    gc.total_allocated += size;
    gc.object_count++;
    gc.alloc_count++;
    gc.bytes_since_gc += size;
    return payload;
}

static void* gc_alloc_large(size_t size, unsigned int flags, size_t alignment) {
    if (alignment < 16) alignment = 16;
    /* round alignment up to a power of two */
    size_t al = 16;
    while (al < alignment && al <= (size_t)1 << 20) al <<= 1;
    alignment = al;
    if (size > SIZE_MAX - alignment - 64) gc_oom("allocation too large");
    size_t need = size + alignment + 64;
    char* raw = (char*)malloc(need);
    if (!raw) gc_oom("large object");
    uintptr_t a = ((uintptr_t)raw + 64 + alignment - 1) & ~(uintptr_t)(alignment - 1);
    char* payload = (char*)a;
    struct gc_cell* c = (struct gc_cell*)(payload - CELL_HDR);
    ((void**)c)[-1] = raw; /* raw base at payload-32 */
    c->size = size;
    c->flags = CELL_IN_USE | (flags & FLAG_ATOMIC);
    memset(payload, 0, size);
    gc_large_insert(payload, size, raw);
    gc.total_allocated += size;
    gc.object_count++;
    gc.alloc_count++;
    gc.bytes_since_gc += size;
    return payload;
}

static void gc_free_cell_payload(void* payload) {
    struct gc_cell* c = (struct gc_cell*)((char*)payload - CELL_HDR);
    c->flags &= ~CELL_IN_USE;
    gc.total_allocated -= c->size;
    gc.object_count--;
    uintptr_t page = ((uintptr_t)c) & ~((uintptr_t)gc_page_size - 1);
    uintptr_t owner = gc_page_dir_find(page);
    if (owner) {
        struct gc_slab* s = (struct gc_slab*)owner;
        if (s->live) s->live--;
        uint32_t idx = gc_class_index(s->cell_size);
        *(void**)payload = gc.class_freelists[idx];
        gc.class_freelists[idx] = payload;
    }
}

static void gc_free_large_payload(void* payload) {
    struct gc_cell* c = (struct gc_cell*)((char*)payload - CELL_HDR);
    gc.total_allocated -= c->size;
    gc.object_count--;
    size_t idx = gc_large_find_index((char*)payload);
    if (idx < gc.large_count && gc.larges[idx].payload == payload) {
        void* raw = gc.larges[idx].raw;
        gc_large_remove(idx);
        free(raw);
    }
}

/* Caller must hold the GC lock (or be single-threaded). */
static void* gc_alloc_locked(size_t size, unsigned int flags, size_t alignment) {
    uint32_t cell = gc_cell_size_for(size);
    if (cell != 0 && alignment <= 16) {
        return gc_alloc_small(size, cell, flags);
    }
    return gc_alloc_large(size, flags, alignment);
}

static int gc_should_auto_collect(void);
static void gc_collect_locked_impl(void);

void* leash_gc_malloc_ex(size_t size, unsigned int flags); /* must stay non-static:
   GCC -O2 constant propagation would otherwise specialize away the generic
   symbol the generated LLVM code links against. */

void* leash_gc_malloc(size_t size) {
    return leash_gc_malloc_ex(size, 0);
}

void* leash_gc_malloc_ex(size_t size, unsigned int flags) {
    if (size == 0) return NULL;
    if (size > SIZE_MAX - 4096) {
        fprintf(stderr, "Leash GC: allocation too large!\n");
        abort();
    }
    GC_LOCK();
    if (gc_should_auto_collect()) {
        gc_collect_locked_impl();
    }
    void* p = gc_alloc_locked(size, flags, 16);
    GC_UNLOCK();
    return p;
}

void* leash_gc_aligned_alloc_ex(size_t size, size_t alignment, unsigned int flags) {
    if (size == 0) return NULL;
    if (size > SIZE_MAX - 4096) {
        fprintf(stderr, "Leash GC: allocation too large!\n");
        abort();
    }
    GC_LOCK();
    if (gc_should_auto_collect()) {
        gc_collect_locked_impl();
    }
    void* p = gc_alloc_locked(size, flags, alignment);
    GC_UNLOCK();
    return p;
}

void* leash_gc_aligned_alloc(size_t size, size_t alignment) {
    /* Tracked through the GC now (previously a raw posix_memalign that the
       collector could neither scan nor free — matrix temporaries leaked).
       Not marked ATOMIC here: callers wanting pointer-free semantics pass
       flags via leash_gc_aligned_alloc_ex. */
    return leash_gc_aligned_alloc_ex(size, alignment, 0);
}

void* leash_gc_malloc_rooted(size_t size) {
    if (size == 0) return NULL;
    if (size > SIZE_MAX - 4096) {
        fprintf(stderr, "Leash GC: allocation too large!\n");
        abort();
    }
    GC_LOCK();
    if (gc_should_auto_collect()) {
        gc_collect_locked_impl();
    }
    void* p = gc_alloc_locked(size, 0, 16);
    if (gc.root_count >= gc.root_capacity) {
        size_t new_cap = gc.root_capacity ? gc.root_capacity * 2 : ROOTS_INIT_CAP;
        void** nr = (void**)realloc(gc.roots, new_cap * sizeof(void*));
        if (!nr) gc_oom("root set");
        memset(nr + gc.root_capacity, 0, (new_cap - gc.root_capacity) * sizeof(void*));
        gc.roots = nr;
        gc.root_capacity = new_cap;
    }
    gc.roots[gc.root_count++] = p;
    GC_UNLOCK();
    return p;
}

/* Ownership routing for a payload the GC handed out (caller holds lock). */
static int gc_payload_is_large(const void* payload) {
    size_t idx = gc_large_find_index((const char*)payload);
    return idx < gc.large_count && gc.larges[idx].payload == (const char*)payload;
}

static void gc_free_payload_locked(void* payload) {
    if (gc_payload_is_large(payload)) gc_free_large_payload(payload);
    else gc_free_cell_payload(payload);
}

void* leash_gc_realloc(void* ptr, size_t new_size) {
    if (!ptr) return leash_gc_malloc(new_size);

    GC_LOCK();
    struct gc_cell* c = (struct gc_cell*)((char*)ptr - CELL_HDR);
    unsigned flags = c->flags & FLAG_ATOMIC;
    size_t old_size = c->size;

    if (new_size == 0) {
        gc_free_payload_locked(ptr);
        GC_UNLOCK();
        return NULL;
    }

    if (new_size <= old_size) {
        /* Shrink in place: cell capacity is unchanged, bytes beyond new_size
           are never observed again. */
        gc.total_allocated -= (old_size - new_size);
        c->size = new_size;
        if (gc_payload_is_large(ptr)) {
            gc.larges[gc_large_find_index((const char*)ptr)].size = new_size;
        }
        GC_UNLOCK();
        return ptr;
    }

    /* Growth: allocate fresh (never extends in place — slack bytes may hold
       stale data from a previous occupant and must not be observed). */
    if (new_size > SIZE_MAX - 4096) {
        fprintf(stderr, "Leash GC: allocation too large!\n");
        GC_UNLOCK();
        abort();
    }
    if (gc_should_auto_collect()) gc_collect_locked_impl();
    void* np = gc_alloc_locked(new_size, flags, 16);
    memcpy(np, ptr, old_size);
    for (size_t i = 0; i < gc.root_count; i++) {
        if (gc.roots[i] == ptr) gc.roots[i] = np;
    }
    gc_free_payload_locked(ptr);
    GC_UNLOCK();
    return np;
}

/* ===== Root Management ===== */
void leash_gc_register_root(void* ptr) {
    if (!ptr) return;
    GC_LOCK();
    if (gc.root_count >= gc.root_capacity) {
        size_t new_cap = gc.root_capacity ? gc.root_capacity * 2 : ROOTS_INIT_CAP;
        void** nr = (void**)realloc(gc.roots, new_cap * sizeof(void*));
        if (!nr) gc_oom("root set");
        memset(nr + gc.root_capacity, 0, (new_cap - gc.root_capacity) * sizeof(void*));
        gc.roots = nr;
        gc.root_capacity = new_cap;
    }
    gc.roots[gc.root_count++] = ptr;
    GC_UNLOCK();
}

void leash_gc_unregister_root(void* ptr) {
    if (!ptr) return;
    GC_LOCK();
    for (size_t i = 0; i < gc.root_count; i++) {
        if (gc.roots[i] == ptr) {
            gc.roots[i] = gc.roots[gc.root_count - 1];
            gc.roots[gc.root_count - 1] = NULL;
            gc.root_count--;
            break;
        }
    }
    GC_UNLOCK();
}

void leash_gc_register_scan_region(void* start, size_t nbytes) {
    if (!start || !nbytes) return;
    GC_LOCK();
    if (gc.region_count >= gc.region_capacity) {
        size_t cap = gc.region_capacity ? gc.region_capacity * 2 : 8;
        struct gc_region* nr = (struct gc_region*)realloc(gc.regions, cap * sizeof(*nr));
        if (!nr) gc_oom("scan region table");
        gc.regions = nr;
        gc.region_capacity = cap;
    }
    gc.regions[gc.region_count].start = start;
    gc.regions[gc.region_count].nbytes = nbytes;
    gc.region_count++;
    GC_UNLOCK();
}

/* ===== Time ===== */
static unsigned long long gc_now_ns(void) {
#ifdef _WIN32
    static LARGE_INTEGER freq = {0};
    LARGE_INTEGER counter;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&counter);
    return (unsigned long long)(counter.QuadPart * 1000000000ULL / freq.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000000000ULL + (unsigned long long)ts.tv_nsec;
#endif
}

/* ===== Stack bounds (conservative main-stack scanning) =====
   Captured on the collecting thread at every collection: only the TOP of the
   stack (highest address; stacks grow down on all supported architectures)
   is cached here — the low bound is the collector's own frame. Without
   trustworthy bounds the collector refuses to run rather than do a partial
   scan that could miss live roots. */
static void gc_capture_stack_bounds(void) {
    char* top = NULL;
#if defined(_WIN32)
    MEMORY_BASIC_INFORMATION mbi;
    char probe;
    if (VirtualQuery(&probe, &mbi, sizeof(mbi)) != 0) {
        void* base = mbi.AllocationBase;
        top = (char*)mbi.BaseAddress + mbi.RegionSize;
        /* Walk upward through contiguous regions of the same allocation
           (the stack's guard/committed pages) to the true top. */
        for (;;) {
            MEMORY_BASIC_INFORMATION nb;
            if (VirtualQuery(top, &nb, sizeof(nb)) == 0) break;
            if (nb.AllocationBase != base) break;
            char* ntop = (char*)nb.BaseAddress + nb.RegionSize;
            if (ntop <= top) break;
            top = ntop;
        }
    }
#elif defined(__APPLE__)
    top = (char*)pthread_get_stackaddr_np(pthread_self());
#elif defined(__linux__)
    pthread_attr_t attr;
    void* addr = NULL;
    size_t size = 0;
    if (pthread_getattr_np(pthread_self(), &attr) == 0) {
        if (pthread_attr_getstack(&attr, &addr, &size) == 0 && addr && size) {
            top = (char*)addr + size;
        }
        pthread_attr_destroy(&attr);
    }
#else
    (void)top; /* unknown platform: bounds stay unknown, collection refuses */
#endif
    if (top) gc.stack_top = top; /* keep the last good value on failure */
}

/* ===== Marking (iterative worklist, no recursion) ===== */
static void gc_worklist_push(struct gc_cell* c) {
    if (gc.worklist_count == gc.worklist_cap) {
        size_t cap = gc.worklist_cap ? gc.worklist_cap * 2 : 1024;
        struct gc_cell** nw = (struct gc_cell**)realloc(gc.worklist, cap * sizeof(*nw));
        if (!nw) gc_oom("mark worklist");
        gc.worklist = nw;
        gc.worklist_cap = cap;
    }
    gc.worklist[gc.worklist_count++] = c;
}

static void gc_mark_cell(struct gc_cell* c) {
    if (c->flags & FLAG_MARKED) return;
    c->flags |= FLAG_MARKED;
    gc_worklist_push(c);
}

static void gc_mark_candidate(const void* p) {
    struct gc_cell* c = gc_find_object(p);
    if (c) gc_mark_cell(c);
}

/* Treat every aligned word in [start, start+nbytes) as a candidate pointer. */
static void gc_mark_words(const void* start, size_t nbytes) {
    const char* s = (const char*)start;
    if (!s || nbytes < sizeof(uintptr_t)) return;
    uintptr_t end = (uintptr_t)s + nbytes;
    uintptr_t addr = ((uintptr_t)s + sizeof(uintptr_t) - 1)
                     & ~(uintptr_t)(sizeof(uintptr_t) - 1);
    for (; addr + sizeof(uintptr_t) <= end; addr += sizeof(uintptr_t)) {
        gc_mark_candidate((const void*)*(const uintptr_t*)addr);
    }
}

static void gc_mark_drain(void) {
    while (gc.worklist_count) {
        struct gc_cell* c = gc.worklist[--gc.worklist_count];
        if (c->flags & FLAG_ATOMIC) continue; /* pointer-free payload */
        gc_mark_words((const char*)c + CELL_HDR, c->size);
    }
}

static void gc_mark_roots(void) {
    for (size_t i = 0; i < gc.root_count; i++) {
        if (gc.roots[i]) gc_mark_candidate(gc.roots[i]);
    }
    for (size_t i = 0; i < gc.region_count; i++) {
        gc_mark_words(gc.regions[i].start, gc.regions[i].nbytes);
    }
}

static void gc_mark_stack(void) {
    char probe = 0; /* address-only, but initialized to keep -Wmaybe-uninit quiet */
    char* sp = &probe;
    char* top = gc.stack_top;
    if (!top || sp > top) return; /* precondition checked by the caller */
    gc_mark_words(sp, (size_t)(top - sp));
}

/* ===== Sweep ===== */
/* Purging an empty slab leaves freelist heads pointing into unmapped memory,
   so after any purge the free lists are rebuilt from the surviving slabs
   (old heads are discarded without ever being dereferenced). */
static void gc_freelist_rebuild(void) {
    for (uint32_t i = 0; i < NUM_CLASSES; i++) gc.class_freelists[i] = NULL;
    for (struct gc_slab* s = gc.slabs; s; s = s->next) {
        uint32_t idx = gc_class_index(s->cell_size);
        for (char* p = s->cells; p < s->bump; p += s->cell_size) {
            struct gc_cell* c = (struct gc_cell*)p;
            if (c->flags & CELL_IN_USE) continue;
            void* payload = p + CELL_HDR;
            *(void**)payload = gc.class_freelists[idx];
            gc.class_freelists[idx] = payload;
        }
    }
}

static void gc_sweep(void) {
    struct gc_slab* s = gc.slabs;
    int purged_any = 0;
    while (s) {
        struct gc_slab* next = s->next;
        uint32_t cs = s->cell_size;
        uint32_t idx = gc_class_index(cs);
        uint32_t live = 0;
        for (char* p = s->cells; p < s->bump; p += cs) {
            struct gc_cell* c = (struct gc_cell*)p;
            if (!(c->flags & CELL_IN_USE)) continue;
            if (c->flags & FLAG_MARKED) {
                c->flags = CELL_IN_USE | (c->flags & FLAG_ATOMIC);
                live++;
                continue;
            }
            void* payload = p + CELL_HDR;
            gc.total_allocated -= c->size;
            gc.object_count--;
            c->flags = 0;
            *(void**)payload = gc.class_freelists[idx];
            gc.class_freelists[idx] = payload;
        }
        s->live = live;
        if (live == 0) {
            gc_slab_purge(s);
            purged_any = 1;
        }
        s = next;
    }

    size_t i = 0;
    while (i < gc.large_count) {
        struct gc_large* L = &gc.larges[i];
        struct gc_cell* c = (struct gc_cell*)(L->payload - CELL_HDR);
        if (!(c->flags & FLAG_MARKED)) {
            gc_free_large_payload(L->payload); /* removes entry i */
            continue;
        }
        c->flags = CELL_IN_USE | (c->flags & FLAG_ATOMIC);
        i++;
    }

    if (purged_any) gc_freelist_rebuild();
    gc.live_bytes = gc.total_allocated;
}

/* ===== Collection ===== */
static int gc_in_collect = 0;

static void gc_collect_locked_impl(void) {
    /* Caller holds the GC lock. Roots are: the explicit root array, the
       registered scan regions, this thread's stack (conservative, from the
       collector's frame up to the stack top) and the callee-saved registers
       spilled by setjmp. */
    gc_capture_stack_bounds();
    char probe;
    if (!gc.stack_top || &probe > gc.stack_top) {
        /* Unknown bounds, or bounds captured on a different thread (mismatch
           would make the scan cross unmapped memory). Refuse: a partial scan
           could free live objects. */
        static int warned = 0;
        if (!warned) {
            warned = 1;
            fprintf(stderr, "Leash GC: cannot determine stack bounds; "
                            "automatic collection disabled\n");
        }
        gc.auto_collect = 0;
        return;
    }
    unsigned long long t0 = gc_now_ns();
    gc_in_collect = 1;
    jmp_buf regs;
    int sj = setjmp(regs); /* spill callee-saved registers into this frame */
    (void)sj;
    gc_mark_words(regs, sizeof(regs)); /* redundant with the stack scan, kept
                                          so the frame's contents are marked
                                          even if scanning changes */
    gc_mark_roots();
    gc_mark_stack();
    gc_mark_drain();
    gc_sweep();
    gc.bytes_since_gc = 0;
    gc.collect_count++;
    gc.gc_time_ns += gc_now_ns() - t0;
    gc_in_collect = 0;
}

/* ===== Auto-collection gating ===== */
static int gc_quiescent(void) {
    if (gc_atomic_load(&gc_active_workers) != 0) return 0;
    if (gc_atomic_load(&gc_foreign_threads) != 0) return 0;
    return 1;
}

static int gc_should_auto_collect(void) {
    if (!gc.auto_collect || gc_in_collect) return 0;
    if (!gc_quiescent()) return 0;
    size_t threshold = gc.live_bytes * 2;
    if (threshold < (size_t)gc.threshold_floor) threshold = (size_t)gc.threshold_floor;
    return gc.bytes_since_gc > threshold;
}

/* ===== String/Vector helpers ===== */
void* leash_gc_alloc_string(size_t len) {
    if (len > SIZE_MAX - 1) {
        fprintf(stderr, "Leash GC: string allocation too large!\n");
        abort();
    }
    /* Strings never hold pointers: ATOMIC lets the collector skip them. */
    return leash_gc_malloc_ex(len + 1, FLAG_ATOMIC);
}

void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) {
    if (elem_size != 0 && capacity > SIZE_MAX / elem_size) {
        fprintf(stderr, "Leash GC: vector allocation too large!\n");
        abort();
    }
    /* Not ATOMIC: vectors of objects hold pointers the collector must see. */
    return leash_gc_malloc(elem_size * capacity);
}

/* ===== Public collection entry ===== */
void leash_gc_collect(void) {
    GC_LOCK();
    if (!gc_quiescent()) {
        static int warned = 0;
        if (!warned) {
            warned = 1;
            fprintf(stderr, "Leash GC: collect skipped (worker threads active)\n");
        }
        GC_UNLOCK();
        return;
    }
    gc_collect_locked_impl();
    GC_UNLOCK();
}

/* ===== Lifecycle ===== */
static int gc_inited = 0;

void leash_gc_init(void) {
    /* Called from main() before any threads are spawned. Idempotent. */
    if (gc_inited) return;
    gc_inited = 1;

#ifdef _WIN32
    InitializeCriticalSection(&gc_mutex);
    InterlockedExchange(&gc_mutex_ready, 1);
    gc_main_thread_id = GetCurrentThreadId();
#else
    gc_main_thread = pthread_self();
#endif

    gc.threshold_floor = INITIAL_THRESHOLD;
    gc.auto_collect = 1;

    /* Page size and slab geometry: slabs are whole multiples of the page. */
#if defined(_WIN32)
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    size_t page = (size_t)si.dwPageSize;
#else
    long ps = sysconf(_SC_PAGESIZE);
    size_t page = ps > 0 ? (size_t)ps : 4096;
#endif
    if (page > 0) gc_page_size = page;
    size_t map = SLAB_NOMINAL_SIZE;
    if (map < gc_page_size) map = gc_page_size;
    map = ((map + gc_page_size - 1) / gc_page_size) * gc_page_size;
    gc_slab_map = map;

    const char* env = getenv("LEASH_GC_AUTO");
    if (env && (*env == '0' || *env == 'n' || *env == 'N' ||
                *env == 'f' || *env == 'F')) {
        gc.auto_collect = 0;
    }
    env = getenv("LEASH_GC_STATS");
    if (env && *env && *env != '0') {
        atexit(leash_gc_print_stats);
    }
}

void leash_gc_shutdown(void) {
    GC_LOCK();
    struct gc_slab* s = gc.slabs;
    while (s) {
        struct gc_slab* next = s->next;
        gc_page_free(s->base, s->map_size);
        s = next;
    }
    gc.slabs = NULL;
    for (uint32_t i = 0; i < NUM_CLASSES; i++) {
        gc.class_slabs[i] = NULL;
        gc.class_bump[i] = NULL;
        gc.class_freelists[i] = NULL;
    }
    for (size_t i = 0; i < gc.large_count; i++) free(gc.larges[i].raw);
    free(gc.larges);
    gc.larges = NULL;
    gc.large_count = gc.large_cap = 0;
    free(gc.page_dir);
    gc.page_dir = NULL;
    gc.page_dir_cap = gc.page_dir_count = 0;
    free(gc.roots);
    gc.roots = NULL;
    gc.root_count = gc.root_capacity = 0;
    free(gc.regions);
    gc.regions = NULL;
    gc.region_count = gc.region_capacity = 0;
    free(gc.worklist);
    gc.worklist = NULL;
    gc.worklist_count = gc.worklist_cap = 0;
    gc.total_allocated = 0;
    gc.object_count = 0;
    gc.bytes_since_gc = 0;
    gc.live_bytes = 0;
    GC_UNLOCK();
}

/* ===== Statistics ===== */
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
    size_t slab_bytes = 0;
    size_t slab_count = 0;
    for (struct gc_slab* s = gc.slabs; s; s = s->next) {
        slab_bytes += s->map_size;
        slab_count++;
    }
    fprintf(stderr,
        "GC stats: %zu objects, %zu bytes live, %zu bytes since last gc, "
        "%zu slab bytes in %zu slabs, %zu large objects, %zu allocs, "
        "%zu collections, %.3f ms in gc\n",
        gc.object_count, gc.total_allocated, gc.bytes_since_gc,
        slab_bytes, slab_count, gc.large_count, gc.alloc_count,
        gc.collect_count, gc.gc_time_ns / 1000000.0);
    GC_UNLOCK();
}

void leash_gc_verify(void) {
    GC_LOCK();
    for (struct gc_slab* s = gc.slabs; s; s = s->next) {
        uint32_t live = 0;
        for (char* p = s->cells; p < s->bump; p += s->cell_size) {
            const struct gc_cell* c = (const struct gc_cell*)p;
            if (!(c->flags & CELL_IN_USE)) continue;
            live++;
            if (p + CELL_HDR + c->size > s->end) {
                fprintf(stderr, "GC verify: cell payload exceeds slab bounds\n");
                break;
            }
        }
        if (live != s->live) {
            fprintf(stderr, "GC verify: slab live count mismatch (%u vs %u)\n",
                    live, s->live);
        }
    }
    for (size_t i = 0; i < gc.large_count; i++) {
        const struct gc_cell* c =
            (const struct gc_cell*)(gc.larges[i].payload - CELL_HDR);
        if (!(c->flags & CELL_IN_USE)) {
            fprintf(stderr, "GC verify: large object not in use\n");
        }
        if (c->size != gc.larges[i].size) {
            fprintf(stderr, "GC verify: large object size mismatch\n");
        }
        if (i > 0 && gc.larges[i - 1].payload >= gc.larges[i].payload) {
            fprintf(stderr, "GC verify: large object table not sorted\n");
        }
    }
    GC_UNLOCK();
}

#endif /* NO_GC */


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
    for (; i < n; i++) res[i] = (b[i] == 0) ? 0 : (a[i] / b[i]);
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
    for (; i < n; i++) res[i] = (b[i] == 0) ? 0 : (a[i] / b[i]);
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
 * Thread pool with static worker re-use. Task hand-off is a mutex +
 * condition variable state machine (0 = idle/done, 1 = ready, 2 = busy,
 * 3 = shutdown) — the old spin loops (sched_yield/Sleep(0)) burned a core
 * per worker per operation.
 *
 * The dispatcher brackets each operation with leash_gc_worker_begin/end:
 * the pool threads dereference GC-owned operand/result buffers while the
 * op runs, so automatic collection must stay away for the duration.
 */

static void sequential_matrix_op(void* res, const void* a, const void* b,
                                 int64_t n, int op, int elem_size, int is_int) {
    if (is_int) {
        if (elem_size == 4) leash_matrix_binary_op_int32((int32_t*)res, (const int32_t*)a, (const int32_t*)b, n, op);
        else leash_matrix_binary_op_int64((int64_t*)res, (const int64_t*)a, (const int64_t*)b, n, op);
    } else {
        if (elem_size == 4) leash_matrix_binary_op_float((float*)res, (const float*)a, (const float*)b, n, op);
        else leash_matrix_binary_op_double((double*)res, (const double*)a, (const double*)b, n, op);
    }
}

#if defined(_WIN32)

#define MAX_POOL_THREADS 64

typedef struct {
    void* res;
    const void* a;
    const void* b;
    int64_t start;
    int64_t end;
    int op;
    int elem_size;
    int is_int;
    int state; /* 0 = idle/done, 1 = ready, 2 = busy, 3 = shutdown */
} thread_task;

static thread_task g_tasks[MAX_POOL_THREADS];
static CRITICAL_SECTION g_pool_cs;
static CRITICAL_SECTION g_dispatch_cs;
static CONDITION_VARIABLE g_pool_cv;
static INIT_ONCE g_pool_once = INIT_ONCE_STATIC_INIT;
static int g_pool_initialized = 0;
static int g_num_threads = 0;

static BOOL CALLBACK pool_sync_init(PINIT_ONCE once, PVOID param, PVOID* ctx) {
    (void)once; (void)param; (void)ctx;
    InitializeCriticalSection(&g_pool_cs);
    InitializeCriticalSection(&g_dispatch_cs);
    InitializeConditionVariable(&g_pool_cv);
    return TRUE;
}

static void pool_sync_ready(void) {
    InitOnceExecuteOnce(&g_pool_once, pool_sync_init, NULL, NULL);
}

static DWORD WINAPI pool_worker(LPVOID arg) {
    thread_task* ta = &g_tasks[(intptr_t)arg];
    EnterCriticalSection(&g_pool_cs);
    for (;;) {
        while (ta->state == 0) {
            SleepConditionVariableCS(&g_pool_cv, &g_pool_cs, INFINITE);
        }
        if (ta->state == 3) break; /* shutdown */
        ta->state = 2;             /* busy */
        void* res = ta->res;
        const void* a = ta->a;
        const void* b = ta->b;
        int64_t start = ta->start, end = ta->end;
        int op = ta->op, elem_size = ta->elem_size, is_int = ta->is_int;
        LeaveCriticalSection(&g_pool_cs);

        int64_t n = end - start;
        if (n > 0) {
            if (is_int) {
                if (elem_size == 4) leash_matrix_binary_op_int32((int32_t*)res + start, (const int32_t*)a + start, (const int32_t*)b + start, n, op);
                else leash_matrix_binary_op_int64((int64_t*)res + start, (const int64_t*)a + start, (const int64_t*)b + start, n, op);
            } else {
                if (elem_size == 4) leash_matrix_binary_op_float((float*)res + start, (const float*)a + start, (const float*)b + start, n, op);
                else leash_matrix_binary_op_double((double*)res + start, (const double*)a + start, (const double*)b + start, n, op);
            }
        }

        EnterCriticalSection(&g_pool_cs);
        ta->state = 0;
        WakeAllConditionVariable(&g_pool_cv);
    }
    LeaveCriticalSection(&g_pool_cs);
    return 0;
}

static void init_thread_pool(void) {
    pool_sync_ready();
    EnterCriticalSection(&g_pool_cs);
    if (g_pool_initialized) {
        LeaveCriticalSection(&g_pool_cs);
        return;
    }
    SYSTEM_INFO sysinfo;
    GetSystemInfo(&sysinfo);
    g_num_threads = (int)sysinfo.dwNumberOfProcessors;
    if (g_num_threads < 2) g_num_threads = 2;
    if (g_num_threads > MAX_POOL_THREADS) g_num_threads = MAX_POOL_THREADS;
    /* Pool threads exist now: the GC must take locks on its fast path. */
    leash_gc_thread_spawned();
    for (int i = 0; i < g_num_threads; i++) {
        g_tasks[i].state = 0;
        HANDLE h = CreateThread(NULL, 0, pool_worker, (LPVOID)(intptr_t)i, 0, NULL);
        if (!h) {
            /* Could not spawn the full pool: keep the threads we did start
               idle and fall back to sequential execution. */
            g_num_threads = i;
            break;
        }
        CloseHandle(h);
    }
    g_pool_initialized = 1;
    LeaveCriticalSection(&g_pool_cs);
}

/* Static scheduling with adaptive chunking */
static void parallel_dispatch(void* res, const void* a, const void* b,
                              int64_t n, int op, int elem_size, int is_int)
{
    if (!g_pool_initialized) init_thread_pool();
    int num_workers = g_num_threads;
    if (num_workers < 2 || n < 1024) {
        sequential_matrix_op(res, a, b, n, op, elem_size, is_int);
        return;
    }
    /* Whole-dispatch ownership: a second dispatcher (Leash worker thread)
       blocks here instead of clobbering the task array mid-flight. */
    leash_gc_worker_begin();
    EnterCriticalSection(&g_dispatch_cs);
    EnterCriticalSection(&g_pool_cs);
    int64_t chunk = (n + num_workers - 1) / num_workers;
    for (int t = 0; t < num_workers; t++) {
        thread_task* tk = &g_tasks[t];
        tk->res = res;
        tk->a = a;
        tk->b = b;
        tk->start = t * chunk;
        tk->end = (t + 1) * chunk < n ? (t + 1) * chunk : n;
        tk->op = op;
        tk->elem_size = elem_size;
        tk->is_int = is_int;
        tk->state = 1;
    }
    WakeAllConditionVariable(&g_pool_cv);
    /* Worker num_workers-1 already covers [(num_workers-1)*chunk, n); the main
       thread must NOT also process that range or the last chunk is written
       twice. The main thread simply waits for all workers to finish. */
    for (int t = 0; t < num_workers; t++) {
        while (g_tasks[t].state != 0) {
            SleepConditionVariableCS(&g_pool_cv, &g_pool_cs, INFINITE);
        }
    }
    LeaveCriticalSection(&g_pool_cs);
    LeaveCriticalSection(&g_dispatch_cs);
    leash_gc_worker_end();
}

#else /* POSIX - pthreads with thread pool */

#define MAX_POOL_THREADS 64

typedef struct {
    void* res;
    const void* a;
    const void* b;
    int64_t start;
    int64_t end;
    int op;
    int elem_size;
    int is_int;
    int state; /* 0 = idle/done, 1 = ready, 2 = busy, 3 = shutdown */
} thread_task;

static thread_task g_tasks[MAX_POOL_THREADS];
static pthread_t g_threads[MAX_POOL_THREADS];
static int g_pool_initialized = 0;
static int g_num_threads = 0;
static pthread_mutex_t g_pool_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_dispatch_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pool_cond = PTHREAD_COND_INITIALIZER;

static void* pool_worker(void* arg) {
    thread_task* ta = &g_tasks[(intptr_t)arg];
    pthread_mutex_lock(&g_pool_mutex);
    for (;;) {
        while (ta->state == 0) {
            pthread_cond_wait(&g_pool_cond, &g_pool_mutex);
        }
        if (ta->state == 3) break; /* shutdown */
        ta->state = 2;             /* busy */
        /* Task fields copied under the lock: the dispatcher may rewrite
           them as soon as the task is marked done. */
        void* res = ta->res;
        const void* a = ta->a;
        const void* b = ta->b;
        int64_t start = ta->start, end = ta->end;
        int op = ta->op, elem_size = ta->elem_size, is_int = ta->is_int;
        pthread_mutex_unlock(&g_pool_mutex);

        int64_t n = end - start;
        if (n > 0) {
            if (is_int) {
                if (elem_size == 4) leash_matrix_binary_op_int32((int32_t*)res + start, (const int32_t*)a + start, (const int32_t*)b + start, n, op);
                else leash_matrix_binary_op_int64((int64_t*)res + start, (const int64_t*)a + start, (const int64_t*)b + start, n, op);
            } else {
                if (elem_size == 4) leash_matrix_binary_op_float((float*)res + start, (const float*)a + start, (const float*)b + start, n, op);
                else leash_matrix_binary_op_double((double*)res + start, (const double*)a + start, (const double*)b + start, n, op);
            }
        }

        pthread_mutex_lock(&g_pool_mutex);
        ta->state = 0;
        pthread_cond_broadcast(&g_pool_cond);
    }
    pthread_mutex_unlock(&g_pool_mutex);
    return NULL;
}

static void init_thread_pool(void) {
    pthread_mutex_lock(&g_pool_mutex);
    if (g_pool_initialized) {
        pthread_mutex_unlock(&g_pool_mutex);
        return;
    }
    int want = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (want < 2) want = 2;
    if (want > MAX_POOL_THREADS) want = MAX_POOL_THREADS;
    /* Pool threads exist now: the GC must take locks on its fast path.
       Set the flag while still single-threaded (before pthread_create) so
       the first worker is guaranteed to observe it. */
    leash_gc_thread_spawned();
    int started = 0;
    int ok = 1;
    for (int i = 0; i < want; i++) {
        g_tasks[i].state = 0;
        if (pthread_create(&g_threads[i], NULL, pool_worker, (void*)(intptr_t)i) != 0) {
            ok = 0;
            break;
        }
        started++;
    }
    if (ok) {
        g_num_threads = want;
        g_pool_initialized = 1;
        pthread_mutex_unlock(&g_pool_mutex);
        return;
    }
    /* Could not spawn the worker pool: shut down the threads we did start
       and fall back to sequential execution. */
    for (int i = 0; i < started; i++) g_tasks[i].state = 3;
    pthread_cond_broadcast(&g_pool_cond);
    g_num_threads = 0;
    g_pool_initialized = 1; /* sequential mode; never retry */
    pthread_mutex_unlock(&g_pool_mutex);
    for (int i = 0; i < started; i++) pthread_join(g_threads[i], NULL);
}

static void parallel_dispatch(void* res, const void* a, const void* b,
                              int64_t n, int op, int elem_size, int is_int)
{
    if (!g_pool_initialized) init_thread_pool();
    int num_workers = g_num_threads;
    if (num_workers < 2 || n < 1024) {
        sequential_matrix_op(res, a, b, n, op, elem_size, is_int);
        return;
    }
    /* Whole-dispatch ownership: a second dispatcher (Leash worker thread)
       blocks here instead of clobbering the task array mid-flight. */
    leash_gc_worker_begin();
    pthread_mutex_lock(&g_dispatch_mutex);
    pthread_mutex_lock(&g_pool_mutex);
    int64_t chunk = (n + num_workers - 1) / num_workers;
    for (int t = 0; t < num_workers; t++) {
        thread_task* tk = &g_tasks[t];
        tk->res = res;
        tk->a = a;
        tk->b = b;
        tk->start = t * chunk;
        tk->end = (t + 1) * chunk < n ? (t + 1) * chunk : n;
        tk->op = op;
        tk->elem_size = elem_size;
        tk->is_int = is_int;
        tk->state = 1;
    }
    pthread_cond_broadcast(&g_pool_cond);
    /* Worker num_workers-1 already covers [(num_workers-1)*chunk, n); the main
       thread must NOT also process that range or the last chunk is written
       twice. The main thread simply waits for all workers to finish. */
    for (int t = 0; t < num_workers; t++) {
        while (g_tasks[t].state != 0) {
            pthread_cond_wait(&g_pool_cond, &g_pool_mutex);
        }
    }
    pthread_mutex_unlock(&g_pool_mutex);
    pthread_mutex_unlock(&g_dispatch_mutex);
    leash_gc_worker_end();
}

#endif /* _WIN32 / POSIX */

void leash_matrix_parallel_op_float(
    float* res, const float* a, const float* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 4, 0);
}

void leash_matrix_parallel_op_double(
    double* res, const double* a, const double* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 8, 0);
}

void leash_matrix_parallel_op_int32(
    int32_t* res, const int32_t* a, const int32_t* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 4, 1);
}

void leash_matrix_parallel_op_int64(
    int64_t* res, const int64_t* a, const int64_t* b, int64_t n, int op)
{
    parallel_dispatch(res, a, b, n, op, 8, 1);
}


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
    if (n <= 0 || elem_size <= 0) return;
    /* Guard against signed overflow of the byte count. */
    if (n > (int64_t)(SIZE_MAX / (size_t)elem_size)) {
        fprintf(stderr, "Leash: vector copy too large!\n");
        abort();
    }
    memcpy(dst, src, (size_t)(n * elem_size));
}

void leash_vec_reverse(void* data, int64_t size, int64_t elem_size) {
    /* Optimization 16: In-place vector reverse.
       Note: the swap scratch buffer must hold a full element. A fixed 64-byte
       stack buffer would overflow for element sizes > 64 (e.g. vectors of
       large structs), so fall back to a heap buffer when needed. */
    char* d = (char*)data;
    void* tmp;
    char small_tmp[64];
    int use_heap = (elem_size > (int64_t)sizeof(small_tmp));
    if (use_heap) {
        tmp = malloc((size_t)elem_size);
        if (!tmp) return; /* nothing useful we can do on OOM */
    } else {
        tmp = small_tmp;
    }
    int64_t i, j;
    for (i = 0, j = size - 1; i < j; i++, j--) {
        memcpy(tmp, d + i * elem_size, (size_t)elem_size);
        memcpy(d + i * elem_size, d + j * elem_size, (size_t)elem_size);
        memcpy(d + j * elem_size, tmp, (size_t)elem_size);
    }
    if (use_heap) free(tmp);
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

void leash_fast_memcpy(void* restrict dst, const void* restrict src, size_t n) {
    /* The word-copy loop below requires size_t-aligned pointers. If either
       operand isn't aligned, fall back to a plain memcpy (which handles
       arbitrary alignment) instead of invoking undefined behaviour. */
    if (((uintptr_t)dst % sizeof(size_t)) != 0 ||
        ((uintptr_t)src % sizeof(size_t)) != 0) {
        memcpy(dst, src, n);
        return;
    }
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

/* =========================================================================
 * Wide integer formatting (any bit width, 1..512+ bits)
 *
 * The compiler emits little-endian raw byte representations of arbitrary
 * precision integers (LLVM `iN` values stored to stack) and calls:
 *
 *   void leash_bigint_fmt(char *out, const unsigned char *bytes,
 *                         unsigned bitwidth, int is_signed);
 *
 * `out` must have room for at least (bitwidth / 3 + 4) bytes, which covers
 * the maximum decimal digit count of any N-bit integer plus a sign and a
 * NUL terminator. The value is written as a NUL-terminated C string.
 *
 * `bitwidth` is the EXACT integer width: for widths not divisible by 8,
 * LLVM leaves the padding bits above bit (bitwidth-1) undefined, so they
 * are explicitly masked off here.
 * ========================================================================= */

/* Divide the limb array by 10, in place. Returns the remainder digit.
 * Limbs are base-2^64, little-endian order (limbs[0] = least significant).
 * `rem` is always < 10 between iterations, so the 32-bit half-limb path
 * below never overflows. */
static unsigned long long leash_bigint_divmod10(unsigned long long *limbs,
                                                unsigned nlimbs) {
    unsigned long long rem = 0;
    int i;
    for (i = (int)nlimbs - 1; i >= 0; --i) {
#if defined(__SIZEOF_INT128__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpedantic"
        unsigned __int128 cur = ((unsigned __int128)rem << 64) | limbs[i];
        limbs[i] = (unsigned long long)(cur / 10u);
        rem = (unsigned long long)(cur % 10u);
#pragma GCC diagnostic pop
#else
        unsigned long long n = limbs[i];
        unsigned long long hi = (rem << 32) | (n >> 32);
        unsigned long long qh = hi / 10u;
        unsigned long long lo = ((hi % 10u) << 32) | (n & 0xFFFFFFFFULL);
        limbs[i] = (qh << 32) | (lo / 10u);
        rem = lo % 10u;
#endif
    }
    return rem;
}

void leash_bigint_fmt(char *out, const unsigned char *bytes, unsigned bitwidth,
                      int is_signed) {
    unsigned long long limbs[16]; /* supports up to 1024 bits */
    unsigned nbytes, nlimbs, top_bits, i, ndig = 0, j = 0;
    unsigned total_bits;
    int neg = 0;
    char tmp[400];

    if (!out || !bytes || bitwidth == 0) {
        if (out) { out[0] = '0'; out[1] = '\0'; }
        return;
    }
    if (bitwidth > sizeof(limbs) * 8u)
        bitwidth = sizeof(limbs) * 8u; /* hard cap: 1024 bits */

    nbytes = (bitwidth + 7u) / 8u;
    nlimbs = (nbytes + 7u) / 8u;
    for (i = 0; i < nlimbs; ++i) limbs[i] = 0;
    for (i = 0; i < nbytes; ++i)
        limbs[i / 8u] |= (unsigned long long)bytes[i] << ((i % 8u) * 8u);

    total_bits = bitwidth;
    top_bits = total_bits % 64u;
    if (top_bits != 0u)
        limbs[nlimbs - 1u] &= (1ULL << top_bits) - 1ULL;

    if (is_signed) {
        unsigned limb_i = (total_bits - 1u) / 64u;
        unsigned off = (total_bits - 1u) % 64u;
        if ((limbs[limb_i] >> off) & 1ULL) {
            neg = 1;
            /* Two's-complement negate modulo 2^total_bits */
            for (i = 0; i < nlimbs; ++i) limbs[i] = ~limbs[i];
            for (i = 0; i < nlimbs; ++i) {
                limbs[i] += 1ULL;
                if (limbs[i] != 0ULL) break; /* carry absorbed */
            }
            if (top_bits != 0u)
                limbs[nlimbs - 1u] &= (1ULL << top_bits) - 1ULL;
        }
    }

    /* Extract decimal digits by repeated division by 10 */
    for (;;) {
        int zero = 1;
        for (i = 0; i < nlimbs; ++i)
            if (limbs[i] != 0ULL) { zero = 0; break; }
        if (zero && ndig > 0) break;
        tmp[ndig++] = (char)('0' + (int)leash_bigint_divmod10(limbs, nlimbs));
        if (ndig >= sizeof(tmp)) break; /* unreachable safety net */
    }

    if (neg) out[j++] = '-';
    while (ndig > 0) out[j++] = tmp[--ndig];
    out[j] = '\0';
}

/* Parse a decimal string into an arbitrary-width integer.
 * Returns 1 on success, 0 on syntax error or range overflow.
 * The result is written little-endian into `out` (ceil(bitwidth/8) bytes),
 * masked to exactly `bitwidth` bits. Range rules follow two's complement:
 *   unsigned : magnitude must be < 2^bitwidth
 *   signed   : magnitude must be <= 2^(bitwidth-1); equality only with '-' */
int leash_bigint_parse(const char *s, unsigned bitwidth, int is_signed,
                       unsigned char *out) {
    unsigned l32[64]; /* base-2^32 limbs; supports up to 2048 bits */
    unsigned nl32, i;
    int neg = 0, any_digit = 0;

    if (!s || !out || bitwidth == 0) return 0;
    if (bitwidth > sizeof(l32) * 32u) bitwidth = sizeof(l32) * 32u;
    nl32 = (bitwidth + 31u) / 32u;

    for (i = 0; i < nl32; ++i) l32[i] = 0;

    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') ++s;
    if (*s == '+') { ++s; }
    else if (*s == '-') { neg = 1; ++s; }

    while (*s >= '0' && *s <= '9') {
        unsigned carry = (unsigned)(*s - '0');
        any_digit = 1;
        for (i = 0; i < nl32; ++i) {
            unsigned long long cur =
                (unsigned long long)l32[i] * 10ULL + carry;
            l32[i] = (unsigned)cur;
            carry = (unsigned)(cur >> 32);
        }
        if (carry != 0u) return 0; /* exceeded capacity -> out of range */
        ++s;
    }
    if (!any_digit) return 0;
    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') ++s;
    if (*s != '\0') return 0; /* trailing garbage */

    /* Magnitude must fit in bitwidth bits */
    for (i = bitwidth; i < nl32 * 32u; ++i)
        if ((l32[i / 32u] >> (i % 32u)) & 1u) return 0;

    if (is_signed && !neg) {
        /* non-negative: sign bit must be clear */
        if ((l32[(bitwidth - 1u) / 32u] >> ((bitwidth - 1u) % 32u)) & 1u)
            return 0;
    } else if (is_signed && neg) {
        /* negative: magnitude must be <= 2^(bitwidth-1) */
        unsigned hb = (bitwidth - 1u);
        unsigned high_word = l32[hb / 32u], high_bit_off = hb % 32u;
        unsigned masked_high = high_word & ~((1u << high_bit_off) - 1u);
        if (masked_high != 0u) {
            /* equals exactly 2^(bitwidth-1)? then remaining low bits are 0 */
            unsigned j2;
            int rest_zero = ((high_word & ~(1u << high_bit_off)) == 0u);
            if (rest_zero)
                for (j2 = 0; j2 < hb / 32u; ++j2)
                    if (l32[j2] != 0u) { rest_zero = 0; break; }
            if (!rest_zero) return 0;
        }
    }

    /* Write out little-endian */
    {
        unsigned nbytes = (bitwidth + 7u) / 8u;
        if (neg) {
            /* Two's-complement negate modulo 2^bitwidth */
            unsigned long long carry = 1ULL;
            unsigned w;
            for (w = 0; w < nl32; ++w) {
                unsigned long long cur =
                    (unsigned long long)(~l32[w]) + carry;
                l32[w] = (unsigned)cur;
                carry = cur >> 32;
            }
        }
        /* Mask to exact width and store */
        {
            unsigned w;
            for (w = 0; w < nl32; ++w) {
                unsigned base = w * 32u;
                if (base + 32u > bitwidth) {
                    unsigned valid = bitwidth - base;
                    if (valid < 32u)
                        l32[w] &= (valid == 0u) ? 0u : ((1u << valid) - 1u);
                }
            }
        }
        for (i = 0; i < nbytes; ++i)
            out[i] = (unsigned char)((l32[i / 4u] >> ((i % 4u) * 8u)) & 0xFFu);
    }
    return 1;
}


/* ================================================================ */
/* ===== Futures (native async/await) ============================ */
/* ================================================================ */

/*
 * A future is the join handle created by an `async fnc` call. The state
 * block itself is GC-allocated and registered as a root from creation
 * until await, so neither the future nor the worker's boxed result can be
 * swept while the task is in flight (collect() is explicit-only in this
 * runtime, but the root keeps `future->value` reachable for users who do
 * trigger collections concurrently).
 *
 * Layout (non-moving mark-sweep GC — embedding OS primitives is safe):
 *   [ pthread_mutex_t | pthread_cond_t | int done | void* value ]
 * The GC's conservative payload scan may see the mutex/cond words; worst
 * case that falsely retains an object for one cycle — never frees early.
 */

#include <string.h>

#ifndef _WIN32
# include <pthread.h>
#endif

#ifdef _WIN32
typedef struct {
    CRITICAL_SECTION     mu;
    CONDITION_VARIABLE   cv;
    int                  done;
    void*                value;
} leash_future_t;
#else
typedef struct {
    pthread_mutex_t      mu;
    pthread_cond_t       cv;
    int                  done;
    void*                value;
} leash_future_t;
#endif

void* leash_future_new(void) {
    /* Rooted at creation: the handle must be reachable before the caller
       can store it anywhere, so a concurrent collect() cannot sweep it. */
    leash_future_t* f = (leash_future_t*)leash_gc_malloc_rooted(sizeof(leash_future_t));
    if (!f) {
        fprintf(stderr, "Leash: Out of memory (future)\n");
        abort();
    }
#ifdef _WIN32
    InitializeCriticalSection(&f->mu);
    InitializeConditionVariable(&f->cv);
#else
    pthread_mutex_init(&f->mu, NULL);
    pthread_cond_init(&f->cv, NULL);
#endif
    f->done = 0;
    f->value = NULL;
    leash_gc_register_root(f);
    return f;
}

void leash_future_complete(void* fut, void* value) {
    leash_future_t* f = (leash_future_t*)fut;
    if (!f) return;
#ifdef _WIN32
    EnterCriticalSection(&f->mu);
    f->value = value;
    f->done = 1;
    WakeAllConditionVariable(&f->cv);
    LeaveCriticalSection(&f->mu);
#else
    pthread_mutex_lock(&f->mu);
    f->value = value;
    f->done = 1;
    pthread_cond_broadcast(&f->cv);
    pthread_mutex_unlock(&f->mu);
#endif
}

void* leash_future_await(void* fut) {
    leash_future_t* f = (leash_future_t*)fut;
    void* value = NULL;
    if (!f) return NULL;
#ifdef _WIN32
    EnterCriticalSection(&f->mu);
    while (!f->done) {
        SleepConditionVariableCS(&f->cv, &f->mu, INFINITE);
    }
    value = f->value;
    LeaveCriticalSection(&f->mu);
#else
    pthread_mutex_lock(&f->mu);
    while (!f->done) {
        pthread_cond_wait(&f->cv, &f->mu);
    }
    value = f->value;
    pthread_mutex_unlock(&f->mu);
#endif
    /* The future stays rooted: generated code drops the root only after it
       has loaded the result out of the box, so the box cannot be swept in
       between. */
    return value;
}

int leash_future_is_done(void* fut) {
    leash_future_t* f = (leash_future_t*)fut;
    int done = 0;
    if (!f) return 1;
#ifdef _WIN32
    EnterCriticalSection(&f->mu);
    done = f->done;
    LeaveCriticalSection(&f->mu);
#else
    pthread_mutex_lock(&f->mu);
    done = f->done;
    pthread_mutex_unlock(&f->mu);
#endif
    return done;
}
