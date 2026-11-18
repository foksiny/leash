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
#if defined(__linux__) && defined(__GLIBC__)
#include <malloc.h>
#endif

#if defined(_WIN32)
#include <windows.h>
#else
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/wait.h>
#endif

/* Thread-local storage qualifier.
 *
 * MSVC has __declspec(thread); gcc and clang use __thread.
 *
 * The Windows (MinGW) case is not cosmetic: clang's x86_64-w64-mingw32
 * target expands __declspec(x) to __attribute__((x)), and `thread` is
 * not a valid attribute there. Clang warns "unknown attribute 'thread'
 * ignored" and the variable silently becomes a plain global shared by
 * every thread. The generated arena IR references
 * @leasharena_tls_ar as `external thread_local global`, so the runtime
 * definition has to be a real TLS symbol: with a plain global the IR's
 * inline fast paths read a different address than the runtime does,
 * the two arena states desynchronise, and the checkpoint stack is
 * indexed out of bounds (a page fault at cps[-1] on the first frame
 * exit of a worker thread). The same silent downgrade turned the
 * GC's per-thread "did I lock?" guard into a process-wide flag. */
#if defined(_MSC_VER)
#define LEASH_TLS __declspec(thread)
#else
#define LEASH_TLS __thread
#endif

/* ---- showb output-buffer mutex ---------------------------------------
   The module-level showb globals (_leash_showb_buffer/_size/_cap) are
   shared by every thread: unserialised growth lets two workers realloc
   the same base (use-after-realloc / lost stores / heap corruption).
   Implemented as a small atomic spin lock so it works identically in
   full-GC and NO_GC (autofree) builds with no extra link dependencies;
   the critical sections are a few instructions long. */
#ifdef _WIN32
static volatile LONG lsh_showb_spin = 0;
void leash_showb_lock(void) {
    while (InterlockedCompareExchange(&lsh_showb_spin, 1, 0) != 0) {
        Sleep(0);
    }
}
void leash_showb_unlock(void) {
    InterlockedExchange(&lsh_showb_spin, 0);
}
#else
static volatile int lsh_showb_spin = 0;
void leash_showb_lock(void) {
    while (__sync_lock_test_and_set(&lsh_showb_spin, 1)) {
        sched_yield();
    }
}
void leash_showb_unlock(void) {
    __sync_lock_release(&lsh_showb_spin);
}
#endif

/* Refcounted SIGPIPE immunity shared by the native shims (http, httpserver):
 * OpenSSL writes can raise SIGPIPE when the peer already went away, and the
 * disposition is process-global while requests serve on threads. SIG_IGN
 * stays until every holder (any shim, any thread) drops it. */
#ifndef _WIN32
#include <signal.h>
static pthread_mutex_t leash_spipe_mu = PTHREAD_MUTEX_INITIALIZER;
static int leash_spipe_n = 0;
void leash_spipe_hold(void) {
    pthread_mutex_lock(&leash_spipe_mu);
    if (leash_spipe_n == 0) signal(SIGPIPE, SIG_IGN);
    leash_spipe_n++;
    pthread_mutex_unlock(&leash_spipe_mu);
}
void leash_spipe_drop(void) {
    pthread_mutex_lock(&leash_spipe_mu);
    leash_spipe_n--;
    if (leash_spipe_n == 0) signal(SIGPIPE, SIG_DFL);
    pthread_mutex_unlock(&leash_spipe_mu);
}
#else
void leash_spipe_hold(void) {}
void leash_spipe_drop(void) {}
#endif

#if defined(LEASH_ARENA)
/* ===== Scoped arena backend (experimental) =====
 * Thread-local bump allocator over 32 KB blocks.
 *
 * Lifetime: each function (and each instrumented loop) pushes a
 * checkpoint on entry; on exit everything since the checkpoint is
 * freed wholesale by unmapping/frees of the blocks above it plus a
 * bump reset. No per-object free, no scanning, no lock: allocation
 * is align + bump + memset, release is O(blocks).
 *
 * Layout: every payload carries an 8-byte header at ptr-8 holding
 * the requested size (strings keep len at ptr-8 with the total
 * allocation at ptr-16; see leash_gc_alloc_string_cap). Headers are
 * invisible to C callers holding the payload pointer.
 *
 * Safety model: pointers are valid until their allocating frame
 * exits. Local temporaries need no library care (they die with the
 * frame; there is no use-after-free window for locals). Code that
 * outlives the frame escapes explicitly: return values via the
 * leasharena_escape_* helpers (copy into the caller's frame) or
 * program-lifetime data via leasharena_root_alloc. Storing a
 * callee's temporary into a caller-owned aggregate (heap field,
 * global, slice backing) requires the same escape — documented as
 * the experimental mode's escape rule, mirroring autofree's
 * aggregate-escape limitation. */
#define ARENA_TLS LEASH_TLS

#define ARENA_BLOCK_SIZE ((size_t)32 * 1024)
#define ARENA_INIT_BLOCK_SIZE ((size_t)4 * 1024)
#define ARENA_ALIGN ((size_t)16)

/* ===== Arena header layout =====
 *
 * Every arena allocation carries one 8-byte header immediately before its
 * payload, at ptr-8. The high 16 bits are a tag and the low 48 bits are
 * the size/length. Strings use the same slot for their length and put a
 * tagged total one word earlier (see "arena string layout" below); the
 * two tags are disjoint so a generic header can never be mistaken for a
 * string and vice versa.
 *
 * Tagging the GENERIC header is what makes the whole layout safe against
 * interior pointers. Previously ptr-8 held a bare size: an interior
 * pointer whose preceding bytes happened to look like a small integer
 * passed every check, and a realloc could copy from the middle of an
 * unrelated object. Now the tag is validated before the size word is
 * trusted, so a pointer must be a real allocation start to pass.
 *
 * The tag uses bits the 48-bit size ceiling already reserved, so this
 * costs zero bytes per allocation and adds no payload padding. */
#define ARENA_HDR_MAGIC ((uint64_t)0x4C48)        /* 'L','H' */
#define ARENA_HDR_MAGIC_SHIFT 48
#define ARENA_HDR_SIZE_MASK ((uint64_t)0x0000FFFFFFFFFFFFULL)
static inline uint64_t ar_hdr_pack(size_t size) {
    return (ARENA_HDR_MAGIC << ARENA_HDR_MAGIC_SHIFT) |
           ((uint64_t)size & ARENA_HDR_SIZE_MASK);
}
static inline size_t ar_hdr_size(uint64_t raw) {
    return (size_t)(raw & ARENA_HDR_SIZE_MASK);
}
static inline int ar_hdr_valid(uint64_t raw) {
    return (raw >> ARENA_HDR_MAGIC_SHIFT) == ARENA_HDR_MAGIC;
}

/* Upper bound on a single arena allocation. The bump path below adds
 * header/alignment bytes and checks the end with uintptr_t arithmetic; a
 * request near SIZE_MAX wraps that arithmetic into a small address and
 * hands back a tiny buffer (plus a negative-size memset). The 48-bit
 * ceiling is the same one the header's size field and arena_check_payload
 * use; on 32-bit targets half of SIZE_MAX is plenty. */
#if SIZE_MAX > 0xFFFFFFFFULL
#define ARENA_MAX_ALLOC ((size_t)ARENA_HDR_SIZE_MASK)
#else
#define ARENA_MAX_ALLOC (SIZE_MAX / 2)
#endif

typedef struct arena_block {
    struct arena_block* next; /* all blocks, newest first */
    char* base;               /* start of the region usable for payloads */
    size_t cap;               /* usable bytes after the header */
    int sole;                 /* one dedicated large allocation fills the block */
} arena_block;

typedef struct arena_cp {
    arena_block* block;
    char* off;      /* bump offset inside block->base at checkpoint */
    char* end;      /* block end at checkpoint (inline exit restores it) */
    size_t bytes;   /* cumulative allocated bytes at checkpoint (stats) */
} arena_cp;

typedef struct arena_stack {
    arena_block* head;   /* newest block */
    char* cur;           /* bump pointer */
    char* end;           /* end of the newest block's usable area */
    arena_cp* cps;       /* checkpoint stack (malloc'd, grows) */
    size_t cp_len;
    size_t cp_cap;
    size_t bytes;        /* total payload bytes currently live (stats) */
} arena_stack;

ARENA_TLS arena_stack leasharena_tls_ar; /* named for IR fast paths (see gc.h) */
static ARENA_TLS arena_stack tls_root;
static ARENA_TLS int leasharena_tls_arena_dead; /* set by shutdown: further allocs abort */
static ARENA_TLS size_t arena_live_block_bytes;
static ARENA_TLS size_t arena_peak_block_bytes;
static ARENA_TLS size_t arena_root_peak_block_bytes;
/* Last arena block used by a string lookup. Stores raw bounds rather than
 * a block pointer so a freed block is safe to compare against. Cleared by
 * every free path that can invalidate it. */
static ARENA_TLS const char* arena_str_cache_base;
static ARENA_TLS const char* arena_str_cache_end;

/* Registered pointer-bearing global/static slots. The GC uses these as
 * conservative roots; the arena uses them as a safety check: no such slot
 * may point into a frame that is about to be rewound. */
#define ARENA_SCAN_REGION_MAX 512
typedef struct arena_scan_region {
    void* start;
    size_t nbytes;
} arena_scan_region;
static arena_scan_region arena_scan_regions[ARENA_SCAN_REGION_MAX];
static int arena_scan_region_count;
static int arena_verify_roots = 1;

/* ---- spawn transfer ---------------------------------------------
 *
 * A spawn argument block outlives the frame that built it: the
 * worker reads it after the spawner's frame is gone. The GC reclaims
 * the block when the worker is done with it. The arena has no
 * collector, so a block (and the copies of the heap arguments) would
 * stay resident for the rest of the program: a program that spawns
 * many workers would grow the root arena without bound.
 *
 * The transfer is a small private allocation: the argument block and
 * the copies of the string arguments. The worker copies what it
 * needs into its own frame and releases the transfer right away, so
 * the transfer lives only as long as the hand-off -- not as long as
 * the worker.
 */
#define LEASH_XFER_MAGIC ((uint64_t)0x53504658u) /* 'SPFX' */
/* A transfer string carries its byte length in an 8-byte prefix so the
 * worker's adopt step keeps embedded NULs. Leash strings are length-
 * prefixed and may contain '\0'; `strlen` on the receiving side would
 * silently truncate binary arguments. Layout: [u64 magic|len][bytes][NUL]. */
#define LEASH_XFER_STR_MAGIC ((uint32_t)0x58535452u) /* 'XSTR' */

typedef struct leash_xfer {
    uint64_t magic;
    arena_stack st;          /* private arena for copied arguments */
    struct leash_xfer* next;  /* transfers adopted by this thread */
} leash_xfer;

static ARENA_TLS leash_xfer* tls_xfers;
static void xfer_free_all(void);

/* Defined further down with the arena string layout; needed by
 * leash_gc_realloc and sole-block string growth. */
void* leash_gc_alloc_string_cap_in(arena_stack* st, size_t len, size_t cap);
static uint64_t ar_str_pack_total(size_t total);
static void ar_str_require(const char* s, const char* region_end,
                           const char* what, uint64_t* out_total,
                           uint64_t* out_len);

/* Release the per-thread stage cache (defined with the escape
 * helpers); called when the thread's arena goes away. */
static void arena_stage_cache_release(void);

/* Arena string header tag (see the "arena string layout" section
 * below). Declared up here because the tip-growth helper uses it to
 * tell a string payload apart from an ordinary arena allocation. */
#define ARENA_STR_MAGIC ((uint64_t)0x4C53)            /* 'L','S' */
#define ARENA_STR_MAGIC_SHIFT 48
#define ARENA_STR_TOTAL_MASK ((uint64_t)0x0000FFFFFFFFFFFFULL)
#define ARENA_STR_LEN_MASK ((uint64_t)0x7FFFFFFFFFFFFFFFULL)
/* Bit 63 of the length word marks a string that another variable,
 * field, container or call frame can still reach. A shared string is
 * never mutated in place: append allocates a fresh buffer instead. */
#define ARENA_STR_SHARED_BIT (1ULL << 63)

/* Optional use-after-free detector.
 *
 * The arena reclaims a scope's memory by rewinding the bump pointer, so
 * a stale pointer into that region keeps reading whatever lands there
 * next — the same class of bug a garbage collector rules out by never
 * freeing live memory. With LEASH_ARENA_POISON=1 every reclaimed byte is
 * filled with 0xDD, so a stale read shows up as obvious garbage instead
 * of silently wrong data. Off by default: the fill costs a memset per
 * scope exit. */
#define ARENA_POISON_BYTE ((unsigned char)0xDD)
static int leash_arena_poison;
/* Exported so generated code can take the same decision the runtime
 * does: the inlined arena pop fast path must not silently skip the
 * reclaim fill when poisoning is enabled (a stale read into a dead
 * frame has to show up as 0xDD, not as whatever lands there next). */
int leash_arena_poison_on = 1;

static void arena_poison_range(char* from, char* to) {
    if (!leash_arena_poison) return;
    if (!from || !to || to <= from) return;
    memset(from, ARENA_POISON_BYTE, (size_t)(to - from));
}

static inline void arena_block_stats_add(size_t bytes) {
    arena_live_block_bytes += bytes;
    if (arena_live_block_bytes > arena_peak_block_bytes) arena_peak_block_bytes = arena_live_block_bytes;
}
static inline void arena_block_stats_sub(size_t bytes) {
    if (arena_live_block_bytes >= bytes) arena_live_block_bytes -= bytes;
}

static void arena_str_cache_set(const arena_block* b) {
    if (!b) return;
    arena_str_cache_base = b->base;
    arena_str_cache_end = b->base + b->cap;
}

static void arena_str_cache_forget(const arena_block* b) {
    if (!b) return;
    if (arena_str_cache_base == b->base) {
        arena_str_cache_base = NULL;
        arena_str_cache_end = NULL;
    }
}

static void arena_oom(const char* what) {
    fprintf(stderr, "Leash arena: out of memory allocating %s\n", what);
    abort();
}


static arena_block* arena_new_block_sized(arena_stack* st, size_t cap, int sole) {
    size_t total = sizeof(arena_block) + cap;
    arena_block* b = (arena_block*)malloc(total);
    if (!b) arena_oom("block");
    b->next = st->head;
    b->base = (char*)(b + 1);
    b->cap = cap;
    b->sole = sole;
    st->head = b;
    st->cur = b->base;
    st->end = b->base + cap;
    arena_block_stats_add(total);
    if (st == &tls_root && total > arena_root_peak_block_bytes) {
        arena_root_peak_block_bytes = total;
    }
    return b;
}

static arena_block* arena_new_block(arena_stack* st, size_t need) {
    /* `need == 0` is the lazy initial block: start small so programs
     * that allocate little (or never) do not carry a full 32 KB block
     * in their resident set. Growth blocks keep the normal 32 KB size. */
    size_t cap = need == 0 ? ARENA_INIT_BLOCK_SIZE : (need > ARENA_BLOCK_SIZE ? need : ARENA_BLOCK_SIZE);
    return arena_new_block_sized(st, cap, need > ARENA_BLOCK_SIZE);
}

static void arena_ensure_stack(arena_stack* st) {
    if (!st->cps) {
        st->cp_cap = 32;
        st->cps = (arena_cp*)calloc(st->cp_cap, sizeof(*st->cps));
        if (!st->cps) arena_oom("checkpoints");
    }
    if (!st->head) arena_new_block(st, 0);
}

/* Release every block and the checkpoint array of an arena. Used by the
 * program shutdown (main thread) and by the worker-thread exit hook. */
static void arena_free_stack(arena_stack* st) {
    arena_block* b = st->head;
    while (b) {
        arena_block* n = b->next;
        arena_str_cache_forget(b);
        free(b);
        b = n;
    }
    st->head = NULL; st->cur = NULL; st->end = NULL;
    st->cp_len = 0; st->bytes = 0;
    free(st->cps);
    st->cps = NULL; st->cp_cap = 0;
    /* The stage cache belongs to the thread, not to one arena: the
     * first arena to go away releases it. */
    arena_stage_cache_release();
}

/* Bump a `need`-byte raw region. `align` is the payload alignment;
 * the (possibly padded) total is returned via *out_total so arena
 * strings can recover `cap`. */
static char* arena_raw_ex(arena_stack* st, size_t need, size_t align, size_t* out_total, int zero) {
    if (__builtin_expect(leasharena_tls_arena_dead != 0, 0)) {
        fprintf(stderr, "Leash arena: allocation after shutdown\n");
        abort();
    }
    /* Reject sizes that would wrap the pointer arithmetic below. Without
     * this, a near-SIZE_MAX request passes the bounds check with a wrapped
     * address, returns a pointer into an ordinary small block, and memsets
     * a wraparound (negative) length. Fail deterministically, like every
     * other arena OOM. */
    if (__builtin_expect(need > ARENA_MAX_ALLOC, 0)) {
        fprintf(stderr, "Leash arena: allocation too large (%zu bytes)\n", need);
        abort();
    }
    /* The alignment is used as `(x + align - 1) & ~(align - 1)`, which
     * only aligns for a power of two. aligned_alloc's contract requires
     * one, but a bad C/FFI caller must not get a misaligned payload (and a
     * misaligned 8-byte header store) instead of a diagnostic. */
    if (__builtin_expect(align == 0 || (align & (align - 1)) != 0, 0)) {
        fprintf(stderr, "Leash arena: invalid allocation alignment (%zu)\n", align);
        abort();
    }
    /* Fast path: avoid repeated ensure_stack checks after init */
    if (__builtin_expect(st->head != NULL, 1)) {
        /* align is a power of two >= 8, so the payload lands aligned and
         * the 8-byte header before it stays 8-aligned for the u64 store. */
        size_t head_room = 8;
        uintptr_t cur = (uintptr_t)st->cur;
        uintptr_t base = (cur + head_room + align - 1) & ~(uintptr_t)(align - 1);
        uintptr_t hdr = base - head_room;
        if (__builtin_expect(hdr >= cur && (uintptr_t)st->end >= hdr + head_room + need, 1)) {
            char* payload = (char*)hdr + head_room;
            size_t total = (size_t)((char*)payload - (char*)hdr) + need;
            st->cur = (char*)(((uintptr_t)((char*)hdr + total) + 7) & ~(uintptr_t)7);
            if (out_total) *out_total = total;
            if (zero) memset(payload, 0, need);
            return payload;
        }
    }
    arena_ensure_stack(st);
    size_t head_room = 8;
    int fresh = 0;
    for (;;) {
        uintptr_t cur = (uintptr_t)st->cur;
        uintptr_t base = (cur + head_room + align - 1) & ~(uintptr_t)(align - 1);
        uintptr_t hdr = base - head_room;
        if (hdr >= cur && (uintptr_t)st->end >= hdr + head_room + need) {
            char* hdr_ptr = (char*)hdr;
            char* payload = hdr_ptr + head_room;
            size_t total = (size_t)((char*)payload - (char*)hdr) + need;
            st->cur = (char*)hdr + total;
            /* Keep the bump 8-aligned for the next header store. */
            st->cur = (char*)(((uintptr_t)st->cur + 7) & ~(uintptr_t)7);
            /* A freshly created oversize block holds exactly one
             * allocation: consume its tail (< page slack) so no later
             * bump can share it and it stays reclaimable as a whole. */
            if (fresh && st->head && st->head->sole) st->cur = st->end;
            if (out_total) *out_total = total;
            if (zero) memset(payload, 0, need);
            return payload;
        }
        size_t want = need + head_room + 2 * align + 8;
        arena_new_block(st, want);
        fresh = 1;
    }
}

static char* arena_raw(arena_stack* st, size_t need, size_t align, size_t* out_total) {
    return arena_raw_ex(st, need, align, out_total, 1);
}

static void* arena_alloc_ex(arena_stack* st, size_t need, size_t align, int zero) {
    if (need == 0) return NULL;
    size_t total = 0;
    char* payload = arena_raw_ex(st, need, align, &total, zero);
    *(uint64_t*)(void*)(payload - 8) = ar_hdr_pack(need);
    st->bytes += need;
    return (void*)payload;
}

/* Bump-allocate a `need`-byte payload (zero-filled) and store `need`
 * in the 8-byte header at ptr-8. Returns the payload pointer. */
static void* arena_alloc(arena_stack* st, size_t need, size_t align) {
    return arena_alloc_ex(st, need, align, 1);
}

/* Same, but skips the payload zero-fill. Only valid when the caller
 * is guaranteed to initialize every byte it may read (string bodies
 * are memcpy'd + NUL-terminated, hash nodes are fully assigned,
 * clone payloads are memcpy'd, vec bodies are element-by-element
 * until first read). Bucket arrays and any structure read before
 * written must stay on the zero path. */
static void* arena_alloc_nz(arena_stack* st, size_t need, size_t align) {
    return arena_alloc_ex(st, need, align, 0);
}

static void* arena_root_alloc(arena_stack* st, size_t need, size_t align) {
    return arena_alloc(st, need, align);
}

/* Find whether `ptr` (payload) belongs to `st`'s blocks. */
static int arena_owns(arena_stack* st, const void* ptr) {
    if (!ptr) return 0;
    const char* p = (const char*)ptr;
    /* Hot path: the pointer is almost always in the newest block. */
    arena_block* h = st->head;
    if (h && p >= h->base && p < h->base + h->cap) return 1;
    for (arena_block* b = h ? h->next : NULL; b; b = b->next) {
        if (p >= b->base && p < b->base + b->cap) return 1;
    }
    return 0;
}

static arena_block* arena_block_of(arena_stack* st, const void* ptr) {
    if (!ptr) return NULL;
    const char* p = (const char*)ptr;
    /* Hot path: the newest block holds nearly every live allocation. */
    arena_block* h = st->head;
    if (h && p >= h->base && p < h->base + h->cap) return h;
    for (arena_block* b = h ? h->next : NULL; b; b = b->next) {
        if (p >= b->base && p < b->base + b->cap) return b;
    }
    return NULL;
}

/* Validate that `ptr` is an allocation START (not an interior address)
 * and that its header is coherent, before any code reads the size word
 * or copies bytes out of the payload. `arena_owns`/`arena_block_of`
 * only prove the address lies inside some block, which an interior
 * pointer also satisfies: trusting the word at ptr-8 then reads
 * whatever payload bytes happen to sit there.
 *
 * The check is now structural, not heuristic. Every generic allocation
 * writes a tagged header at ptr-8 (see "Arena header layout"): the tag
 * must match, the header must lie inside the block (so ptr-8 is not the
 * block's own metadata), and the size must fit in the block. A user-
 * writable payload can still forge the tag only by deliberately copying
 * it from another header, but an accidental interior offset is rejected
 * deterministically. Returns the validated payload size. */
static size_t arena_check_payload(arena_stack* st, const void* ptr, const char* what) {
    arena_block* b = arena_block_of(st, ptr);
    if (!b) {
        fprintf(stderr, "Leash arena: %s: pointer is not in this thread's arena\n", what);
        abort();
    }
    const char* p = (const char*)ptr;
    if (p - 8 < b->base) {
        fprintf(stderr, "Leash arena: %s: not an allocation start (header outside block)\n", what);
        abort();
    }
    uint64_t raw = *(const uint64_t*)(const void*)(p - 8);
    if (!ar_hdr_valid(raw)) {
        fprintf(stderr, "Leash arena: %s: not an allocation start (bad header tag)\n", what);
        abort();
    }
    uint64_t sz = ar_hdr_size(raw);
    if (sz > (uint64_t)b->cap || p + sz > b->base + b->cap) {
        fprintf(stderr, "Leash arena: %s: not an allocation start (bad size header)\n", what);
        abort();
    }
    return (size_t)sz;
}

/* Reclaim a superseded large allocation's block immediately. Blocks
 * flagged `sole` hold exactly one allocation (a dedicated oversize
 * block whose tail was consumed), so once that allocation is dead —
 * the caller has copied its bytes elsewhere — the whole block can go.
 * Skipped when the newest block still points at it (growth alloc
 * never lands here by construction, but guard anyway) or when any
 * saved checkpoint references it as its rewind block (a scope below
 * would otherwise restore through freed memory). */
static void arena_try_free_solo(arena_stack* st, const void* ptr, size_t sub_bytes) {
    arena_block* b = arena_block_of(st, ptr);
    if (!b || !b->sole) return;
    if (st->head == b) return;
    for (size_t i = 0; i < st->cp_len; i++) {
        if (st->cps[i].block == b) return;
    }
    arena_block** link = &st->head;
    while (*link && *link != b) link = &(*link)->next;
    if (!*link) return;
    *link = b->next;
    size_t bsz = sizeof(*b) + b->cap;
    arena_block_stats_sub(bsz);
    arena_str_cache_forget(b);
    free(b);
    if (st->bytes >= sub_bytes) st->bytes -= sub_bytes;
}

void leasharena_try_free_solo(void* ptr, size_t old_size_bytes) {
    arena_try_free_solo(&leasharena_tls_ar, ptr, old_size_bytes);
}

/* Grow an arena allocation in place when it still sits at the arena's
 * bump tip. The old buffer is then extended instead of being copied
 * into a fresh allocation: the pointer stays valid (callers may have
 * cached it) and the superseded bytes never accumulate in an arena that
 * never rewinds (the root arena).
 *
 * `old_bytes` is the caller's authoritative record of the current
 * allocation size; it is cross-checked against the arena's size header
 * so a pointer that is not a plain arena payload (an arena string, say,
 * which carries a different header) is never grown in place.
 *
 * Returns the same pointer on success, NULL when the object is not at
 * the tip or the current block cannot hold the larger size (the caller
 * falls back to allocate + copy). */
static void* arena_grow_tip(arena_stack* st, void* ptr,
                            size_t old_bytes, size_t new_bytes) {
    if (!ptr || new_bytes == 0 || new_bytes > ARENA_MAX_ALLOC) return NULL;
    arena_block* b = arena_block_of(st, ptr);
    if (!b || (char*)ptr - 8 < b->base) return NULL;
    char* p = (char*)ptr;
    /* An arena string's payload sits behind a 16-byte header; its
     * trailing word is the length, not an allocation size. Only read
     * that word when it is actually inside the owning block. */
    if (p - 16 >= b->base) {
        uint64_t raw = *(const uint64_t*)(const void*)(p - 16);
        if ((raw >> ARENA_STR_MAGIC_SHIFT) == ARENA_STR_MAGIC) return NULL;
    }
    /* Cross-check the caller's size against a TAGGED arena header: a
     * bare payload word at ptr-8 cannot stand in for a real allocation. */
    uint64_t hdr = *(const uint64_t*)(const void*)(p - 8);
    if (!ar_hdr_valid(hdr) || ar_hdr_size(hdr) != old_bytes) return NULL;
    size_t old_adv = (old_bytes + 7) & ~(size_t)7;
    /* Tip test: the old allocation ends exactly at the bump pointer. */
    if (p + old_adv != st->cur) return NULL;
    size_t new_adv = (new_bytes + 7) & ~(size_t)7;
    if (new_adv <= old_adv) return NULL;
    size_t delta = new_adv - old_adv;
    if ((size_t)(st->end - st->cur) < delta) return NULL;
    st->cur += delta;
    st->bytes += delta;
    *(uint64_t*)(void*)(p - 8) = ar_hdr_pack(new_bytes);
    return p;
}

/* Grow an arena string whose buffer occupies a dedicated sole block.
 * Strings carry a tagged total header at s-16 and an 8-byte allocation
 * header before that, so the generic arena_grow_tip cannot touch them.
 * Reallocating the block keeps the same payload address when possible and
 * avoids allocating/copying a whole second buffer for large appends. */
static char* arena_grow_sole_string(arena_stack* st, char* s,
                                    size_t old_total, size_t new_total) {
    if (!st || !s || new_total <= old_total || new_total > ARENA_MAX_ALLOC) return NULL;
    char* alloc_base = s - 16;
    arena_block* b = arena_block_of(st, alloc_base);
    if (!b || !b->sole || b != st->head || st->cur != st->end) return NULL;
    /* The string header must be inside the block and the whole old
     * allocation must fit in it. Without this a stale/interior `s`
     * whose header lands in a sole block could rebase the block at an
     * arbitrary offset and write a new header outside the allocation. */
    if (alloc_base < b->base) return NULL;
    size_t off = (size_t)(alloc_base - b->base);
    if (off > b->cap || old_total > b->cap - off) return NULL;
    for (size_t i = 0; i < st->cp_len; i++) {
        if (st->cps[i].block == b) return NULL;
    }
    size_t new_cap;
    /* Round the new capacity up to a page without overflowing; the
     * rounded value must still cover the grown allocation. */
    if (off > b->cap || new_total > b->cap - off + 4095) return NULL;
    new_cap = (off + new_total + 4095) & ~(size_t)4095;
    if (new_cap < off + new_total) return NULL;
    size_t old_cap = b->cap;
    arena_str_cache_forget(b);
    arena_block* nb = (arena_block*)realloc(b, sizeof(*nb) + new_cap);
    if (!nb) return NULL;
    nb->base = (char*)(nb + 1);
    nb->cap = new_cap;
    arena_str_cache_set(nb);
    char* out = nb->base + off + 16;
    *(uint64_t*)(void*)(out - 16) = ar_str_pack_total(new_total);
    st->head = nb;
    st->cur = nb->base + new_cap;
    st->end = st->cur;
    if (new_cap >= old_cap) arena_block_stats_add(new_cap - old_cap);
    else arena_block_stats_sub(old_cap - new_cap);
    st->bytes += new_total - old_total;
    return out;
}

/* Keep an in-place growth from straddling a checkpoint.
 *
 * A checkpoint rewinds the bump pointer to the offset it recorded. An
 * allocation that was created BEFORE the checkpoint but is grown past it
 * would be cut in half by that rewind: the object still points at the
 * buffer, but the buffer's tail has been reclaimed (and poisoned). The
 * compiler's loop analysis normally keeps such loops uncheckpointed,
 * but the runtime must not depend on that analysis being exhaustive.
 *
 * The fix keeps the extension alive: every checkpoint whose offset lies
 * inside the grown allocation moves its rewind target past the
 * allocation's new end. The bytes between the old offset and the new end
 * stay reserved (the allocation's tail, plus anything the checkpoint
 * would have reclaimed) - conservative, but never a dangling pointer. */
static void arena_protect_extension(arena_stack* st, arena_block* b,
                                    const char* start, const char* end) {
    if (!st || !b || !start || !end || end <= start) return;
    for (size_t i = st->cp_len; i-- > 0;) {
        arena_cp* cp = &st->cps[i];
        if (cp->block != b) continue;
        /* The allocation predates the checkpoint (it starts before the
         * checkpoint's offset) and now reaches past it: the rewind
         * would truncate it. */
        if (cp->off > start && cp->off < end) {
            size_t d = (size_t)(end - cp->off);
            cp->off = (char*)end;
            cp->bytes += d;
        }
    }
}

/* Free every block newer than `keep` (NULL = free all). `keep`'s own
 * bump is left for the caller to restore. */
static void arena_free_above(arena_stack* st, arena_block* keep) {
    while (st->head && st->head != keep) {
        arena_block* dead = st->head;
        st->head = dead->next;
        size_t dsz = sizeof(*dead) + dead->cap;
        arena_block_stats_sub(dsz);
        arena_str_cache_forget(dead);
        free(dead);
    }
}

static void arena_push_cp(arena_stack* st) {
    arena_ensure_stack(st);
    if (st->cp_len >= st->cp_cap) {
        size_t nc = st->cp_cap * 2;
        arena_cp* nr = (arena_cp*)realloc(st->cps, nc * sizeof(*nr));
        if (!nr) arena_oom("checkpoint growth");
        st->cps = nr;
        st->cp_cap = nc;
    }
    st->cps[st->cp_len].block = st->head;
    st->cps[st->cp_len].off = st->cur;
    st->cps[st->cp_len].end = st->end;
    st->cps[st->cp_len].bytes = st->bytes;
    st->cp_len++;
}

/* True when `p` points into a block/range that this checkpoint pop (or
 * loop reset) is about to reclaim. */
static int arena_pointer_would_reclaim(arena_stack* st, arena_cp* cp, const void* p) {
    if (!p) return 0;
    const char* q = (const char*)p;
    arena_block* b = st->head;
    while (b) {
        if (q >= b->base && q < b->base + b->cap) {
            if (b != cp->block) return 1;
            return q >= cp->off;
        }
        if (b == cp->block) break;
        b = b->next;
    }
    return 0;
}

/* Before a frame is popped, check every registered pointer-bearing global
 * slot. A slot that points into the dying frame would become a dangling
 * pointer with no collector to repair it, so fail loudly instead. */
static void arena_verify_scan_regions(arena_stack* st, arena_cp* cp) {
    if (!arena_verify_roots) return;
    for (int i = 0; i < arena_scan_region_count; i++) {
        char* base = (char*)arena_scan_regions[i].start;
        size_t nbytes = arena_scan_regions[i].nbytes;
        for (size_t off = 0; off + sizeof(void*) <= nbytes; off += sizeof(void*)) {
            void* value = NULL;
            memcpy(&value, base + off, sizeof(value));
            if (arena_pointer_would_reclaim(st, cp, value)) {
                fprintf(stderr,
                        "Leash arena: a registered global/static slot at %p still points into the frame being reclaimed (%p). "
                        "A heap value that outlives its frame must be copied or rooted; prefer the GC backend for ownership patterns the arena cannot prove.\n",
                        (void*)(base + off), value);
                abort();
            }
        }
    }
}

void leasharena_verify_current_frame(void) {
    if (!arena_verify_roots || leasharena_tls_ar.cp_len == 0) return;
    arena_cp cp = leasharena_tls_ar.cps[leasharena_tls_ar.cp_len - 1];
    arena_verify_scan_regions(&leasharena_tls_ar, &cp);
}

static void arena_pop_cp(arena_stack* st) {
    if (st->cp_len == 0) {
        fprintf(stderr, "Leash arena: scope underflow (pop with no active frame)\n");
        abort();
    }
    st->cp_len--;
    arena_cp cp = st->cps[st->cp_len];
    char* old_cur = st->cur;
    arena_verify_scan_regions(st, &cp);
    arena_free_above(st, cp.block);
    st->head = cp.block;
    st->cur = cp.off;
    st->end = cp.end;
    st->bytes = cp.bytes;
    /* Poison only the part of the reclaimed region that is still mapped
     * (blocks above the checkpoint were just freed). */
    if (cp.block) {
        char* lim = old_cur;
        char* blk_end = cp.block->base + cp.block->cap;
        if (lim > blk_end) lim = blk_end;
        arena_poison_range(cp.off, lim);
    }
}

/* Reset (without popping) to the frame at stack index `frame`. */
static void arena_reset_to(arena_stack* st, size_t frame) {
    /* frame is the depth index (0-based) of the checkpoint to keep */
    if (frame >= st->cp_len) return;
    arena_cp cp = st->cps[frame];
    char* old_cur = st->cur;
    arena_verify_scan_regions(st, &cp);
    arena_free_above(st, cp.block);
    st->head = cp.block;
    st->cur = cp.off;
    st->end = cp.end;
    st->bytes = cp.bytes;
    if (cp.block) {
        char* lim = old_cur;
        char* blk_end = cp.block->base + cp.block->cap;
        if (lim > blk_end) lim = blk_end;
        arena_poison_range(cp.off, lim);
    }
}

/* ---- runtime entry points (same API as the GC / stub) ---- */

#if defined(__linux__)
#include <ucontext.h>
static void leash_segv_diag_handler(int sig, siginfo_t* info, void* ctx) {
    (void)sig;
    ucontext_t* u = (ucontext_t*)ctx;
    /* One call, one format string: the diagnostic is identical but costs
     * fewer instructions and less rodata in every arena binary. */
    fprintf(stderr,
            "\n=== LEASH SEGV ===\n"
            "RIP=%p, si_addr=%p\n"
            "RAX=%p, RDX=%p, RBX=%p, RCX=%p, RSI=%p\n",
            (void*)u->uc_mcontext.gregs[REG_RIP], info->si_addr,
            (void*)u->uc_mcontext.gregs[REG_RAX],
            (void*)u->uc_mcontext.gregs[REG_RDX],
            (void*)u->uc_mcontext.gregs[REG_RBX],
            (void*)u->uc_mcontext.gregs[REG_RCX],
            (void*)u->uc_mcontext.gregs[REG_RSI]);
    fflush(stderr);
    abort();
}
#endif

