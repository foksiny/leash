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
