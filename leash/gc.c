/**
 * Leash Custom Garbage Collector - Minimal Working Version
 * A simple mark-and-sweep GC for Leash.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Configuration */
#define MAX_ROOTS 10000
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
    if (gc.roots != NULL) return;  /* Already initialized */
    
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
    
    size_t total_size = sizeof(struct gc_object) + size;
    struct gc_object* obj = (struct gc_object*)malloc(total_size);
    if (!obj) {
        fprintf(stderr, "Leash GC: Out of memory!\n");
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
    return user_ptr;
}

void* leash_gc_realloc(void* ptr, size_t new_size) {
    if (!ptr) return leash_gc_malloc(new_size);
    if (new_size == 0) return NULL;
    
    struct gc_object* obj = ((struct gc_object*)ptr) - 1;
    if (obj->size >= new_size) return ptr;
    
    void* new_ptr = leash_gc_malloc(new_size);
    if (!new_ptr) return NULL;
    
    memcpy(new_ptr, ptr, obj->size);
    return new_ptr;
}

/* Root Management */
void leash_gc_register_root(void* ptr) {
    if (!ptr) return;
    
    if (gc.root_count >= gc.root_capacity) {
        size_t new_cap = gc.root_capacity * 2;
        void** new_roots = (void**)realloc(gc.roots, new_cap * sizeof(void*));
        if (!new_roots) return;
        gc.roots = new_roots;
        memset(gc.roots + gc.root_capacity, 0, (new_cap - gc.root_capacity) * sizeof(void*));
        gc.root_capacity = new_cap;
    }
    gc.roots[gc.root_count++] = ptr;
}

void leash_gc_unregister_root(void* ptr) {
    if (!ptr) return;
    size_t i;
    for (i = 0; i < gc.root_count; i++) {
        if (gc.roots[i] == ptr) {
            gc.roots[i] = gc.roots[gc.root_count - 1];
            gc.roots[gc.root_count - 1] = NULL;
            gc.root_count--;
            return;
        }
    }
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
            /* Check if it points to one of our objects */
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
            /* Check if root points to one of our objects */
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
    mark_from_roots();
    sweep();
    gc.collect_count++;
}

/* Utility Functions */
void* leash_gc_alloc_string(size_t len) {
    return leash_gc_malloc(len + 1);
}

void* leash_gc_alloc_vector_data(size_t elem_size, size_t capacity) {
    return leash_gc_malloc(elem_size * capacity);
}