void leash_gc_init(void) {
#if defined(__linux__)
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = leash_segv_diag_handler;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL);
#endif
    /* Both arenas are created lazily on first allocation/enter. init
     * itself allocates nothing: allocation-free programs (fib, pi, ...)
     * should not carry an arena block or checkpoint array at all. */
    /* Poison reclaimed scope bytes by default so stale reads into a
     * dead frame become obvious garbage (0xDD) instead of silently
     * valid-looking data. Set LEASH_ARENA_POISON=0 to disable the
     * memset cost on scope exit/reset (e.g. for peak-speed runs where
     * lifetime correctness is already established). */
    {
        const char* p = getenv("LEASH_ARENA_POISON");
        leash_arena_poison = (!p || p[0] != '0' || p[1] != '\0') ? 1 : 0;
        leash_arena_poison_on = leash_arena_poison;
    }
    /* Frame-exit validation of registered global/static slots. It catches
     * a frame-local value stored into long-lived storage before the frame
     * is reclaimed. Set LEASH_ARENA_VERIFY=0 only after lifetimes are
     * proven. */
    {
        const char* v = getenv("LEASH_ARENA_VERIFY");
        arena_verify_roots = (!v || v[0] != '0' || v[1] != '\0') ? 1 : 0;
    }
#if defined(__linux__) && defined(__GLIBC__)
    /* Fewer glibc malloc arenas reduces per-thread heap/page overhead
     * and peak RSS for single-threaded arena programs. */
    mallopt(M_ARENA_MAX, 2);
#endif
    leasharena_tls_arena_dead = 0;
}

void leash_gc_shutdown(void) {
    if (getenv("LEASH_ARENA_STATS")) {
        size_t blocks = 0, cap = 0;
        for (arena_block* b = leasharena_tls_ar.head; b; b = b->next) { blocks++; cap += b->cap; }
        fprintf(stderr, "Arena stats: peak %zu block bytes (now %zu in %zu blocks), root peak %zu block bytes, current payload %zu (+root %zu)\n",
                arena_peak_block_bytes, cap, blocks, arena_root_peak_block_bytes,
                leasharena_tls_ar.bytes, tls_root.bytes);
    }
    /* Free both arenas on the calling thread. Worker arenas are
     * released by their own thread-exit hook (see
     * leash_gc_thread_attach); only the main thread runs this at
     * program exit. */
    arena_free_stack(&leasharena_tls_ar);
    arena_free_stack(&tls_root);
    leasharena_tls_arena_dead = 1;
}

void* leash_gc_malloc(size_t size) {
    return arena_alloc(&leasharena_tls_ar, size, ARENA_ALIGN);
}

/* Hot allocation: the payload contents do not need a zero-fill
 * (see arena_alloc_nz for the safety contract). */
void* leash_gc_malloc_nz(size_t size) {
    return arena_alloc_nz(&leasharena_tls_ar, size, ARENA_ALIGN);
}

/* Pointer-aligned payloads (vector/hash headers, hash nodes): 8-byte
 * alignment avoids the 16-byte padding the general allocator reserves. */
void* leash_gc_malloc_compact(size_t size) {
    return arena_alloc(&leasharena_tls_ar, size, (size_t)8);
}

void* leash_gc_malloc_ex(size_t size, unsigned int flags) {
    (void)flags; /* arena never scans */
    return leash_gc_malloc(size);
}

void* leash_gc_realloc(void* ptr, size_t new_size) {
    if (!ptr) return leash_gc_malloc(new_size);
    if (new_size == 0) return NULL; /* no individual free in arena */

    /* Ownership FIRST. The size header lives in front of the payload,
     * so reading it before we know the pointer is ours can touch memory
     * we do not own: a foreign pointer (a C library's malloc block, a
     * pointer from another thread's arena) would fault or read a bogus
     * size, and an interior pointer would read whatever bytes happen to
     * sit in front of it. */
    arena_stack* st = NULL;
    int in_root = arena_owns(&tls_root, ptr);
    if (in_root) st = &tls_root;
    else if (arena_owns(&leasharena_tls_ar, ptr)) st = &leasharena_tls_ar;
    else {
        fprintf(stderr, "Leash arena: realloc of a pointer that does not belong to this thread's arena\n");
        abort();
    }

    /* An arena string's payload sits behind a 16-byte header: the word
     * at ptr-8 is the string LENGTH, not an allocation size, and the
     * allocation size lives at ptr-16 behind the magic tag. Growing it
     * as a plain buffer would overwrite the length word (losing the
     * string's identity) and write past the end of the allocation
     * (the bump pointer only reserves `total` bytes). Strings get their
     * own path: a fresh string with the requested capacity that keeps
     * the bytes and the header.
     *
     * This check MUST run before arena_check_payload below. That helper
     * reads the word at ptr-8 as an allocation size; for a string it is
     * the length, and a SHARED string carries bit 63 in that word, so the
     * generic check would reject a perfectly valid string as "not an
     * allocation start" (and abort). Ownership was already proven above,
     * so reading the two header words here is safe. */
    if ((uintptr_t)ptr >= 16) {
        arena_block* sb = arena_block_of(st, ptr);
        if (sb && (char*)ptr - 16 >= sb->base) {
            uint64_t raw = *(const uint64_t*)(const void*)((char*)ptr - 16);
            if ((raw >> ARENA_STR_MAGIC_SHIFT) == ARENA_STR_MAGIC) {
                uint64_t total = 0, slen = 0;
                ar_str_require((const char*)ptr, sb->base + sb->cap,
                               "realloc of string", &total, &slen);
                uint64_t lenword = *(const uint64_t*)(const void*)((char*)ptr - 8);
                size_t cap = new_size > (size_t)slen ? new_size : (size_t)slen;
                char* out = (char*)leash_gc_alloc_string_cap_in(st, (size_t)slen, cap);
                memcpy(out, ptr, (size_t)slen + 1);
                /* A shared string still has other holders pointing at the old
                 * buffer (a second variable, a container slot, a struct field).
                 * Reclaiming its dedicated sole block would leave those holders
                 * dangling -- the same rule the append and root-store paths
                 * already honour. */
                if (!(lenword & ARENA_STR_SHARED_BIT)) {
                    arena_try_free_solo(st, ptr, (size_t)total - 8);
                }
                return (void*)out;
            }
        }
    }

    /* A realloc size near SIZE_MAX wraps the (n+7)&~7 rounding below into
     * a tiny (or zero) capacity, after which the header would advertise a
     * huge size over a tiny buffer and every later element write would
     * run past the end. Reject it up front, at allocation-time strictness. */
    if (new_size > ARENA_MAX_ALLOC) {
        fprintf(stderr, "Leash arena: realloc size too large (%zu bytes)\n", new_size);
        abort();
    }

    /* Is `ptr` an allocation START, not just an address inside a block?
     * The tagged header at ptr-8 must describe a real allocation: its tag
     * must match, its header must lie in the block, and its size must fit
     * in that block. An interior pointer is rejected structurally now. */
    size_t old_size = arena_check_payload(st, ptr, "realloc");
    /* Root objects stay in the root arena (never moved into scopes). */
    if (in_root) {
        /* Still at the root arena's tip? Extend in place: no copy, and
         * the superseded buffer never accumulates in the never-freed
         * root arena. */
        void* tp = arena_grow_tip(&tls_root, ptr, old_size, new_size);
        if (tp) return tp;
        void* np = leash_gc_malloc_rooted_nz(new_size, ARENA_ALIGN);
        memcpy(np, ptr, old_size < new_size ? old_size : new_size);
        /* The root arena never rewinds its bump pointer, so a
         * superseded buffer would stay resident for the rest of the
         * program. Reclaim it when it is a dedicated single-allocation
         * block (the common case for a growing root buffer). */
        arena_try_free_solo(&tls_root, ptr, old_size);
        return np;
    }
    /* Tip extension: if the object ends exactly at the bump, grow in
     * place (amortized O(1) for string/vec growth on the tip). */
    char* p = (char*)ptr;
    /* Exact tip test: the old allocation's (8-aligned) end equals the
     * bump. Anything else is a non-tip growth (copy; the old bytes stay
     * until the scope resets). */
    size_t old_adv = (old_size + 7) & ~(size_t)7;
    char* alloc_end = p + old_adv;
    if (alloc_end == leasharena_tls_ar.cur) {
        /* Grow within the current block. */
        size_t new_adv = (new_size + 7) & ~(size_t)7;
        if (new_adv >= old_adv) {
            size_t delta = new_adv - old_adv;
            if ((size_t)(leasharena_tls_ar.end - leasharena_tls_ar.cur) >= delta) {
                /* The extension is fresh capacity. Every caller of
                 * leash_gc_realloc copies the live bytes over the old
                 * ones and bounds its use of the buffer by the element
                 * count, so the new tail is never read before it is
                 * written -- and the arena never scans a payload, so
                 * there is no collector that could observe it either.
                 * The fill would be pure overhead on the hot growth
                 * path (a vector that doubles pays it once per byte
                 * it ever allocates). */
                *(uint64_t*)(void*)(p - 8) = ar_hdr_pack(new_size);
                leasharena_tls_ar.cur += delta;
                leasharena_tls_ar.bytes += (new_size > old_size ? new_size - old_size : 0);
                /* The buffer may predate a checkpoint: growing it past
                 * that checkpoint would let the checkpoint's rewind
                 * truncate the live buffer. Keep the extension alive. */
                arena_protect_extension(&leasharena_tls_ar,
                                        leasharena_tls_ar.head, p, p + new_adv);
                return (void*)p;
            }
        } else {
            /* Shrink a tip allocation: reclaim the tail. The reclaimed
             * bytes are zeroed: the bump pointer moves back over them
             * and the next allocation may take the no-zero-fill path,
             * which would otherwise expose the old contents. */
            size_t delta = old_adv - new_adv;
            /* Zero exactly the reclaimed region [p+new_adv, p+old_adv).
             * Starting at p+new_size would be shifted LEFT by the
             * new-size padding (0-7 bytes) and leave that many bytes of
             * old data live at the tail of the reclaimed region. */
            memset(p + new_adv, 0, delta);
            *(uint64_t*)(void*)(p - 8) = ar_hdr_pack(new_size);
            leasharena_tls_ar.cur -= delta;
            if (new_size < old_size) leasharena_tls_ar.bytes -= (old_size - new_size);
            return (void*)p;
        }
    }
    /* Sole-block growth: a dedicated oversize block holds exactly one
     * allocation that consumed the whole block (see arena_raw_ex). Grow
     * that block itself with realloc so large vectors/strings extend in
     * place: no byte copy and no transient old+new resident peak. */
    {
        arena_block* b = arena_block_of(&leasharena_tls_ar, ptr);
        if (b && b->sole && b == leasharena_tls_ar.head &&
            leasharena_tls_ar.cur == leasharena_tls_ar.end) {
            size_t off = (size_t)(p - b->base);
            int referenced = 0;
            for (size_t i = 0; i < leasharena_tls_ar.cp_len; i++) {
                if (leasharena_tls_ar.cps[i].block == b) { referenced = 1; break; }
            }
            if (!referenced && off + old_adv <= b->cap) {
                size_t need_cap = off + ((new_size + 7) & ~(size_t)7);
                size_t new_cap = (need_cap + 4095) & ~(size_t)4095;
                size_t old_cap = b->cap;
                arena_str_cache_forget(b);
                arena_block* nb = (arena_block*)realloc(b, sizeof(*nb) + new_cap);
                if (nb) {
                    /* realloc may move the block: the usable-area base is
                     * an absolute pointer stored inside it, so repoint. */
                    nb->base = (char*)(nb + 1);
                    nb->cap = new_cap;
                    arena_str_cache_set(nb);
                    char* np = nb->base + off;
                    /* The extension is fresh capacity: see the tip
                     * growth path above for why it is not zeroed. */
                    *(uint64_t*)(void*)(np - 8) = ar_hdr_pack(new_size);
                    leasharena_tls_ar.head = nb;
                    leasharena_tls_ar.cur = nb->base + new_cap;
                    leasharena_tls_ar.end = nb->base + new_cap;
                    if (new_cap >= old_cap) arena_block_stats_add(new_cap - old_cap);
                    else arena_block_stats_sub(old_cap - new_cap);
                    if (new_size >= old_size) leasharena_tls_ar.bytes += new_size - old_size;
                    else if (leasharena_tls_ar.bytes >= old_size - new_size)
                        leasharena_tls_ar.bytes -= old_size - new_size;
                    return (void*)np;
                }
                /* realloc failed: fall back to allocate + copy. */
            }
        }
    }
    /* Non-tip growth: a fresh buffer plus a copy of the live bytes.
     * The arena does not need the new buffer zeroed (see the tip
     * growth path above). */
    void* np = leash_gc_malloc_nz(new_size);
    memcpy(np, ptr, old_size < new_size ? old_size : new_size);
    arena_try_free_solo(&leasharena_tls_ar, ptr, old_size);
    return np;
}

void* leash_gc_aligned_alloc(size_t size, size_t alignment) {
    return arena_alloc(&leasharena_tls_ar, size, alignment < ARENA_ALIGN ? ARENA_ALIGN : alignment);
}

void* leash_gc_aligned_alloc_ex(size_t size, size_t alignment, unsigned int flags) {
    (void)flags;
    return leash_gc_aligned_alloc(size, alignment);
}

void leash_gc_collect(void) {}
void leash_gc_register_root(void* ptr) { (void)ptr; }
void leash_gc_unregister_root(void* ptr) { (void)ptr; }
void leash_gc_register_scan_region(void* start, size_t nbytes) {
    if (!start || nbytes == 0) return;
    for (int i = 0; i < arena_scan_region_count; i++) {
        if (arena_scan_regions[i].start == start &&
            arena_scan_regions[i].nbytes == nbytes) return;
    }
    if (arena_scan_region_count >= ARENA_SCAN_REGION_MAX) {
        fprintf(stderr, "Leash arena: too many registered global/static scan regions (limit %d)\n",
                ARENA_SCAN_REGION_MAX);
        abort();
    }
    arena_scan_regions[arena_scan_region_count].start = start;
    arena_scan_regions[arena_scan_region_count].nbytes = nbytes;
    arena_scan_region_count++;
}

void* leash_gc_malloc_rooted(size_t size) {
    if (size == 0) return NULL;
    return arena_root_alloc(&tls_root, size, ARENA_ALIGN);
}

/* Arena-only allocation without the zero-fill.
 *
 * The arena has no collector: it never scans or traces a payload, so
 * the zero-fill the GC needs (a fresh cell must be safe to scan
 * before the program writes it) is pure overhead here. It may only be
 * skipped for a payload that is fully initialised before anything can
 * read it -- see the call sites, each of which writes every byte it
 * may later read. */
void* leash_gc_malloc_compact_nz(size_t size) {
    if (size == 0) return NULL;
    return arena_alloc_nz(&leasharena_tls_ar, size, (size_t)8);
}

void* leash_gc_malloc_rooted_nz(size_t size, size_t alignment) {
    if (size == 0) return NULL;
    return arena_alloc_nz(&tls_root, size, alignment < ARENA_ALIGN ? ARENA_ALIGN : alignment);
}

void* leasharena_root_alloc(size_t size) {
    if (size == 0) return NULL;
    return leash_gc_malloc_rooted(size);
}

void* leasharena_root_alloc_aligned(size_t size, size_t alignment) {
    return arena_root_alloc(&tls_root, size, alignment < ARENA_ALIGN ? ARENA_ALIGN : alignment);
}

/* ---- scope API (called by generated code) ---- */

void leasharena_enter(void) {
    arena_ensure_stack(&leasharena_tls_ar);
    arena_push_cp(&leasharena_tls_ar);
}

void leasharena_enter_slow(void) {
    arena_push_cp(&leasharena_tls_ar);
}

void leasharena_exit_slow(void) {
    arena_pop_cp(&leasharena_tls_ar);
}

void leasharena_exit(void) {
    arena_pop_cp(&leasharena_tls_ar);
}

void leasharena_exit_n(int64_t nframes) {
    arena_ensure_stack(&leasharena_tls_ar);
    if (nframes < 0) return;
    if ((size_t)nframes > leasharena_tls_ar.cp_len) {
        fprintf(stderr, "Leash arena: scope underflow (exit_n(%lld) with depth %zu)\n",
                (long long)nframes, leasharena_tls_ar.cp_len);
        abort();
    }
    for (int64_t i = 0; i < nframes; i++) arena_pop_cp(&leasharena_tls_ar);
}

int64_t leasharena_loop_enter(void) {
    arena_ensure_stack(&leasharena_tls_ar);
    arena_push_cp(&leasharena_tls_ar);
    return (int64_t)(leasharena_tls_ar.cp_len - 1);
}

void leasharena_loop_reset_slow(int64_t frame) {
    if (frame < 0) {
        fprintf(stderr, "Leash arena: invalid loop frame (%lld)\n", (long long)frame);
        abort();
    }
    arena_ensure_stack(&leasharena_tls_ar);
    if ((size_t)frame >= leasharena_tls_ar.cp_len) {
        fprintf(stderr, "Leash arena: loop reset frame %lld out of range (depth %zu)\n",
                (long long)frame, leasharena_tls_ar.cp_len);
        abort();
    }
    leasharena_tls_ar.cp_len = (size_t)frame + 1;
    arena_reset_to(&leasharena_tls_ar, (size_t)frame);
}

void leasharena_loop_reset(int64_t frame) {
    if (frame < 0) {
        fprintf(stderr, "Leash arena: invalid loop frame (%lld)\n", (long long)frame);
        abort();
    }
    arena_ensure_stack(&leasharena_tls_ar);
    /* frame must still be on the stack (inner frames balanced). */
    if ((size_t)frame >= leasharena_tls_ar.cp_len) {
        fprintf(stderr, "Leash arena: loop reset frame %lld out of range (depth %zu)\n",
                (long long)frame, leasharena_tls_ar.cp_len);
        abort();
    }
    /* Drop any frames above the loop frame (should not happen with
     * balanced inner functions) then reset to the loop frame. */
    leasharena_tls_ar.cp_len = (size_t)frame + 1;
    arena_reset_to(&leasharena_tls_ar, (size_t)frame);
}

void leasharena_loop_exit(int64_t frame) {
    if (frame < 0) {
        fprintf(stderr, "Leash arena: invalid loop frame (%lld)\n", (long long)frame);
        abort();
    }
    arena_ensure_stack(&leasharena_tls_ar);
    if ((size_t)frame >= leasharena_tls_ar.cp_len) {
        fprintf(stderr, "Leash arena: loop exit frame %lld out of range (depth %zu)\n",
                (long long)frame, leasharena_tls_ar.cp_len);
        abort();
    }
    leasharena_tls_ar.cp_len = (size_t)frame + 1;
    arena_pop_cp(&leasharena_tls_ar);
}

/* ---- arena string layout ----
 * payload = [u64 total|magic][u64 len][cap bytes][NUL], data = payload+16.
 * len lives at data-8 (shared prefix code keeps working); total lives
 * at data-16 and yields cap = total - 17.
 *
 * The top 16 bits of the total word carry a magic tag. The arena hands
 * out plain C pointers, so a pointer that is NOT an arena string (an
 * inline hash key, a byte buffer, a pointer into the middle of some
 * other allocation) can reach the string helpers. Without a tag such a
 * pointer looks like a string whose "length" is whatever bytes happen
 * to sit in front of it -- a bogus length leads to a wild read at
 * s[len] and to silently truncated copies. The tag makes a
 * non-string pointer fail the identity check with overwhelming
 * probability, so the length is only ever trusted for a real string.
 * The real total stays in the low 48 bits (128 TB of string is far
 * beyond anything the runtime can allocate). */
#define ARENA_STR_HDR ((size_t)16)

static uint64_t ar_str_pack_total(size_t total) {
    return ((ARENA_STR_MAGIC << ARENA_STR_MAGIC_SHIFT) | (uint64_t)total);
}

/* Read and FULLY validate a candidate arena-string header. `region_end`
 * is one-past-the-end of the block that contains s-16; it is the single
 * most important argument here. Matching the magic and having a
 * self-consistent (total, len) pair is NOT enough: a dangling pointer
 * into a reused region, or a caller-forged header, can advertise a
 * megabyte-long string inside a 32 KB block, and the `s[len]` terminator
 * test (or a later memcpy using cap = total-17) then reads/writes far
 * outside the block. Requiring the whole allocation to fit in the block
 * turns that whole class of bug into a rejected lookup.
 *
 * Returns 1 only for a real arena string whose header and payload are
 * contained in `region_end`; the caller may then trust `len` and
 * `total`. Pass NULL for `region_end` only on paths that have not
 * established containment (there are none on the consumer side). */
static inline int ar_str_read(const char* s, const char* region_end,
                              uint64_t* out_total, uint64_t* out_len) {
    if ((uintptr_t)s < 16) return 0;
    const char* base = s - 16;
    if (region_end && base >= region_end) return 0;
    uint64_t raw = *(const uint64_t*)(const void*)base;
    if ((raw >> ARENA_STR_MAGIC_SHIFT) != ARENA_STR_MAGIC) return 0;
    uint64_t total = raw & ARENA_STR_TOTAL_MASK;
    if (total < 17) return 0;
    /* region_end - base is the bytes actually available in the block. */
    if (region_end && total > (uint64_t)(region_end - base)) return 0;
    uint64_t len = *(const uint64_t*)(const void*)(s - 8) & ARENA_STR_LEN_MASK;
    if (len > total - 17) return 0;
    if (s[len] != '\0') return 0;
    if (out_total) *out_total = total;
    if (out_len) *out_len = len;
    return 1;
}

/* Strict form for paths that KNOW the pointer should be an arena string
 * (a string currently held in a realloc/root slot). A failure there is
 * corruption, not a non-string literal, so fail loudly. */
static void ar_str_require(const char* s, const char* region_end,
                           const char* what, uint64_t* out_total,
                           uint64_t* out_len) {
    if (!ar_str_read(s, region_end, out_total, out_len)) {
        fprintf(stderr, "Leash arena: %s: corrupt string header "
                        "(tag, bounds, length or terminator invalid)\n", what);
        abort();
    }
}

/* Capacity policy for arena strings.
 *
 * Short strings dominate real workloads (tokens, keys, formatted fields),
 * so the first allocation reserves a slim buffer instead of a fat one.
 * The request is rounded up to a 16-byte payload; the allocation's tail
 * padding is real, usable capacity (the length word advertises it), so a
 * 15-byte string fits without a second allocation and a 16-byte buffer
 * costs 40 bytes total instead of 88.
 *
 * Growth is geometric while the buffer is small — few reallocations, and
 * the tip-extension path grows most accumulators in place — then tapers
 * to +25% so large strings waste at most a quarter of their buffer. */#define ARENA_STR_MIN_CAP ((size_t)16)
#define ARENA_STR_DOUBLE_UNTIL ((size_t)256)

static inline size_t ar_str_cap_for(size_t need, size_t cur_cap) {
    size_t nc;
    if (cur_cap == 0) {
        nc = need < ARENA_STR_MIN_CAP ? ARENA_STR_MIN_CAP : need;
    } else if (cur_cap < ARENA_STR_DOUBLE_UNTIL) {
        nc = cur_cap * 2;
    } else {
        nc = cur_cap + (cur_cap >> 2);
    }
    if (nc < need) nc = need;
    return nc;
}

void* leash_gc_alloc_string_cap_in(arena_stack* st, size_t len, size_t cap) {
    if (len > SIZE_MAX - 32 || cap > ARENA_MAX_ALLOC - 32) {
        fprintf(stderr, "Leash arena: string allocation too large!\n");
        abort();
    }
    if (cap < len) cap = len;
    size_t total = ARENA_STR_HDR + cap + 1;
    if (total < len) abort();
    size_t pad = (8 - (total & 7)) & 7;
    total += pad;
    /* Raw bump of `total` bytes; the caller fully initializes every
     * byte it may read (headers, data bytes, the NUL terminator), so
     * the zero path (memset) is unnecessary here — skip it on the hot
     * string-construction path. */
    size_t raw_total = 0;
    arena_ensure_stack(st);
    char* cur_before = st->cur;
    if (leasharena_tls_arena_dead) {
        fprintf(stderr, "Leash arena: allocation after shutdown\n");
        abort();
    }
    /* Allocate via arena_raw with need = total - 8 (generic size sense
     * after its own 8-byte header would over-count; call raw directly). */
    char* payload = arena_raw_ex(st, total - 8, (size_t)8, &raw_total, 0);
    /* payload is the string data area? No: arena_raw returns payload
     * past its 8-byte header. We want base+16 as data. Back out: base is
     * payload-8. Data is base+16 = payload+8. */
    char* data = payload + 8;
    *(uint64_t*)(void*)(data - 16) = ar_str_pack_total(total);
    *(uint64_t*)(void*)(data - 8) = (uint64_t)len;
    st->bytes += total - 8;
    data[len] = '\0';
    return (void*)data;
}

void* leash_gc_alloc_string_cap(size_t len, size_t cap) {
    return leash_gc_alloc_string_cap_in(&leasharena_tls_ar, len, cap);
}

void* leasharena_root_alloc_string_cap(size_t len, size_t cap);
void* leasharena_root_alloc_string_cap(size_t len, size_t cap) {
    return leash_gc_alloc_string_cap_in(&tls_root, len, cap);
}

void* leash_gc_alloc_string(size_t len) { return leash_gc_alloc_string_cap(len, len); }

/* Shared tail of every string lookup: validate against the block bounds
 * once, then publish len/cap. Keeping this in one place shrinks the hot,
 * cached and full-walk paths to a single call each. */
static int ar_str_info_bounded(const char* s, const char* region_end,
                               size_t* out_len, size_t* out_cap) {
    uint64_t total = 0, len = 0;
    if (!ar_str_read(s, region_end, &total, &len)) return 0;
    if (out_len) *out_len = (size_t)len;
    if (out_cap) *out_cap = (size_t)(total - 17);
    return 1;
}

static int arena_str_info(const char* s, size_t* out_len, size_t* out_cap) {
    if (!s || (uintptr_t)s < 16) return 0;
    const char* base = s - 16;
    /* Hot path: most strings live in the newest TLS block. Those pointer
     * checks are strictly cheaper than walking both arena block lists. */
    arena_stack* tls = &leasharena_tls_ar;
    if (tls->head && base >= tls->head->base && base < tls->head->base + tls->head->cap) {
        return ar_str_info_bounded(s, tls->head->base + tls->head->cap, out_len, out_cap);
    }
    /* Last block used by a string lookup: repeated comparisons of strings
     * from the same older block avoid a full block-list walk. The range
     * is cleared whenever that block is freed. */
    if (arena_str_cache_base && base >= arena_str_cache_base &&
        base < arena_str_cache_end) {
        return ar_str_info_bounded(s, arena_str_cache_end, out_len, out_cap);
    }
    /* String base must be inside one of our arenas. Resolve the block once
     * and remember it for the next lookup. */
    arena_block* owner = arena_block_of(&leasharena_tls_ar, base);
    if (!owner) owner = arena_block_of(&tls_root, base);
    if (owner) {
        arena_str_cache_set(owner);
        return ar_str_info_bounded(s, owner->base + owner->cap, out_len, out_cap);
    }
    return 0;
}

int64_t leash_str_bsize(const char* s) {
    if (!s) return 0;
    size_t len = 0;
    if (arena_str_info(s, &len, NULL)) return (int64_t)len;
    return (int64_t)strlen(s);
}

void leash_str_share(const char* s) {
    /* Mark a string reachable from another location (a second variable,
     * a container slot, a struct field, a call argument) as shared, so
     * the in-place append fast paths copy instead of mutating it. The
     * high bit of the length word is unused for the length itself. */
    if (!s) return;
    size_t len = 0;
    if (!arena_str_info(s, &len, NULL)) return;
    *(uint64_t*)(void*)(s - 8) |= ARENA_STR_SHARED_BIT;
}

/* Core append with the piece length already known: avoids two
 * arena_str_info block walks (one for s, one per piece bsize) that the
 * generic wrapper performed on every append. Small edit but this is
 * THE hot path (per-char/per-int loops), so one lookup total is kept. */
char* leash_str_append_span(char* s, const char* piece, size_t pl) {
    size_t sl = 0;
    size_t cur_cap = 0;
    size_t old_total = 0;
    size_t grow_cap = 0;
    int tracked = arena_str_info(s, &sl, &cur_cap);
    int shared = 0;
    arena_stack* old_owner = NULL;
    if (tracked) {
        shared = (*(uint64_t*)(const void*)(s - 8) & ARENA_STR_SHARED_BIT) != 0;
    } else {
        sl = strlen(s ? s : "");
    }
    size_t tot = sl + pl;
    if (tracked && !shared) {
        if (tot <= cur_cap) {
            if (pl) memcpy(s + sl, piece, pl);
            s[tot] = '\0';
            *(uint64_t*)(void*)(s - 8) = (uint64_t)tot;
            return s;
        }
        /* Growth past cap: try to extend this string buffer in place
         * when it is still the bump tip (the accumulator loop case,
         * where each iteration would otherwise abandon a large copy).
         * Old total lives at s-16 (magic-tagged); the allocation ends
         * at s-16+total. */
        old_total = *(uint64_t*)(void*)(s - 16) & ARENA_STR_TOTAL_MASK;
        size_t new_cap = ar_str_cap_for(tot, cur_cap);
        grow_cap = new_cap;
        size_t new_total = 16 + new_cap + 1;
        new_total = (new_total + 7) & ~(size_t)7;
        arena_stack* st = &leasharena_tls_ar;
        char* old_end = (s - 16) + old_total;
        if (old_total >= 17 && old_end == st->cur) {
            size_t want = new_total - old_total;
            if ((size_t)(st->end - st->cur) >= want) {
                /* Keep the magic tag: the total word is what tells the
                 * string helpers "this is an arena string". Dropping it
                 * would make every later lookup fall back to strlen and
                 * disable the in-place growth path. */
                *(uint64_t*)(void*)(s - 16) = ar_str_pack_total(new_total);
                *(uint64_t*)(void*)(s - 8) = (uint64_t)tot;
                st->cur += want;
                st->bytes += want;
                /* The string may predate a checkpoint: growing it past
                 * that checkpoint would let the checkpoint's rewind
                 * truncate the live string. Keep the extension alive. */
                arena_protect_extension(st, st->head, s - 16, (s - 16) + new_total);
                if (pl) memcpy(s + sl, piece, pl);
                s[tot] = '\0';
                return s;
            }
        }
        /* A large string lives in its own block. Realloc that block
         * instead of copying to a fresh one and freeing the old one. */
        if (arena_owns(&tls_root, s - 16)) old_owner = &tls_root;
        else if (arena_owns(st, s - 16)) old_owner = st;
        if (old_owner) {
            /* Growing a sole block uses realloc on the block itself, which
             * may MOVE it (freeing the old block). `piece` can alias `s`
             * -- `s = s + s` is emitted by the compiler -- so reading it
             * after the move would be a use-after-free. A sole block holds
             * one allocation, so any piece inside that block aliases the
             * string; for that case fall through to the alias-safe
             * allocate-then-copy path below. */
            int piece_aliases = 0;
            arena_block* sb = arena_block_of(old_owner, s - 16);
            if (sb) {
                const char* pb = (const char*)piece;
                if (pb >= sb->base && pb < sb->base + sb->cap) piece_aliases = 1;
            }
            if (!piece_aliases) {
                char* grown = arena_grow_sole_string(old_owner, s, old_total, new_total);
                if (grown) {
                    *(uint64_t*)(void*)(grown - 8) = (uint64_t)tot;
                    if (pl) memcpy(grown + sl, piece, pl);
                    grown[tot] = '\0';
                    return grown;
                }
            }
        }
    }
    /* A grown string keeps geometric slack so the next append does not
     * copy the whole buffer again; a fresh string starts slim. */
    char* out = (char*)leash_gc_alloc_string_cap(tot, grow_cap ? grow_cap : ar_str_cap_for(tot, 0));
    if (sl) memcpy(out, s, sl);
    if (pl) memcpy(out + sl, piece, pl);
    out[tot] = '\0';
    if (tracked && !shared && old_owner) {
        /* old_total tracks the allocation size (total-8 bytes). A
         * shared string is still reachable through its other owner, so
         * a dedicated oversize block must not be reclaimed here. */
        arena_try_free_solo(old_owner, s, old_total > 8 ? old_total - 8 : 0);
    }
    return out;
}

char* leash_str_append(char* s, const char* piece) {
    if (!s) {
        size_t plen = (size_t)leash_str_bsize(piece);
        size_t cap = ar_str_cap_for(plen, 0);
        char* out = (char*)leash_gc_alloc_string_cap(plen, cap);
        if (plen) memcpy(out, piece ? piece : "", plen);
        out[plen] = '\0';
        return out;
    }
    if (!piece) return s;
    int64_t pl_i = leash_str_bsize(piece);
    if (pl_i < 0) pl_i = 0;
    return leash_str_append_span(s, piece, (size_t)pl_i);
}

char* leash_str_concat_ex(const char* s1, const char* s2) {
    if (!s1) s1 = "";
    if (!s2) s2 = "";
    size_t l1 = (size_t)leash_str_bsize(s1);
    size_t l2 = (size_t)leash_str_bsize(s2);
    size_t tot = l1 + l2;
    char* out = (char*)leash_gc_alloc_string_cap(tot, tot);
    if (l1) memcpy(out, s1, l1);
    if (l2) memcpy(out + l1, s2, l2);
    out[tot] = '\0';
    return out;
}

/* Fast decimal helpers (match GC mode). */
static int _ar_int_digits_i64(int64_t v) {
    if (v == 0) return 1;
    int neg = v < 0;
    uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
    int d = neg ? 1 : 0;
    while (u) { d++; u /= 10; }
    return d;
}
static int _ar_uint_digits(uint64_t v) {
    if (v == 0) return 1;
    int d = 0;
    while (v) { d++; v /= 10; }
    return d;
}

