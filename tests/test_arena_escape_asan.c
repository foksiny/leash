/* Minimal reproducer for aggregate-return escape: a vec created in a
 * frame that has already popped is staged by the caller. Under ASan the
 * staging read is a use-after-free until the escape path is redesigned. */
#include <stdio.h>
#include <stdlib.h>

#include "../leash/gc.h"

void* leash_sh_vec_new(int64_t elem_size, int64_t scan);
void leash_sh_vec_pushb(void* vec, void* val);
void** leasharena_escape_slot(void);
void leasharena_escape_vec(void** slot, int64_t nframes);

static void* g_stale;

static void producer(void) {
    leasharena_enter();
    int64_t value = 1;
    g_stale = leash_sh_vec_new(8, 0);
    leash_sh_vec_pushb(g_stale, &value);
    leasharena_exit(); /* frees the block holding the vec header */
}

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();
    producer();
    void** slot = leasharena_escape_slot();
    *slot = g_stale;
    leasharena_escape_vec(slot, 0);
    puts("escape ok");
    return 0;
}
