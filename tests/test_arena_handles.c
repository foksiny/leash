/* Standalone test for the 32-bit idx/gen handle table and compacting
 * string data stack in the arena runtime. */
#include <stdio.h>
#include <stdlib.h>
#include "../leash/gc.h"
int leasharena_handle_selftest(void);
int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();
    int rc = leasharena_handle_selftest();
    if (rc != 0) { fprintf(stderr, "handle selftest failed: %d\n", rc); return 1; }
    puts("handle selftest ok");
    return 0;
}