char* leash_str_append_int(char* s, int64_t v, int is_uint) {
    char buf[32];
    int d;
    if (is_uint) {
        uint64_t u = (uint64_t)v;
        d = _ar_uint_digits(u);
        if (u == 0) { buf[0] = '0'; buf[1] = '\0'; }
        else {
            int pos = d;
            buf[pos] = '\0';
            while (u) { buf[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    } else {
        d = _ar_int_digits_i64(v);
        if (v == 0) { buf[0] = '0'; buf[1] = '\0'; }
        else {
            int neg = v < 0;
            uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
            int pos = d;
            buf[pos] = '\0';
            if (neg) { buf[0] = '-'; pos = d; }
            while (u) { buf[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    }
    if (d > 0 && !s) {
        char* out = (char*)leash_gc_alloc_string_cap((size_t)d, ar_str_cap_for((size_t)d, 0));
        memcpy(out, buf, (size_t)d);
        out[d] = '\0';
        return out;
    }
    return leash_str_append_span(s, buf, d > 0 ? (size_t)d : 0);
}

char* leash_str_append_char(char* s, char c) {
    if (!s) {
        char* out = (char*)leash_gc_alloc_string_cap(1, ARENA_STR_MIN_CAP);
        out[0] = c;
        out[1] = '\0';
        return out;
    }
    /* Hot path (arena, per-char loops): the capacity belongs to this
     * string buffer no matter which arena block owns it. Requiring the
     * newest TLS block made a sole-block accumulator fall into the
     * generic append on every character, which rescanned the block list
     * even though the spare capacity was already there. */
    size_t len = 0, cap = 0;
    if (arena_str_info(s, &len, &cap) &&
        !(*(uint64_t*)(const void*)(s - 8) & ARENA_STR_SHARED_BIT)) {
        size_t tot = len + 1;
        if (tot <= cap) {
            s[len] = c;
            s[tot] = '\0';
            *(uint64_t*)(void*)(s - 8) = (uint64_t)tot;
            return s;
        }
    }
    return leash_str_append_span(s, &c, 1);
}

char* leash_int_to_str_fast(int64_t v) {
    int d = _ar_int_digits_i64(v);
    char* out = (char*)leash_gc_alloc_string((size_t)d);
    if (v == 0) { out[0] = '0'; out[1] = '\0'; return out; }
    int neg = v < 0;
    uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
    int pos = d;
    out[pos] = '\0';
    if (neg) { out[0] = '-'; pos = d; }
    while (u) { out[--pos] = (char)('0' + (u % 10)); u /= 10; }
    return out;
}

char* leash_uint_to_str_fast(uint64_t v) {
    int d = _ar_uint_digits(v);
    char* out = (char*)leash_gc_alloc_string((size_t)d);
    if (v == 0) { out[0] = '0'; out[1] = '\0'; return out; }
    int pos = d;
    out[pos] = '\0';
    while (v) { out[--pos] = (char)('0' + (v % 10)); v /= 10; }
    return out;
}

char* leash_str_concat_int(const char* s, int64_t v, int is_uint) {
    if (!s) s = "";
    size_t sl = (size_t)leash_str_bsize(s);
    int d = is_uint ? _ar_uint_digits((uint64_t)v) : _ar_int_digits_i64(v);
    size_t tot = sl + (size_t)d;
    char* out = (char*)leash_gc_alloc_string_cap(tot, tot);
    if (sl) memcpy(out, s, sl);
    char* tail = out + sl;
    if (is_uint) {
        uint64_t u = (uint64_t)v;
        if (u == 0) { tail[0] = '0'; }
        else {
            int pos = d;
            while (u) { tail[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    } else {
        if (v == 0) { tail[0] = '0'; }
        else {
            int neg = v < 0;
            uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
            int pos = d;
            if (neg) { tail[0] = '-'; pos = d; }
            while (u) { tail[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    }
    out[tot] = '\0';
    return out;
}

void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) {
    /* Same overflow guard as the GC backend: a wrapped size would
     * allocate a tiny buffer and the vector would write past it. */
    if (elem_size != 0 && capacity > SIZE_MAX / elem_size) {
        fprintf(stderr, "Leash arena: vector allocation too large!\n");
        abort();
    }
    return arena_alloc_nz(&leasharena_tls_ar, elem_size * capacity, ARENA_ALIGN);
}

void leash_gc_thread_spawned(void) {}

/* Worker threads own an arena of their own (the scope arena is
 * thread-local). When such a thread exits, its blocks and its
 * checkpoint array would otherwise stay resident for the rest of the
 * process: the main thread's shutdown only frees the calling thread's
 * arenas. A thread-exit destructor releases the arena when the thread
 * goes away, so a program that spawns many short-lived workers does not
 * accumulate one arena per worker. */
#if !defined(_WIN32)
static pthread_once_t arena_tls_key_once = PTHREAD_ONCE_INIT;
static pthread_key_t arena_tls_key;

/* Release BOTH thread-local arenas when the thread exits. The scope
 * arena belongs to the thread; the root arena may hold blocks the
 * thread allocated (a helper's rooted container growth), and leaving
 * them resident made a program that spawns many short-lived workers
 * accumulate root storage without bound. Borrowed pointers (parent-
 * owned objects a worker stored into its own containers) are not
 * touched here: only this thread's own blocks are freed. */
static void arena_tls_dtor(void* p) {
    if (!p) return;
    /* Clear the key so POSIX does not call this destructor again on the
     * remaining destructor iterations (frees are idempotent, but a second
     * pass has no work to do). */
    pthread_setspecific(arena_tls_key, NULL);
    xfer_free_all();
    arena_free_stack((arena_stack*)p);
    arena_free_stack(&tls_root);
    /* Match the Windows path: after thread exit this thread's TLS arena is
     * gone, so any later allocation must fail loudly instead of silently
     * rebuilding an arena behind unwound frames. */
    leasharena_tls_arena_dead = 1;
}

static void arena_tls_make_key(void) {
    pthread_key_create(&arena_tls_key, arena_tls_dtor);
}
#else
/* Windows: fiber-local storage is the thread-exit hook (FlsAlloc takes
 * a callback that runs when the thread or fiber goes away). */
static DWORD arena_tls_fls = FLS_OUT_OF_INDEXES;
static INIT_ONCE arena_tls_fls_once = INIT_ONCE_STATIC_INIT;

static void NTAPI arena_tls_fls_cb(void* p) {
    if (!p) return;
    xfer_free_all();
    arena_free_stack((arena_stack*)p);
    /* The worker's own root arena goes with it (see the POSIX path). */
    arena_free_stack(&tls_root);
    /* The thread is on its way out: any further allocation from it
     * would rebuild an arena behind the (already unwound) frames, so
     * fail loudly instead, exactly like the main-thread shutdown. */
    leasharena_tls_arena_dead = 1;
}

static BOOL CALLBACK arena_tls_fls_init(PINIT_ONCE once, PVOID param, PVOID* ctx) {
    (void)once; (void)param; (void)ctx;
    arena_tls_fls = FlsAlloc(arena_tls_fls_cb);
    return TRUE;
}
#endif

void leash_gc_thread_attach(void) {
#if !defined(_WIN32)
    /* Register this thread's arena for release at thread exit. */
    pthread_once(&arena_tls_key_once, arena_tls_make_key);
    pthread_setspecific(arena_tls_key, &leasharena_tls_ar);
#else
    /* Same contract on Windows: the FLS callback frees the arena when
     * the worker thread exits. */
    InitOnceExecuteOnce(&arena_tls_fls_once, arena_tls_fls_init, NULL, NULL);
    if (arena_tls_fls != FLS_OUT_OF_INDEXES) {
        FlsSetValue(arena_tls_fls, &leasharena_tls_ar);
    }
#endif
}
void leash_gc_worker_begin(void) { arena_ensure_stack(&leasharena_tls_ar); }
void leash_gc_worker_end(void) {}
size_t leash_gc_get_allocated(void) { return leasharena_tls_ar.bytes + tls_root.bytes; }
size_t leash_gc_get_object_count(void) { return 0; }
void leash_gc_print_stats(void) {
    fprintf(stderr, "Arena stats: %zu bytes live (current) + %zu (root), %zu checkpoints\n",
            leasharena_tls_ar.bytes, tls_root.bytes, leasharena_tls_ar.cp_len);
}
void leash_gc_verify(void) {}

#elif defined(NO_GC)
/* ===== Stub mode: plain malloc/free, no GC tracking ===== */

void leash_gc_init(void) {}
void leash_gc_shutdown(void) {}
void leasharena_try_free_solo(void* ptr, size_t old_size_bytes) { (void)ptr; (void)old_size_bytes; }
/* The spawn transfer is an arena-only mechanism: the GC reclaims the
 * argument block on its own. The stub keeps the shared spawn path
 * linkable in every mode. */
void leasharena_xfer_free(void* block) { (void)block; }

void* leash_gc_malloc(size_t size) {
    if (size == 0) return NULL;
    void* p = malloc(size);
    if (!p) { fprintf(stderr, "Leash: Out of memory!\n"); abort(); }
    memset(p, 0, size);
    return p;
}

void* leash_gc_malloc_nz(size_t size) {
    if (size == 0) return NULL;
    void* p = malloc(size);
    if (!p) { fprintf(stderr, "Leash: Out of memory!\n"); abort(); }
    return p;
}

void* leash_gc_malloc_compact(size_t size) { return leash_gc_malloc(size); }

/* The GC backend must keep the zero-fill: a fresh cell is scanned by
 * the collector before the program writes it. */
void* leash_gc_malloc_compact_nz(size_t size) { return leash_gc_malloc(size); }

void* leash_gc_malloc_rooted_nz(size_t size, size_t alignment) {
    (void)alignment;
    return leash_gc_malloc(size);
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
void* leash_gc_alloc_string_cap(size_t len, size_t cap) {
    /* Stub mode (malloc-backed, --no-gc/--autofree): strings are plain
     * malloc'd C strings with NO 8-byte prefix. Autofree frees tracked
     * pointers directly, so data must equal the malloc base. GC mode
     * (below #else) uses the [u64 len][bytes][NUL] prefix layout; all
     * shared prefix stores/deletes are guarded with #ifndef NO_GC so
     * they never touch base-8 here. */
    (void)cap;
    char* data = (char*)leash_gc_malloc(len + 1);
    data[len] = '\0';
    return (void*)data;
}
void* leash_gc_alloc_string(size_t len) { return leash_gc_alloc_string_cap(len, len); }
int64_t leash_str_bsize(const char* s) { if (!s) return 0; return (int64_t)strlen(s); }
void leash_str_share(const char* s) { (void)s; }
char* leash_str_append(char* s, const char* piece) {
    size_t sl = s ? strlen(s) : 0;
    size_t pl = piece ? strlen(piece) : 0;
    size_t tot = sl + pl;
    char* out = (char*)leash_gc_alloc_string_cap(tot, tot);
    if (sl) memcpy(out, s, sl);
    if (pl) memcpy(out + sl, piece, pl);
    out[tot] = '\0';
    return out;
}
char* leash_str_concat_ex(const char* s1, const char* s2) {
    if (!s1) s1 = "";
    if (!s2) s2 = "";
    size_t l1 = strlen(s1), l2 = strlen(s2);
    size_t tot = l1 + l2;
    char* out = (char*)leash_gc_alloc_string_cap(tot, tot);
    memcpy(out, s1, l1);
    memcpy(out + l1, s2, l2);
    out[tot] = '\0';
    return out;
}
char* leash_str_append_int(char* s, int64_t v, int is_uint) {
    char buf[32];
    if (is_uint) snprintf(buf, sizeof(buf), "%llu", (unsigned long long)v);
    else snprintf(buf, sizeof(buf), "%lld", (long long)v);
    return leash_str_append(s, buf);
}
char* leash_str_append_char(char* s, char c) {
    char buf[2]; buf[0] = c; buf[1] = '\0';
    return leash_str_append(s, buf);
}
char* leash_int_to_str_fast(int64_t v) {
    char buf[32]; int n = snprintf(buf, sizeof(buf), "%lld", (long long)v);
    char* out = (char*)leash_gc_alloc_string((size_t)n);
    memcpy(out, buf, (size_t)n + 1);
    return out;
}
char* leash_uint_to_str_fast(uint64_t v) {
    char buf[32]; int n = snprintf(buf, sizeof(buf), "%llu", (unsigned long long)v);
    char* out = (char*)leash_gc_alloc_string((size_t)n);
    memcpy(out, buf, (size_t)n + 1);
    return out;
}
char* leash_str_concat_int(const char* s, int64_t v, int is_uint) {
    char buf[32];
    if (is_uint) snprintf(buf, sizeof(buf), "%llu", (unsigned long long)v);
    else snprintf(buf, sizeof(buf), "%lld", (long long)v);
    return leash_str_concat_ex(s ? s : "", buf);
}
/* NOTE: leash_sh_input is defined once in the shared region after the
 * NO_GC #endif (it uses leash_gc_alloc_string_cap, provided in both
 * modes). Do not redefine it here: two definitions in a NO_GC build
 * are a hard redefinition error. */
void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) { return leash_gc_malloc(elem_size * capacity); }
void leash_gc_thread_spawned(void) {}
void leash_gc_thread_attach(void) {}
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
static LEASH_TLS int gc_i_hold_lock = 0;
static LEASH_TLS int gc_thread_worker = 0;
#else
static pthread_mutex_t gc_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_t gc_main_thread;
static LEASH_TLS int gc_i_hold_lock = 0;
static LEASH_TLS int gc_thread_worker = 0;
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

void leash_gc_thread_attach(void) {
    /* M6: explicit hook for threads created by native libraries (FFI) that
       will call back into Leash code. Marking the thread as a worker and
       flipping the multithreaded flag BEFORE its first GC operation makes
       the switch to locked mode deterministic; without this, the first
       GC touch raced any in-flight lock-free section on the main thread.
       The remaining window (main thread already inside a lock-free
       section when an UNANNOUNCED foreign thread first touches the GC)
       cannot be closed from the foreign side alone — native code spawning
       callbacks into Leash should call this from the new thread. */
    gc_thread_worker = 1;
    leash_gc_thread_spawned();
}

/* ===== Configuration (lean: minimum peak RSS) =====
 *  - INITIAL_THRESHOLD: floor on bytes allocated between collections.
 *    Small (256 KB) keeps churn workloads near live-set size.
 *  - SLAB_NOMINAL_SIZE: per-size-class slab mapping. Small slabs (16 KB)
 *    cut pinning waste: one live cell pins at most 16 KB, not 64 KB.
 *  - gc_should_auto_collect collects once trash ~= live (see below), so
 *    steady-state peak stays <= ~2x live + 256 KB instead of the old
 *    2x live + 2 MB. GC runs more often but each scan is cheap for the
 *    small live sets that dominate churn. */
#define INITIAL_THRESHOLD (256 * 1024)  /* 256KB floor between auto-collections */
#define SLAB_NOMINAL_SIZE (16 * 1024)   /* 16KB slabs, rounded to page size */
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
    size_t bytes_since_probe;  /* separate counter for /proc RSS probe */
    size_t live_bytes;
    size_t threshold_floor;
    unsigned long long gc_time_ns;
    /* environment */
    char* stack_top;            /* conservative scan upper bound (NULL = unknown) */
    int auto_collect;
    size_t max_rss_bytes;      /* hard host-protection ceiling (0 = off) */
} gc = {0};

/* ===== Hard RSS ceiling =====
 * Every leash-compiled program links this GC. If resident memory exceeds
 * LEASH_GC_MAX_RSS_MB (default 1024), we force an emergency collection;
 * if RSS is still above the ceiling afterwards, we abort rather than
 * eat the host machine. This fires even when auto-collect is disabled. */
static size_t gc_rss_bytes(void) {
#if !defined(_WIN32)
    FILE* f = fopen("/proc/self/statm", "r");
    if (!f) return 0;
    unsigned long total_pages = 0, resident_pages = 0;
    int ok = fscanf(f, "%lu %lu", &total_pages, &resident_pages);
    fclose(f);
    if (ok != 2) return 0;
    long ps = sysconf(_SC_PAGESIZE);
    size_t page = ps > 0 ? (size_t)ps : 4096;
    return (size_t)resident_pages * page;
#else
    return 0;
#endif
}

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
    gc.bytes_since_probe += size;
    return payload;
}

static void* gc_alloc_large(size_t size, unsigned int flags, size_t alignment) {
    if (alignment < 16) alignment = 16;
    /* round alignment up to a power of two. The old loop capped at 2 MiB
       and silently under-satisfied anything larger. */
    size_t al = 16;
    while (al < alignment && al < ((size_t)1 << 62)) al <<= 1;
    if (al < alignment) {
        fprintf(stderr, "Leash GC: unsupported alignment %zu\n", alignment);
        abort();
    }
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
    gc.bytes_since_probe += size;
    return payload;
}

static void gc_free_cell_payload(void* payload) {
    struct gc_cell* c = (struct gc_cell*)((char*)payload - CELL_HDR);
    /* M8: a double free pushed the same cell onto the freelist twice (or
       built a self-referential list), handing it to two live objects. */
    if (!(c->flags & CELL_IN_USE)) return;
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
    if (!(c->flags & CELL_IN_USE)) return; /* double free */
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

void* leash_gc_malloc_nz(size_t size) {
    return leash_gc_malloc_ex(size, 0);
}

void* leash_gc_malloc_compact(size_t size) {
    return leash_gc_malloc_ex(size, 0);
}

/* The GC backend must keep the zero-fill: a fresh cell is scanned by
 * the collector before the program writes it. */
void* leash_gc_malloc_compact_nz(size_t size) {
    return leash_gc_malloc_ex(size, 0);
}

void* leash_gc_malloc_rooted_nz(size_t size, size_t alignment) {
    (void)alignment;
    return leash_gc_malloc_rooted(size);
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
#ifdef _WIN32
static volatile DWORD gc_stack_top_thread = 0;
#else
static volatile pthread_t gc_stack_top_thread;
#endif

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
    if (top) {
        gc.stack_top = top;
#ifdef _WIN32
        gc_stack_top_thread = GetCurrentThreadId();
#else
        gc_stack_top_thread = pthread_self();
#endif
    } else {
        /* M7: a failed capture must NOT keep another thread's (higher)
           top: scanning from this thread's frame up to that address
           crosses an unmapped gap. Invalidate instead. */
        gc.stack_top = NULL;
#ifdef _WIN32
        gc_stack_top_thread = 0;
#else
        memset((void*)&gc_stack_top_thread, 0, sizeof(gc_stack_top_thread));
#endif
    }
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
#if defined(__SANITIZE_ADDRESS__) /* GCC */
#  define GC_NO_ASAN __attribute__((no_sanitize_address))
#elif defined(__has_feature) /* clang */
#  if __has_feature(address_sanitizer)
#    define GC_NO_ASAN __attribute__((no_sanitize_address))
#  endif
#endif
#ifndef GC_NO_ASAN
#  define GC_NO_ASAN
#endif

/* The conservative scanner deliberately reads raw stack/heap bytes; ASan
   frame redzones make that look like an overflow, so exclude the scanning
   entry points the same way Boehm GC does. Everything else (allocator,
   memmove, freelist code) stays fully sanitized. */
static GC_NO_ASAN void gc_mark_words(const void* start, size_t nbytes) {
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

static GC_NO_ASAN void gc_mark_stack(void) {
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
    int stack_owner_ok;
#ifdef _WIN32
    stack_owner_ok = (gc_stack_top_thread == GetCurrentThreadId());
#else
    stack_owner_ok = pthread_equal(gc_stack_top_thread, pthread_self()) != 0;
#endif
    if (!gc.stack_top || !stack_owner_ok || &probe > gc.stack_top) {
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
    gc.bytes_since_probe = 0;
    gc.collect_count++;
    gc.gc_time_ns += gc_now_ns() - t0;
    gc_in_collect = 0;

    /* Host protection: if resident memory is still above the ceiling after
     * a full collection, the program is genuinely leaking (or the limit is
     * too small). Abort instead of pushing the machine into OOM death. */
    if (gc.max_rss_bytes != 0) {
        size_t rss = gc_rss_bytes();
        if (rss > gc.max_rss_bytes) {
            fprintf(stderr,
                    "Leash GC: memory ceiling exceeded (%.0f MB used, limit %.0f MB)\n"
                    "Set LEASH_GC_MAX_RSS_MB higher if this program legitimately needs more.\n",
                    (double)rss / (1024.0 * 1024.0),
                    (double)gc.max_rss_bytes / (1024.0 * 1024.0));
            fflush(stderr);
            exit(70);
        }
    }

#if defined(__GLIBC__)
    /* Return freed libc arena pages to the OS after big sweeps. */
    {
        extern int malloc_trim(size_t);
        malloc_trim(0);
    }
#endif
}

/* ===== Auto-collection gating ===== */
static int gc_quiescent(void) {
    if (gc_atomic_load(&gc_active_workers) != 0) return 0;
    if (gc_atomic_load(&gc_foreign_threads) != 0) return 0;
    return 1;
}

static int gc_should_auto_collect(void) {
    if (gc_in_collect) return 0;
    /* Host protection: force a collection when resident memory blows past
     * the ceiling. Probed at most about once per MB allocated so the
     * /proc read never shows up in profiles. Uses bytes_since_probe so
     * the probe never resets the GC threshold accounting. */
    if (gc.max_rss_bytes != 0 && gc.bytes_since_probe >= ((size_t)1 << 20)) {
        gc.bytes_since_probe = 0;
        if (gc_rss_bytes() > gc.max_rss_bytes) return 1;
    }
    if (!gc.auto_collect) return 0;
    if (!gc_quiescent()) return 0;
    /* Lean: collect after allocating roughly the live size again
     * (plus the 256 KB floor), so peak stays near 2x live + 256 KB
     * instead of 2x live + 2 MB. */
    size_t threshold = gc.live_bytes + (size_t)gc.threshold_floor;
    if (threshold < (size_t)gc.threshold_floor) threshold = (size_t)gc.threshold_floor;
    return gc.bytes_since_gc > threshold;
}

/* ===== String/Vector helpers ===== */
/* Growable string layout (GC heap strings only):
 * GC payload = [u64 len_with_shared][bytes cap][NUL]
 * Data pointer = payload+8. Len lower 63 bits = byte length,
 * top bit = shared (COW) flag. Cap = gc_cell.size - 9.
 * Literals (rodata) have no prefix and fall back to strlen. */
#define STR_LEN_MASK ((uint64_t)((1ULL<<63)-1))
#define STR_SHARED_BIT (1ULL<<63)

void* leash_gc_alloc_string_cap(size_t len, size_t cap) {
    if (len > SIZE_MAX - 10) {
        fprintf(stderr, "Leash GC: string allocation too large!\n");
        abort();
    }
    if (cap < len) cap = len;
    void* payload = leash_gc_malloc_ex(8 + cap + 1, FLAG_ATOMIC);
    uint64_t* hdr = (uint64_t*)payload;
    *hdr = (uint64_t)len;
    char* data = (char*)payload + 8;
    data[len] = '\0';
    return (void*)data;
}

void* leash_gc_alloc_string(size_t len) {
    return leash_gc_alloc_string_cap(len, len);
}

/* O(1) byte length: GC strings carry len in 8B prefix, so reading it
 * avoids an O(n) strlen scan. Literals (rodata) and FFI strings have
 * no prefix and fall back to strlen.
 * The lookup is taken under the GC lock so concurrent page-dir growth
 * cannot race the probe (the lock is free on the single-thread path). */
int64_t leash_str_bsize(const char* s) {
    if (!s) return 0;
    /* Try prefix path: s-8 must be start of a GC payload. */
    if ((uintptr_t)s >= 8) {
        GC_LOCK();
        const char* payload = s - 8;
        struct gc_cell* c = gc_find_object(payload);
        if (c && (const char*)c + CELL_HDR == payload && c->size >= 9) {
            uint64_t raw = *(const uint64_t*)payload;
            uint64_t len = raw & STR_LEN_MASK;
            /* Validate len against the NUL terminator at s[len].
             * No interior scan: prefix strings never contain NUL
             * by construction (all creators use memcpy/strlen lengths). */
            if (len <= (uint64_t)(c->size - 9) && s[len] == '\0') {
                GC_UNLOCK();
                return (int64_t)len;
            }
        }
        GC_UNLOCK();
    }
    return (int64_t)strlen(s);
}

void leash_str_share(const char* s) {
    if (!s) return;
    if ((uintptr_t)s < 8) return;
    GC_LOCK();
    const char* payload = s - 8;
    struct gc_cell* c = gc_find_object(payload);
    if (c && (const char*)c + CELL_HDR == payload) {
        uint64_t* hdr = (uint64_t*)payload;
        *hdr |= STR_SHARED_BIT;
    }
    GC_UNLOCK();
}

char* leash_str_append(char* s, const char* piece) {
    if (!s) {
        /* Append to NULL == copy of piece (lean cap: exact for small). */
        size_t plen = (size_t)leash_str_bsize(piece);
        size_t cap = plen < 32 ? 32 : plen + (plen >> 2);
        char* out = (char*)leash_gc_alloc_string_cap(plen, cap);
        if (plen) memcpy(out, piece ? piece : "", plen);
        out[plen] = '\0';
        return out;
    }
    if (!piece) return s;
    /* Single probe: derive sl and cap from the GC prefix instead of
     * leash_str_bsize's probe + a second probe in the cap check below. */
    GC_LOCK();
    const char* payload = s - 8;
    struct gc_cell* c = NULL;
    if ((uintptr_t)s >= 8) {
        c = gc_find_object(payload);
    }
    int64_t sl = 0;
    int can_inplace = 0;
    size_t cap = 0;
    if (c && (const char*)c + CELL_HDR == payload && c->size >= 9) {
        uint64_t raw = *(const uint64_t*)payload;
        uint64_t len = raw & STR_LEN_MASK;
        int shared = (raw & STR_SHARED_BIT) != 0;
        cap = c->size - 9;
        /* Validate len with the NUL terminator (bsize's check). */
        if (len <= cap && s[len] == '\0') {
            sl = (int64_t)len;
            if (!shared) can_inplace = 1;
        } else {
            sl = (int64_t)strlen(s);
        }
    } else {
        sl = (int64_t)strlen(s);
    }
    GC_UNLOCK();
    int64_t pl = leash_str_bsize(piece);
    int64_t tot = sl + pl;
    if (can_inplace && (size_t)tot <= cap) {
        /* In-place: no copy of prefix. */
        if (pl > 0) memcpy(s + sl, piece, (size_t)pl);
        s[tot] = '\0';
        /* Update len prefix (keep shared bit clear). */
        *(uint64_t*)(s - 8) = (uint64_t)tot;
        return s;
    }
    /* Copy path: lean geometric growth. Double while small (fast
     * startup), then +25% so the final buffer wastes <=25% instead
     * of 100%. Minimum 32 keeps tiny appends in one alloc. */
    size_t t = (size_t)tot;
    size_t new_cap = t;
    if (new_cap < 32) new_cap = 32;
    else if (cap == 0 || cap < 256) {
        size_t doubled = cap ? cap * 2 : t;
        if (doubled > new_cap) new_cap = doubled;
    } else {
        size_t grown = cap + (cap >> 2);
        if (grown > new_cap) new_cap = grown;
    }
    char* out = (char*)leash_gc_alloc_string_cap(t, new_cap);
    if (sl > 0) memcpy(out, s, (size_t)sl);
    if (pl > 0) memcpy(out + sl, piece, (size_t)pl);
    out[tot] = '\0';
    return out;
}

char* leash_str_concat_ex(const char* s1, const char* s2) {
    if (!s1) s1 = "";
    if (!s2) s2 = "";
    int64_t l1 = leash_str_bsize(s1);
    int64_t l2 = leash_str_bsize(s2);
    int64_t tot = l1 + l2;
    size_t t = (size_t)tot;
    /* Exact cap: concat results are typically consumed once (printed,
     * stored, or used as a hash key), so spare cap would just waste
     * slab until the next append. The next append reallocs once. */
    size_t cap = t;
    char* out = (char*)leash_gc_alloc_string_cap(t, cap);
    if (l1 > 0) memcpy(out, s1, (size_t)l1);
    if (l2 > 0) memcpy(out + l1, s2, (size_t)l2);
    out[tot] = '\0';
    return out;
}

/* Forward declarations for digit counting (full GC branch defines them
 * below at line ~1571). Declared here so NOI_GC's append_int and the GC
 * copy path can use them regardless of define order. */
static int _int_digits_i64(int64_t v);
static int _uint_digits(uint64_t v);

char* leash_str_append_int(char* s, int64_t v, int is_uint) {
    const char* empty = "";
    if (!s) s = (char*)empty;
    int d = is_uint ? _uint_digits((uint64_t)v) : _int_digits_i64(v);
    /* Reuse leash_str_append's in-place logic by constructing a
     * temporary digit buffer on stack, then appending once. This
     * avoids the extra heap allocation of leash_int_to_str. */
    char buf[32];
    if (is_uint) {
        uint64_t u = (uint64_t)v;
        if (u == 0) { buf[0] = '0'; buf[1] = '\0'; }
        else {
            int pos = d;
            buf[pos] = '\0';
            while (u) { buf[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    } else {
        if (v == 0) { buf[0] = '0'; buf[1] = '\0'; }
        else {
            int neg = v < 0;
            uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
            int pos = d;
            buf[pos] = '\0';
            if (neg) { buf[0] = '-'; pos = d; }
            while (u) { buf[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    }
    return leash_str_append(s, buf);
}

char* leash_str_append_char(char* s, char c) {
    /* Fast path (hot per-char loop): probe the GC prefix once and append
     * in place when the string is exclusive-owned with spare capacity.
     * Avoids buf + double bsize + double probe of the generic append. */
    if (s && (uintptr_t)s >= 8) {
        const char* payload = s - 8;
        GC_LOCK();
        struct gc_cell* cell = gc_find_object(payload);
        int ok = (cell && (const char*)cell + CELL_HDR == payload && cell->size >= 9);
        uint64_t raw = ok ? *(const uint64_t*)payload : 0;
        size_t cap = ok ? cell->size - 9 : 0;
        GC_UNLOCK();
        if (ok) {
            uint64_t len = raw & STR_LEN_MASK;
            int shared = (raw & STR_SHARED_BIT) != 0;
            if (!shared && len < cap && len <= (uint64_t)cap && s[len] == '\0') {
                s[len] = c;
                s[len + 1] = '\0';
                *(uint64_t*)(void*)(s - 8) = (uint64_t)(len + 1);
                return s;
            }
        }
    }
    {
        char buf[2];
        buf[0] = c;
        buf[1] = '\0';
        return leash_str_append(s, buf);
    }
}

/* Fast decimal conversion, no snprintf. Allocates exact-cap string. */
static int _int_digits_i64(int64_t v) {
    if (v == 0) return 1;
    int neg = v < 0;
    uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
    int d = neg ? 1 : 0;
    while (u) { d++; u /= 10; }
    return d;
}
static int _uint_digits(uint64_t v) {
    if (v == 0) return 1;
    int d = 0;
    while (v) { d++; v /= 10; }
    return d;
}
char* leash_int_to_str_fast(int64_t v) {
    int d = _int_digits_i64(v);
    char* out = (char*)leash_gc_alloc_string((size_t)d);
    if (v == 0) { out[0] = '0'; out[1] = '\0'; return out; }
    int neg = v < 0;
    uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
    int pos = d;
    out[pos] = '\0';
    if (neg) { out[0] = '-'; pos = d; }
    else pos = d;
    /* pos is capped at d; writing goes to pos-1 .. (neg?1:0). */
    while (u) { out[--pos] = (char)('0' + (u % 10)); u /= 10; }
    return out;
}
char* leash_uint_to_str_fast(uint64_t v) {
    int d = _uint_digits(v);
    char* out = (char*)leash_gc_alloc_string((size_t)d);
    if (v == 0) { out[0] = '0'; out[1] = '\0'; return out; }
    int pos = d;
    out[pos] = '\0';
    while (v) { out[--pos] = (char)('0' + (v % 10)); v /= 10; }
    return out;
}
char* leash_str_concat_int(const char* s, int64_t v, int is_uint) {
    if (!s) s = "";
    int64_t sl = leash_str_bsize(s);
    int d = is_uint ? _uint_digits((uint64_t)v) : _int_digits_i64(v);
    int64_t tot = sl + d;
    size_t t = (size_t)tot;
    char* out = (char*)leash_gc_alloc_string_cap(t, t);
    if (sl > 0) memcpy(out, s, (size_t)sl);
    char* tail = out + sl;
    if (is_uint) {
        uint64_t u = (uint64_t)v;
        if (u == 0) { tail[0] = '0'; }
        else {
            int pos = d;
            while (u) { tail[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    } else {
        if (v == 0) { tail[0] = '0'; }
        else {
            int neg = v < 0;
            uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
            int pos = d;
            if (neg) { tail[0] = '-'; pos = d; }
            while (u) { tail[--pos] = (char)('0' + (u % 10)); u /= 10; }
        }
    }
    out[tot] = '\0';
    return out;
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
    env = getenv("LEASH_GC_MAX_RSS_MB");
    if (env && *env) {
        long mb = strtol(env, NULL, 10);
        if (mb > 0) gc.max_rss_bytes = (size_t)mb << 20;
        if (mb == 0) gc.max_rss_bytes = 0; /* explicitly disabled */
    } else {
        gc.max_rss_bytes = (size_t)1024 << 20; /* default 1 GiB ceiling */
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
    gc.bytes_since_probe = 0;
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

void leasharena_try_free_solo(void* ptr, size_t old_size_bytes) { (void)ptr; (void)old_size_bytes; }

/* The spawn transfer is an arena-only mechanism: the GC reclaims the
 * argument block on its own. The stub keeps the shared spawn path
 * linkable in every mode. */
void leasharena_xfer_free(void* block) { (void)block; }

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

#endif

/* Shared helpers below are compiled in all modes. GC-mode strings
 * carry an 8-byte length prefix at s-8 (populated by the mode's
 * allocators); arena-mode strings carry len at s-8 too (total at
 * s-16); stub-mode strings are plain malloc'd C strings with no
 * prefix and bsize via strlen. Every prefix store in the shared
 * region must use LEASH_STR_SET_PREFIX so stub builds never write
 * into base-8 heap metadata. */
#ifdef NO_GC
#define LEASH_STR_SET_PREFIX(ptr, n) ((void)0)
#else
#define LEASH_STR_SET_PREFIX(ptr, n) *(uint64_t*)((ptr) - 8) = (uint64_t)(n)
#endif


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
    /* Guard against overflow of the byte count. The old signed cast
       made SIZE_MAX/(size_t)1 come out as -1, so every copy with 1-byte
       elements aborted. */
    if ((uint64_t)n > (uint64_t)SIZE_MAX / (uint64_t)elem_size) {
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
    if (size <= 1 || elem_size <= 0) return;
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
    /* A negative size used to become a huge size_t and walk qsort far out
       of bounds. */
    if (size <= 1) return;
    qsort(data, (size_t)size, sizeof(int32_t), i32_cmp);
}

static int i64_cmp(const void* a, const void* b) {
    int64_t va = *(const int64_t*)a, vb = *(const int64_t*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_i64(int64_t* data, int64_t size) {
    /* A negative size used to become a huge size_t and walk qsort far out
       of bounds. */
    if (size <= 1) return;
    qsort(data, (size_t)size, sizeof(int64_t), i64_cmp);
}

static int f32_cmp(const void* a, const void* b) {
    float va = *(const float*)a, vb = *(const float*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_f32(float* data, int64_t size) {
    /* A negative size used to become a huge size_t and walk qsort far out
       of bounds. */
    if (size <= 1) return;
    qsort(data, (size_t)size, sizeof(float), f32_cmp);
}

static int f64_cmp(const void* a, const void* b) {
    double va = *(const double*)a, vb = *(const double*)b;
    return (va > vb) - (va < vb);
}

void leash_vec_sort_f64(double* data, int64_t size) {
    /* A negative size used to become a huge size_t and walk qsort far out
       of bounds. */
    if (size <= 1) return;
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
    /* M9: leash_gc_malloc_rooted ALREADY added the future to gc.roots;
       registering again made every future a permanent double root (its
       await unregisters only once), so futures — and everything they
       keep alive — leaked forever. */
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

/* =========================================================================
 * Leash Self-Hosted Compiler Runtime Helpers: Vec, Hash, File, Exec
 * ========================================================================= */

typedef struct LeashVec {
    void*   data;       /* element array, byte-addressed */
    int64_t size;
    int64_t cap;
    int32_t elem_size; /* bytes per element (>= 1) */
    int8_t scan;       /* 1: data may contain GC pointers */
    /* Arena only: the header is reachable from a second holder (another
     * variable, a container slot, a call argument). A root slot that
     * overwrites its value must not write over a header somebody else
     * can still see, so the flag suppresses the in-place reuse. The
     * field lives in the old padding: the layout the compiler's IR
     * assumes (data, size, cap, elem_size, scan) is unchanged. */
    uint8_t shared;
    uint8_t _pad[2];
} LeashVec;

#ifndef FLAG_ATOMIC
#define FLAG_ATOMIC 0x02U  /* matches the real GC's pointer-free payload flag */
#endif

/* Element slot address (byte addressing for any element width). */
static inline void* lv_at(const LeashVec* v, int64_t i) {
    return (char*)v->data + (size_t)i * (size_t)v->elem_size;
}

/* Data buffers of pointer-free elements are ATOMIC: the collector never
 * scans them (precise tracing — no false pins from raw integer payloads)
 * and marking them costs nothing. */
static void* lv_alloc_data(int64_t cap, int64_t esz, int64_t scan) {
    if (esz <= 0) esz = 8;
    if (cap < 0) cap = 0;
    /* `cap * esz` feeds both an allocation size and a memcpy length. A
     * wrapped product would allocate a tiny buffer that element writes
     * then overrun. */
    if ((uint64_t)cap > (uint64_t)(SIZE_MAX / (size_t)esz)) {
        fprintf(stderr, "Leash: vector allocation too large\n");
        abort();
    }
#if defined(LEASH_ARENA)
    if ((uint64_t)cap > (uint64_t)ARENA_MAX_ALLOC / (uint64_t)esz) {
        fprintf(stderr, "Leash arena: vector allocation too large\n");
        abort();
    }
    /* The arena never scans: the GC's zero-fill exists so a fresh
     * cell can be traced safely. A vec writes every live element
     * before it is read and bounds every access by v->size, so the
     * unused capacity is never observed. Skip the fill. */
    (void)scan;
    return arena_alloc_nz(&leasharena_tls_ar, (size_t)cap * (size_t)esz, ARENA_ALIGN);
#else
    return leash_gc_malloc_ex((size_t)cap * (size_t)esz,
                               scan ? 0 : FLAG_ATOMIC);
#endif
}

static void leash_sh_vec_ensure(void* vec_ptr, int64_t need);

/* stdin as an opaque handle: on Windows `stdin` is not a linkable symbol,
   so generated code must go through here instead of @stdin. */
void* leash_sh_stdin(void) {
    return (void*)stdin;
}

/* The console input helper.
 *
 * This is a STRONG definition on purpose: it used to be weak so a
 * NO_GC build could define it twice (it now lives once in the shared
 * region, so that is no longer needed), but a weak definition does not
 * satisfy a strong reference in COFF/PE: every Windows build that
 * called the input helper failed to link with "reference undefined to
 * leash_sh_input" (the compiler itself is such a program). */
char* leash_sh_input(const char* prompt) {
    if (prompt) {
        fwrite(prompt, 1, prompt ? strlen(prompt) : 0, stdout);
        fflush(stdout);
    }
    char* buf = (char*)leash_gc_alloc_string_cap(4096, 4096);
    buf[0] = '\0';
    if (!fgets(buf, 4096, stdin)) {
        LEASH_STR_SET_PREFIX(buf, 0);
        return buf;
    }
    size_t len = strlen(buf);
    while (len > 0 && (buf[len - 1] == '\n' || buf[len - 1] == '\r')) {
        buf[--len] = '\0';
    }
    LEASH_STR_SET_PREFIX(buf, len);
    return buf;
}

void* leash_sh_vec_new(int64_t elem_size, int64_t scan) {
    LeashVec* v = (LeashVec*)leash_gc_malloc_compact_nz(sizeof(LeashVec));
    v->elem_size = elem_size > 0 ? elem_size : 8;
    v->scan = scan ? 1 : 0;
    /* Lazy data: never-pushed vectors cost one small header only. The
     * first reserve/ensure allocates the buffer. */
    v->cap = 0;
    v->size = 0;
    v->data = NULL;
    v->shared = 0;
    return (void*)v;
}

/* Mark a vec header as reachable from a second holder. A root slot
 * that overwrites its value writes over the previous root copy only
 * when that copy is exclusively owned; the flag says otherwise. */
void leash_sh_vec_share(void* vec_ptr) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (v) v->shared = 1;
}

/* Slow path for pushb: grow when full, then byte-copy the element in. */
void leash_sh_vec_pushb(void* vec_ptr, const void* elem) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !elem) return;
    leash_sh_vec_ensure(v, v->size + 1);
    memcpy(lv_at(v, v->size), elem, (size_t)v->elem_size);
    v->size++;
}

void leash_sh_vec_get(void* vec_ptr, int64_t idx, void* out) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !v->data || idx < 0 || idx >= v->size || !out) {
        fprintf(stderr, "Runtime error: Vector index out of bounds.\n");
        exit(1);
    }
    memcpy(out, lv_at(v, idx), (size_t)v->elem_size);
}

void leash_sh_vec_set(void* vec_ptr, int64_t idx, const void* elem) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !v->data || idx < 0 || idx >= v->size || !elem) {
        fprintf(stderr, "Runtime error: Vector index out of bounds.\n");
        exit(1);
    }
    memcpy(lv_at(v, idx), elem, (size_t)v->elem_size);
}

void leash_sh_vec_remove(void* vec_ptr, int64_t idx, void* out) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !v->data) return;
    if (idx < 0) idx += v->size;
    if (idx < 0 || idx >= v->size) {
        fprintf(stderr, "Runtime error: Vector remove index out of bounds.\n");
        exit(1);
    }
    if (out) memcpy(out, lv_at(v, idx), (size_t)v->elem_size);
    if (idx + 1 < v->size) {
        memmove(lv_at(v, idx), lv_at(v, idx + 1),
                (size_t)(v->size - idx - 1) * (size_t)v->elem_size);
    }
    v->size--;
}

/* GC-allocating wrapper around leash_bigint_fmt: returns a decimal string. */
char* leash_bigint_to_str(const unsigned char* bytes, int64_t nbits, int64_t is_signed) {
    size_t cap;
    char* out;
    if (nbits > 512) nbits = 512;
    cap = (size_t)(nbits / 3 + 8);
    out = (char*)leash_gc_alloc_string_cap(cap, cap);
    leash_bigint_fmt(out, bytes, (unsigned)nbits, (int)is_signed);
    size_t len = strlen(out);
    LEASH_STR_SET_PREFIX(out, len);
    return out;
}

/* =========================================================================
 * showb: buffered output. Content accumulates in a private buffer and is
 * flushed once at program exit (Python parity).
 * ========================================================================= */

static char* leash_showb_buf = NULL;
static size_t leash_showb_len = 0;
static size_t leash_showb_cap = 0;

void leash_sh_showb_append(const char* s) {
    size_t n;
    if (!s) return;
    n = strlen(s);
    if (leash_showb_len + n + 1 > leash_showb_cap) {
        size_t nc = leash_showb_cap ? leash_showb_cap * 2 : 256;
        while (nc < leash_showb_len + n + 1) nc *= 2;
        leash_showb_buf = (char*)realloc(leash_showb_buf, nc);
        leash_showb_cap = nc;
    }
    memcpy(leash_showb_buf + leash_showb_len, s, n);
    leash_showb_len += n;
    leash_showb_buf[leash_showb_len] = 0;
}

void leash_sh_showb_flush(void) {
    if (leash_showb_len > 0 && leash_showb_buf) {
        fwrite(leash_showb_buf, 1, leash_showb_len, stdout);
    }
}

/* showb format for vectors: elements appended with NO separators.
 * mode: 0=int, 1=string, 2=float, 3=char, 4=bool */
void leash_sh_vec_append_buf(void* vec_ptr, int64_t mode) {
    LeashVec* v = (LeashVec*)vec_ptr;
    int64_t i;
    char tmp[64];
    if (!v) return;
    for (i = 0; i < v->size; i++) {
        void* slot = lv_at(v, i);
        int64_t iv = 0;
        double d = 0.0;
        switch (mode) {
        case 1:
            leash_sh_showb_append(*(const char* const*)slot ? *(const char* const*)slot : "");
            break;
        case 2:
            memcpy(&d, slot, sizeof(d));
            snprintf(tmp, sizeof tmp, "%f", d);
            leash_sh_showb_append(tmp);
            break;
        case 3:
            tmp[0] = *(const char*)slot;
            tmp[1] = 0;
            leash_sh_showb_append(tmp);
            break;
        case 4:
            leash_sh_showb_append(*(const unsigned char*)slot ? "true" : "false");
            break;
        default:
            memset(&iv, 0, sizeof(iv));
            memcpy(&iv, slot, (size_t)(v->elem_size < 8 ? v->elem_size : 8));
            snprintf(tmp, sizeof tmp, "%lld", (long long)iv);
            leash_sh_showb_append(tmp);
            break;
        }
    }
}

void leash_sh_vec_append_buf_wide(void* vec_ptr, int64_t nbits, int64_t is_signed) {
    LeashVec* v = (LeashVec*)vec_ptr;
    int64_t i;
    if (!v) return;
    for (i = 0; i < v->size; i++) {
        char* str = leash_bigint_to_str((const unsigned char*)lv_at(v, i), nbits, is_signed);
        leash_sh_showb_append(str);
    }
}

/* Structural show() for vectors: "[e1, e2, ...]".
 * mode: 0=int, 1=string, 2=float, 3=char, 4=bool */
void leash_sh_vec_print(void* vec_ptr, int64_t mode) {
    LeashVec* v = (LeashVec*)vec_ptr;
    int64_t i;
    if (!v) { printf("[]"); return; }
    putchar('[');
    for (i = 0; i < v->size; i++) {
        void* slot = lv_at(v, i);
        int64_t iv = 0;
        double d = 0.0;
        if (i) printf(", ");
        switch (mode) {
        case 1:
            printf("%s", *(const char* const*)slot ? *(const char* const*)slot : "");
            break;
        case 2:
            memcpy(&d, slot, sizeof(d));
            printf("%f", d);
            break;
        case 3:
            printf("%c", *(const char*)slot);
            break;
        case 4:
            printf("%s", *(const unsigned char*)slot ? "true" : "false");
            break;
        default:
            memset(&iv, 0, sizeof(iv));
            memcpy(&iv, slot, (size_t)(v->elem_size < 8 ? v->elem_size : 8));
            printf("%lld", (long long)iv);
            break;
        }
    }
    putchar(']');
}

/* Structural show() for vectors of wide ints (raw inline elements). */
void leash_sh_vec_print_wide(void* vec_ptr, int64_t nbits, int64_t is_signed) {
    LeashVec* v = (LeashVec*)vec_ptr;
    int64_t i;
    if (!v) { printf("[]"); return; }
    putchar('[');
    for (i = 0; i < v->size; i++) {
        if (i) printf(", ");
        char* str = leash_bigint_to_str((const unsigned char*)lv_at(v, i), nbits, is_signed);
        printf("%s", str);
    }
    putchar(']');
}


void leash_sh_vec_popb(void* vec_ptr, void* out) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || v->size <= 0 || !out) {
        fprintf(stderr, "Runtime error: popb called on empty vector.\n");
        exit(1);
    }
    v->size--;
    memcpy(out, lv_at(v, v->size), (size_t)v->elem_size);
}

/* Capacity policy for a growth step: the new element count.
 * Bulk reservations grow to exactly `need`; incremental pushb doubles
 * for small/medium buffers (fewer growth events = less dead slack in
 * shared blocks when non-tip, and power-of-two finals for fixed-size
 * builds), but uses +50% for large buffers where tighter final capacity
 * dominates. */
static int64_t lv_grow_cap(const LeashVec* v, int64_t need) {
    int64_t nc = v->cap ? v->cap : 8;
#ifdef LEASH_ARENA
    if (need - v->size > 8) {
        nc = need > nc ? need : nc;
        if (v->cap > 0 && v->cap * 2 > nc) { nc = v->cap * 2; }
    } else {
        while (nc < need) {
            if (nc < 1024) {
                nc *= 2;
            } else if ((size_t)need * (size_t)v->elem_size > (size_t)512 * 1024) {
                nc += nc / 2;
            } else {
                nc *= 2;
            }
        }
    }
#else
    while (nc < need) {
        if (nc < 1024) nc *= 2;
        else nc += nc / 2;
    }
#endif
    return nc;
}

static void leash_sh_vec_ensure(void* vec_ptr, int64_t need) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || v->cap >= need) return;
    int64_t nc = lv_grow_cap(v, need);
    if (v->data) {
        /* Grow via realloc: arena tip-extends when the old buffer is
         * the bump (the pushb steady state), avoiding a copy + abandon;
         * GC reallocates and frees the old cell immediately. The buffer
         * is reallocated even when the vector is empty: a buffer with
         * capacity but no elements (after popb, say) must not be
         * abandoned, or the arena keeps the old bytes until the scope
         * ends (and the GC keeps the old cell alive until the next
         * collection). */
        void* nd = leash_gc_realloc(v->data, (size_t)nc * (size_t)v->elem_size);
        v->data = nd;
        v->cap = nc;
        return;
    }
    void* nd = lv_alloc_data(nc, v->elem_size, v->scan);
    v->data = nd;
    v->cap = nc;
}

/* Ensure capacity for `need` elements, allocating the growth buffer in
 * the ROOT arena. Used for vecs owned outside the current frame: the
 * buffer must outlive the frame that grows it, and a scope-arena
 * allocation dies with that frame (leaving the caller-owned vec with a
 * dangling data pointer). */
static void lv_ensure_rooted(void* vec_ptr, int64_t need) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || v->cap >= need) return;
    int64_t nc = lv_grow_cap(v, need);
    size_t total = (size_t)nc * (size_t)v->elem_size;
    void* nd = NULL;
#if defined(LEASH_ARENA)
    if (v->data) {
        /* Still at the root arena's tip? Extend in place: no copy, and
         * the superseded buffer never accumulates in the root arena. */
        nd = arena_grow_tip(&tls_root, v->data,
                            (size_t)v->cap * (size_t)v->elem_size, total);
    }
    if (!nd) nd = leash_gc_malloc_rooted_nz(total, ARENA_ALIGN);
#else
    if (!v->data || v->cap == 0) {
        v->data = lv_alloc_data(nc, v->elem_size, v->scan);
        v->cap = nc;
        return;
    }
    nd = leash_gc_malloc_rooted(total);
#endif
    if (!nd) {
        leash_sh_vec_ensure(vec_ptr, need);
        return;
    }
    void* old_data = v->data;
#if defined(LEASH_ARENA)
    size_t old_bytes = (size_t)v->cap * (size_t)v->elem_size;
#endif
    size_t ncopy = 0;
    if (old_data && v->size > 0) {
        ncopy = (size_t)v->size * (size_t)v->elem_size;
        if (ncopy > total) ncopy = total;
        if (nd != old_data) memcpy(nd, old_data, ncopy);
    }
#if defined(LEASH_ARENA)
    /* The arena never scans: nothing treats the unused capacity as a
     * set of roots, so the tail does not have to be zeroed. The GC
     * backend scans root buffers conservatively and does need it. */
#else
    /* Root buffers are conservatively scanned; zero the tail so stale
     * pointers in unused capacity are not treated as live roots. */
    size_t ntail = total - ncopy;
    if (ntail) memset((char*)nd + ncopy, 0, ntail);
#endif
    v->data = nd;
    v->cap = nc;
#if defined(LEASH_ARENA)
    /* The root arena never rewinds its bump pointer, so a superseded
     * buffer would stay resident for the rest of the program. Reclaim
     * it when it is a dedicated single-allocation block. */
    if (old_data && nd != old_data) arena_try_free_solo(&tls_root, old_data, old_bytes);
#endif
}

/* extend: append `len` elements from a packed source buffer (elements laid
   out at src_elem_size bytes). Matching widths take the one-memcpy path. */
static void lv_extend(void* vec_ptr, const void* src, int64_t len, int64_t src_elem_size, int rooted) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !src || len <= 0) return;
    if (rooted) lv_ensure_rooted(v, v->size + len);
    else leash_sh_vec_ensure(v, v->size + len);
    if (src_elem_size == v->elem_size) {
        memcpy(lv_at(v, v->size), src, (size_t)len * (size_t)v->elem_size);
        v->size += len;
        return;
    }
    const unsigned char* p = (const unsigned char*)src;
    int64_t i;
    size_t cp = (size_t)(src_elem_size < v->elem_size ? src_elem_size : v->elem_size);
    for (i = 0; i < len; i++) {
        memset(lv_at(v, v->size + i), 0, (size_t)v->elem_size);
        memcpy(lv_at(v, v->size + i), p + (size_t)i * (size_t)src_elem_size, cp);
    }
    v->size += len;
}

void leash_sh_vec_extend(void* vec_ptr, const void* src, int64_t len, int64_t src_elem_size) {
    lv_extend(vec_ptr, src, len, src_elem_size, 0);
}

/* Rooted extend: the target vec is owned outside the current frame, so
 * its growth buffer must outlive this frame. */
void leash_sh_vec_extend_rooted(void* vec_ptr, const void* src, int64_t len, int64_t src_elem_size) {
    lv_extend(vec_ptr, src, len, src_elem_size, 1);
}

/* insertv: splice all elements of another vec at idx (negative idx wraps). */
static void lv_insertv(void* vec_ptr, int64_t idx, void* other_vec_ptr, int rooted) {
    LeashVec* v = (LeashVec*)vec_ptr;
    LeashVec* o = (LeashVec*)other_vec_ptr;
    if (!v || !o || o->size <= 0) return;
    if (idx < 0) idx += v->size;
    if (idx < 0) idx = 0;
    if (idx > v->size) idx = v->size;
    if (rooted) lv_ensure_rooted(v, v->size + o->size);
    else leash_sh_vec_ensure(v, v->size + o->size);
    if (v->elem_size == o->elem_size) {
        memmove(lv_at(v, idx + o->size), lv_at(v, idx),
                (size_t)(v->size - idx) * (size_t)v->elem_size);
        memcpy(lv_at(v, idx), o->data, (size_t)o->size * (size_t)o->elem_size);
    } else {
        int64_t i;
        for (i = o->size - 1; i >= 0; i--) {
            memset(lv_at(v, idx + v->size), 0, (size_t)v->elem_size);
            memcpy(lv_at(v, idx + v->size), lv_at(o, i),
                   (size_t)(o->elem_size < v->elem_size ? o->elem_size : v->elem_size));
        }
    }
    v->size += o->size;
}

void leash_sh_vec_insertv(void* vec_ptr, int64_t idx, void* other_vec_ptr) {
    lv_insertv(vec_ptr, idx, other_vec_ptr, 0);
}

/* Rooted insertv: the target vec is owned outside the current frame, so
 * its growth buffer must outlive this frame. */
void leash_sh_vec_insertv_rooted(void* vec_ptr, int64_t idx, void* other_vec_ptr) {
    lv_insertv(vec_ptr, idx, other_vec_ptr, 1);
}

/* Public growth entry: ensure capacity for `need` elements. Called by the
 * compiler's inline pushb fast path when the vector is full. */
void leash_sh_vec_reserve(void* vec_ptr, int64_t need) {
    leash_sh_vec_ensure(vec_ptr, need);
}

/* Rooted reserve for vecs owned outside the current frame (parameters):
 * the growth buffer must outlive the callee that grows it, so it comes
 * from the root arena (never freed). */
void leash_sh_vec_reserve_rooted(void* vec_ptr, int64_t need) {
    lv_ensure_rooted(vec_ptr, need);
}

void* leash_sh_vec_from_parts(void* data, int64_t size, int64_t elem_size, int64_t scan) {
    LeashVec* v = (LeashVec*)leash_gc_malloc_compact_nz(sizeof(LeashVec));
    v->elem_size = elem_size > 0 ? elem_size : 8;
    v->scan = scan ? 1 : 0;
    v->size = size;
    v->cap = size > 0 ? size : 8;
    v->shared = 0;
    /* Takes ownership of `data`; empty input falls back to a fresh buffer. */
    if (data && size > 0) {
        v->data = data;
    } else {
        v->data = lv_alloc_data(v->cap, v->elem_size, v->scan);
        v->size = 0;
    }
    return (void*)v;
}

/* Value-semantics copy for struct-typed params/assignments (python parity). */
void* leash_sh_clone_obj(void* p, int64_t n) {
    if (!p || n <= 0) return p;
    void* q = leash_gc_malloc_nz((size_t)n);
    if (!q) return p;
    memcpy(q, p, (size_t)n);
    return q;
}

int64_t leash_sh_vec_size(void* vec_ptr) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v) return 0;
    return v->size;
}

#if defined(LEASH_ARENA) && defined(LEASH_ARENA_O3)
/* O3+ arena layout: the node carries no key pointer. The key bytes are
 * stored inline right after the node, so the pointer is derived from
 * the node address instead of stored: 8 bytes less per entry. Arena
 * nodes are never relocated nor freed one by one, so the stored
 * pointer could never dangle -- it was pure overhead. */
typedef struct LeashHashNode {
    void* val;
    struct LeashHashNode* next;
    uint64_t hash;
} LeashHashNode;
#define LEASH_HASH_KEY(n) ((char*)((n) + 1))
#define LEASH_HASH_KEY_INIT(n) ((void)(n))
#define LEASH_HASH_KEY_SLOT(n) (&(char*){LEASH_HASH_KEY(n)})
#else
typedef struct LeashHashNode {
    char* key;
    void* val;
    struct LeashHashNode* next;
    uint64_t hash;
} LeashHashNode;
#define LEASH_HASH_KEY(n) ((n)->key)
#define LEASH_HASH_KEY_INIT(n) ((n)->key = (char*)((char*)(n) + sizeof(LeashHashNode)))
#define LEASH_HASH_KEY_SLOT(n) (&(n)->key)
#endif

/* A staged node carries pointers and a u64 hash, so each node image must
 * start 8-byte aligned. Round the stride (node struct + inline key) up to
 * 8 in BOTH the sizing pass and the copy pass; overflowing the stage by a
 * few bytes or leaving a later node misaligned is undefined behaviour on
 * targets that require aligned 64-bit accesses. */
#define AR_HASH_NODE_SZ(klen) \
    (((sizeof(LeashHashNode) + (size_t)(klen) + 1) + 7) & ~(size_t)7)

typedef struct LeashHash {
    LeashHashNode** buckets;
    int64_t num_buckets;
    int64_t count;
    /* Arena only: the table's storage lives in the root arena, so it
     * outlives the frame that mutates it. A hash owned outside the
     * current frame (a parameter, a global) must be rooted before a
     * callee inserts into it -- otherwise the node the callee allocates
     * dies with the callee's frame and the table keeps a dangling
     * pointer. */
    int64_t rooted;
} LeashHash;

/* LeashHash.rooted is a bit set: bit 0 marks a table whose storage
 * lives in the root arena, bit 1 marks a header that a second holder
 * can still reach (see leash_sh_hash_share). */
#define LH_HASH_ROOTED ((int64_t)1)
#define LH_HASH_SHARED ((int64_t)2)

/* Mark a hash header as reachable from a second holder (see
 * leash_sh_vec_share). The flag only steers the arena's root slots; the
 * GC backend has no root reuse and ignores it. */
void leash_sh_hash_share(void* hash_ptr) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (h) h->rooted |= LH_HASH_SHARED;
}

/* Allocate hash storage in the arena the hash lives in: the root arena
 * for a rooted table, the current frame's arena otherwise. */
static void* hash_alloc(const LeashHash* h, size_t size) {
#if defined(LEASH_ARENA)
    if (h && (h->rooted & LH_HASH_ROOTED)) return leash_gc_malloc_rooted(size);
#else
    (void)h;
#endif
    return leash_gc_malloc_compact(size);
}

/* Allocate hash storage without the zero-fill. Only for a payload the
 * caller writes in full before anything can read it (a node: key,
 * hash, value and chain pointer are all assigned). Bucket arrays keep
 * the zeroing path: they are read before they are written. */
static void* hash_alloc_nz(const LeashHash* h, size_t size) {
#if defined(LEASH_ARENA)
    if (h && (h->rooted & LH_HASH_ROOTED)) return leash_gc_malloc_rooted_nz(size, (size_t)8);
    return leash_gc_malloc_compact_nz(size);
#else
    (void)h;
    (void)size;
    return leash_gc_malloc_compact(size);
#endif
}

/* FxHash-like: fast, good distribution for short "keyN" strings. */
static inline uint64_t _leash_sh_str_hash_len(const char* s, size_t len) {
    /* FxHash: fast for short keys (common in arena workloads). */
    uint64_t h = 0x517cc1b727220a95ULL + (uint64_t)len;
    while (len >= 8) {
        uint64_t w;
        memcpy(&w, s, 8);
        h = (h ^ w) * 0x517cc1b727220a95ULL;
        s += 8;
        len -= 8;
    }
    if (len >= 4) {
        uint32_t w;
        memcpy(&w, s, 4);
        h = (h ^ (uint64_t)w) * 0x517cc1b727220a95ULL;
        s += 4;
        len -= 4;
    }
    while (len--) {
        h = (h ^ (uint64_t)(uint8_t)(*s++)) * 0x517cc1b727220a95ULL;
    }
    h ^= h >> 32;
    return h;
}
static inline uint64_t _leash_sh_str_hash(const char* s) {
    if (!s) return 0;
    return _leash_sh_str_hash_len(s, strlen(s));
}

void* leash_sh_hash_new(void) {
    LeashHash* h = (LeashHash*)leash_gc_malloc_compact_nz(sizeof(LeashHash));
    h->num_buckets = 64;
    h->count = 0;
    h->rooted = 0;
    h->buckets = (LeashHashNode**)leash_gc_malloc_compact(h->num_buckets * sizeof(LeashHashNode*));
    return (void*)h;
}

/* Move a hash table's storage into the root arena and mark it rooted.
 *
 * A hash owned outside the current frame (a parameter, a global) can be
 * mutated by a callee. The callee's frame ends when it returns, so any
 * node it allocates in the scope arena dies with it and the table is
 * left pointing at freed memory. Relocating the table into the root
 * arena (program lifetime) makes the mutation safe.
 *
 * The values the nodes point at are the compiler's business: it roots
 * them before the insert (see ar_root_value). */
void leash_sh_hash_make_rooted(void* hash_ptr) {
#if defined(LEASH_ARENA)
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || (h->rooted & LH_HASH_ROOTED)) return;
    int64_t nb = h->num_buckets > 0 ? h->num_buckets : 64;
    LeashHashNode** nbuckets = (LeashHashNode**)leash_gc_malloc_rooted((size_t)nb * sizeof(LeashHashNode*));
    if (!nbuckets) return;
    memset(nbuckets, 0, (size_t)nb * sizeof(LeashHashNode*));
    if (h->buckets) {
        for (int64_t i = 0; i < h->num_buckets; i++) {
            LeashHashNode* cur = h->buckets[i];
            LeashHashNode** tail = &nbuckets[i];
            while (cur) {
                LeashHashNode* next = cur->next;
                size_t klen = strlen(LEASH_HASH_KEY(cur));
                size_t sz = sizeof(LeashHashNode) + klen + 1;
                LeashHashNode* nn = (LeashHashNode*)leash_gc_malloc_rooted(sz);
                if (!nn) { tail = &cur->next; cur = next; continue; }
                LEASH_HASH_KEY_INIT(nn);
                memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(cur), klen + 1);
                nn->val = cur->val;
                nn->hash = cur->hash;
                nn->next = NULL;
                *tail = nn;
                tail = &nn->next;
                cur = next;
            }
        }
    }
    h->buckets = nbuckets;
    h->num_buckets = nb;
    h->rooted = LH_HASH_ROOTED;
#else
    (void)hash_ptr;
#endif
}

/* Pre-size a hash table for `n` entries.
 *
 * The bucket array grows by doubling; every growth abandons the old
 * array. In the arena that abandoned ladder is dead weight that stays
 * resident until the scope ends, so a table filled in one loop pays for
 * every rung of the ladder (a 20k-entry table allocates 64..32768
 * buckets, ~520 KB of which only the last 256 KB is live). Knowing the
 * entry count up front lets the table allocate its final bucket array
 * once.
 *
 * The arena's realloc grows the array in place when it is still the bump
 * tip, so the initial 64-bucket array is usually reused rather than
 * abandoned. */
void leash_sh_hash_reserve(void* hash_ptr, int64_t n) {
#if defined(LEASH_ARENA)
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || n <= 0) return;
    /* Keep the load factor under 3/4, like the growth path. */
    int64_t need = h->num_buckets > 0 ? h->num_buckets : 64;
    while (need * 3 / 4 < n) {
        need *= 2;
        if (need > ((int64_t)1 << 30)) return;
    }
    if (need <= h->num_buckets) return;
    if (h->count == 0) {
        /* Empty table: grow the bucket array in place when it is still
         * the arena's bump tip (the usual case: the table was created
         * just before the loop that fills it). */
        LeashHashNode** nb = (LeashHashNode**)leash_gc_realloc(
            h->buckets, (size_t)need * sizeof(LeashHashNode*));
        if (!nb) return;
        memset(nb, 0, (size_t)need * sizeof(LeashHashNode*));
        h->buckets = nb;
        h->num_buckets = need;
        return;
    }
    /* Non-empty table: allocate the final array and rehash into it. */
    int64_t old_num = h->num_buckets;
    LeashHashNode** old_b = h->buckets;
    LeashHashNode** new_b = (LeashHashNode**)hash_alloc(
        h, (size_t)need * sizeof(LeashHashNode*));
    if (!new_b) return;
    for (int64_t i = 0; i < old_num; i++) {
        LeashHashNode* node = old_b[i];
        while (node) {
            LeashHashNode* next = node->next;
            int64_t nidx = (int64_t)(node->hash & (uint64_t)(need - 1));
            node->next = new_b[nidx];
            new_b[nidx] = node;
            node = next;
        }
    }
    h->buckets = new_b;
    h->num_buckets = need;
#else
    (void)hash_ptr;
    (void)n;
#endif
}

// Scalar hash keys: the runtime hash is string-keyed, so int/uint/float
// keys are stringified through these helpers before lookup/insert.
char* leash_sh_int_key(int64_t v) {
    return leash_int_to_str_fast(v);
}

char* leash_sh_uint_key(uint64_t v) {
    return leash_uint_to_str_fast(v);
}

char* leash_sh_float_key(double v) {
    char buf[48];
    int n = snprintf(buf, sizeof(buf), "%.17g", v);
    if (n < 0) n = 0;
    char* out = (char*)leash_gc_alloc_string((size_t)n);
    memcpy(out, buf, (size_t)n);
    out[n] = '\0';
    return out;
}

/* Insert a key/value pair. The key bytes are copied into the node, so
 * the caller may pass a temporary buffer -- the fused path below hands
 * over a stack buffer. `klen` and `hash` are the caller's, so the key
 * does not have to be a heap string. */
static void leash_sh_hash_insert(LeashHash* h, const char* key, size_t klen, uint64_t hash, void* val) {
    if (!h->buckets) {
        h->num_buckets = 64;
        h->buckets = (LeashHashNode**)hash_alloc(h, h->num_buckets * sizeof(LeashHashNode*));
    }
    int64_t idx = (int64_t)(hash & (uint64_t)(h->num_buckets - 1));
    LeashHashNode* cur = h->buckets[idx];
    while (cur) {
        /* The node key is an inline, NUL-terminated byte string -- NOT a
         * runtime string object (it has no length prefix). Asking the
         * string helpers for its length would read whatever bytes sit
         * in front of it: in arena mode that is the node's own fields,
         * which can look like a string header and yield a bogus length
         * (breaking the duplicate-key check) or even an out-of-bounds
         * read. Compare the bytes and the terminator instead. */
        if (cur->hash == hash && memcmp(LEASH_HASH_KEY(cur), key, klen) == 0 && LEASH_HASH_KEY(cur)[klen] == '\0') {
            cur->val = val;
            return;
        }
        cur = cur->next;
    }
    if (h->count >= h->num_buckets * 3 / 4) {
        int64_t old_num = h->num_buckets;
        LeashHashNode** old_b = h->buckets;
        int64_t new_num = old_num * 2;
        LeashHashNode** new_b = (LeashHashNode**)hash_alloc(h, new_num * sizeof(LeashHashNode*));
        if (!new_b) return;
        for (int64_t i = 0; i < old_num; i++) {
            LeashHashNode* node = old_b[i];
            while (node) {
                LeashHashNode* next = node->next;
                int64_t nidx = (int64_t)(node->hash & (uint64_t)(new_num - 1));
                node->next = new_b[nidx];
                new_b[nidx] = node;
                node = next;
            }
        }
        h->buckets = new_b;
        h->num_buckets = new_num;
        idx = (int64_t)(hash & (uint64_t)(new_num - 1));
#if defined(LEASH_ARENA)
        leasharena_try_free_solo(old_b, (size_t)old_num * sizeof(LeashHashNode*));
#endif
    }
    /* Single allocation for node + key payload (key bytes follow struct).
     * The node is written in full below (key bytes, hash, value and
     * chain pointer), so the arena can skip the zero-fill. */
    size_t alloc_sz = sizeof(LeashHashNode) + klen + 1;
    LeashHashNode* node = (LeashHashNode*)hash_alloc_nz(h, alloc_sz);
    if (!node) return;
    LEASH_HASH_KEY_INIT(node);
    memcpy(LEASH_HASH_KEY(node), key, klen);
    LEASH_HASH_KEY(node)[klen] = '\0';
    /* Patch prefix len for key string (prefix at key-8? No: key here is
     * inline after struct, not a full GC string. For bsize we rely on
     * strlen fallback for inline keys, so do not write prefix. Ensure
     * memcmp path uses klen comparison, not bsize prefix. */
    node->hash = hash;
    node->val = val;
    node->next = h->buckets[idx];
    h->buckets[idx] = node;
    h->count++;
}

void leash_sh_hash_push(void* hash_ptr, const char* key, void* val) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !key) return;
    int64_t klen_i = leash_str_bsize(key);
    size_t klen = klen_i >= 0 ? (size_t)klen_i : strlen(key);
    uint64_t hash = _leash_sh_str_hash_len(key, klen);
    leash_sh_hash_insert(h, key, klen, hash, val);
}

/* Look up a key whose length the caller already knows. */
static void* leash_sh_hash_lookup(LeashHash* h, const char* key, size_t klen, uint64_t hash) {
    if (!h || !h->buckets || !key) return NULL;
    int64_t idx = (int64_t)(hash & (uint64_t)(h->num_buckets - 1));
    LeashHashNode* cur = h->buckets[idx];
    while (cur) {
        if (cur->hash == hash && memcmp(LEASH_HASH_KEY(cur), key, klen) == 0 && LEASH_HASH_KEY(cur)[klen] == '\0') {
            return cur->val;
        }
        cur = cur->next;
    }
    return NULL;
}

void* leash_sh_hash_get(void* hash_ptr, const char* key) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !key) return NULL;
    int64_t klen_i = leash_str_bsize(key);
    size_t klen = klen_i >= 0 ? (size_t)klen_i : strlen(key);
    uint64_t hash = _leash_sh_str_hash_len(key, klen);
    return leash_sh_hash_lookup(h, key, klen, hash);
}

int64_t leash_sh_hash_isin(void* hash_ptr, const char* key) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !h->buckets || !key) return 0;
    int64_t klen_i = leash_str_bsize(key);
    size_t klen = klen_i >= 0 ? (size_t)klen_i : strlen(key);
    uint64_t hash = _leash_sh_str_hash_len(key, klen);
    int64_t idx = (int64_t)(hash & (uint64_t)(h->num_buckets - 1));
    LeashHashNode* cur = h->buckets[idx];
    while (cur) {
        if (cur->hash == hash && memcmp(LEASH_HASH_KEY(cur), key, klen) == 0 && LEASH_HASH_KEY(cur)[klen] == '\0') {
            return 1;
        }
        cur = cur->next;
    }
    return 0;
}

