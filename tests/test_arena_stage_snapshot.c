/* Escape staging of values whose producing frame has already popped.
 *
 * The escape snapshot keeps an exact byte copy of a reclaimed block, so
 * staging must read EVERY header, chain, key and payload from that single
 * image. Reading the header from the snapshot and the node chain from the
 * freed block used to be a use-after-free (ASan reports it in
 * leasharena_escape_hash); a torn copy is wrong even when the bytes are
 * still mapped.
 *
 * Build & run:  python3 tests/test_arena_runtime.py
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../leash/gc.h"

extern void* leash_sh_hash_new(void);
extern void leash_sh_hash_push(void*, const char*, void*);
extern void* leash_sh_hash_get(void*, const char*);
extern int64_t leash_sh_hash_size(void*);
extern void* leash_sh_vec_new(int64_t elem_size, int64_t scan);
extern void leash_sh_vec_pushb(void*, const void*);
extern int64_t leash_sh_vec_size(void*);
extern void leash_sh_vec_get(void*, int64_t, void*);
extern void** leasharena_escape_slot(void);

static void* g_stale;
static int64_t g_hash_val = 42;
static int64_t g_vec_val = 7;

/* Force a fresh block inside the producer frame so the value's own block
 * is actually freed (and snapshotted) on frame exit, not merely rewound. */
static void fresh_block(void) {
    volatile unsigned char* p = (unsigned char*)leash_gc_malloc(100000);
    p[0] = 1;
}

static void produce_hash(void) {
    leasharena_enter();
    fresh_block();
    g_stale = leash_sh_hash_new();
    leash_sh_hash_push(g_stale, "snapshot-key", &g_hash_val);
    leasharena_exit();
}

static void produce_string(void) {
    leasharena_enter();
    fresh_block();
    char* s = (char*)leash_gc_alloc_string_cap(11, 11);
    memcpy(s, "snapshot!!", 11);
    s[11] = '\0';
    g_stale = s;
    leasharena_exit();
}

static void produce_vec(void) {
    leasharena_enter();
    fresh_block();
    g_stale = leash_sh_vec_new(8, 0);
    leash_sh_vec_pushb(g_stale, &g_vec_val);
    leasharena_exit();
}

static void check_hash(void* h) {
    if (leash_sh_hash_size(h) != 1 ||
        leash_sh_hash_get(h, "snapshot-key") != &g_hash_val) {
        fprintf(stderr, "FAIL: staged hash lost its exact contents\n");
        exit(2);
    }
}

static void test_generic_hash(void) {
    produce_hash();
    leasharena_enter();
    void** slot = leasharena_escape_slot();
    *slot = g_stale;
    leasharena_escape_hash(slot, 1);
    check_hash(*slot);
}

static void test_typed_hash(void) {
    produce_hash();
    /* {kind TE_HASH, child leaf} */
    ar_te tree[2];
    memset(tree, 0, sizeof(tree));
    tree[0].kind = 3; tree[0].child = 1; tree[0].aux = 0;
    tree[1].kind = 0; tree[1].child = -1; tree[1].aux = 0;
    leasharena_enter();
    void** slot = leasharena_escape_slot();
    *slot = g_stale;
    leasharena_escape_hash_typed_d(slot, 1, tree, 0);
    check_hash(*slot);
}

static void test_generic_string(void) {
    produce_string();
    leasharena_enter();
    void** slot = leasharena_escape_slot();
    *slot = g_stale;
    leasharena_escape_string(slot, 1);
    if (strcmp((const char*)*slot, "snapshot!!") != 0) {
        fprintf(stderr, "FAIL: staged string is torn\n");
        exit(2);
    }
}

static void test_generic_vec(void) {
    produce_vec();
    leasharena_enter();
    void** slot = leasharena_escape_slot();
    *slot = g_stale;
    leasharena_escape_vec(slot, 1);
    int64_t out = 0;
    if (leash_sh_vec_size(*slot) != 1) {
        fprintf(stderr, "FAIL: staged vec lost its size\n");
        exit(2);
    }
    leash_sh_vec_get(*slot, 0, &out);
    if (out != g_vec_val) {
        fprintf(stderr, "FAIL: staged vec is torn\n");
        exit(2);
    }
}

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();
    test_generic_hash();
    test_typed_hash();
    test_generic_string();
    test_generic_vec();
    puts("stage snapshots ok");
    return 0;
}
