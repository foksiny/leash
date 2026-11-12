/* Standalone harness for the arena's optional poison mode.
 *
 * The arena reclaims a scope by rewinding the bump pointer, so a stale
 * pointer into that region keeps reading whatever lands there next.
 * With LEASH_ARENA_POISON=1 the reclaimed bytes are filled with 0xDD,
 * which turns that silent corruption into visible garbage.
 *
 * Build & run:  python3 tests/test_arena_poison.py
 * (or) cc tests/test_arena_poison.c leash/gc.c -DLEASH_ARENA -O1 \
 *         -Wall -Wextra -o test_arena_poison -pthread
 */

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../leash/gc.h"

#define POISON_BYTE 0xDD

/* Every byte of [p, p+len) must be the poison byte. */
static void assert_poisoned(const void* p, size_t len) {
    const unsigned char* b = (const unsigned char*)p;
    for (size_t i = 0; i < len; i++) {
        if (b[i] != POISON_BYTE) {
            fprintf(stderr, "expected poison at offset %zu, got 0x%02X\n",
                    i, b[i]);
            assert(b[i] == POISON_BYTE);
        }
    }
}

static void test_scope_exit_poisons(void) {
    setenv("LEASH_ARENA_POISON", "1", 1);
    leash_gc_init();

    /* A scope that allocates a string and then dies. */
    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(32, 32);
    assert(s != NULL);
    memset(s, 'A', 32);
    s[32] = '\0';
    /* The allocation covers [s-16, s+33): the 16-byte header, the
     * payload and the NUL terminator (padded to 8). */
    size_t total = 16 + 32 + 1;
    total = (total + 7) & ~(size_t)7;
    const char* base = s - 16;

    leasharena_exit();

    /* The scope is gone: every reclaimed byte must read as poison. */
    assert_poisoned(base, total);
}

static void test_loop_reset_poisons(void) {
    setenv("LEASH_ARENA_POISON", "1", 1);
    leash_gc_init();

    int64_t frame = leasharena_loop_enter();
    char* s = (char*)leash_gc_alloc_string_cap(16, 16);
    assert(s != NULL);
    memset(s, 'B', 16);
    s[16] = '\0';
    size_t total = 16 + 16 + 1;
    total = (total + 7) & ~(size_t)7;
    const char* base = s - 16;

    /* Rewinding to the loop frame reclaims the same bytes. */
    leasharena_loop_reset(frame);
    assert_poisoned(base, total);

    leasharena_loop_exit(frame);
}

static void test_escaped_value_survives(void) {
    setenv("LEASH_ARENA_POISON", "1", 1);
    leash_gc_init();

    /* A string that escapes its scope is copied out before the scope is
     * reclaimed, so the live copy must not be poisoned. */
    char* slot = NULL;
    leasharena_enter();
    char* inner = (char*)leash_gc_alloc_string_cap(8, 8);
    assert(inner != NULL);
    memcpy(inner, "abcdefg", 8);
    slot = inner;
    /* escape_string stages, pops nframes, and commits into the caller
     * frame -- so it performs the exit for that scope. */
    leasharena_escape_string((void**)&slot, 1);

    assert(strcmp(slot, "abcdefg") == 0);
}

static void test_poison_on_by_default(void) {
    unsetenv("LEASH_ARENA_POISON");
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(32, 32);
    assert(s != NULL);
    memset(s, 'C', 32);
    s[32] = '\0';
    size_t total = 16 + 32 + 1;
    total = (total + 7) & ~(size_t)7;
    const char* base = s - 16;

    leasharena_exit();

    /* Poison is on by default: reclaimed bytes become obvious garbage. */
    assert_poisoned(base, total);
}

static void test_poison_can_be_disabled(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(32, 32);
    assert(s != NULL);
    memset(s, 'D', 32);
    s[32] = '\0';
    const char* base = s - 16;

    leasharena_exit();

    /* Explicit opt-out keeps the old contents (no memset cost). */
    const unsigned char* b = (const unsigned char*)base;
    int poisoned = 0;
    for (size_t i = 0; i < 16 + 32 + 1; i++) {
        if (b[i] == POISON_BYTE) { poisoned = 1; break; }
    }
    assert(!poisoned);
}

int main(void) {
    test_scope_exit_poisons();
    test_loop_reset_poisons();
    test_escaped_value_survives();
    test_poison_on_by_default();
    test_poison_can_be_disabled();
    printf("all tests passed\n");
    return 0;
}