int64_t leash_sh_hash_size(void* hash_ptr) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h) return 0;
    return h->count;
}

void* leash_sh_hash_getkey(void* hash_ptr, void* val) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !h->buckets) return NULL;
    for (int64_t i = 0; i < h->num_buckets; i++) {
        LeashHashNode* cur = h->buckets[i];
        while (cur) {
            if (cur->val == val) return (void*)LEASH_HASH_KEY(cur);
            cur = cur->next;
        }
    }
    return NULL;
}

#if defined(LEASH_ARENA) && defined(LEASH_ARENA_O3)
/* Fused hash keys: build "<prefix><decimal>" in a stack buffer and hand
 * it to the table directly.
 *
 * The arena never reclaims a temporary before its scope ends, so the
 * ordinary path -- build the key string, copy it into the node -- keeps
 * every temporary key resident for the whole loop that fills the table.
 * Building the key on the stack removes the temporary entirely: the key
 * bytes are written once, into the node, and nothing else is allocated.
 * This is arena-only because in the GC the temporary is collected
 * anyway; here it is the difference between a table that fits and one
 * that does not. */
#define LEASH_FUSED_KEY_MAX 96

/* Write the decimal form of v at out; returns the byte count. */
static size_t leash_fmt_i64(char* out, int64_t v, int is_uint) {
    size_t d = is_uint ? _ar_uint_digits((uint64_t)v) : _ar_int_digits_i64(v);
    if (is_uint) {
        uint64_t u = (uint64_t)v;
        if (u == 0) { out[0] = '0'; return 1; }
        size_t pos = d;
        while (u) { out[--pos] = (char)('0' + (u % 10)); u /= 10; }
    } else {
        if (v == 0) { out[0] = '0'; return 1; }
        int neg = v < 0;
        uint64_t u = neg ? (uint64_t)(-(v + 1)) + 1 : (uint64_t)v;
        size_t pos = d;
        if (neg) { out[0] = '-'; pos = d; }
        while (u) { out[--pos] = (char)('0' + (u % 10)); u /= 10; }
    }
    return d;
}

/* Build prefix + decimal(v) into buf. Returns the key length, or 0 when
 * the key does not fit (the caller falls back to the ordinary path). */
static size_t leash_fused_key(char* buf, size_t bufsz, const char* prefix, int64_t v, int is_uint) {
    size_t plen = (size_t)leash_str_bsize(prefix);
    /* 24 bytes covers any int64 in decimal, sign included. */
    if (plen + 24 > bufsz) return 0;
    if (plen) memcpy(buf, prefix, plen);
    size_t d = leash_fmt_i64(buf + plen, v, is_uint);
    size_t klen = plen + d;
    buf[klen] = '\0';
    return klen;
}

void leash_sh_hash_push_int(void* hash_ptr, const char* prefix, int64_t v, int is_uint, void* val) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !prefix) return;
    char buf[LEASH_FUSED_KEY_MAX + 1];
    size_t klen = leash_fused_key(buf, sizeof(buf), prefix, v, is_uint);
    if (klen == 0) {
        /* Long prefix: build the key the ordinary way. */
        leash_sh_hash_push(h, leash_str_concat_int(prefix, v, is_uint), val);
        return;
    }
    uint64_t hash = _leash_sh_str_hash_len(buf, klen);
    leash_sh_hash_insert(h, buf, klen, hash, val);
}

