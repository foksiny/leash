/* Cross-thread root visibility harness.
 *
 * A root allocation made before the first spawn, and another made after
 * it, must both be resolvable from a worker's thread-local block index.
 * The worker repeatedly validates a container pointer and reads a root
 * string, so a missing registry entry (or a stale cached block) aborts
 * instead of silently reading freed memory. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../leash/gc.h"

static void* early_blob;
static char* early_str;
static void* late_blob;

static void* worker(void* arg) {
    (void)arg;
    leash_gc_thread_attach();
    leash_gc_worker_begin();
    volatile int64_t acc = 0;
    for (int i = 0; i < 20000; i++) {
        if (leasharena_checked(early_blob) != early_blob) return (void*)1;
        if (leasharena_checked(late_blob) != late_blob) return (void*)1;
        acc += leash_str_bsize(early_str);
    }
    if (acc != 16 * 20000) return (void*)2;
    leash_gc_worker_end();
    return NULL;
}

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();
    early_blob = leash_gc_malloc_rooted(64);
    early_str = (char*)leasharena_root_alloc_string_cap(16, 16);
    if (!early_blob || !early_str) return 1;
    memcpy(early_str, "0123456789abcdef", 16);
    early_str[16] = 0;
    *(uint64_t*)(void*)(early_str - 8) = 16;

    /* Publish the blocks that already exist, then make another one. */
    leash_gc_thread_spawned();
    late_blob = leash_gc_malloc_rooted(64);
    if (!late_blob) return 1;

    pthread_t th[4];
    for (int i = 0; i < 4; i++)
        if (pthread_create(&th[i], NULL, worker, NULL) != 0) return 1;
    for (int i = 0; i < 4; i++) {
        void* rc = NULL;
        pthread_join(th[i], &rc);
        if (rc != NULL) return 1;
    }
    printf("cross-thread root ok\n");
    leash_gc_shutdown();
    return 0;
}