void* leash_sh_hash_get_int(void* hash_ptr, const char* prefix, int64_t v, int is_uint) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !prefix) return NULL;
    char buf[LEASH_FUSED_KEY_MAX + 1];
    size_t klen = leash_fused_key(buf, sizeof(buf), prefix, v, is_uint);
    if (klen == 0) {
        return leash_sh_hash_get(h, leash_str_concat_int(prefix, v, is_uint));
    }
    uint64_t hash = _leash_sh_str_hash_len(buf, klen);
    return leash_sh_hash_lookup(h, buf, klen, hash);
}
#endif /* LEASH_ARENA && LEASH_ARENA_O3 */

void leash_sh_hash_delete(void* hash_ptr, const char* key) {
    LeashHash* h = (LeashHash*)hash_ptr;
    if (!h || !h->buckets) return;
    int64_t klen_i = leash_str_bsize(key);
    size_t klen = klen_i >= 0 ? (size_t)klen_i : strlen(key);
    uint64_t hash = _leash_sh_str_hash_len(key, klen);
    int64_t idx = (int64_t)(hash & (uint64_t)(h->num_buckets - 1));
    LeashHashNode* cur = h->buckets[idx];
    LeashHashNode* prev = NULL;
    while (cur) {
        if (cur->hash == hash && memcmp(LEASH_HASH_KEY(cur), key, klen) == 0 && LEASH_HASH_KEY(cur)[klen] == '\0') {
            if (prev) prev->next = cur->next;
            else h->buckets[idx] = cur->next;
            h->count--;
            return;
        }
        prev = cur;
        cur = cur->next;
    }
}

#ifdef LEASH_ARENA
/* ===== Arena escape helpers (shared region, arena mode only) =====
 * Generated code calls these at return sites to copy a heap value out
 * of the current scopes into the caller's frame. Each helper stages a
 * copy of the value, pops nframes, allocates the image in the new top
 * frame, copies it in, releases the stage, and stores the new pointer
 * back through slot. Strings publish a 16-byte [total|magic][len]
 * header (see the arena branch); vectors copy header + data; hashes
 * copy buckets + nodes (keys inline, vals shared — see the escape rule
 * in the docs).
 *
 * The stage only has to survive the scope pop inside this one call, so
 * it does not have to live on the heap: small payloads (the common
 * case) are staged in the C stack frame of the helper and never touch
 * the allocator at all. Only large payloads fall back to malloc. */

/* Stack staging limit. Beyond this the copy would bloat every frame
 * of a recursive call chain, so big payloads use the heap. */
#define ARENA_ESCAPE_STACK_MAX ((size_t)2048)
/* Composite stages (a vec or a hash carries a header plus a payload
 * copy plus an index table) need more room than a plain string, but
 * they are still far cheaper on the stack than in the heap: the escape
 * helper's frame lives across the scope pop, so the staged copy stays
 * valid until the commit. */
#define ARENA_STAGE_STACK_MAX ((size_t)8192)

/* alloca for the escape staging path.
 *
 * MSVC has _alloca (in <malloc.h>); every other supported compiler
 * (gcc/clang, including the MinGW cross builds) has __builtin_alloca,
 * which needs no header. The old test only covered _MSC_VER, so the
 * Windows (MinGW) arena build failed to compile: <alloca.h> does not
 * exist there. */
#if defined(_MSC_VER)
#include <malloc.h>
#define ARENA_ALLOCA(n) _alloca(n)
#elif defined(__GNUC__) || defined(__clang__)
#define ARENA_ALLOCA(n) __builtin_alloca(n)
#else
#include <alloca.h>
#define ARENA_ALLOCA(n) alloca(n)
#endif

/* Stage `n` bytes: on the stack when small, on the heap otherwise.
 * `heapvar` is set to 1 when the caller must free the stage.
 *
 * This is a macro on purpose: alloca() releases its memory when the
 * function that called it returns, so the allocation has to happen in
 * the escape helper's own frame -- a helper *function* would hand back
 * a pointer to memory that died with it. */
#define ARENA_STAGE_BYTES(nbytes, heapvar, capvar, outvar)                  \
    do {                                                                    \
        size_t ar_stage_n = (nbytes);                                       \
        if (ar_stage_n <= ARENA_ESCAPE_STACK_MAX) {                         \
            (heapvar) = 0;                                                  \
            (capvar) = 0;                                                   \
            (outvar) = (char*)ARENA_ALLOCA(ar_stage_n);                     \
        } else {                                                            \
            (heapvar) = 1;                                                  \
            (outvar) = (char*)arena_stage_alloc(ar_stage_n, &(capvar));     \
        }                                                                   \
    } while (0)

/* Same, with a caller-chosen limit (composite stages). */
#define ARENA_STAGE_BYTES_MAX(nbytes, limit, heapvar, capvar, outvar)      \
    do {                                                                    \
        size_t ar_stage_n = (nbytes);                                       \
        if (ar_stage_n <= (limit)) {                                        \
            (heapvar) = 0;                                                  \
            (capvar) = 0;                                                   \
            (outvar) = (char*)ARENA_ALLOCA(ar_stage_n);                     \
        } else {                                                            \
            (heapvar) = 1;                                                  \
            (outvar) = (char*)arena_stage_alloc(ar_stage_n, &(capvar));     \
        }                                                                   \
    } while (0)

/* ---- per-thread stage cache ----
 *
 * The stage/commit pair (the escape helpers) copies a value that must
 * survive a scope pop into a temporary block, then copies it into the
 * new frame and releases the block again. A program that returns
 * structs or tuples with heap fields does this on every call, so the
 * allocator round trips dominate the cost of the escape.
 *
 * The cache keeps the released blocks around and hands them back to the
 * next stage of a similar size: the first few calls allocate, then the
 * steady state does not touch the heap allocator at all. The blocks are
 * per thread (no locking) and are released when the thread's arena is
 * released (program shutdown, worker exit). */
#define ARENA_STAGE_CACHE_SLOTS 16

/* Blocks larger than this are not cached: one huge escape stage would
 * otherwise stay resident for the rest of the thread's life even when
 * the program returns a single big value. */
#define ARENA_STAGE_CACHE_MAX ((size_t)256 * 1024)

typedef struct arena_stage_cache_entry {
    void* ptr;
    size_t bytes;   /* actual capacity of `ptr`, not the last request */
} arena_stage_cache_entry;

static ARENA_TLS arena_stage_cache_entry arena_stage_cache[ARENA_STAGE_CACHE_SLOTS];
static ARENA_TLS int arena_stage_cache_len;

/* Take a block of at least `bytes` from the cache (or malloc one). The
 * returned capacity is reported through *cap_out so the caller can hand
 * the same number back to arena_stage_free: recording the requested
 * size instead of the real capacity would shrink the cached block every
 * time it is reused.
 *
 * A stage block is the only copy of a value that must survive a scope
 * pop, so returning NULL on OOM would leave the escape helpers with a
 * dangling slot (they pop the scope and then cannot commit). Abort like
 * every other arena OOM instead: deterministic failure, no silent
 * corruption. */
static void* arena_stage_alloc(size_t bytes, size_t* cap_out) {
    if (cap_out) *cap_out = bytes;
    for (int i = 0; i < arena_stage_cache_len; i++) {
        /* Reuse a block that fits without wasting much: a block at
         * most twice the request keeps the cache useful for mixed
         * payload sizes without pinning a large block for a small
         * request. */
        if (arena_stage_cache[i].bytes >= bytes &&
            arena_stage_cache[i].bytes <= bytes * 2) {
            void* p = arena_stage_cache[i].ptr;
            size_t cap = arena_stage_cache[i].bytes;
            arena_stage_cache[i] = arena_stage_cache[arena_stage_cache_len - 1];
            arena_stage_cache_len--;
            if (cap_out) *cap_out = cap;
            return p;
        }
    }
    void* p = malloc(bytes);
    if (!p) arena_oom("escape stage");
    if (cap_out) *cap_out = bytes;
    return p;
}

/* Release a block back to the cache (or free it when the cache is
 * full). `cap` is the block's real capacity (see arena_stage_alloc);
 * pass the exact value the allocator reported, not the last request. */
static void arena_stage_free(void* p, size_t cap) {
    if (!p) return;
    if (cap <= ARENA_STAGE_CACHE_MAX &&
        arena_stage_cache_len < ARENA_STAGE_CACHE_SLOTS) {
        arena_stage_cache[arena_stage_cache_len].ptr = p;
        arena_stage_cache[arena_stage_cache_len].bytes = cap;
        arena_stage_cache_len++;
        return;
    }
    free(p);
}

/* Release every cached block. Called when the thread's arena goes
 * away (program shutdown, worker-thread exit). */
static void arena_stage_cache_release(void) {
    for (int i = 0; i < arena_stage_cache_len; i++) {
        free(arena_stage_cache[i].ptr);
        arena_stage_cache[i].ptr = NULL;
        arena_stage_cache[i].bytes = 0;
    }
    arena_stage_cache_len = 0;
}

/* Stage strings and blobs carry their real cached capacity in a word in
 * front of the payload so every release path returns the exact capacity
 * to the cache (see the contract on arena_stage_free). */
static void arena_stage_prefixed_free(void* stage) {
    if (!stage) return;
    char* block = (char*)stage - sizeof(size_t);
    size_t cap = *(size_t*)(void*)block;
    arena_stage_free(block, cap);
}

/* A vec stage is one contiguous block: [header][element copy][string
 * pool][entry table]. The single-value escape path hands the helper a
 * stack buffer (no allocation at all); the tuple path, whose stage has
 * to survive the scope pop, hands it one malloc'd block. Either way the
 * stage costs ONE allocation instead of the four the old code made. */
typedef struct arena_vec_stage {
    LeashVec hdr;
    char* data;            /* copy of the live element bytes */
    size_t data_bytes;     /* total buffer size the commit allocates */
    size_t live_bytes;     /* bytes actually copied out of `data` */
    /* Staged pointees for pointer-element vecs (vec<string>): each
     * entry pointer that names an arena string gets a copy of its
     * bytes plus its index so commit can re-home them in the new
     * frame. */
    int64_t* entry_idx;
    char** entry_stage;
    size_t entry_n, entry_cap;
    char* pool;            /* the string images, one block for all */
    char* block;           /* owning heap block, NULL when caller-owned */
    size_t block_bytes;    /* size of `block` (for the release) */
} arena_vec_stage;

/* A hash stage is one contiguous block: [header][bucket array][node
 * pool]. The single-value escape path hands the helper a stack buffer
 * (no allocation at all); the tuple path, whose stage has to survive
 * the scope pop, hands it one malloc'd block. Either way the stage
 * costs ONE allocation instead of the three the old code made. */
typedef struct arena_hash_stage {
    LeashHashNode** buckets;
    int64_t num_buckets;
    int64_t count;
    /* Every staged node image lives in ONE block: the old code malloc'd
     * a block per node and a block per free-list record, so escaping an
     * N-entry table cost 2N allocator round trips. One block costs
     * one allocation and one free. */
    char* pool;
    char* block;           /* owning heap block, NULL when caller-owned */
    size_t block_bytes;    /* size of `block` (for the release) */
} arena_hash_stage;

/* Stage layout helpers (defined with the stage/commit code below). */
static void lv_stage_sizes(const LeashVec* v, size_t* live_bytes,
                           size_t* pool_bytes, size_t* n_entries);
static size_t lv_stage_bytes(size_t live, size_t pool, size_t n);
static arena_vec_stage* lv_stage_vec_into(char* buf, LeashVec* v);
static size_t lh_stage_bytes(int64_t nbuckets, size_t pool_bytes);
static arena_hash_stage* lh_stage_hash_into(char* buf, LeashHash* h);

void leasharena_escape_string(void** slot, int64_t nframes) {
    if (!slot) return;
    char* s = (char*)*slot;
    if (!s) {
        leasharena_exit_n(nframes);
        return;
    }
    int64_t len_i = leash_str_bsize(s);
    size_t len = len_i > 0 ? (size_t)len_i : 0;
    int heap = 0;
    size_t stage_cap = 0;
    char* stage = NULL;
    ARENA_STAGE_BYTES(len + 1, heap, stage_cap, stage);
    if (!stage) { leasharena_exit_n(nframes); return; }
    memcpy(stage, s, len + 1);
    leasharena_exit_n(nframes);
    char* out = (char*)leash_gc_alloc_string_cap(len, len);
    memcpy(out, stage, len + 1);
    if (heap) arena_stage_free(stage, stage_cap);
    *slot = (void*)out;
}

void leasharena_escape_vec(void** slot, int64_t nframes) {
    if (!slot) return;
    LeashVec* v = (LeashVec*)*slot;
    if (!v) {
        leasharena_exit_n(nframes);
        return;
    }
    /* Stage into the helper's own stack frame: the frame outlives the
     * scope pop below, so the copy stays valid until the commit and
     * no allocator is involved at all for a small vec. */
    size_t live = 0, pool = 0, n = 0;
    lv_stage_sizes(v, &live, &pool, &n);
    size_t stage_bytes = lv_stage_bytes(live, pool, n);
    int heap = 0;
    size_t stage_cap = 0;
    char* buf = NULL;
    ARENA_STAGE_BYTES_MAX(stage_bytes, ARENA_STAGE_STACK_MAX, heap, stage_cap, buf);
    if (!buf) { leasharena_exit_n(nframes); return; }
    arena_vec_stage* st = lv_stage_vec_into(buf, v);
    st->block = heap ? buf : NULL;
    st->block_bytes = heap ? stage_cap : stage_bytes;
    leasharena_exit_n(nframes);
    /* The commit releases the stage block (heap case); the stack case
     * has nothing to release. */
    leasharena_commit_vec(slot, (void*)st);
}

/* Stage/commit (tuple path) live below; the single-value escape uses
 * the same machinery so both share one implementation. */
void* leasharena_stage_hash(void* hash);
void leasharena_commit_hash(void** slot, void* stage);

void leasharena_escape_hash(void** slot, int64_t nframes) {
    if (!slot) return;
    LeashHash* h = (LeashHash*)*slot;
    if (!h || !h->buckets) {
        leasharena_exit_n(nframes);
        return;
    }
    /* Stage into the helper's own stack frame: the frame outlives the
     * scope pop below, so the copy stays valid until the commit and
     * no allocator is involved at all for a small table. */
    int64_t nb = h->num_buckets;
    size_t node_bytes = 0;
    for (int64_t i = 0; i < nb; i++) {
        for (LeashHashNode* cur = h->buckets[i]; cur; cur = cur->next) {
            size_t klen = strlen(LEASH_HASH_KEY(cur));
            node_bytes += AR_HASH_NODE_SZ(klen);
        }
    }
    int heap = 0;
    size_t stage_cap = 0;
    char* buf = NULL;
    size_t stage_bytes = lh_stage_bytes(nb, node_bytes);
    ARENA_STAGE_BYTES_MAX(stage_bytes, ARENA_STAGE_STACK_MAX, heap, stage_cap, buf);
    if (!buf) { leasharena_exit_n(nframes); return; }
    arena_hash_stage* st = lh_stage_hash_into(buf, h);
    st->block = heap ? buf : NULL;
    st->block_bytes = heap ? stage_cap : stage_bytes;
    leasharena_exit_n(nframes);
    /* The commit releases the stage block (heap case); the stack case
     * has nothing to release. */
    leasharena_commit_hash(slot, (void*)st);
}

void leasharena_escape_blob(void** slot, int64_t size, int64_t nframes) {
    if (!slot) return;
    void* p = *slot;
    if (!p || size <= 0) {
        leasharena_exit_n(nframes);
        return;
    }
    int heap = 0;
    size_t stage_cap = 0;
    char* stage = NULL;
    ARENA_STAGE_BYTES((size_t)size, heap, stage_cap, stage);
    if (!stage) { leasharena_exit_n(nframes); return; }
    memcpy(stage, p, (size_t)size);
    leasharena_exit_n(nframes);
    void* np = leash_gc_malloc((size_t)size);
    memcpy(np, stage, (size_t)size);
    if (heap) arena_stage_free(stage, stage_cap);
    *slot = np;
}

/* Shared byte-count guard for every slice/element path. A wrapped
 * `len * elem_size` would allocate a small buffer and then memcpy the
 * unwrapped (or wrapped) count into it; the ceiling also keeps the value
 * representable in an arena header. */
static size_t ar_bytes_checked(int64_t len, int64_t elem_size, const char* what) {
    if (len <= 0 || elem_size <= 0) return 0;
    uint64_t l = (uint64_t)len, e = (uint64_t)elem_size;
    if (l > (uint64_t)ARENA_MAX_ALLOC / e) {
        fprintf(stderr, "Leash arena: %s too large (%lld x %lld elements)\n",
                what, (long long)len, (long long)elem_size);
        abort();
    }
    return (size_t)(l * e);
}

void leasharena_escape_slice(void* agg, int64_t elem_size, int64_t nframes) {
    if (!agg) return;
    /* agg points at a {i64 len, i8* data} aggregate on the stack. */
    struct slice_view { int64_t len; void* data; };
    struct slice_view* sv = (struct slice_view*)agg;
    if (!sv || elem_size <= 0) {
        leasharena_exit_n(nframes);
        return;
    }
    int64_t len = sv->len;
    void* data = sv->data;
    if (!data || len <= 0) {
        leasharena_exit_n(nframes);
        return;
    }
    size_t bytes = ar_bytes_checked(len, elem_size, "slice escape");
    int heap = 0;
    char* stage = NULL;
    size_t stage_cap = 0;
    ARENA_STAGE_BYTES(bytes, heap, stage_cap, stage);
    if (!stage) { leasharena_exit_n(nframes); return; }
    memcpy(stage, data, bytes);
    leasharena_exit_n(nframes);
    void* nb = leash_gc_malloc(bytes);
    memcpy(nb, stage, bytes);
    if (heap) arena_stage_free(stage, stage_cap);
    sv->data = nb;
}

/* ---- stage/commit (tuple returns) and root copies (spawn args) ---- */
void* leasharena_stage_string(void* s) {
    if (!s) return NULL;
    int64_t len_i = leash_str_bsize((const char*)s);
    size_t len = len_i > 0 ? (size_t)len_i : 0;
    size_t bytes = sizeof(size_t) + len + 1;
    size_t cap = 0;
    char* block = (char*)arena_stage_alloc(bytes, &cap);
    *(size_t*)(void*)block = cap;
    char* stage = block + sizeof(size_t);
    memcpy(stage, s, len + 1);
    return (void*)stage;
}

void leasharena_commit_string(void** slot, void* stage) {
    if (!slot) return;
    if (!stage) { *slot = NULL; return; }
    size_t len = strlen((const char*)stage);
    char* out = (char*)leash_gc_alloc_string_cap(len, len);
    memcpy(out, stage, len + 1);
    arena_stage_prefixed_free(stage);
    *slot = (void*)out;
}

/* Size of the three payload areas a vec stage needs. */
static void lv_stage_sizes(const LeashVec* v, size_t* live_bytes,
                           size_t* pool_bytes, size_t* n_entries) {
    int64_t esz = v->elem_size > 0 ? v->elem_size : 8;
    size_t total = v->cap > 0 ? (size_t)v->cap * (size_t)esz : 0;
    size_t live = (size_t)(v->size > 0 ? v->size : 0) * (size_t)esz;
    if (live > total) live = total;
    size_t pool = 0, n = 0;
    if (v->scan && v->data && v->size > 0) {
        for (int64_t i = 0; i < v->size; i++) {
            void* ent = *(void**)((char*)v->data + (size_t)i * (size_t)esz);
            if (!ent) continue;
            size_t elen = 0, ecap = 0;
            if (!arena_str_info((const char*)ent, &elen, &ecap)) continue;
            pool += elen + 1;
            n++;
        }
    }
    *live_bytes = live;
    *pool_bytes = pool;
    *n_entries = n;
}

/* Bytes a vec stage block needs (header + payload areas, aligned). */
static size_t lv_stage_bytes(size_t live, size_t pool, size_t n) {
    size_t sz = sizeof(arena_vec_stage) + live + pool
              + n * (sizeof(int64_t) + sizeof(char*)) + 16;
    return (sz + 15) & ~(size_t)15;
}

/* Fill a vec stage in `buf` (sized by lv_stage_bytes). The block is
 * laid out as [header][element copy][string pool][entry table]. */
static arena_vec_stage* lv_stage_vec_into(char* buf, LeashVec* v) {
    int64_t esz = v->elem_size > 0 ? v->elem_size : 8;
    size_t live, pool, n;
    lv_stage_sizes(v, &live, &pool, &n);
    size_t data_bytes = v->cap > 0 ? (size_t)v->cap * (size_t)esz : 0;
    arena_vec_stage* st = (arena_vec_stage*)buf;
    memset(st, 0, sizeof(*st));
    st->hdr = *v;
    /* Only the LIVE elements are worth copying: the unused capacity is
     * slack that the next push refills. Copying it would allocate (and
     * touch) cap*elem_size bytes for size elements. */
    st->data_bytes = live;
    st->live_bytes = live;
    char* cur = buf + sizeof(arena_vec_stage);
    st->data = cur; cur += live;
    st->pool = pool ? cur : NULL; cur += pool;
    cur = (char*)(((uintptr_t)cur + 7) & ~(uintptr_t)7);
    st->entry_idx = n ? (int64_t*)cur : NULL; cur += n * sizeof(int64_t);
    st->entry_stage = n ? (char**)cur : NULL;
    st->entry_cap = n;
    st->entry_n = 0;
    if (live && v->data) memcpy(st->data, v->data, live);
    /* Carve the string images out of the pool. */
    if (pool) {
        char* pool_cur = st->pool;
        for (int64_t i = 0; i < v->size; i++) {
            void* ent = *(void**)((char*)v->data + (size_t)i * (size_t)esz);
            if (!ent) continue;
            size_t elen = 0, ecap = 0;
            if (!arena_str_info((const char*)ent, &elen, &ecap)) continue;
            memcpy(pool_cur, ent, elen + 1);
            st->entry_idx[st->entry_n] = i;
            st->entry_stage[st->entry_n] = pool_cur;
            pool_cur += elen + 1;
            st->entry_n++;
        }
    }
    return st;
}

void* leasharena_stage_vec(void* vec) {
    LeashVec* v = (LeashVec*)vec;
    if (!v) return NULL;
    size_t live, pool, n;
    lv_stage_sizes(v, &live, &pool, &n);
    size_t sb = lv_stage_bytes(live, pool, n);
    size_t cap = 0;
    char* buf = (char*)arena_stage_alloc(sb, &cap);
    arena_vec_stage* st = lv_stage_vec_into(buf, v);
    st->block = buf;
    st->block_bytes = cap;
    return (void*)st;
}

void leasharena_commit_vec(void** slot, void* stage) {
    if (!slot) return;
    if (!stage) { *slot = NULL; return; }
    arena_vec_stage* st = (arena_vec_stage*)stage;
    LeashVec* nv = (LeashVec*)leash_gc_malloc_compact_nz(sizeof(LeashVec));
    *nv = st->hdr;
    if (st->data_bytes && st->data) {
        void* nd = leash_gc_malloc(st->data_bytes);
        memcpy(nd, st->data, st->live_bytes);
        nv->data = nd;
        /* The committed buffer holds only the live elements, so the
         * capacity must say so: a vec that claims more capacity than
         * it owns would write past the end of the buffer. */
        nv->cap = nv->size;
        int64_t esz = nv->elem_size > 0 ? nv->elem_size : 8;
        for (size_t k = 0; k < st->entry_n; k++) {
            size_t elen = strlen(st->entry_stage[k]);
            char* ns = (char*)leash_gc_alloc_string_cap(elen, elen);
            memcpy(ns, st->entry_stage[k], elen + 1);
            *(void**)((char*)nd + (size_t)st->entry_idx[k] * (size_t)esz) = (void*)ns;
        }
    } else {
        /* No live elements: the staged buffer held nothing, and the
         * header's data pointer still refers to the dying frame. */
        nv->data = NULL;
        nv->cap = 0;
    }
    if (st->block) arena_stage_free(st->block, st->block_bytes);
    *slot = (void*)nv;
}

/* Bytes a hash stage block needs (header + buckets + node pool). */
static size_t lh_stage_bytes(int64_t nbuckets, size_t pool_bytes) {
    size_t sz = sizeof(arena_hash_stage)
              + (size_t)(nbuckets > 0 ? nbuckets : 1) * sizeof(LeashHashNode*)
              + pool_bytes + 16;
    return (sz + 15) & ~(size_t)15;
}

/* Fill a hash stage in `buf` (sized by lh_stage_bytes). */
static arena_hash_stage* lh_stage_hash_into(char* buf, LeashHash* h) {
    int64_t nb = h->num_buckets;
    /* Pass 1: size the node pool. */
    size_t node_bytes = 0;
    for (int64_t i = 0; i < nb; i++) {
        for (LeashHashNode* cur = h->buckets[i]; cur; cur = cur->next) {
            size_t klen = strlen(LEASH_HASH_KEY(cur));
            node_bytes += AR_HASH_NODE_SZ(klen);
        }
    }
    arena_hash_stage* st = (arena_hash_stage*)buf;
    memset(st, 0, sizeof(*st));
    st->num_buckets = nb;
    st->count = h->count;
    char* cur = buf + sizeof(arena_hash_stage);
    st->buckets = (LeashHashNode**)cur;
    cur += (size_t)(nb > 0 ? nb : 1) * sizeof(LeashHashNode*);
    st->pool = node_bytes ? cur : NULL;
    memset(st->buckets, 0, (size_t)(nb > 0 ? nb : 1) * sizeof(LeashHashNode*));
    /* Pass 2: carve the node images out of the pool. */
    char* pool_cur = st->pool;
    for (int64_t i = 0; i < nb; i++) {
        LeashHashNode* node = h->buckets[i];
        LeashHashNode** tail = &st->buckets[i];
        while (node) {
            size_t klen = strlen(LEASH_HASH_KEY(node));
            size_t sz = AR_HASH_NODE_SZ(klen);
            LeashHashNode* sn = (LeashHashNode*)pool_cur;
            pool_cur += sz;
            LEASH_HASH_KEY_INIT(sn);
            memcpy(LEASH_HASH_KEY(sn), LEASH_HASH_KEY(node), klen + 1);
            sn->val = node->val;
            sn->hash = node->hash;
            sn->next = NULL;
            *tail = sn;
            tail = &sn->next;
            node = node->next;
        }
    }
    return st;
}

void* leasharena_stage_hash(void* hash) {
    LeashHash* h = (LeashHash*)hash;
    if (!h || !h->buckets) return NULL;
    int64_t nb = h->num_buckets;
    size_t node_bytes = 0;
    for (int64_t i = 0; i < nb; i++) {
        for (LeashHashNode* cur = h->buckets[i]; cur; cur = cur->next) {
            size_t klen = strlen(LEASH_HASH_KEY(cur));
            node_bytes += AR_HASH_NODE_SZ(klen);
        }
    }
    size_t bytes = lh_stage_bytes(nb, node_bytes);
    size_t cap = 0;
    char* buf = (char*)arena_stage_alloc(bytes, &cap);
    arena_hash_stage* st = lh_stage_hash_into(buf, h);
    st->block = buf;
    st->block_bytes = cap;
    return (void*)st;
}

void leasharena_commit_hash(void** slot, void* stage) {
    if (!slot) return;
    if (!stage) { *slot = NULL; return; }
    arena_hash_stage* st = (arena_hash_stage*)stage;
    LeashHash* nh = (LeashHash*)leash_gc_malloc_compact_nz(sizeof(LeashHash));
    nh->num_buckets = st->num_buckets;
    nh->count = st->count;
    nh->rooted = 0;
    nh->buckets = (LeashHashNode**)leash_gc_malloc((size_t)(st->num_buckets > 0 ? st->num_buckets : 1) * sizeof(LeashHashNode*));
    for (int64_t i = 0; i < st->num_buckets; i++) {
        LeashHashNode* scur = st->buckets[i];
        LeashHashNode** tail = &nh->buckets[i];
        while (scur) {
            size_t klen = strlen(LEASH_HASH_KEY(scur));
            size_t sz = sizeof(LeashHashNode) + klen + 1;
            LeashHashNode* nn = (LeashHashNode*)leash_gc_malloc(sz);
            LEASH_HASH_KEY_INIT(nn);
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(scur), klen + 1);
            nn->val = scur->val;
            nn->hash = scur->hash;
            nn->next = NULL;
            *tail = nn;
            tail = &nn->next;
            scur = scur->next;
        }
    }
    /* One release for the whole stage block. */
    if (st->block) arena_stage_free(st->block, st->block_bytes);
    *slot = (void*)nh;
}

/* ---------------------------------------------------------------------
 * Typed escape of nested containers and aggregates.
 *
 * The generic escape helpers understand one level only: a vec<string>
 * gets its string elements re-homed, but a vec<vec<int>> has the inner
 * LeashVec pointers copied raw into the caller's frame. The inner
 * vector's header and element array were allocated in the dying frame,
 * so after the pop the outer vector points at reclaimed memory: the
 * read faults, or (poisoning off) returns stale bytes.
 *
 * The type tree is a flat, compile-time-constant array of
 * {off, kind, child, aux} entries, emitted by the compiler:
 *
 *   kind 0  leaf       no heap reference
 *   kind 1  string     the value is a string
 *   kind 2  vec        child = descriptor of the element type
 *   kind 3  hash       child = descriptor of the value type
 *   kind 4  aggregate  struct/class body; child = first field entry,
 *                      aux = field count, off = body size
 *   kind 5  field      off = byte offset of the field in the body,
 *                      child = descriptor of the field's type
 *
 * `off` is a null-based getelementptr constant expression: its numeric
 * value is the byte offset of a field (or the size of an aggregate
 * body), so the runtime uses the exact LLVM layout instead of a
 * hand-computed one.
 *
 * Every container is staged BEFORE the scope pop (a child may live in
 * the dying frame) and rebuilt AFTER it, in the caller's frame. The
 * stage owns every block it allocates; the commit allocates the new
 * objects and the release frees the stage blocks.
 */
#define TE_LEAF  0
#define TE_STR   1
#define TE_VEC   2
#define TE_HASH  3
#define TE_AGG   4
#define TE_FIELD 5

/* Depth cap for the recursive walk. A cyclic value graph (a list that
 * points back at itself) would recurse forever; the cap turns the tail
 * into a shallow copy instead of a hang. */
#define TE_MAX_DEPTH 2048

/* ar_te is declared in gc.h (the compiler-emitted descriptor table
 * entry). Do not redeclare it here: a second, distinct struct type in
 * this translation unit makes every typed-escape prototype conflict. */
#define TE_STAGE_MAGIC 0x54455348u /* 'TESH' */

typedef struct te_stage {
    uint32_t magic;
    int32_t kind;            /* TE_VEC, TE_HASH, TE_AGG */
    size_t self_bytes;       /* real stage-cache capacity of this struct */
    size_t data_cap;
    size_t child_cap;
    size_t bucket_len_cap;
    size_t hval_cap;
    size_t node_pool_cap;
    size_t body_cap;
    size_t fstage_cap;
    /* vec */
    LeashVec vhdr;
    char* data;              /* element byte copy */
    size_t data_bytes;
    int64_t n;               /* element count */
    void** child;            /* one child stage per element */
    /* hash */
    int64_t nbuckets;
    int64_t hcount;
    int64_t n_nodes;
    int64_t* bucket_len;     /* node count per bucket */
    char* node_pool;         /* [node images + keys], one block */
    size_t node_pool_bytes;
    void** hval;             /* value stage per node */
    /* aggregate */
    char* body;              /* body byte copy */
    size_t body_bytes;
    void** fstage;           /* one stage per heap field */
    int32_t nfields;
} te_stage;

static te_stage* te_stage_new(int kind) {
    size_t cap = 0;
    te_stage* st = (te_stage*)arena_stage_alloc(sizeof(te_stage), &cap);
    memset(st, 0, sizeof(*st));
    st->self_bytes = cap;
    st->magic = TE_STAGE_MAGIC;
    st->kind = kind;
    return st;
}

static void* te_stage_val(void* val, const ar_te* tree, int32_t node, int depth);
static te_stage* te_stage_vec(void* vp, const ar_te* tree, int32_t node, int depth);
static te_stage* te_stage_hash(void* hp, const ar_te* tree, int32_t node, int depth);
static te_stage* te_stage_agg(void* bp, const ar_te* tree, int32_t node, int depth);
static void* te_commit_val(void* stage, const ar_te* tree, int32_t node);
static void* te_commit_vec(te_stage* st, const ar_te* tree, int32_t node);
static void* te_commit_hash(te_stage* st, const ar_te* tree, int32_t node);
static void* te_commit_agg(te_stage* st, const ar_te* tree, int32_t node);

static void te_stage_release(te_stage* st, const ar_te* tree, int32_t node);

/* A child stage is either a staged string (a plain byte block) or a
 * container stage. The descriptor tells the two apart. */
static void te_release_child(void* p, const ar_te* tree, int32_t child) {
    if (!p || child < 0) return;
    int32_t kind = tree[child].kind;
    if (kind == TE_STR) {
        arena_stage_prefixed_free(p);
        return;
    }
    te_stage_release((te_stage*)p, tree, child);
}

/* Release every block a stage owns. Children are referenced (not owned)
 * by the parent's arrays, so the parent releases them recursively.
 * `node` is the descriptor of the staged value. */
static void te_stage_release(te_stage* st, const ar_te* tree, int32_t node) {
    if (!st) return;
    if (st->kind == TE_VEC) {
        if (st->child) {
            int32_t ec = node >= 0 ? tree[node].child : -1;
            for (int64_t i = 0; i < st->n; i++) {
                te_release_child(st->child[i], tree, ec);
            }
            arena_stage_free(st->child, st->child_cap);
        }
        if (st->data) arena_stage_free(st->data, st->data_cap);
    } else if (st->kind == TE_HASH) {
        if (st->hval) {
            int32_t vc = node >= 0 ? tree[node].child : -1;
            for (int64_t i = 0; i < st->n_nodes; i++) {
                te_release_child(st->hval[i], tree, vc);
            }
            arena_stage_free(st->hval, st->hval_cap);
        }
        if (st->bucket_len) arena_stage_free(st->bucket_len, st->bucket_len_cap);
        if (st->node_pool) arena_stage_free(st->node_pool, st->node_pool_cap);
    } else if (st->kind == TE_AGG) {
        if (st->fstage) {
            int32_t first = node >= 0 ? tree[node].child : -1;
            for (int32_t i = 0; i < st->nfields; i++) {
                int32_t fc = (first >= 0) ? tree[first + i].child : -1;
                te_release_child(st->fstage[i], tree, fc);
            }
            arena_stage_free(st->fstage, st->fstage_cap);
        }
        if (st->body) arena_stage_free(st->body, st->body_cap);
    }
    arena_stage_free(st, st->self_bytes);
}

/* Stage a vec. `node` is the vec's descriptor: its child is the
 * descriptor of the element type. */
static te_stage* te_stage_vec(void* vp, const ar_te* tree, int32_t node, int depth) {
    LeashVec* v = (LeashVec*)vp;
    te_stage* st = te_stage_new(TE_VEC);
    if (!v) return st;
    st->vhdr = *v;
    int64_t esz = v->elem_size > 0 ? v->elem_size : 8;
    st->n = v->size;
    /* Only the live elements are staged: the unused capacity is slack
     * that the next push refills. */
    st->data_bytes = v->size > 0 ? (size_t)v->size * (size_t)esz : 0;
    if (st->data_bytes) {
        st->data = (char*)arena_stage_alloc(st->data_bytes, &st->data_cap);
        if (v->data) memcpy(st->data, v->data, st->data_bytes);
    }
    int32_t ec = tree[node].child;
    if (st->n > 0 && ec >= 0 && depth < TE_MAX_DEPTH) {
        int32_t ek = tree[ec].kind;
        if (ek == TE_STR || ek == TE_VEC || ek == TE_HASH || ek == TE_AGG) {
            st->child = (void**)arena_stage_alloc((size_t)st->n * sizeof(void*), &st->child_cap);
            memset(st->child, 0, (size_t)st->n * sizeof(void*));
            for (int64_t i = 0; i < st->n; i++) {
                void* ent = v->data ? *(void**)((char*)v->data + (size_t)i * (size_t)esz) : NULL;
                if (!ent) continue;
                st->child[i] = te_stage_val(ent, tree, ec, depth + 1);
            }
        }
    }
    return st;
}

/* Stage a hash. `node` is the hash's descriptor: its child is the
 * descriptor of the value type. */
static te_stage* te_stage_hash(void* hp, const ar_te* tree, int32_t node, int depth) {
    LeashHash* h = (LeashHash*)hp;
    te_stage* st = te_stage_new(TE_HASH);
    if (!h || !h->buckets) return st;
    st->nbuckets = h->num_buckets;
    st->hcount = h->count;
    int32_t vc = tree[node].child;
    int32_t vk = vc >= 0 ? tree[vc].kind : TE_LEAF;
    /* Pass 1: node count, byte size and per-bucket lengths. */
    int64_t count = 0;
    size_t pool = 0;
    for (int64_t b = 0; b < st->nbuckets; b++) {
        for (LeashHashNode* c = h->buckets[b]; c; c = c->next) {
            count++;
            pool += AR_HASH_NODE_SZ(strlen(LEASH_HASH_KEY(c)));
        }
    }
    st->n_nodes = count;
    st->bucket_len = (int64_t*)arena_stage_alloc((size_t)(st->nbuckets > 0 ? st->nbuckets : 1) * sizeof(int64_t), &st->bucket_len_cap);
    memset(st->bucket_len, 0, (size_t)(st->nbuckets > 0 ? st->nbuckets : 1) * sizeof(int64_t));
    for (int64_t b = 0; b < st->nbuckets; b++) {
        for (LeashHashNode* c = h->buckets[b]; c; c = c->next) st->bucket_len[b]++;
    }
    if (count > 0 && (vk == TE_STR || vk == TE_VEC || vk == TE_HASH || vk == TE_AGG)) {
        st->hval = (void**)arena_stage_alloc((size_t)count * sizeof(void*), &st->hval_cap);
        memset(st->hval, 0, (size_t)count * sizeof(void*));
    }
    if (pool) {
        st->node_pool = (char*)arena_stage_alloc(pool, &st->node_pool_cap);
        st->node_pool_bytes = pool;
    }
    /* Pass 2: copy every node image (key included) and stage the value
     * containers. Nodes are laid out in bucket order. */
    char* cur = st->node_pool;
    int64_t ni = 0;
    for (int64_t b = 0; b < st->nbuckets; b++) {
        for (LeashHashNode* c = h->buckets[b]; c; c = c->next, ni++) {
            size_t klen = strlen(LEASH_HASH_KEY(c));
            size_t sz = AR_HASH_NODE_SZ(klen);
            LeashHashNode* nn = (LeashHashNode*)cur;
            cur += sz;
            LEASH_HASH_KEY_INIT(nn);
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(c), klen + 1);
            nn->hash = c->hash;
            nn->next = NULL;
            nn->val = c->val;
            if (st->hval && c->val && depth < TE_MAX_DEPTH) {
                st->hval[ni] = te_stage_val(c->val, tree, vc, depth + 1);
            }
        }
    }
    return st;
}

/* Stage a struct/class body. The body is a byte image whose heap
 * fields live at the offsets the compiler recorded. */
static te_stage* te_stage_agg(void* bp, const ar_te* tree, int32_t node, int depth) {
    te_stage* st = te_stage_new(TE_AGG);
    size_t body_bytes = tree[node].off ? (size_t)(uintptr_t)tree[node].off : 0;
    st->body_bytes = body_bytes;
    if (body_bytes && bp) {
        st->body = (char*)arena_stage_alloc(body_bytes, &st->body_cap);
        memcpy(st->body, bp, body_bytes);
    }
    int32_t nf = tree[node].aux;
    int32_t first = tree[node].child;
    st->nfields = nf;
    if (nf > 0 && first >= 0 && st->body && depth < TE_MAX_DEPTH) {
        st->fstage = (void**)arena_stage_alloc((size_t)nf * sizeof(void*), &st->fstage_cap);
        memset(st->fstage, 0, (size_t)nf * sizeof(void*));
        for (int32_t i = 0; i < nf; i++) {
            const ar_te* f = &tree[first + i];
            size_t off = f->off ? (size_t)(uintptr_t)f->off : 0;
            if (off + sizeof(void*) > body_bytes) continue;
            void* fv = *(void**)(st->body + off);
            if (!fv) continue;
            st->fstage[i] = te_stage_val(fv, tree, f->child, depth + 1);
        }
    }
    return st;
}

/* Stage a value by descriptor (vec element, hash value, aggregate
 * field). */
static void* te_stage_val(void* val, const ar_te* tree, int32_t node, int depth) {
    if (!val || node < 0 || depth >= TE_MAX_DEPTH) return NULL;
    int32_t k = tree[node].kind;
    if (k == TE_STR) return leasharena_stage_string(val);
    if (k == TE_VEC) return (void*)te_stage_vec(val, tree, node, depth);
    if (k == TE_HASH) return (void*)te_stage_hash(val, tree, node, depth);
    if (k == TE_AGG) return (void*)te_stage_agg(val, tree, node, depth);
    return NULL;
}

/* Commit a staged string into the caller's frame. The stage block is
 * left alone: the stage release owns it. */
static void* te_commit_str(void* stage) {
    if (!stage) return NULL;
    size_t len = strlen((const char*)stage);
    char* out = (char*)leash_gc_alloc_string_cap(len, len);
    memcpy(out, stage, len + 1);
    return out;
}

/* Commit a vec stage into the caller's frame. */
static void* te_commit_vec(te_stage* st, const ar_te* tree, int32_t node) {
    if (!st) return NULL;
    LeashVec* nv = (LeashVec*)leash_gc_malloc_compact_nz(sizeof(LeashVec));
    *nv = st->vhdr;
    int64_t esz = nv->elem_size > 0 ? nv->elem_size : 8;
    if (st->data_bytes) {
        void* nd = leash_gc_malloc(st->data_bytes);
        memcpy(nd, st->data, st->data_bytes);
        nv->data = nd;
        /* The committed buffer holds only the live elements, so the
         * capacity must say so (see leasharena_commit_vec). */
        nv->cap = nv->size;
    } else {
        /* No live elements: the header's data pointer still refers to
         * the dying frame, so it must not survive the commit. */
        nv->data = NULL;
        nv->cap = 0;
    }
    int32_t ec = tree[node].child;
    if (ec >= 0 && st->child) {
        for (int64_t i = 0; i < st->n; i++) {
            void* cs = st->child[i];
            if (!cs) continue;
            void* ent = te_commit_val(cs, tree, ec);
            if (nv->data) *(void**)((char*)nv->data + (size_t)i * (size_t)esz) = ent;
        }
    }
    return (void*)nv;
}

/* Commit a hash stage into the caller's frame. */
static void* te_commit_hash(te_stage* st, const ar_te* tree, int32_t node) {
    if (!st) return NULL;
    LeashHash* nh = (LeashHash*)leash_gc_malloc_compact_nz(sizeof(LeashHash));
    nh->num_buckets = st->nbuckets;
    nh->count = st->hcount;
    nh->rooted = 0;
    nh->buckets = (LeashHashNode**)leash_gc_malloc_compact((size_t)(st->nbuckets > 0 ? st->nbuckets : 1) * sizeof(LeashHashNode*));
    for (int64_t b = 0; b < st->nbuckets; b++) nh->buckets[b] = NULL;
    int32_t vc = tree[node].child;
    /* Rebuild the chains. The staged node pool lives on the malloc
     * stage and is released by te_stage_release right after this call,
     * so the committed table must own a copy of every node (node image
     * + inline key) in the caller's arena/GC heap. */
    int64_t ni = 0;
    char* cur = st->node_pool;
    for (int64_t b = 0; b < st->nbuckets; b++) {
        LeashHashNode** tail = &nh->buckets[b];
        for (int64_t k = 0; k < st->bucket_len[b]; k++, ni++) {
            LeashHashNode* sn = (LeashHashNode*)cur;
            size_t klen = strlen(LEASH_HASH_KEY(sn));
            cur += AR_HASH_NODE_SZ(klen);
            LeashHashNode* nn = (LeashHashNode*)leash_gc_malloc_compact(sizeof(LeashHashNode) + klen + 1);
            if (!nn) return (void*)nh;
            LEASH_HASH_KEY_INIT(nn);
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(sn), klen + 1);
            nn->hash = sn->hash;
            nn->val = sn->val;
            nn->next = NULL;
            if (st->hval && st->hval[ni]) {
                nn->val = te_commit_val(st->hval[ni], tree, vc);
            }
            *tail = nn;
            tail = &nn->next;
        }
    }
    return (void*)nh;
}

/* Commit an aggregate body into the caller's frame. */
static void* te_commit_agg(te_stage* st, const ar_te* tree, int32_t node) {
    if (!st || !st->body_bytes || !st->body) return NULL;
    void* nb = leash_gc_malloc(st->body_bytes);
    memcpy(nb, st->body, st->body_bytes);
    int32_t nf = tree[node].aux;
    int32_t first = tree[node].child;
    if (nf > 0 && first >= 0 && st->fstage) {
        for (int32_t i = 0; i < nf; i++) {
            void* fs = st->fstage[i];
            if (!fs) continue;
            const ar_te* f = &tree[first + i];
            size_t off = f->off ? (size_t)(uintptr_t)f->off : 0;
            if (off + sizeof(void*) > st->body_bytes) continue;
            void* nv = te_commit_val(fs, tree, f->child);
            *(void**)((char*)nb + off) = nv;
        }
    }
    return nb;
}

/* Commit a staged value by descriptor. */
static void* te_commit_val(void* stage, const ar_te* tree, int32_t node) {
    if (!stage || node < 0) return NULL;
    int32_t k = tree[node].kind;
    if (k == TE_STR) return te_commit_str(stage);
    if (k == TE_VEC) return te_commit_vec((te_stage*)stage, tree, node);
    if (k == TE_HASH) return te_commit_hash((te_stage*)stage, tree, node);
    if (k == TE_AGG) return te_commit_agg((te_stage*)stage, tree, node);
    return NULL;
}

/* Escape entry point: stage a vec with a type tree, pop the frames,
 * then rebuild it in the caller's frame. */
void leasharena_escape_vec_typed_d(void** slot, int64_t nframes,
                                    const ar_te* tree, int32_t te_node) {
    if (!slot) { leasharena_exit_n(nframes); return; }
    LeashVec* v = (LeashVec*)*slot;
    if (!v) { leasharena_exit_n(nframes); return; }
    if (te_node < 0 || !tree) { leasharena_escape_vec(slot, nframes); return; }
    te_stage* st = te_stage_vec(v, tree, te_node, 0);
    leasharena_exit_n(nframes);
    void* out = te_commit_vec(st, tree, te_node);
    te_stage_release(st, tree, te_node);
    *slot = out;
}

/* Hash variant of the escape entry point. */
void leasharena_escape_hash_typed_d(void** slot, int64_t nframes,
                                     const ar_te* tree, int32_t te_node) {
    if (!slot) { leasharena_exit_n(nframes); return; }
    LeashHash* h = (LeashHash*)*slot;
    if (!h || !h->buckets) { leasharena_exit_n(nframes); return; }
    if (te_node < 0 || !tree) { leasharena_escape_hash(slot, nframes); return; }
    te_stage* st = te_stage_hash(h, tree, te_node, 0);
    leasharena_exit_n(nframes);
    void* out = te_commit_hash(st, tree, te_node);
    te_stage_release(st, tree, te_node);
    *slot = out;
}

/* ---- aggregate graph copy -----------------------------------------
 *
 * An aggregate (class or struct) that is returned by value from a frame
 * must be moved to the caller's frame. If the aggregate references other
 * instances (e.g. recursive data structures like an AST or a linked
 * list), those instances must be moved too, and the references between
 * them preserved.
 *
 * Identity mapping (ptr_map): maps original instance -> new copy, both during
 * staging (to stop the walk at an instance that was already staged)
 * and while committing (to point a field at the copy of the instance
 * it referred to).
 */
typedef struct ptr_map_slot {
    void* key;
    void* val;
} ptr_map_slot;

typedef struct ptr_map {
    ptr_map_slot* slots;
    size_t cap;
    size_t len;
    size_t bytes;   /* real stage-cache capacity of `slots` */
} ptr_map;

static void ptr_map_init(ptr_map* m, size_t want) {
    size_t c = 16;
    while (c < want * 2) c *= 2;
    size_t bytes = c * sizeof(ptr_map_slot);
    m->slots = (ptr_map_slot*)arena_stage_alloc(bytes, &m->bytes);
    if (!m->slots) arena_oom("graph map");
    memset(m->slots, 0, bytes);
    m->cap = c;
    m->len = 0;
}

static void ptr_map_free(ptr_map* m) {
    arena_stage_free(m->slots, m->bytes);
    m->slots = NULL;
    m->cap = 0;
    m->len = 0;
    m->bytes = 0;
}

static size_t ptr_map_hash(void* p) {
    uint64_t x = (uint64_t)(uintptr_t)p;
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return (size_t)x;
}

static void ptr_map_put(ptr_map* m, void* key, void* val);

static void ptr_map_grow(ptr_map* m) {
    ptr_map_slot* old = m->slots;
    size_t old_cap = m->cap;
    size_t old_bytes = m->bytes;
    m->cap = old_cap * 2;
    size_t bytes = m->cap * sizeof(ptr_map_slot);
    m->slots = (ptr_map_slot*)arena_stage_alloc(bytes, &m->bytes);
    if (!m->slots) arena_oom("graph map");
    memset(m->slots, 0, bytes);
    m->len = 0;
    for (size_t i = 0; i < old_cap; i++) {
        if (old[i].key) ptr_map_put(m, old[i].key, old[i].val);
    }
    arena_stage_free(old, old_bytes);
}

static void ptr_map_put(ptr_map* m, void* key, void* val) {
    if (!m->slots) ptr_map_init(m, 16);
    if ((m->len + 1) * 4 >= m->cap * 3) ptr_map_grow(m);
    size_t mask = m->cap - 1;
    size_t i = ptr_map_hash(key) & mask;
    while (m->slots[i].key) {
        if (m->slots[i].key == key) { m->slots[i].val = val; return; }
        i = (i + 1) & mask;
    }
    m->slots[i].key = key;
    m->slots[i].val = val;
    m->len++;
}

static void* ptr_map_get(ptr_map* m, void* key) {
    if (!m->slots || !key) return NULL;
    size_t mask = m->cap - 1;
    size_t i = ptr_map_hash(key) & mask;
    while (m->slots[i].key) {
        if (m->slots[i].key == key) return m->slots[i].val;
        i = (i + 1) & mask;
    }
    return NULL;
}

typedef enum gcopy_field_kind {
    GC_F_STR = 1,
    GC_F_VEC = 2,
    GC_F_HASH = 3,
    GC_F_AGG = 4
} gcopy_field_kind;

typedef struct gcopy_field {
    int32_t kind;
    int32_t off;      /* byte offset of the field in the body */
    void* staged;     /* the staged value (string/vec/hash) */
    void* target;     /* GC_F_AGG: the original instance */
    int32_t node;     /* type tree descriptor node */
} gcopy_field;

typedef struct gcopy_inst {
    void* orig;       /* the original instance */
    void* staged;     /* the staged body */
    size_t body;      /* the body size */
    size_t staged_bytes; /* real stage-cache capacity of `staged` */
    gcopy_field* fields;
    size_t fields_bytes; /* real stage-cache capacity of `fields` */
    int32_t nfields;
} gcopy_inst;

typedef struct gcopy_graph {
    gcopy_inst* insts;
    size_t insts_bytes; /* real stage-cache capacity of `insts` */
    size_t graph_bytes; /* real stage-cache capacity of this struct */
    int32_t n;
    int32_t cap;
} gcopy_graph;

static gcopy_graph* gcopy_graph_new(void) {
    size_t gbytes = 0;
    gcopy_graph* g = (gcopy_graph*)arena_stage_alloc(sizeof(gcopy_graph), &gbytes);
    if (!g) arena_oom("graph copy");
    memset(g, 0, sizeof(gcopy_graph));
    g->graph_bytes = gbytes;
    g->insts_bytes = 0;
    g->cap = 8;
    size_t bytes = (size_t)g->cap * sizeof(gcopy_inst);
    g->insts = (gcopy_inst*)arena_stage_alloc(bytes, &g->insts_bytes);
    if (!g->insts) arena_oom("graph copy");
    memset(g->insts, 0, bytes);
    return g;
}

static int32_t gcopy_graph_add(gcopy_graph* g, void* orig, void* staged,
                               size_t body, size_t staged_cap) {
    if (g->n >= g->cap) {
        int32_t nc = g->cap * 2;
        size_t bytes = (size_t)nc * sizeof(gcopy_inst);
        size_t new_bytes = 0;
        gcopy_inst* ni = (gcopy_inst*)arena_stage_alloc(bytes, &new_bytes);
        if (!ni) arena_oom("graph copy");
        memcpy(ni, g->insts, (size_t)g->cap * sizeof(gcopy_inst));
        memset(ni + g->cap, 0, (size_t)(nc - g->cap) * sizeof(gcopy_inst));
        arena_stage_free(g->insts, g->insts_bytes);
        g->insts = ni;
        g->insts_bytes = new_bytes;
        g->cap = nc;
    }
    int32_t idx = g->n++;
    g->insts[idx].orig = orig;
    g->insts[idx].staged = staged;
    g->insts[idx].body = body;
    g->insts[idx].staged_bytes = staged_cap;
    g->insts[idx].fields = NULL;
    g->insts[idx].fields_bytes = 0;
    g->insts[idx].nfields = 0;
    return idx;
}

/* Stage one instance and everything it reaches. The instance is marked
 * as seen BEFORE its fields are walked, so a field that points back at
 * it stops the walk instead of recursing forever. */
static void gcopy_stage_inst(gcopy_graph* g, void* inst, const ar_te* tree,
                             int32_t node, ptr_map* seen, int depth) {
    if (!inst || node < 0 || !tree || depth > TE_MAX_DEPTH) return;
    if (tree[node].kind != TE_AGG) return;
    if (ptr_map_get(seen, inst)) return;
    size_t body = tree[node].off ? (size_t)(uintptr_t)tree[node].off : 0;
    if (!body) return;
    size_t staged_cap = 0;
    void* staged = arena_stage_alloc(body, &staged_cap);
    memcpy(staged, inst, body);
    int32_t idx = gcopy_graph_add(g, inst, staged, body, staged_cap);
    ptr_map_put(seen, inst, (void*)(uintptr_t)(idx + 1));
    int32_t nf = tree[node].aux;
    int32_t first = tree[node].child;
    if (nf <= 0 || first < 0) return;
    size_t fields_bytes = 0;
    gcopy_field* fields = (gcopy_field*)arena_stage_alloc((size_t)nf * sizeof(gcopy_field), &fields_bytes);
    if (!fields) arena_oom("graph copy");
    memset(fields, 0, (size_t)nf * sizeof(gcopy_field));
    g->insts[idx].fields = fields;
    g->insts[idx].fields_bytes = fields_bytes;
    for (int32_t i = 0; i < nf; i++) {
        const ar_te* fe = &tree[first + i];
        size_t off = fe->off ? (size_t)(uintptr_t)fe->off : 0;
        if (off + sizeof(void*) > body) continue;
        int32_t fc = fe->child;
        int32_t fk = fc >= 0 ? tree[fc].kind : TE_LEAF;
        void** fslot = (void**)((char*)staged + off);
        void* fv = *fslot;
        if (!fv) continue;
        gcopy_field* gf = &fields[g->insts[idx].nfields];
        if (fk == TE_STR) {
            gf->kind = GC_F_STR;
            gf->off = (int32_t)off;
            gf->staged = leasharena_stage_string(fv);
            gf->node = -1;
            g->insts[idx].nfields++;
        } else if (fk == TE_VEC) {
            gf->kind = GC_F_VEC;
            gf->off = (int32_t)off;
            gf->staged = te_stage_vec(fv, tree, fc, depth + 1);
            gf->node = fc;
            g->insts[idx].nfields++;
        } else if (fk == TE_HASH) {
            gf->kind = GC_F_HASH;
            gf->off = (int32_t)off;
            gf->staged = te_stage_hash(fv, tree, fc, depth + 1);
            gf->node = fc;
            g->insts[idx].nfields++;
        } else if (fk == TE_AGG) {
            gf->kind = GC_F_AGG;
            gf->off = (int32_t)off;
            gf->target = fv;
            gf->node = fc;
            g->insts[idx].nfields++;
            gcopy_stage_inst(g, fv, tree, fc, seen, depth + 1);
        }
    }
}

/* Copy the staged graph into the caller's frame. The bodies go first,
 * then the leaf fields, then the aggregate fields: a field that points
 * at another instance can only be rewritten once every body has its
 * final address. */
static void gcopy_commit_graph(void** slot, gcopy_graph* g, const ar_te* tree) {
    if (!g) { if (slot) *slot = NULL; return; }
    ptr_map done;
    ptr_map_init(&done, (size_t)g->n * 2 + 8);
    for (int32_t i = 0; i < g->n; i++) {
        gcopy_inst* in = &g->insts[i];
        void* nb = leash_gc_malloc(in->body);
        if (!nb) arena_oom("aggregate copy");
        memcpy(nb, in->staged, in->body);
        ptr_map_put(&done, in->orig, nb);
    }
    for (int32_t i = 0; i < g->n; i++) {
        gcopy_inst* in = &g->insts[i];
        void* nb = ptr_map_get(&done, in->orig);
        if (!nb) continue;
        for (int32_t j = 0; j < in->nfields; j++) {
            gcopy_field* f = &in->fields[j];
            void** fslot = (void**)((char*)nb + f->off);
            if (f->kind == GC_F_STR) leasharena_commit_string(fslot, f->staged);
            else if (f->kind == GC_F_VEC) *fslot = te_commit_vec((te_stage*)f->staged, tree, f->node);
            else if (f->kind == GC_F_HASH) *fslot = te_commit_hash((te_stage*)f->staged, tree, f->node);
        }
    }
    for (int32_t i = 0; i < g->n; i++) {
        gcopy_inst* in = &g->insts[i];
        void* nb = ptr_map_get(&done, in->orig);
        if (!nb) continue;
        for (int32_t j = 0; j < in->nfields; j++) {
            gcopy_field* f = &in->fields[j];
            if (f->kind != GC_F_AGG) continue;
            void* target = ptr_map_get(&done, f->target);
            *(void**)((char*)nb + f->off) = target;
        }
    }
    if (slot) *slot = g->n > 0 ? ptr_map_get(&done, g->insts[0].orig) : NULL;
    ptr_map_free(&done);
    for (int32_t i = 0; i < g->n; i++) {
        gcopy_inst* in = &g->insts[i];
        if (in->staged) arena_stage_free(in->staged, in->staged_bytes);
        for (int32_t j = 0; j < in->nfields; j++) {
            gcopy_field* f = &in->fields[j];
            if (f->kind == GC_F_VEC) te_stage_release((te_stage*)f->staged, tree, f->node);
            else if (f->kind == GC_F_HASH) te_stage_release((te_stage*)f->staged, tree, f->node);
        }
        arena_stage_free(in->fields, in->fields_bytes);
    }
    arena_stage_free(g->insts, g->insts_bytes);
    arena_stage_free(g, g->graph_bytes);
}

/* Escape an aggregate graph: the body and every instance it reaches are
 * copied into the caller's frame, and the graph keeps its shape -- an
 * instance that is reachable twice is copied once, and a cycle points
 * at the copy. */
void leasharena_escape_agg_typed(void** slot, int64_t nframes,
                                 const ar_te* tree, int32_t node) {
    if (!slot) { leasharena_exit_n(nframes); return; }
    void* inst = *slot;
    if (!inst || node < 0 || !tree || tree[node].kind != TE_AGG) {
        leasharena_exit_n(nframes);
        if (slot) *slot = NULL;
        return;
    }
    gcopy_graph* g = gcopy_graph_new();
    ptr_map seen;
    ptr_map_init(&seen, 32);
    gcopy_stage_inst(g, inst, tree, node, &seen, 0);
    ptr_map_free(&seen);
    leasharena_exit_n(nframes);
    gcopy_commit_graph(slot, g, tree);
}

static void* te_root_val(void* val, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen);
static void* te_root_vec(void* vp, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen);
static void* te_root_hash(void* hp, const ar_te* tree, int32_t node, int depth,
                          ptr_map* seen);
static void* te_root_agg(void* bp, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen);

static void* te_root_vec(void* vp, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen) {
    LeashVec* v = (LeashVec*)vp;
    if (!v) return NULL;
    /* Containers participate in the identity map just like aggregate
     * bodies: two fields holding the same vec must still alias after the
     * copy, and a vec whose element points back at it must terminate. */
    if (seen) {
        void* prev = ptr_map_get(seen, vp);
        if (prev) return prev;
    }
    int64_t esz = v->elem_size > 0 ? v->elem_size : 8;
    int64_t n = v->size;
    /* Only the LIVE elements are worth copying: the unused capacity is
     * slack that the next push refills. Copying it would allocate (and
     * zero) cap*elem_size bytes for size elements. */
    size_t data_bytes = (size_t)(n > 0 ? n : 0) * (size_t)esz;
    LeashVec* nv = (LeashVec*)leash_gc_malloc_rooted_nz(sizeof(LeashVec), (size_t)8);
    *nv = *v;
    if (seen) ptr_map_put(seen, vp, nv);
    nv->cap = n > 0 ? n : 0;
    /* The copy is a fresh object owned by the root slot: nobody else
     * can reach it, so the shared flag must be clear (a set flag would
     * stop the next store from reusing the copy in place). */
    nv->shared = 0;
    if (data_bytes && v->data) {
        void* nd = leash_gc_malloc_rooted(data_bytes);
        memcpy(nd, v->data, data_bytes);
        nv->data = nd;
    } else {
        nv->data = NULL;
    }
    int32_t ec = tree[node].child;
    int32_t ek = ec >= 0 ? tree[ec].kind : TE_LEAF;
    if (ek != TE_STR && ek != TE_VEC && ek != TE_HASH && ek != TE_AGG) return (void*)nv;
    for (int64_t i = 0; i < n; i++) {
        void** eslot = nv->data ? (void**)((char*)nv->data + (size_t)i * (size_t)esz) : NULL;
        if (!eslot || !*eslot) continue;
        if (ek == TE_STR) {
            leasharena_root_copy_string(eslot);
        } else {
            void* nc = te_root_val(*eslot, tree, ec, depth + 1, seen);
            *eslot = nc;
        }
    }
    return (void*)nv;
}

static void* te_root_hash(void* hp, const ar_te* tree, int32_t node, int depth,
                          ptr_map* seen) {
    LeashHash* h = (LeashHash*)hp;
    if (!h || !h->buckets) return NULL;
    /* Same identity rule as vecs and aggregates: preserve aliasing and
     * break cycles through a root-copied table. */
    if (seen) {
        void* prev = ptr_map_get(seen, hp);
        if (prev) return prev;
    }
    int64_t nb = h->num_buckets > 0 ? h->num_buckets : 64;
    LeashHash* nh = (LeashHash*)leash_gc_malloc_rooted_nz(sizeof(LeashHash), (size_t)8);
    if (seen) ptr_map_put(seen, hp, nh);
    nh->num_buckets = nb;
    nh->count = h->count;
    nh->rooted = LH_HASH_ROOTED;
    nh->buckets = (LeashHashNode**)leash_gc_malloc_rooted((size_t)nb * sizeof(LeashHashNode*));
    for (int64_t b = 0; b < nb; b++) nh->buckets[b] = NULL;
    int32_t vc = tree[node].child;
    int32_t vk = vc >= 0 ? tree[vc].kind : TE_LEAF;
    for (int64_t b = 0; b < h->num_buckets; b++) {
        LeashHashNode** tail = &nh->buckets[b];
        for (LeashHashNode* c = h->buckets[b]; c; c = c->next) {
            size_t klen = strlen(LEASH_HASH_KEY(c));
            size_t sz = sizeof(LeashHashNode) + klen + 1;
            LeashHashNode* nn = (LeashHashNode*)leash_gc_malloc_rooted(sz);
            LEASH_HASH_KEY_INIT(nn);
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(c), klen + 1);
            nn->hash = c->hash;
            nn->next = NULL;
            nn->val = c->val;
            if (vk == TE_STR) {
                if (c->val) {
                    void* vs = c->val;
                    leasharena_root_copy_string(&vs);
                    nn->val = vs;
                }
            } else if (vk == TE_VEC || vk == TE_HASH || vk == TE_AGG) {
                if (c->val) nn->val = te_root_val(c->val, tree, vc, depth + 1, seen);
            }
            *tail = nn;
            tail = &nn->next;
        }
    }
    return (void*)nh;
}

static void* te_root_agg(void* bp, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen) {
    size_t body_bytes = tree[node].off ? (size_t)(uintptr_t)tree[node].off : 0;
    if (!body_bytes || !bp) return NULL;
    /* An instance that was already copied keeps its copy: a cycle (a
     * node that points back at itself) must not send the walk into an
     * endless recursion, and the copy has to keep the graph's shape. */
    if (seen) {
        void* prev = ptr_map_get(seen, bp);
        if (prev) return prev;
    }
    void* nb = leash_gc_malloc_rooted(body_bytes);
    memcpy(nb, bp, body_bytes);
    if (seen) ptr_map_put(seen, bp, nb);
    int32_t nf = tree[node].aux;
    int32_t first = tree[node].child;
    if (nf > 0 && first >= 0) {
        for (int32_t i = 0; i < nf; i++) {
            const ar_te* f = &tree[first + i];
            size_t off = f->off ? (size_t)(uintptr_t)f->off : 0;
            if (off + sizeof(void*) > body_bytes) continue;
            void** fslot = (void**)((char*)nb + off);
            if (!*fslot) continue;
            void* nv = te_root_val(*fslot, tree, f->child, depth + 1, seen);
            *fslot = nv;
        }
    }
    return nb;
}

static void* te_root_val(void* val, const ar_te* tree, int32_t node, int depth,
                         ptr_map* seen) {
    if (!val || node < 0 || depth >= TE_MAX_DEPTH) return NULL;
    int32_t k = tree[node].kind;
    if (k == TE_STR) {
        void* s = val;
        leasharena_root_copy_string(&s);
        return s;
    }
    if (k == TE_VEC) return te_root_vec(val, tree, node, depth, seen);
    if (k == TE_HASH) return te_root_hash(val, tree, node, depth, seen);
    if (k == TE_AGG) return te_root_agg(val, tree, node, depth, seen);
    return NULL;
}

void leasharena_root_copy_vec_typed_d(void** slot, const ar_te* tree, int32_t te_node) {
    if (!slot || !*slot) return;
    if (te_node < 0 || !tree) { leasharena_root_copy_vec(slot); return; }
    ptr_map seen;
    ptr_map_init(&seen, 32);
    *slot = te_root_vec(*slot, tree, te_node, 0, &seen);
    ptr_map_free(&seen);
}

void leasharena_root_copy_hash_typed_d(void** slot, const ar_te* tree, int32_t te_node) {
    if (!slot || !*slot) return;
    if (te_node < 0 || !tree) { leasharena_root_copy_hash(slot); return; }
    ptr_map seen;
    ptr_map_init(&seen, 32);
    *slot = te_root_hash(*slot, tree, te_node, 0, &seen);
    ptr_map_free(&seen);
}

/* Copy a struct/class body into the root arena and root every heap
 * field of the COPY (strings, containers, nested bodies). The plain
 * byte copy of leasharena_root_copy_blob would leave the fields
 * pointing into the frame that is about to die. */
void leasharena_root_copy_blob_typed_d(void** slot, const ar_te* tree,
                                       int32_t te_node) {
    if (!slot || !*slot) return;
    if (te_node < 0 || !tree) return;
    ptr_map seen;
    ptr_map_init(&seen, 32);
    void* nb = te_root_agg(*slot, tree, te_node, 0, &seen);
    ptr_map_free(&seen);
    *slot = nb;
}

void* leasharena_stage_blob(void* ptr, int64_t size) {
    if (!ptr || size <= 0) return NULL;
    size_t bytes = sizeof(size_t) + (size_t)size;
    size_t cap = 0;
    char* block = (char*)arena_stage_alloc(bytes, &cap);
    *(size_t*)(void*)block = cap;
    char* stage = block + sizeof(size_t);
    memcpy(stage, ptr, (size_t)size);
    return stage;
}

void leasharena_commit_blob(void** slot, void* stage, int64_t size) {
    if (!slot) return;
    if (!stage) { *slot = NULL; return; }
    void* np = leash_gc_malloc((size_t)size);
    memcpy(np, stage, (size_t)size);
    arena_stage_prefixed_free(stage);
    *slot = np;
}

/* Root copies: no staging, no pop — straight into tls_root. */
void leasharena_root_copy_string(void** slot) {
    if (!slot || !*slot) return;
    char* s = (char*)*slot;
    int64_t len_i = leash_str_bsize(s);
    size_t len = len_i > 0 ? (size_t)len_i : 0;
    /* Straight into the root arena: the source is still live (no scope
     * pop happens between the read and the copy), so the old malloc'd
     * bounce buffer was pure overhead. */
    char* out = (char*)leasharena_root_alloc_string_cap(len, len);
    memcpy(out, s, len + 1);
    *slot = (void*)out;
}

void leasharena_root_copy_vec(void** slot) {
    if (!slot || !*slot) return;
    LeashVec* v = (LeashVec*)*slot;
    int64_t esz = v->elem_size > 0 ? v->elem_size : 8;
    /* Only the LIVE elements are worth copying: the unused capacity is
     * slack that the next push refills. Copying it would allocate (and
     * zero) cap*elem_size bytes for size elements. */
    int64_t n = v->size > 0 ? v->size : 0;
    size_t data_bytes = (size_t)n * (size_t)esz;
    LeashVec* nv = (LeashVec*)leash_gc_malloc_rooted_nz(sizeof(LeashVec), (size_t)8);
    *nv = *v;
    nv->cap = n;
    nv->shared = 0;
    if (data_bytes && v->data) {
        void* nd = leash_gc_malloc_rooted(data_bytes);
        memcpy(nd, v->data, data_bytes);
        nv->data = nd;
        /* vec<string> / pointer element deep copy: root-copy each
         * arena string entry so it survives the spawner's frame. */
        if (v->scan) {
            for (int64_t i = 0; i < v->size; i++) {
                void** entry = (void**)((char*)nv->data + (size_t)i * (size_t)esz);
                void* ent = *entry;
                if (ent) {
                    int64_t len_i = leash_str_bsize((const char*)ent);
                    (void)len_i;
                    void** eslot = entry;
                    leasharena_root_copy_string(eslot);
                }
            }
        }
    } else {
        nv->data = NULL;
    }
    *slot = (void*)nv;
}

void leasharena_root_copy_hash(void** slot) {
    if (!slot || !*slot) return;
    LeashHash* h = (LeashHash*)*slot;
    if (!h || !h->buckets) return;
    /* No scope pop happens between the read and the copy, so the table
     * is copied straight from the live source: the stage/commit pair
     * (one malloc'd block plus a free) exists for the escape path,
     * where the source dies with the frame. */
    int64_t nb = h->num_buckets > 0 ? h->num_buckets : 64;
    LeashHash* nh = (LeashHash*)leash_gc_malloc_rooted_nz(sizeof(LeashHash), (size_t)8);
    nh->num_buckets = nb;
    nh->count = h->count;
    nh->rooted = LH_HASH_ROOTED;
    nh->buckets = (LeashHashNode**)leash_gc_malloc_rooted((size_t)nb * sizeof(LeashHashNode*));
    /* Only walk buckets the source actually has: a malformed table with
     * num_buckets <= 0 and a non-NULL array must not make us read `nb`
     * (64) pointers from a shorter allocation. */
    int64_t src_nb = h->num_buckets > 0 ? h->num_buckets : 0;
    for (int64_t i = 0; i < src_nb; i++) {
        LeashHashNode* scur = h->buckets[i];
        LeashHashNode** tail = &nh->buckets[i];
        while (scur) {
            size_t klen = strlen(LEASH_HASH_KEY(scur));
            size_t sz = sizeof(LeashHashNode) + klen + 1;
            LeashHashNode* nn = (LeashHashNode*)leash_gc_malloc_rooted(sz);
            LEASH_HASH_KEY_INIT(nn);
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(scur), klen + 1);
            nn->val = scur->val;
            nn->hash = scur->hash;
            nn->next = NULL;
            *tail = nn;
            tail = &nn->next;
            scur = scur->next;
        }
    }
    *slot = (void*)nh;
}

void leasharena_root_copy_blob(void** slot, int64_t size) {
    if (!slot || !*slot || size <= 0) return;
    void* np = leash_gc_malloc_rooted((size_t)size);
    memcpy(np, *slot, (size_t)size);
    *slot = np;
}

void leasharena_root_copy_slice(void* agg, int64_t elem_size) {
    if (!agg) return;
    struct slice_view { int64_t len; void* data; };
    struct slice_view* sv = (struct slice_view*)agg;
    if (!sv || !sv->data || sv->len <= 0 || elem_size <= 0) return;
    size_t bytes = ar_bytes_checked(sv->len, elem_size, "slice root copy");
    void* nb = leash_gc_malloc_rooted(bytes);
    memcpy(nb, sv->data, bytes);
    sv->data = nb;
}

/* ---- root slots -------------------------------------------------
 *
 * A slot that outlives the frame that fills it (a global, a shared
 * variable, a caller's parameter) cannot hold a frame-local pointer:
 * the frame's memory is reclaimed when the frame exits and the slot
 * would dangle -- a use-after-free the GC backend cannot have. The
 * value is therefore copied into the ROOT arena, which is never
 * rewound.
 *
 * Copying on every store would make the root arena grow with the
 * NUMBER of stores instead of with the live data: a loop that assigns
 * a fresh value to a global leaves one root copy per iteration behind,
 * where the GC collects the superseded ones. The root store therefore
 * REUSES the slot's previous root copy whenever that copy cannot be
 * observed from anywhere else:
 *
 *   - a string carries a shared bit (the compiler sets it when the
 *     value is bound to a second holder);
 *   - a vec/hash header carries a shared flag.
 *
 * A clear flag means the slot owns the copy alone, so the new value is
 * written over it and the slot keeps ONE root allocation no matter how
 * often it is overwritten. A set flag means somebody else can still
 * see the old value: a fresh copy is allocated and the old one stays
 * alive (it is live, not leaked).
 */

/* Reclaim a superseded root string copy. *
 * The root arena never rewinds its bump pointer, so a copy the slot
 * owned alone would stay resident for the rest of the program (the GC
 * collects the superseded copies). The copy is released only when
 *
 *   - it is a root string (the magic tag matches),
 *   - the shared bit is clear, i.e. nobody else can observe it, and
 *   - it sits in a DEDICATED block (one that holds nothing else), so
 *     the whole block can go.
 *
 * A copy that shares its block with other live root data stays: the
 * block cannot be released without freeing those objects too. */
static void arena_root_reclaim_string(char* ptr) {
    if (!ptr || (uintptr_t)ptr < 16) return;
    arena_block* b = arena_block_of(&tls_root, ptr);
    if (!b || ptr - 16 < b->base) return;
    uint64_t total = 0, len = 0;
    if (!ar_str_read(ptr, b->base + b->cap, &total, &len)) return;
    if (*(const uint64_t*)(const void*)(ptr - 8) & ARENA_STR_SHARED_BIT) return;
    arena_try_free_solo(&tls_root, ptr, (size_t)(total - 8));
}

/* Store a string into a root slot. Returns the stored pointer: the
 * caller may need it to keep a source local in sync with the root
 * copy. */
void* leasharena_root_store_string(void** slot, void* val) {
    if (!slot) return NULL;
    char* nv = (char*)val;
    if (!nv) { *slot = NULL; return NULL; }
    char* old = (char*)*slot;
    if (old == nv) { return (void*)old; }

    size_t nlen = 0, ncap = 0;
    if (!arena_str_info(nv, &nlen, &ncap)) nlen = strlen(nv);

    /* Reuse the slot's previous root copy when the slot owns it alone. */
    uint64_t old_total = 0;
    size_t old_cap = 0;
    int old_exclusive = 0;
    if (old && arena_owns(&tls_root, old)) {
        arena_block* ob = arena_block_of(&tls_root, old);
        uint64_t total = 0, olen = 0;
        if (ob && old - 16 >= ob->base &&
            ar_str_read(old, ob->base + ob->cap, &total, &olen)) {
            int shared = (*(uint64_t*)(const void*)(old - 8) & ARENA_STR_SHARED_BIT) != 0;
            if (!shared && total >= 17 && olen <= total - 17) {
                size_t ocap = (size_t)(total - 17);
                if (nlen <= ocap) {
                    /* Fits: overwrite the old bytes. The length word is
                     * rewritten without the shared bit: the slot owns
                     * the buffer alone. */
                    memcpy(old, nv, nlen + 1);
                    *(uint64_t*)(void*)(old - 8) = (uint64_t)nlen;
                    *slot = old;
                    return (void*)old;
                }
                /* Does not fit: grow the root buffer in place when it is
                 * still the root arena's bump tip (the usual case: the
                 * slot's copy is the newest root allocation). */
                size_t new_cap = ar_str_cap_for(nlen, ocap);
                size_t new_total = (ARENA_STR_HDR + new_cap + 1 + 7) & ~(size_t)7;
                char* old_end = (old - 16) + (size_t)total;
                if (old_end == tls_root.cur
                    && (size_t)(tls_root.end - tls_root.cur) >= (new_total - (size_t)total)) {
                    size_t want = new_total - (size_t)total;
                    *(uint64_t*)(void*)(old - 16) = ar_str_pack_total(new_total);
                    memcpy(old, nv, nlen + 1);
                    *(uint64_t*)(void*)(old - 8) = (uint64_t)nlen;
                    tls_root.cur += want;
                    tls_root.bytes += want;
                    *slot = old;
                    return (void*)old;
                }
                /* The copy cannot grow in place: a fresh copy replaces
                 * it. The slot owned it alone, so its block can be
                 * reclaimed once the new copy exists. */
                old_total = total;
                old_cap = ocap;
                old_exclusive = 1;
            }
        }
    }
    /* Fresh root copy. The capacity grows GEOMETRICALLY (the same
     * policy the GC uses for its string buffers): an exact-size copy
     * would be full after the next append, which would then copy the
     * whole string again -- quadratic work and quadratic root-arena
     * growth for a string-building loop. With slack, the append path
     * writes in place until the buffer is full, and only then is a
     * bigger copy made (O(log n) copies for a string of n bytes). */
    size_t fresh_cap = ar_str_cap_for(nlen, old_cap ? old_cap : nlen);
    char* out = (char*)leasharena_root_alloc_string_cap(nlen, fresh_cap);
    memcpy(out, nv, nlen + 1);
    *slot = (void*)out;
    if (old_exclusive && old_total >= 17) {
        arena_try_free_solo(&tls_root, old, (size_t)(old_total - 8));
    }
    return (void*)out;
}

/* ---- typed container stores -------------------------------------
 *
 * The typed stores know what a container holds, so every element can
 * be rooted on its own: a vec of strings roots each string into the
 * slot it already owns, a hash roots each value into its node. That
 * is what makes the root arena reuse its storage: the previous root
 * copy of an element is overwritten in place instead of a new copy
 * being allocated next to it.
 *
 * The stores call each other (a vec of hashes, a hash of vecs, a
 * struct holding either), so they are declared here.
 */
void* leasharena_root_store_vec_typed(void** slot, void* val,
                                      const ar_te* tree, int32_t node);
void* leasharena_root_store_hash_typed(void** slot, void* val,
                                       const ar_te* tree, int32_t node);
void leasharena_root_store_blob_typed(void** slot, void* val, int64_t size,
                                      const ar_te* tree, int32_t node);
/* Root one heap reference (a string, a container, an aggregate body)
 * into the slot that owns it. */
static void root_store_ref(void** rslot, void* newv,
                           const ar_te* tree, int32_t node, int depth);

/* Store a vec into a root slot. Returns the stored pointer: the
 * caller may need it to keep a source local in sync with the root
 * copy (the GC backend aliases the two, so a mutation through one
 * must be visible through the other). */
void* leasharena_root_store_vec(void** slot, void* val) {
    return leasharena_root_store_vec_typed(slot, val, NULL, -1);
}

/* Store a vec into a root slot, reusing the slot's previous root
 * copy.
 *
 * `tree`/`node` describe the vec: the descriptor's child is the
 * element descriptor. A vec of strings (or of containers) holds heap
 * pointers: every element is rooted into the slot it already owns, so
 * the previous root copy of the element is reused in place and the
 * root arena does not grow with the number of stores.
 *
 * A vec of plain values copies the element bytes; the copy is only
 * possible when the element buffer holds no pointers at all.
 */
void* leasharena_root_store_vec_typed(void** slot, void* val,
                                      const ar_te* tree, int32_t node) {
    if (!slot) return NULL;
    LeashVec* nv = (LeashVec*)val;
    if (!nv) { *slot = NULL; return NULL; }
    LeashVec* old = (LeashVec*)*slot;
    /* A store of the slot's OWN root copy is a no-op. It must be checked
     * before the alias branch below: marking that copy shared would stop
     * every later store from reusing it, growing the root arena with each
     * iteration. */
    if (old == nv) return (void*)old;
    /* The source may already be a root copy owned by another slot (the
     * compiler re-points a source local at the copy when it stores into
     * a global). Alias that copy instead of making a second one: the GC
     * keeps both holders pointing at the same mutable vector. Mark it
     * shared so neither slot reuses it in place on a later overwrite. */
    if (arena_owns(&tls_root, nv)) {
        nv->shared = 1;
        *slot = (void*)nv;
        return (void*)nv;
    }
    int64_t esz = nv->elem_size > 0 ? nv->elem_size : 8;
    int64_t n = nv->size > 0 ? nv->size : 0;
    int32_t ec = (node >= 0 && tree) ? tree[node].child : -1;
    int32_t ek = ec >= 0 ? tree[ec].kind : TE_LEAF;
    /* A pointer element is a single pointer: the element store roots
     * it through the slot. An aggregate element is stored BY VALUE in
     * the element buffer, so it is not a pointer and cannot be rooted
     * through a slot. */
    int ptr_elems = (ek == TE_STR || ek == TE_VEC || ek == TE_HASH);

    /* Reuse the slot's previous root copy when the slot owns it alone. */
    if (old && !old->shared && old->elem_size == nv->elem_size
        && arena_owns(&tls_root, old)) {
        if (ptr_elems) {
            /* Heap elements: root every element into the slot it
             * already owns. The element store reuses the previous root
             * copy in place, so the root arena stays at the size of
             * the live data. */
            int64_t cap = old->cap > 0 ? old->cap : 0;
            void* data = old->data;
            if (n > cap) {
                int64_t nc = cap > 0 ? cap : 8;
                while (nc < n) nc *= 2;
                size_t old_bytes = (size_t)cap * (size_t)esz;
                size_t new_bytes = (size_t)nc * (size_t)esz;
                void* nd = data ? arena_grow_tip(&tls_root, data, old_bytes, new_bytes) : NULL;
                if (!nd) {
                    nd = leash_gc_malloc_rooted(new_bytes);
                    /* Keep the root copies of the elements that fit:
                     * the new slots take the pointer values, so the
                     * element store reuses them in place. */
                    if (nd && data && old_bytes) memcpy(nd, data, old_bytes);
                }
                if (nd) { data = nd; cap = nc; }
            }
            if (data || n == 0) {
                int64_t i;
                for (i = 0; i < n; i++) {
                    void** eslot = (void**)((char*)data + (size_t)i * (size_t)esz);
                    void* src_el = nv->data
                        ? *(void**)((char*)nv->data + (size_t)i * (size_t)esz)
                        : NULL;
                    if (!src_el) { *eslot = NULL; continue; }
                    root_store_ref(eslot, src_el, tree, ec, 1);
                }
                old->data = data;
                old->cap = cap;
                old->size = n;
                old->shared = 0;
                old->scan = 1;
                *slot = (void*)old;
                return (void*)old;
            }
        } else if (!nv->scan && !old->scan) {
            /* Plain elements: the bytes can be copied over in place. */
            size_t need = (size_t)n * (size_t)esz;
            size_t have = old->data ? (size_t)old->cap * (size_t)esz : 0;
            if (need <= have) {
                if (need && nv->data) memcpy(old->data, nv->data, need);
                old->size = n;
                *slot = old;
                return (void*)old;
            }
            /* Grow the root buffer in place when it is still the root
             * arena's bump tip. */
            if (old->data && have > 0) {
                int64_t nc = old->cap > 0 ? old->cap : 8;
                while (nc < n) nc *= 2;
                size_t new_bytes = (size_t)nc * (size_t)esz;
                void* nd = arena_grow_tip(&tls_root, old->data, have, new_bytes);
                if (nd) {
                    if (need && nv->data) memcpy(nd, nv->data, need);
                    old->data = nd;
                    old->cap = nc;
                    old->size = n;
                    *slot = old;
                    return (void*)old;
                }
            }
        }
    }
    /* Fresh root copy: the header, the live elements and (for pointer
     * elements) a deep copy of every referenced value. */
    *slot = (void*)val;
    if (node >= 0 && tree) {
        leasharena_root_copy_vec_typed_d(slot, tree, node);
    } else {
        leasharena_root_copy_vec(slot);
    }
    return *slot;
}

/* The typed hash store (the rebuild path calls itself for a hash
 * value): declared here for the mutual recursion. */
void* leasharena_root_store_hash_typed(void** slot, void* val,
                                       const ar_te* tree, int32_t node);

/* Store a hash into a root slot. Returns the stored pointer: the
 * caller may need it to keep a source local in sync with the root
 * copy. */
void* leasharena_root_store_hash(void** slot, void* val) {
    return leasharena_root_store_hash_typed(slot, val, NULL, -1);
}

/* The key area of a hash node: the allocation holds the node fields
 * followed by the key bytes and the terminator. */
static size_t hash_node_key_cap(LeashHashNode* n) {
    /* A recycled node is a real arena allocation in the root arena; the
     * same tagged, contained header every other consumer requires. */
    size_t sz = arena_check_payload(&tls_root, n, "hash node key cap");
    if (sz <= sizeof(LeashHashNode)) return 0;
    return sz - sizeof(LeashHashNode);
}

/* Rebuild a root hash table from a source table, recycling the
 * destination's bucket array and its nodes.
 *
 * The root arena never rewinds: a slot that is overwritten with a
 * fresh hash would otherwise leave one table per store behind (the GC
 * collects the superseded ones). Recycling the storage in place keeps
 * the root arena's footprint at the live table size.
 *
 * The old nodes are handed out through a free list built from the
 * previous chains (their `next` pointers link the spare nodes), so the
 * node allocations are reused as well. A node whose key area is too
 * small for the new key is dropped and a bigger one allocated.
 *
 * The values are rooted into the node's slot: a recycled node keeps
 * its previous root copy, which the value store reuses in place.
 *
 * Returns 0 when the rebuild could not be completed (out of memory):
 * the caller falls back to a fresh copy. */
static int hash_rebuild_root(LeashHash* dst, LeashHash* src,
                             const ar_te* tree, int32_t node) {
    if (!dst->buckets || !src->buckets) return 0;
    int64_t nb = dst->num_buckets;
    if (nb <= 0) return 0;
    LeashHashNode* spare = NULL;
    for (int64_t i = 0; i < nb; i++) {
        LeashHashNode* n = dst->buckets[i];
        while (n) {
            LeashHashNode* nx = n->next;
            n->next = spare;
            spare = n;
            n = nx;
        }
        dst->buckets[i] = NULL;
    }
    dst->count = 0;
    int32_t vc = (node >= 0 && tree) ? tree[node].child : -1;
    int32_t vk = vc >= 0 ? tree[vc].kind : TE_LEAF;
    for (int64_t i = 0; i < src->num_buckets; i++) {
        for (LeashHashNode* c = src->buckets[i]; c; c = c->next) {
            size_t klen = strlen(LEASH_HASH_KEY(c));
            LeashHashNode* nn = NULL;
            if (spare) {
                size_t kcap = hash_node_key_cap(spare);
                if (klen + 1 <= kcap) {
                    nn = spare;
                    spare = spare->next;
                }
            }
            if (!nn) {
                nn = (LeashHashNode*)leash_gc_malloc_rooted(sizeof(LeashHashNode) + klen + 1);
                if (!nn) return 0;
                LEASH_HASH_KEY_INIT(nn);
                nn->val = NULL;
            }
            memcpy(LEASH_HASH_KEY(nn), LEASH_HASH_KEY(c), klen + 1);
            nn->hash = c->hash;
            /* The value is rooted into the node's slot: a recycled node
             * keeps its previous root copy, which is reused in place. */
            if (vk == TE_STR) {
                if (c->val) { leasharena_root_store_string(&nn->val, c->val); }
                else { nn->val = NULL; }
            } else if (vk == TE_VEC) {
                if (c->val) { leasharena_root_store_vec_typed(&nn->val, c->val, tree, vc); }
                else { nn->val = NULL; }
            } else if (vk == TE_HASH) {
                if (c->val) { leasharena_root_store_hash_typed(&nn->val, c->val, tree, vc); }
                else { nn->val = NULL; }
            } else if (vk == TE_AGG) {
                size_t bsz = tree[vc].off ? (size_t)(uintptr_t)tree[vc].off : 0;
                if (c->val && bsz) {
                    leasharena_root_store_blob_typed(&nn->val, c->val, (int64_t)bsz, tree, vc);
                } else { nn->val = NULL; }
            } else {
                nn->val = c->val;
            }
            int64_t idx = (int64_t)(c->hash & (uint64_t)(nb - 1));
            nn->next = dst->buckets[idx];
            dst->buckets[idx] = nn;
            dst->count++;
        }
    }
    return 1;
}

/* Store a hash into a root slot, reusing the slot's previous root
 * copy. `tree`/`node` describe the value type: a value that holds heap
 * pointers (a string, a container, a struct) has to be rooted, and the
 * type tree says how. */
void* leasharena_root_store_hash_typed(void** slot, void* val,
                                       const ar_te* tree, int32_t node) {
    if (!slot) return NULL;
    LeashHash* nh = (LeashHash*)val;
    if (!nh) { *slot = NULL; return NULL; }
    LeashHash* old = (LeashHash*)*slot;
    /* Own root copy: no-op, and do not mark it shared (see the vec store). */
    if (old == nh) return (void*)old;
    /* Alias an existing root table instead of copying it: the GC keeps
     * both holders pointing at the same mutable hash. */
    if (arena_owns(&tls_root, nh)) {
        nh->rooted |= LH_HASH_SHARED;
        *slot = (void*)nh;
        return (void*)nh;
    }
    /* Reuse the slot's previous root copy when the slot owns it alone:
     * the bucket array and the nodes are recycled in place, so a loop
     * that stores a fresh hash into a global keeps ONE table instead of
     * one per store. */
    if (old && arena_owns(&tls_root, old) && !(old->rooted & LH_HASH_SHARED)
        && old->buckets && nh->buckets) {
        if (hash_rebuild_root(old, nh, tree, node)) {
            *slot = (void*)old;
            return (void*)old;
        }
    }
    /* Fresh root copy: the bucket array and every node are rebuilt in
     * the root arena so the table outlives the frame that stored it. */
    *slot = (void*)val;
    if (node >= 0 && tree) {
        leasharena_root_copy_hash_typed_d(slot, tree, node);
    } else {
        leasharena_root_copy_hash(slot);
    }
    return *slot;
}

/* ---- typed root blob store -------------------------------------
 *
 * A struct/class value is copied by value, so the copy stored into a
 * global (or a shared variable) must live in the root arena: the
 * frame that produced it dies when the assignment returns.
 *
 * Copying the whole body on every store would make the root arena
 * grow with the NUMBER of stores instead of with the live data: a
 * loop that assigns a fresh struct to a global leaves one root copy
 * per iteration behind, where the GC collects the superseded ones.
 *
 * The store therefore REUSES the slot's previous root copy when it
 * is a root blob of the same size. The body copy skips the heap field
 * slots, so each field keeps the root copy it had; the field store
 * then overwrites that copy in place (when nothing else can observe
 * it) instead of allocating a new one.
 *
 * `tree`/`node` describe the aggregate: the field entries carry the
 * byte offset of every heap field and the descriptor of its type.
 */

static void root_store_ref(void** rslot, void* newv,
                           const ar_te* tree, int32_t node, int depth);
static void root_store_ref_d(void** rslot, void* newv, const ar_te* tree,
                             int32_t node, int depth, ptr_map* seen);
static void root_store_blob_d(void** slot, void* val, int64_t size,
                              const ar_te* tree, int32_t node, int depth,
                              ptr_map* seen);
void leasharena_root_store_blob_typed(void** slot, void* val, int64_t size,
                                      const ar_te* tree, int32_t node);

/* Root one heap reference: a string, a container or an aggregate body.
 * The slot keeps the root copy it already owns, so the store reuses
 * it in place instead of allocating a new copy. */
static void root_store_ref(void** rslot, void* newv,
                           const ar_te* tree, int32_t node, int depth) {
    ptr_map seen;
    ptr_map_init(&seen, 16);
    root_store_ref_d(rslot, newv, tree, node, depth, &seen);
    ptr_map_free(&seen);
}

static void root_store_ref_d(void** rslot, void* newv, const ar_te* tree,
                             int32_t node, int depth, ptr_map* seen) {
    if (!rslot || node < 0 || !tree) return;
    if (depth >= TE_MAX_DEPTH) {
        /* The source lives in the frame being escaped; storing it verbatim
         * in a root slot would leave a dangling pointer after the pop. A
         * null field is the only safe outcome at the depth cap (cycles are
         * already broken by the identity map before reaching it). */
        *rslot = NULL;
        return;
    }
    int32_t k = tree[node].kind;
    if (k == TE_STR) {
        leasharena_root_store_string(rslot, newv);
    } else if (k == TE_VEC) {
        leasharena_root_store_vec_typed(rslot, newv, tree, node);
    } else if (k == TE_HASH) {
        leasharena_root_store_hash_typed(rslot, newv, tree, node);
    } else if (k == TE_AGG) {
        /* A nested struct/class: its body must be rooted too. */
        size_t bsz = tree[node].off ? (size_t)(uintptr_t)tree[node].off : 0;
        if (bsz && newv) {
            root_store_blob_d(rslot, newv, (int64_t)bsz, tree, node,
                              depth + 1, seen);
        }
    }
}

void leasharena_root_store_blob_typed(void** slot, void* val, int64_t size,
                                      const ar_te* tree, int32_t node) {
    ptr_map seen;
    ptr_map_init(&seen, 32);
    root_store_blob_d(slot, val, size, tree, node, 0, &seen);
    ptr_map_free(&seen);
}

static void root_store_blob_d(void** slot, void* val, int64_t size,
                              const ar_te* tree, int32_t node, int depth,
                              ptr_map* seen) {
    if (!slot) return;
    if (depth >= TE_MAX_DEPTH || !val || size <= 0 || node < 0 || !tree) {
        /* Never store a frame-local `val` into a root slot at the depth
         * cap: it dies with the frame this store is escaping. */
        *slot = NULL;
        return;
    }
    /* A field that points back at an instance already copied in this
     * store must point at its copy. Without the identity map a cycle
     * would recurse until the stack overflows. */
    void* mapped = seen ? ptr_map_get(seen, val) : NULL;
    if (mapped) { *slot = mapped; return; }
    int32_t nf = tree[node].aux;
    int32_t first = tree[node].child;
    void* old = *slot;
    int reusable = 0;
    if (old && arena_owns(&tls_root, old)) {
        /* Only reuse a slot's previous copy if it is a genuine allocation
         * start with a tagged header of exactly this size. An interior
         * pointer or a stale region whose preceding word coincidentally
         * equals `size` must get a fresh buffer, not be written through. */
        arena_block* ob = arena_block_of(&tls_root, old);
        if (ob && (char*)old - 8 >= ob->base) {
            uint64_t raw = *(const uint64_t*)(const void*)((char*)old - 8);
            if (ar_hdr_valid(raw) && ar_hdr_size(raw) == (uint64_t)size) reusable = 1;
        }
    }
    char* nb = (char*)(reusable ? old : leash_gc_malloc_rooted((size_t)size));
    if (!nb) return;
    if (seen) ptr_map_put(seen, val, nb);
    if (!reusable) {
        /* A fresh blob starts with empty field slots: the field store
         * must not mistake an uninitialised pointer for a root copy
         * it may overwrite. */
        memset(nb, 0, (size_t)size);
        *slot = nb;
    }
    /* Copy the body, skipping the heap field slots: a reused blob
     * keeps the root copy it had for each field (the field store
     * overwrites that copy in place), a fresh blob starts empty. */
    size_t pos = 0;
    int32_t i;
    for (i = 0; i < nf; i++) {
        const ar_te* f = &tree[first + i];
        size_t off = f->off ? (size_t)(uintptr_t)f->off : 0;
        if (off < pos) continue;
        if (off + sizeof(void*) > (size_t)size) continue;
        if (off > pos) memcpy(nb + pos, (char*)val + pos, off - pos);
        pos = off + sizeof(void*);
    }
    if (pos < (size_t)size) memcpy(nb + pos, (char*)val + pos, (size_t)size - pos);
    /* Root every heap field of the stored body. */
    for (i = 0; i < nf; i++) {
        const ar_te* f = &tree[first + i];
        size_t off = f->off ? (size_t)(uintptr_t)f->off : 0;
        if (off + sizeof(void*) > (size_t)size) continue;
        void** fslot = (void**)(nb + off);
        void* newv = *(void**)((char*)val + off);
        root_store_ref_d(fslot, newv, tree, f->child, depth, seen);
    }
}

/* ---- spawn transfer ---------------------------------------------
 *
 * The spawn argument block and the copies of the heap arguments live
 * in a PRIVATE arena (see the type at the top of this file). The
 * worker adopts the transfer and the thread-exit hook releases it, so
 * the memory is bounded by the number of workers alive at the same
 * time instead of growing with the number of spawns.
 *
 * The copies are made with the root-copy helpers: the transfer stands
 * in for the root arena while the copy runs, so the copy (and every
 * value it holds) lands in the transfer instead of the program-
 * lifetime arena.
 */

#define LEASH_XFER_VEC  1
#define LEASH_XFER_HASH 2
#define LEASH_XFER_BLOB 3

static leash_xfer* xfer_of(void* block) {
    if (!block) return NULL;
    leash_xfer* x = (leash_xfer*)((char*)block - sizeof(leash_xfer));
    if (x->magic != LEASH_XFER_MAGIC) return NULL;
    return x;
}

/* Make sure the transfer's arena has a block to bump into. The first
 * block is small: a transfer holds one worker's arguments, and the
 * default 4 KB block would dominate a small argument list. */
static void xfer_ensure(leash_xfer* x, size_t size) {
    if (!x || x->st.head) return;
    /* The bump header and alignment need up to 32 bytes. A spawn with a short
     * string argument used to reserve a 512-byte block for a 1-byte copy;
     * size the first block from the actual request and keep only a small
     * floor so repeated tiny allocations still fit. */
    size_t cap = size + 32;
    if (cap < 64) cap = 64;
    arena_new_block_sized(&x->st, cap, 0);
}

static void* xfer_alloc(leash_xfer* x, size_t size) {
    if (!x) return NULL;
    xfer_ensure(x, size);
    return arena_alloc(&x->st, size, 8);
}

void* leasharena_xfer_new(int64_t size) {
    size_t sz = size > 0 ? (size_t)size : 0;
    leash_xfer* x = (leash_xfer*)malloc(sizeof(leash_xfer) + sz);
    if (!x) arena_oom("spawn transfer");
    x->magic = LEASH_XFER_MAGIC;
    x->next = NULL;
    x->st.head = NULL; x->st.cur = NULL; x->st.end = NULL;
    x->st.cps = NULL; x->st.cp_len = 0; x->st.cp_cap = 0; x->st.bytes = 0;
    /* The argument block follows the header. */
    return (void*)((char*)x + sizeof(leash_xfer));
}

/* Copy a string into the transfer. The 8-byte length prefix keeps the
 * true byte length (Leash strings can contain embedded NULs), so the
 * worker's adopt step does not have to guess it with strlen. */
void* leasharena_xfer_copy_string(void* block, void* src) {
    leash_xfer* x = xfer_of(block);
    if (!x || !src) return NULL;
    const char* s = (const char*)src;
    size_t len = 0;
    if (!arena_str_info(s, &len, NULL)) len = strlen(s);
    if (len > (size_t)0xFFFFFFFFu) arena_oom("transfer string");
    char* raw = (char*)xfer_alloc(x, len + 8 + 1);
    if (!raw) return NULL;
    *(uint64_t*)(void*)raw = (((uint64_t)len) << 32) | LEASH_XFER_STR_MAGIC;
    char* out = raw + 8;
    if (len) memcpy(out, s, len);
    out[len] = '\0';
    return out;
}

/* Copy an argument string out of the transfer and into the worker's own
 * frame. The transfer copy's length prefix is authoritative (embedded
 * NULs are legal in a Leash string); only fall back to strlen for a
 * pointer that did not come through leasharena_xfer_copy_string.
 * The transfer is released by the generated worker immediately after
 * this call, so the worker keeps a real arena string of its own.
 *
 * The prefix is untrusted: a stale or bogus pointer with the transfer
 * magic in its preceding word could advertise a huge length and make the
 * memcpy below run off its source. The transfer arena is private to the
 * hand-off and is not reachable from `slot` alone, so the best available
 * guard is a hard ceiling on the advertised length: anything larger is
 * corruption, not a real Leash string. */
void leasharena_adopt_string(void** slot) {
    if (!slot || !*slot) return;
    const char* src = (const char*)*slot;
    size_t len;
    uint64_t tag = *(const uint64_t*)(const void*)(src - 8);
    if ((uint32_t)tag == LEASH_XFER_STR_MAGIC) {
        len = (size_t)(uint32_t)(tag >> 32);
        if (len > (size_t)ARENA_MAX_ALLOC) {
            fprintf(stderr, "Leash arena: corrupt transfer string length\n");
            abort();
        }
    } else {
        len = strlen(src);
    }
    char* out = (char*)leash_gc_alloc_string_cap(len, len ? len : ARENA_STR_MIN_CAP);
    if (len) memcpy(out, src, len);
    out[len] = '\0';
    *slot = out;
}

/* Copy a container (or an aggregate body) into the transfer's private
 * arena. */
static void* xfer_copy_rooted(leash_xfer* x, void* val, int kind, int64_t blob_size) {
    if (!x || !val) return NULL;
    xfer_ensure(x, 512);
    arena_stack saved = tls_root;
    tls_root = x->st;
    void* slot = val;
    if (kind == LEASH_XFER_VEC) leasharena_root_copy_vec(&slot);
    else if (kind == LEASH_XFER_HASH) leasharena_root_copy_hash(&slot);
    else leasharena_root_copy_blob(&slot, blob_size);
    /* The copy advanced the bump pointer of the stand-in stack. */
    x->st = tls_root;
    tls_root = saved;
    return slot;
}

void* leasharena_xfer_copy_vec(void* block, void* val) {
    return xfer_copy_rooted(xfer_of(block), val, LEASH_XFER_VEC, 0);
}

void* leasharena_xfer_copy_hash(void* block, void* val) {
    return xfer_copy_rooted(xfer_of(block), val, LEASH_XFER_HASH, 0);
}

void* leasharena_xfer_copy_blob(void* block, void* val, int64_t size) {
    return xfer_copy_rooted(xfer_of(block), val, LEASH_XFER_BLOB, size);
}

/* Copy a slice's backing array into the transfer and point the
 * aggregate at the copy. */
void leasharena_xfer_copy_slice(void* block, void* agg, int64_t elem_size) {
    leash_xfer* x = xfer_of(block);
    if (!x || !agg) return;
    struct slice_view { int64_t len; void* data; };
    struct slice_view* sv = (struct slice_view*)agg;
    if (!sv || !sv->data || sv->len <= 0 || elem_size <= 0) return;
    size_t bytes = ar_bytes_checked(sv->len, elem_size, "slice transfer");
    void* nb = xfer_alloc(x, bytes);
    if (!nb) return;
    memcpy(nb, sv->data, bytes);
    sv->data = nb;
}

/* Release a transfer and its arena. */
static void xfer_release(leash_xfer* x) {
    if (!x) return;
    arena_block* b = x->st.head;
    while (b) {
        arena_block_stats_sub(sizeof(*b) + b->cap);
        b = b->next;
    }
    arena_free_stack(&x->st);
    free(x);
}

/* Adopt a transfer: the worker thread owns it from now on, and the
 * thread-exit hook releases it. */
void leasharena_xfer_adopt(void* block) {
    leash_xfer* x = xfer_of(block);
    if (!x) return;
    x->next = tls_xfers;
    tls_xfers = x;
}

/* Release a transfer no worker took (a spawn that failed). */
void leasharena_xfer_free(void* block) {
    leash_xfer* x = xfer_of(block);
    if (!x) return;
    /* It may already be on this thread's list (the worker adopted it
     * before the spawn reported its failure): unlink it first. */
    leash_xfer** link = &tls_xfers;
    while (*link) {
        if (*link == x) { *link = x->next; break; }
        link = &(*link)->next;
    }
    xfer_release(x);
}

/* Release every transfer this thread adopted. */
static void xfer_free_all(void) {
    leash_xfer* x = tls_xfers;
    tls_xfers = NULL;
    while (x) {
        leash_xfer* n = x->next;
        xfer_release(x);
        x = n;
    }
}
#endif /* LEASH_ARENA */

#include <time.h>
#include <stdarg.h>
#include <unistd.h>


/* ---- Checked numeric conversions & bulk default fills ----------------
 * Shared by all backends (defined after the backend-specific sections).
 * The compiler emits calls to these for conversions that would otherwise
 * truncate silently or produce poison from out-of-range fptosi. */

void* leash_sh_vec_new(int64_t elem_size, int64_t scan);
void* leash_sh_hash_new(void);

/* Convert a double to a sized integer with an explicit range check.
 * Out-of-range, NaN and infinity produce a diagnostic instead of the
 * undefined value LLVM's fptosi would yield. Only for widths <= 64;
 * wider targets use leash_bigint_parse / bigint formatting. */
int64_t leash_int_from_double_checked(double v, int64_t bits, int64_t is_signed) {
    if (bits <= 0 || bits > 64) {
        fprintf(stderr, "Runtime error: unsupported integer width %lld for float conversion.\n",
                (long long)bits);
        exit(1);
    }
    long double lim = 1.0L;
    int64_t i;
    for (i = 0; i < (is_signed ? bits - 1 : bits); ++i) lim *= 2.0L;
    long double lo = is_signed ? -lim : 0.0L;
    long double hi = is_signed ? lim - 1.0L : lim - 1.0L;
    long double x = (long double)v;
    /* A NaN or infinity fails both comparisons, so this catches all UB. */
    if (!(x >= lo && x <= hi)) {
        fprintf(stderr, "Runtime error: floating-point value out of range for %s integer<%lld>.\n",
                is_signed ? "signed" : "unsigned", (long long)bits);
        exit(1);
    }
    return (int64_t)v;
}

/* `show` of a nil value is always a programming error. The C library's
 * printf may print "(null)" or fault depending on libc; produce a clear
 * diagnostic instead. */
void leash_sh_show_guard(const void* value, const char* what) {
    if (value) return;
    if (what && *what)
        fprintf(stderr, "Runtime error: cannot show a nil value (%s).\n", what);
    else
        fprintf(stderr, "Runtime error: cannot show a nil value.\n");
    exit(1);
}

/* Parse a decimal prefix (like atoll) into a sized integer, but reject a
 * value outside the target range instead of silently truncating it. No
 * digits ("" or "abc") yields zero, matching the historical behaviour. */
int leash_int_parse_checked(const char* s, unsigned bits, int is_signed,
                            unsigned char* out) {
    if (!s || !out || bits == 0 || bits > 64) return 0;
    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') ++s;
    int neg = 0;
    if (*s == '+' || *s == '-') { neg = (*s == '-'); ++s; }
    const char* first = s;
    unsigned long long mag = 0;
    while (*s >= '0' && *s <= '9') {
        unsigned long long d = (unsigned long long)(*s - '0');
        if (mag > (0xFFFFFFFFFFFFFFFFULL - d) / 10ULL) return 0;
        mag = mag * 10ULL + d;
        ++s;
    }
    if (s == first) mag = 0; /* no digits -> 0, matching atoll */
    unsigned long long lim;
    if (is_signed) {
        unsigned long long half = (bits == 64) ? 0x8000000000000000ULL
                                               : (1ULL << (bits - 1));
        lim = neg ? half : half - 1ULL;
        if (mag > lim) return 0;
    } else {
        lim = (bits == 64) ? 0xFFFFFFFFFFFFFFFFULL : ((1ULL << bits) - 1ULL);
        if (mag > lim) return 0;
    }
    unsigned long long v = neg ? (0ULL - mag) : mag;
    unsigned nbytes = (bits + 7u) / 8u, i;
    for (i = 0; i < nbytes; ++i) out[i] = (unsigned char)(v >> (8u * i));
    return 1;
}

/* Fill an array of pointer-sized slots in one pass (strings, pointers). */
void leash_sh_array_fill_ptr(void** data, int64_t count, void* value) {
    if (!data || count <= 0) return;
    for (int64_t i = 0; i < count; ++i) data[i] = value;
}

/* Fill an array of vec handles with one fresh empty vec per element. */
void leash_sh_array_fill_vec(void** data, int64_t count, int64_t elem_size, int64_t scan) {
    if (!data || count <= 0) return;
    for (int64_t i = 0; i < count; ++i) data[i] = leash_sh_vec_new(elem_size, scan);
}

/* Fill an array of hash handles with one fresh empty hash per element. */
void leash_sh_array_fill_hash(void** data, int64_t count) {
    if (!data || count <= 0) return;
    for (int64_t i = 0; i < count; ++i) data[i] = leash_sh_hash_new();
}

static struct timespec _leash_sh_start_ts = {0, 0};

void leash_sh_timepass_init(void) {
    clock_gettime(CLOCK_MONOTONIC, &_leash_sh_start_ts);
}

double leash_sh_timepass(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double secs = (double)(now.tv_sec - _leash_sh_start_ts.tv_sec);
    double nsecs = (double)(now.tv_nsec - _leash_sh_start_ts.tv_nsec);
    return secs + nsecs / 1e9;
}

void leash_sh_wait(double seconds) {
    if (seconds <= 0) return;
    usleep((useconds_t)(seconds * 1e6));
}

/* ---- ChaCha20-based CSPRNG (RFC 8439, 64-bit counter, zero nonce) ----
   seed(s) selects a fully deterministic stream (identical to the JVM
   backend's stream for the same seed); unseeded draws pull the key and
   initial counter from OS entropy (getrandom//dev/urandom, bcrypt on
   Windows, time/pid/address mix as a last resort). */
static uint32_t chacha_key[8];
static uint64_t chacha_ctr = 0;
static uint32_t chacha_buf[16];
static int chacha_pos = 16;
static int chacha_seeded = 0;
#ifdef _WIN32
static CRITICAL_SECTION chacha_cs;
static LONG chacha_cs_ready = 0;
#else
static pthread_mutex_t chacha_mutex = PTHREAD_MUTEX_INITIALIZER;
#endif

static void chacha_lock(void) {
#ifdef _WIN32
    /* 0 -> 1 claims init, 2 means ready (workers may race first use). */
    if (InterlockedCompareExchange(&chacha_cs_ready, 1, 0) == 0) {
        InitializeCriticalSection(&chacha_cs);
        InterlockedExchange(&chacha_cs_ready, 2);
    } else {
        while (chacha_cs_ready != 2) Sleep(0);
    }
    EnterCriticalSection(&chacha_cs);
#else
    pthread_mutex_lock(&chacha_mutex);
#endif
}

static void chacha_unlock(void) {
#ifdef _WIN32
    LeaveCriticalSection(&chacha_cs);
#else
    pthread_mutex_unlock(&chacha_mutex);
#endif
}

static uint64_t chacha_splitmix(uint64_t *x) {
    uint64_t z = (*x += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

/* Fill key/ctr from OS entropy. Caller holds the lock. */
static void chacha_seed_from_os(void) {
    unsigned char buf[40];
    int ok = 0;
#if defined(_WIN32)
    HMODULE h = LoadLibraryA("bcrypt.dll");
    if (h) {
        NTSTATUS (WINAPI *pGen)(PUCHAR, ULONG, ULONG) =
            (void*)(uintptr_t)GetProcAddress(h, "BCryptGenRandom");
        if (pGen && pGen(buf, (ULONG)sizeof buf, 0x00000002u) >= 0) ok = 1;
        FreeLibrary(h);
    }
#else
    FILE *f = fopen("/dev/urandom", "rb");
    if (f) {
        size_t n = fread(buf, 1, sizeof buf, f);
        fclose(f);
        if (n == sizeof buf) ok = 1;
    }
#endif
    if (!ok) {
        uint64_t x = (uint64_t)time(NULL) ^ ((uint64_t)clock() << 1);
#ifdef _WIN32
        x ^= (uint64_t)GetCurrentProcessId() * 0x9E3779B97F4A7C15ull;
#else
        x ^= (uint64_t)getpid() * 0x9E3779B97F4A7C15ull;
#endif
        x ^= (uint64_t)(uintptr_t)&buf;
        for (int i = 0; i < 40; i += 8) {
            uint64_t v = chacha_splitmix(&x);
            for (int b = 0; b < 8 && i + b < 40; b++)
                buf[i + b] = (unsigned char)(v >> (8 * b));
        }
    }
    for (int i = 0; i < 8; i++) {
        chacha_key[i] = (uint32_t)buf[4 * i]
            | ((uint32_t)buf[4 * i + 1] << 8)
            | ((uint32_t)buf[4 * i + 2] << 16)
            | ((uint32_t)buf[4 * i + 3] << 24);
    }
    uint64_t c = 0;
    for (int b = 0; b < 8; b++) c |= (uint64_t)buf[32 + b] << (8 * b);
    chacha_ctr = c;
}

#define CHACHA_QR(x, a, b, c, d) do { \
    (x)[a] += (x)[b]; (x)[d] ^= (x)[a]; (x)[d] = ((x)[d] << 16) | ((x)[d] >> 16); \
    (x)[c] += (x)[d]; (x)[b] ^= (x)[c]; (x)[b] = ((x)[b] << 12) | ((x)[b] >> 20); \
    (x)[a] += (x)[b]; (x)[d] ^= (x)[a]; (x)[d] = ((x)[d] << 8) | ((x)[d] >> 24); \
    (x)[c] += (x)[d]; (x)[b] ^= (x)[c]; (x)[b] = ((x)[b] << 7) | ((x)[b] >> 25); \
} while (0)

/* Run one block into chacha_buf (caller holds the lock). */
static void chacha_block_locked(void) {
    uint32_t st[16];
    st[0] = 1634760805u; st[1] = 857760878u;
    st[2] = 2036477234u; st[3] = 1797285236u;
    for (int i = 0; i < 8; i++) st[4 + i] = chacha_key[i];
    st[12] = (uint32_t)(chacha_ctr & 0xFFFFFFFFu);
    st[13] = (uint32_t)((chacha_ctr >> 32) & 0xFFFFFFFFu);
    st[14] = 0; st[15] = 0;
    uint32_t w[16];
    for (int i = 0; i < 16; i++) w[i] = st[i];
    for (int r = 0; r < 10; r++) {
        CHACHA_QR(w, 0, 4, 8, 12); CHACHA_QR(w, 1, 5, 9, 13);
        CHACHA_QR(w, 2, 6, 10, 14); CHACHA_QR(w, 3, 7, 11, 15);
        CHACHA_QR(w, 0, 5, 10, 15); CHACHA_QR(w, 1, 6, 11, 12);
        CHACHA_QR(w, 2, 7, 8, 13); CHACHA_QR(w, 3, 4, 9, 14);
    }
    for (int i = 0; i < 16; i++) chacha_buf[i] = w[i] + st[i];
    chacha_ctr++;
    chacha_pos = 0;
}

/* One uniform 64-bit draw (caller holds the lock). */
static uint64_t chacha_next_locked(void) {
    if (!chacha_seeded) {
        chacha_seed_from_os();
        chacha_seeded = 1;
        chacha_pos = 16;
    }
    if (chacha_pos >= 16) chacha_block_locked();
    uint32_t lo = chacha_buf[chacha_pos++];
    uint32_t hi = chacha_buf[chacha_pos++];
    return ((uint64_t)hi << 32) | lo;
}

/* Uniform range draw (caller holds the lock). Mirrors the JVM backend. */
static int64_t chacha_range_locked(int64_t min, int64_t max) {
    if (max < min) return min;
    /* Modular span so gigantic ranges wrap instead of invoking UB. */
    uint64_t urange = (uint64_t)max - (uint64_t)min + 1;
    uint64_t r;
    if ((int64_t)urange <= 0) {
        /* Wrapped span: 63-bit draw modulo 2^63-1 stays in range. */
        r = (uint64_t)(chacha_next_locked() & 0x7FFFFFFFFFFFFFFFull)
            % (uint64_t)0x7FFFFFFFFFFFFFFFull;
    } else if (urange > (1ull << 62)) {
        r = (uint64_t)(chacha_next_locked() & 0x7FFFFFFFFFFFFFFFull) % urange;
    } else {
        uint64_t space = 1ull << 62;
        uint64_t limit = space - (space % urange);
        do {
            r = (uint64_t)chacha_next_locked() & (space - 1);
        } while (r >= limit);
        r = r % urange;
    }
    return min + (int64_t)r;
}

void leash_sh_seed(int64_t s) {
    chacha_lock();
    /* Deterministic stream: derive the key from a zero-key block at
       counter s (same derivation as the JVM backend). */
    for (int i = 0; i < 8; i++) chacha_key[i] = 0;
    chacha_ctr = (uint64_t)s;
    chacha_block_locked();
    for (int i = 0; i < 8; i++) chacha_key[i] = chacha_buf[i];
    chacha_ctr = 1;
    chacha_pos = 16;
    chacha_seeded = 1;
    chacha_unlock();
}

int64_t leash_sh_rand(int64_t min, int64_t max) {
    chacha_lock();
    int64_t out = chacha_range_locked(min, max);
    chacha_unlock();
    return out;
}

double leash_sh_randf(double min, double max) {
    chacha_lock();
    uint64_t u = chacha_next_locked();
    chacha_unlock();
    double fraction = (double)(u >> 11) * (1.0 / 9007199254740992.0);
    return fraction * (max - min) + min;
}

char* leash_sh_choose(int64_t n, ...) {
    if (n <= 0) return NULL;
    chacha_lock();
    int64_t idx = chacha_range_locked(0, n - 1);
    va_list ap;
    va_start(ap, n);
    char* result = NULL;
    for (int64_t i = 0; i < n; i++) {
        char* s = va_arg(ap, char*);
        if (i == idx) result = s;
    }
    va_end(ap);
    chacha_unlock();
    return result;
}

char* leash_sh_vec_join(void* vec_ptr, const char* sep) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !v->data || v->size == 0) {
        char* empty = (char*)leash_gc_alloc_string(0);
        empty[0] = '\0';
        return empty;
    }
    size_t sep_len = strlen(sep);
    size_t total = 0;
    for (int64_t i = 0; i < v->size; i++) {
        const char* s = *(const char* const*)lv_at(v, i);
        total += (s ? (size_t)leash_str_bsize(s) : 0) + sep_len;
    }
    char* out = (char*)leash_gc_alloc_string(total);
    size_t o = 0;
    for (int64_t i = 0; i < v->size; i++) {
        const char* s = *(const char* const*)lv_at(v, i);
        if (s) {
            size_t l = (size_t)leash_str_bsize(s);
            memcpy(out + o, s, l);
            o += l;
        }
        if (sep_len) {
            memcpy(out + o, sep, sep_len);
            o += sep_len;
        }
    }
    out[o] = '\0';
    return out;
}

/* Number of Unicode code points in a UTF-8 encoded string: this is what
 * `string.size` reports. Every byte that is not a UTF-8 continuation byte
 * (10xxxxxx) starts a code point, so a valid UTF-8 string counts one per
 * character. Bytes that are not part of a well-formed sequence are counted
 * as one code point each (the JVM backend decodes them the same way). */
int64_t leash_utf8_len(const char* s) {
    if (!s) return 0;
    int64_t nbytes = leash_str_bsize(s);
    const unsigned char* p = (const unsigned char*)s;
    int64_t n = 0;
    for (int64_t i = 0; i < nbytes; i++) {
        if ((p[i] & 0xC0) != 0x80) n++;
    }
    return n;
}

char* leash_sh_str_sub(const char* a, const char* b) {
    if (!a) return NULL;
    size_t alen = (size_t)leash_str_bsize(a);
    size_t blen = (b && *b) ? (size_t)leash_str_bsize(b) : 0;
    char* out = (char*)leash_gc_alloc_string(alen);
    size_t o = 0;
    if (blen == 0) {
        memcpy(out, a, alen);
        out[alen] = '\0';
        return out;
    }
    size_t i = 0;
    while (i < alen) {
        if (i + blen <= alen && memcmp(a + i, b, blen) == 0) {
            i += blen;
        } else {
            out[o++] = a[i];
            i++;
        }
    }
    out[o] = '\0';
    /* Shrink prefix len if o < alen (copy occurred). */
    if (o != alen) LEASH_STR_SET_PREFIX(out, o);
    return out;
}

char* leash_sh_str_replace(const char* s, const char* from, const char* to) {
    if (!s) return NULL;
    size_t slen = (size_t)leash_str_bsize(s);
    size_t flen = (from && *from) ? (size_t)leash_str_bsize(from) : 0;
    size_t tlen = (to) ? (size_t)leash_str_bsize(to) : 0;
    if (flen == 0) {
        char* out = (char*)leash_gc_alloc_string(slen);
        memcpy(out, s, slen);
        out[slen] = '\0';
        return out;
    }
    /* upper bound: every occurrence replaced by tlen */
    size_t count = 0;
    for (size_t i = 0; i + flen <= slen; ) {
        if (memcmp(s + i, from, flen) == 0) { count++; i += flen; }
        else i++;
    }
    size_t need = slen + count * (tlen > flen ? tlen - flen : 0);
    char* out = (char*)leash_gc_alloc_string(need);
    size_t o = 0;
    for (size_t i = 0; i < slen; ) {
        if (i + flen <= slen && memcmp(s + i, from, flen) == 0) {
            memcpy(out + o, to, tlen);
            o += tlen;
            i += flen;
        } else {
            out[o++] = s[i];
            i++;
        }
    }
    out[o] = '\0';
    if (o != need) LEASH_STR_SET_PREFIX(out, o);
    return out;
}

char* leash_sh_normescape(const char* s) {
    if (!s) return NULL;
    size_t len = (size_t)leash_str_bsize(s);
    char* out = (char*)leash_gc_alloc_string(len);
    size_t o = 0;
    for (size_t i = 0; i < len; i++) {
        if (s[i] == '\\' && i + 1 < len) {
            char c = s[i + 1];
            i++;
            switch (c) {
                case 'n': out[o++] = '\n'; break;
                case 't': out[o++] = '\t'; break;
                case 'r': out[o++] = '\r'; break;
                case '0': out[o++] = '\0'; break;
                case '\\': out[o++] = '\\'; break;
                case '"': out[o++] = '"'; break;
                case '\'': out[o++] = '\''; break;
                default: out[o++] = '\\'; out[o++] = c; break;
            }
        } else {
            out[o++] = s[i];
        }
    }
    out[o] = '\0';
    if (o != len) LEASH_STR_SET_PREFIX(out, o);
    return out;
}

char* leash_sh_slice_to_str(int64_t len, const char* ptr) {
    if (!ptr || len < 0) len = 0;
    char* out = (char*)leash_gc_alloc_string((size_t)len);
    if (len > 0) memcpy(out, ptr, (size_t)len);
    out[len] = '\0';
    return out;
}

int64_t leash_sh_arr_isin_i64(int64_t val, const int64_t* data, int64_t len) {
    if (!data || len <= 0) return 0;
    for (int64_t i = 0; i < len; i++) {
        if (data[i] == val) return 1;
    }
    return 0;
}

int64_t leash_sh_arr_isin_i8(int8_t val, const int8_t* data, int64_t len) {
    if (!data || len <= 0) return 0;
    for (int64_t i = 0; i < len; i++) {
        if (data[i] == val) return 1;
    }
    return 0;
}

int64_t leash_sh_arr_isin_f64(double val, const double* data, int64_t len) {
    if (!data || len <= 0) return 0;
    for (int64_t i = 0; i < len; i++) {
        if (data[i] == val) return 1;
    }
    return 0;
}

int64_t leash_sh_arr_isin_str(const char* val, const char* const* data, int64_t len) {
    if (!data || len <= 0) return 0;
    for (int64_t i = 0; i < len; i++) {
        if (data[i] && val && strcmp(data[i], val) == 0) return 1;
    }
    return 0;
}

int64_t leash_sh_str_isin(const char* needle, const char* haystack) {
    if (!needle || !haystack) return 0;
    if (!*needle) return 1;
    size_t nl = strlen(needle);
    const char* p = strstr(haystack, needle);
    (void)nl;
    return p ? 1 : 0;
}

/* mode 0: byte compare of an elem_size slot against `elem` (int/float/
   char/bool/wide). mode 1: element is a C string, compare contents. */
int64_t leash_sh_vec_isin(void* vec_ptr, const void* elem, int64_t mode) {
    LeashVec* v = (LeashVec*)vec_ptr;
    if (!v || !v->data || !elem) return 0;
    for (int64_t i = 0; i < v->size; i++) {
        if (mode == 1) {
            const char* a = *(const char* const*)lv_at(v, i);
            const char* b = (const char*)elem;
            if (a == b || (a && b && strcmp(a, b) == 0)) return 1;
        } else {
            if (memcmp(lv_at(v, i), elem, (size_t)v->elem_size) == 0) return 1;
        }
    }
    return 0;
}

void leash_sh_vec_clear(void* p) {
    if (!p) return;
    ((LeashVec*)p)->size = 0;
}

static double leash_unbox_f64(void* p) {
    double d;
    int64_t v = (int64_t)(intptr_t)p;
    memcpy(&d, &v, sizeof(d));
    return d;
}

static void* leash_box_f64(double d) {
    int64_t v;
    memcpy(&v, &d, sizeof(v));
    return (void*)(intptr_t)v;
}

/* Element-wise arithmetic on two dynamic containers.
 *
 * op: 0=+, 1=-, 2=*, 3=/.  is_float: 0 = integer, 1 = float.
 * swap: non-zero computes "b OP a" instead of "a OP b" (the scalar
 * stood on the left of the operator).
 * uns: non-zero means the elements are UNSIGNED integers of the
 * vector's width (the compiler knows the declared element type). A
 * plain `int64_t` accumulator sign-extends a 4-byte unsigned element,
 * so vec<uint>{2147483648} / 2 came out as 3221225472 instead of
 * 1073741824, and 8-byte unsigned values came out negative.
 *
 * The element width comes from the vector itself -- the compiler
 * creates every vector with the element size of its type -- and the
 * result has the same width. The old code always produced an 8-byte
 * element vector and always read 8 bytes per element, so a
 * vec<float<32>> (4-byte elements) was read out of bounds and the
 * result did not match the operand type. */
static int64_t lv_load_int(const void* p, int64_t esz, int uns) {
    if (esz == 1) {
        if (uns) { uint8_t t; memcpy(&t, p, 1); return (int64_t)t; }
        int8_t t; memcpy(&t, p, 1); return (int64_t)t;
    }
    if (esz == 2) {
        if (uns) { uint16_t t; memcpy(&t, p, 2); return (int64_t)t; }
        int16_t t; memcpy(&t, p, 2); return (int64_t)t;
    }
    if (esz == 4) {
        if (uns) { uint32_t t; memcpy(&t, p, 4); return (int64_t)t; }
        int32_t t; memcpy(&t, p, 4); return (int64_t)t;
    }
    int64_t t = 0;
    memcpy(&t, p, 8);
    return t;
}

/* Store one computed element into a `esz`-wide slot. The arithmetic
 * happens in the 64-bit accumulator, so the result wraps into the
 * element width exactly like the machine type would. */
static void lv_store_int(void* out, int64_t r, int64_t esz) {
    switch (esz) {
        case 1: { int8_t t = (int8_t)r; memcpy(out, &t, 1); break; }
        case 2: { int16_t t = (int16_t)r; memcpy(out, &t, 2); break; }
        case 4: { int32_t t = (int32_t)r; memcpy(out, &t, 4); break; }
        default: { int64_t t = r; memcpy(out, &t, 8); break; }
    }
}

/* Compute one element and push it onto the result vector. */
static void lv_binop_push(void* out, int64_t op, int64_t is_float, int64_t esz,
                          int64_t uns, const void* pa, const void* pb) {
    if (is_float && esz == 4) {
        float fx = 0.0f, fy = 0.0f, fr = 0.0f;
        memcpy(&fx, pa, 4);
        memcpy(&fy, pb, 4);
        if (op == 1) fr = fx - fy;
        else if (op == 2) fr = fx * fy;
        else if (op == 3) fr = (fy == 0.0f) ? 0.0f : fx / fy;
        else fr = fx + fy;
        leash_sh_vec_pushb(out, &fr);
        return;
    }
    if (is_float) {
        double dx = 0.0, dy = 0.0, dr = 0.0;
        memcpy(&dx, pa, 8);
        memcpy(&dy, pb, 8);
        if (op == 1) dr = dx - dy;
        else if (op == 2) dr = dx * dy;
        else if (op == 3) dr = (dy == 0.0) ? 0.0 : dx / dy;
        else dr = dx + dy;
        leash_sh_vec_pushb(out, &dr);
        return;
    }
    int64_t ix = lv_load_int(pa, esz, uns);
    int64_t iy = lv_load_int(pb, esz, uns);
    int64_t r = 0;
    if (uns) {
        uint64_t ux = (uint64_t)ix, uy = (uint64_t)iy, ur = 0;
        if (op == 1) ur = ux - uy;
        else if (op == 2) ur = ux * uy;
        else if (op == 3) ur = (uy == 0) ? 0 : ux / uy;
        else ur = ux + uy;
        r = (int64_t)ur;
    } else if (op == 1) {
        r = ix - iy;
    } else if (op == 2) {
        r = ix * iy;
    } else if (op == 3) {
        /* INT64_MIN / -1 is undefined in C; the machine result is
         * INT64_MIN, so special-case it instead of trapping. */
        if (iy == 0) r = 0;
        else if (ix == INT64_MIN && iy == -1) r = INT64_MIN;
        else r = ix / iy;
    } else {
        r = ix + iy;
    }
    char tmp[8];
    lv_store_int(tmp, r, esz);
    leash_sh_vec_pushb(out, tmp);
}

void* leash_sh_vec_binop(void* pa, void* pb, int64_t op, int64_t is_float,
                         int64_t swap, int64_t uns) {
    LeashVec* a = (LeashVec*)pa;
    LeashVec* b = (LeashVec*)pb;
    if (!a || !b) return NULL;
    int64_t esz = a->elem_size > 0 ? a->elem_size : 8;
    void* out = leash_sh_vec_new(esz, 0);
    int64_t n = a->size < b->size ? a->size : b->size;
    for (int64_t i = 0; i < n; i++) {
        if (swap) lv_binop_push(out, op, is_float, esz, uns, lv_at(b, i), lv_at(a, i));
        else lv_binop_push(out, op, is_float, esz, uns, lv_at(a, i), lv_at(b, i));
    }
    return out;
}

/* Element-wise arithmetic between a vector and a scalar: the scalar is
 * broadcast over every element. The compiler lowers "vec + 1" (and the
 * mirrored "1 + vec") to this. */
void* leash_sh_vec_binop_scalar(void* pa, const void* scalar, int64_t op,
                                 int64_t is_float, int64_t swap, int64_t uns) {
    LeashVec* a = (LeashVec*)pa;
    if (!a || !scalar) return NULL;
    int64_t esz = a->elem_size > 0 ? a->elem_size : 8;
    void* out = leash_sh_vec_new(esz, 0);
    for (int64_t i = 0; i < a->size; i++) {
        if (swap) lv_binop_push(out, op, is_float, esz, uns, scalar, lv_at(a, i));
        else lv_binop_push(out, op, is_float, esz, uns, lv_at(a, i), scalar);
    }
    return out;
}

/* Deep equality for two vecs. mode 2: elements are C strings (compare
   contents); otherwise raw bytes compare element by element. */
int64_t leash_sh_vec_eq(void* pa, void* pb, int64_t mode) {
    LeashVec* a = (LeashVec*)pa;
    LeashVec* b = (LeashVec*)pb;
    if (!a || !b) return (a == b) ? 1 : 0;
    if (a->size != b->size) return 0;
    if (a->size == 0) return 1;
    if (mode == 2) {
        for (int64_t i = 0; i < a->size; i++) {
            const char* sx = *(const char* const*)lv_at(a, i);
            const char* sy = *(const char* const*)lv_at(b, i);
            if (sx != sy && (!sx || !sy || strcmp(sx, sy) != 0)) return 0;
        }
        return 1;
    }
    if (a->elem_size == b->elem_size) {
        return memcmp(a->data, b->data,
                      (size_t)a->size * (size_t)a->elem_size) == 0;
    }
    for (int64_t i = 0; i < a->size; i++) {
        if (memcmp(lv_at(a, i), lv_at(b, i),
                   (size_t)(a->elem_size < b->elem_size ? a->elem_size : b->elem_size)) != 0)
            return 0;
    }
    return 1;
}

void* leash_sh_hash_keys(void* hash_ptr) {
    LeashHash* h = (LeashHash*)hash_ptr;
    void* vec = leash_sh_vec_new(8, 1);
    if (!h || !h->buckets) return vec;
    for (int64_t i = 0; i < h->num_buckets; i++) {
        LeashHashNode* cur = h->buckets[i];
        while (cur) {
            /* pushb copies FROM the given address — pass the address of
               the key slot, not the string pointer itself */
            leash_sh_vec_pushb(vec, LEASH_HASH_KEY_SLOT(cur));
            cur = cur->next;
        }
    }
    return vec;
}

void* leash_sh_hash_values(void* hash_ptr) {
    LeashHash* h = (LeashHash*)hash_ptr;
    void* vec = leash_sh_vec_new(8, 1);
    if (!h || !h->buckets) return vec;
    for (int64_t i = 0; i < h->num_buckets; i++) {
        LeashHashNode* cur = h->buckets[i];
        while (cur) {
            leash_sh_vec_pushb(vec, &cur->val);
            cur = cur->next;
        }
    }
    return vec;
}

void* leash_sh_file_open(const char* path, const char* mode) {
    if (!path || !mode) return NULL;
    return (void*)fopen(path, mode);
}

/* Heap-owned empty string for failure paths of functions whose results
   the autofree sweep frees (a string literal is not freeable). Under the
   GC this is collected like any other string; under -DNO_GC it leaks
   like every other untracked allocation. */
static char* leash_empty_heap_str(void) {
    char* buf = (char*)leash_gc_alloc_string(0);
    if (buf) buf[0] = '\0';
    return buf;
}

char* leash_sh_file_read(void* fp) {
    if (!fp) return leash_empty_heap_str();
    FILE* f = (FILE*)fp;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0) sz = 0;
    char* buf = (char*)leash_gc_alloc_string((size_t)sz);
    size_t read_bytes = fread(buf, 1, sz, f);
    buf[read_bytes] = '\0';
    /* Update len prefix if short read. */
    if (read_bytes != (size_t)sz) LEASH_STR_SET_PREFIX(buf, read_bytes);
    return buf;
}

/* A byte-slice: length plus payload pointer.
 *
 * The slice is handed back through an out-pointer instead of by value.
 * A 16-byte struct return crosses the IR/C boundary with a
 * target-dependent convention: clang lowers the generated IR call to
 * "returned in RAX:RDX", but lowers the C definition to "returned
 * through a hidden pointer" on Windows x64. The two sides then disagree
 * and the caller reads the file handle as the slice length (a crash
 * inside the first fseek). Passing a pointer to the result keeps the
 * boundary to plain scalars and pointers on every target. */
typedef struct { int64_t len; char* ptr; } leash_slice_t;

int64_t leash_sh_file_write(void* fp, const char* str) {
    if (!fp || !str) return -1;
    size_t n = strlen(str);
    size_t w = fwrite(str, 1, n, (FILE*)fp);
    return (w == n) ? 0 : -1;
}

int64_t leash_sh_file_close(void* fp) {
    if (!fp) return -1;
    return (fclose((FILE*)fp) == 0) ? 0 : -1;
}

int64_t leash_sh_file_writeb(void* fp, const char* buf, int64_t len) {
    if (!fp || !buf) return -1;
    if (len < 0) len = 0;
    size_t w = fwrite(buf, 1, (size_t)len, (FILE*)fp);
    return (w == (size_t)len) ? 0 : -1;
}

/* Write `size` elements of `esz` bytes as raw bytes, one low byte per
   element (little-endian first byte). Lets Leash code emit exact binary
   output (e.g. JVM .class files), which NUL-free strings cannot hold. */
int64_t leash_sh_file_writevec(void* fp, const void* data, int64_t size, int64_t esz) {
    if (!fp || !data) return -1;
    if (size <= 0) return 0;
    if (esz <= 0) esz = 1;
    for (int64_t i = 0; i < size; i++) {
        uint8_t b = ((const uint8_t*)data)[i * esz];
        if (fputc(b, (FILE*)fp) == EOF) return -1;
    }
    return 0;
}

static void leash_file_slurp(FILE* f, leash_slice_t* out) {
    out->len = 0;
    out->ptr = NULL;
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0) sz = 0;
    char* buf = (char*)leash_gc_malloc(sz + 1);
    size_t rb = fread(buf, 1, (size_t)sz, f);
    buf[rb] = '\0';
    out->len = (int64_t)rb;
    out->ptr = buf;
}

void leash_sh_file_readb(void* fp, leash_slice_t* out) {
    if (!out) return;
    out->len = 0;
    out->ptr = NULL;
    if (!fp) return;
    leash_file_slurp((FILE*)fp, out);
}

char* leash_sh_file_readln(void* fp) {
    if (!fp) return leash_empty_heap_str();
    char tmp[4096];
    if (!fgets(tmp, sizeof(tmp), (FILE*)fp)) return leash_empty_heap_str();
    size_t n = strlen(tmp);
    while (n > 0 && (tmp[n - 1] == '\n' || tmp[n - 1] == '\r')) { tmp[--n] = '\0'; }
    char* buf = (char*)leash_gc_alloc_string(n);
    memcpy(buf, tmp, n);
    buf[n] = '\0';
    return buf;
}

void leash_sh_file_readlnb(void* fp, leash_slice_t* out) {
    if (!out) return;
    out->len = 0;
    out->ptr = NULL;
    if (!fp) return;
    char tmp[4096];
    if (!fgets(tmp, sizeof(tmp), (FILE*)fp)) {
        out->ptr = (char*)leash_gc_malloc(1);
        out->ptr[0] = '\0';
        return;
    }
    size_t n = strlen(tmp);
    while (n > 0 && (tmp[n - 1] == '\n' || tmp[n - 1] == '\r')) { tmp[--n] = '\0'; }
    char* buf = (char*)leash_gc_malloc(n + 1);
    memcpy(buf, tmp, n + 1);
    out->len = (int64_t)n;
    out->ptr = buf;
}

void leash_sh_file_rewind(void* fp) {
    if (fp) fseek((FILE*)fp, 0, SEEK_SET);
}

int64_t leash_sh_file_rename(const char* oldn, const char* newn) {
    if (!oldn || !newn) return -1;
    return (rename(oldn, newn) == 0) ? 0 : -1;
}

int64_t leash_sh_file_delete(const char* path) {
    if (!path) return -1;
    return (remove(path) == 0) ? 0 : -1;
}

static char* leash_replace_build(const char* buf, const char* oldstr, const char* newstr,
                                 int64_t replace_all, int64_t* out_count) {
    size_t old_len = (size_t)leash_str_bsize(oldstr);
    size_t new_len = (size_t)leash_str_bsize(newstr);
    size_t file_size = (size_t)leash_str_bsize(buf);
    int64_t count = 0;
    if (old_len == 0) {
        *out_count = 0;
        return NULL;
    }
    size_t need = file_size * (new_len + 1) + 1;
    char* result = (char*)leash_gc_alloc_string_cap(need, need);
    if (replace_all) {
        const char* src = buf;
        const char* end = buf + file_size;
        char* dst = result;
        while (src < end) {
            if ((size_t)(end - src) >= old_len && memcmp(src, oldstr, old_len) == 0) {
                memcpy(dst, newstr, new_len);
                src += old_len;
                dst += new_len;
                count++;
            } else {
                *dst++ = *src++;
            }
        }
        *dst = '\0';
        size_t out_len = (size_t)(dst - result);
        LEASH_STR_SET_PREFIX(result, out_len);
    } else {
        const char* found = strstr(buf, oldstr);
        if (!found) {
            memcpy(result, buf, file_size);
            result[file_size] = '\0';
            LEASH_STR_SET_PREFIX(result, file_size);
            *out_count = 0;
            return result;
        }
        size_t prefix = (size_t)(found - buf);
        size_t suffix_len = file_size - prefix - old_len;
        size_t out_len = prefix + new_len + suffix_len;
        memcpy(result, buf, prefix);
        memcpy(result + prefix, newstr, new_len);
        memcpy(result + prefix + new_len, found + old_len, suffix_len);
        result[out_len] = '\0';
        LEASH_STR_SET_PREFIX(result, out_len);
        count = 1;
    }
    *out_count = count;
    return result;
}

int64_t leash_sh_file_replace(void* fp, const char* oldstr, const char* newstr, int64_t all) {
    if (!fp) return 0;
    FILE* f = (FILE*)fp;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0) sz = 0;
    char* buf = (char*)leash_gc_malloc(sz + 1);
    size_t rb = fread(buf, 1, (size_t)sz, f);
    buf[rb] = '\0';

    int64_t count = 0;
    char* result = leash_replace_build(buf, oldstr, newstr, all, &count);
    if (result == NULL) { fseek(f, 0, SEEK_SET); return 0; }

    size_t out_len = strlen(result);
    fseek(f, 0, SEEK_SET);
    fwrite(result, 1, out_len, f);
    fflush(f);
    ftruncate(fileno(f), (off_t)out_len);
    fseek(f, 0, SEEK_SET);
    return count;
}

char* leash_sh_exec(const char* cmd, const char* mode) {
    if (!cmd) return leash_empty_heap_str();
    if (mode && strcmp(mode, "silent") == 0) {
        /* capture but do not forward to stdout */
        FILE* fp = popen(cmd, "r");
        if (!fp) return leash_empty_heap_str();
        size_t cap = 8192, len = 0;
        char* buf = (char*)leash_gc_alloc_string_cap(cap, cap);
        size_t n;
        while ((n = fread(buf + len, 1, cap - len - 1, fp)) > 0) {
            len += n;
            if (cap - len < 2) break;
        }
        buf[len] = '\0';
        LEASH_STR_SET_PREFIX(buf, len);
        pclose(fp);
        return buf;
    }
    if (mode && strcmp(mode, "wait") == 0) {
        /* run and forward stdout, also capture and return */
        FILE* fp = popen(cmd, "r");
        if (!fp) return leash_empty_heap_str();
        size_t cap = 8192, len = 0;
        char* buf = (char*)leash_gc_alloc_string_cap(cap, cap);
        size_t n;
        while ((n = fread(buf + len, 1, cap - len - 1, fp)) > 0) {
            len += n;
            if (cap - len < 2) break;
        }
        buf[len] = '\0';
        LEASH_STR_SET_PREFIX(buf, len);
        pclose(fp);
        return buf;
    }
    if (mode && strcmp(mode, "run") == 0) {
        /* run inheriting stdin/stdout/stderr, wait, return the exit code */
        int code = system(cmd);
        int exit_code = 0;
#ifndef _WIN32
        if (WIFEXITED(code)) {
            exit_code = WEXITSTATUS(code);
        } else {
            exit_code = code;
        }
#else
        exit_code = code;
#endif
        char* buf = leash_int_to_str_fast(exit_code);
        return buf;
    }
    if (mode && strcmp(mode, "code") == 0) {
        int code = 0;
        FILE* fp = popen(cmd, "r");
        if (fp) {
            char sink[512];
            while (fread(sink, 1, sizeof(sink), fp) > 0) {}
            code = pclose(fp);
        }
        int exit_code = 0;
#ifndef _WIN32
        if (WIFEXITED(code)) {
            exit_code = WEXITSTATUS(code);
        } else {
            exit_code = code;
        }
#else
        exit_code = code;
#endif
        char* buf = leash_int_to_str_fast(exit_code);
        return buf;
    } else {
        system(cmd);
        return leash_empty_heap_str();
    }
}

