/* Standalone harness for the arena backend's runtime invariants.
 *
 * These are the properties the generated code relies on but cannot
 * express itself:
 *
 *   - an arena string keeps its identity tag across an in-place growth
 *     (the tag is what tells the string helpers "this is an arena
 *     string"; losing it silently disables the in-place growth path);
 *   - a vec owned outside the current frame keeps a valid buffer after
 *     the frame that grew it is popped (the growth buffer must come
 *     from the root arena, not the dying scope);
 *   - a hash owned outside the current frame keeps valid nodes after
 *     the frame that inserted into it is popped;
 *   - the root arena's tip-growth never rewrites a string payload.
 *
 * Build & run:  python3 tests/test_arena_runtime.py
 * (or) cc tests/test_arena_runtime.c leash/gc.c -DLEASH_ARENA -O1 \
 *         -Wall -Wextra -o test_arena_runtime -pthread
 */

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __linux__
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

#include "../leash/gc.h"

/* Runtime helpers the generated module declares; they are not part of
 * the public gc.h surface, so the harness declares them itself. */
void* leash_sh_vec_new(int64_t elem_size, int64_t scan);
void leash_sh_vec_pushb(void* vec_ptr, const void* elem);
void leash_sh_vec_get(void* vec_ptr, int64_t idx, void* out);
void leash_sh_vec_reserve_rooted(void* vec_ptr, int64_t need);
int64_t leash_sh_vec_size(void* vec_ptr);
void* leash_sh_hash_new(void);
void leash_sh_hash_push(void* hash_ptr, const char* key, void* val);
void* leash_sh_hash_get(void* hash_ptr, const char* key);
void leash_sh_hash_make_rooted(void* hash_ptr);
void leasharena_escape_hash(void** slot, int64_t nframes);
void* leasharena_xfer_new(int64_t size);
void* leasharena_xfer_copy_string(void* block, void* src);
void leasharena_adopt_string(void** slot);
void leasharena_xfer_free(void* block);

/* The string helper used by the harness: append a known-length piece. */
char* leash_str_append_span(char* s, const char* piece, size_t pl);

/* A string that grows at the bump tip must keep its identity tag: the
 * tag lives in the header word in front of the payload and is what the
 * string helpers use to recognise an arena string. Losing it does not
 * corrupt the bytes, but it turns every later append into a fresh
 * allocation (the in-place path is keyed off the tag), so the growth
 * loop below would see the pointer change. */
static void test_string_growth_keeps_identity(void) {
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(4, 4);
    assert(s != NULL);
    memcpy(s, "abcd", 5);

    for (int i = 0; i < 64; i++) {
        char* t = leash_str_append_span(s, "x", 1);
        /* In-place growth keeps the pointer: the buffer is still the
         * arena's bump tip. A lost identity tag would make the helper
         * fall back to allocate + copy, moving the string. */
        assert(t == s);
        s = t;
    }
    assert(strlen(s) == 68);
    assert(s[67] == 'x');
    assert(s[68] == '\0');
    /* The length word still describes the string. */
    assert(leash_str_bsize(s) == 68);
    leasharena_exit();
}

/* Repeated COW marking (container swaps, repeated stores) must leave the
 * string intact and must not need expensive ownership work every time. */
static void test_repeated_share_keeps_string(void) {
    leash_gc_init();
    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(11, 11);
    assert(s != NULL);
    memcpy(s, "hello world", 12);
    leash_str_share(s);
    leash_str_share(s);
    leash_str_share(s);
    /* A string literal is a global constant, not an arena string. Sharing
     * it must not inspect bytes in front of the pointer. */
    leash_str_share("literal");
    assert(leash_str_bsize(s) == 11);
    assert(strcmp(s, "hello world") == 0);
    leasharena_exit();
}

/* A registered long-lived slot pointing into the frame being popped must
 * fail loudly instead of silently becoming a dangling pointer. */
static void* arena_verify_test_slot;

static void test_scan_region_verifier_catches_dangling(void) {
#ifdef __linux__
    leash_gc_init();
    leasharena_enter();
    void* frame_value = leash_gc_malloc(8);
    assert(frame_value != NULL);
    arena_verify_test_slot = frame_value;
    leash_gc_register_scan_region(&arena_verify_test_slot, sizeof(arena_verify_test_slot));
    pid_t pid = fork();
    assert(pid >= 0);
    if (pid == 0) {
        leasharena_verify_current_frame();
        _exit(0);
    }
    int status = 0;
    assert(waitpid(pid, &status, 0) == pid);
    assert(!WIFEXITED(status) || WEXITSTATUS(status) != 0);
    arena_verify_test_slot = NULL;
    leasharena_exit();
#endif
}

/* A vec owned outside the current frame grows into the root arena: the
 * buffer must survive the frame that grew it. */
static void test_rooted_vec_survives_frame(void) {
    leash_gc_init();

    /* Outer scope owns the vec header (like a caller's local). */
    leasharena_enter();
    void* v = leash_sh_vec_new(8, 0);
    assert(v != NULL);

    /* Inner scope is the "callee" that grows the vec. */
    leasharena_enter();
    leash_sh_vec_reserve_rooted(v, 4);
    int64_t x = 42;
    leash_sh_vec_pushb(v, &x);
    int64_t y = 0;
    leash_sh_vec_get(v, 0, &y);
    assert(y == 42);
    leasharena_exit();

    /* The inner frame is gone. The buffer came from the root arena, so
     * the element must still be there. */
    int64_t z = 0;
    leash_sh_vec_get(v, 0, &z);
    assert(z == 42);

    /* Growing again must keep working (the second buffer is rooted
     * too, and the tip-growth path must not corrupt the first one). */
    leasharena_enter();
    leash_sh_vec_reserve_rooted(v, 64);
    int64_t w = 7;
    leash_sh_vec_pushb(v, &w);
    leasharena_exit();
    leash_sh_vec_get(v, 0, &z);
    assert(z == 42);
    leash_sh_vec_get(v, 1, &z);
    assert(z == 7);

    leasharena_exit();
}

/* A hash owned outside the current frame keeps its nodes: the node the
 * "callee" allocates must not die with the callee's frame. */
static void test_rooted_hash_survives_frame(void) {
    leash_gc_init();

    leasharena_enter();
    void* h = leash_sh_hash_new();
    assert(h != NULL);

    leasharena_enter();
    leash_sh_hash_make_rooted(h);
    leash_sh_hash_push(h, "k", (void*)"value");
    leasharena_exit();

    void* got = leash_sh_hash_get(h, "k");
    assert(got != NULL);
    assert(strcmp((const char*)got, "value") == 0);

    /* A second insert into the rooted table must also survive. */
    leasharena_enter();
    leash_sh_hash_push(h, "k2", (void*)"value2");
    leasharena_exit();
    got = leash_sh_hash_get(h, "k2");
    assert(got != NULL);
    assert(strcmp((const char*)got, "value2") == 0);

    leasharena_exit();
}

/* The tip-growth helper must never treat a string payload as a plain
 * arena allocation: a string's trailing header word is its length, not
 * an allocation size. */
static void test_tip_growth_ignores_strings(void) {
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(7, 7);
    assert(s != NULL);
    memcpy(s, "abcdefg", 8);

    /* Reallocating the string (as the runtime would for a buffer) must
     * not rewrite the string's header in place. The string keeps its
     * bytes and its length. */
    char* r = (char*)leash_gc_realloc(s, 64);
    assert(r != NULL);
    /* The result must still be a valid arena string: the identity tag
     * in front of the payload and the length word must survive. A plain
     * buffer realloc would write the new size over the length word and
     * leave the string unrecognisable (and would write past the end of
     * the allocation, because the bump pointer only reserved the
     * string's own total). */
    uint64_t raw = *(const uint64_t*)(const void*)(r - 16);
    assert((raw >> 48) == 0x4C53ULL);            /* 'L','S' */
    uint64_t total = raw & 0x0000FFFFFFFFFFFFULL;
    uint64_t len = *(const uint64_t*)(const void*)(r - 8);
    assert(len == 7);
    assert(total >= (uint64_t)len + 17);
    assert(strcmp(r, "abcdefg") == 0);
    assert(r[7] == '\0');
    /* The string still works as a string: appending extends it. */
    char* t = leash_str_append_span(r, "x", 1);
    assert(leash_str_bsize(t) == 8);
    assert(strcmp(t, "abcdefgx") == 0);
    if (r != s) {
        /* The helper refused the in-place growth and copied instead:
         * the original must still hold its bytes. */
        assert(strcmp(s, "abcdefg") == 0);
        assert(leash_str_bsize(s) == 7);
    }
    leasharena_exit();
}

/* realloc of a SHARED arena string must stay on the string-aware path.
 *
 * A shared string carries bit 63 in its length word. The generic realloc
 * header check reads the word at ptr-8 as an allocation size, so without
 * the string check running FIRST it rejects the string as "not an
 * allocation start" and aborts. The string path must also leave the old
 * buffer alone when it is shared: other holders still point at it, so a
 * dedicated sole block must not be reclaimed (a use-after-free). */
static void test_realloc_shared_string(void) {
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(5, 8);
    assert(s != NULL);
    memcpy(s, "abcde", 6);
    leash_str_share(s);

    char* r = (char*)leash_gc_realloc(s, 64);
    assert(r != NULL);
    assert(r != s);                       /* string realloc makes a fresh buffer */
    assert(leash_str_bsize(r) == 5);
    assert(strcmp(r, "abcde") == 0);
    /* The shared original must remain valid: realloc must not reclaim it. */
    assert(strcmp(s, "abcde") == 0);
    assert(leash_str_bsize(s) == 5);
    leasharena_exit();
}

/* The sole-block case: a large shared string lives in a dedicated block.
 * Reclaiming that block on realloc would dangle the other holder. Under
 * ASan this test fails hard on the use-after-free; without sanitizers the
 * content assertions still verify the copy. */
static void test_realloc_shared_sole_string(void) {
    leash_gc_init();

    leasharena_enter();
    size_t n = 40 * 1024;
    char* s = (char*)leash_gc_alloc_string_cap(n, n);
    assert(s != NULL);
    memset(s, 'q', n);
    s[n] = '\0';
    leash_str_share(s);

    char* r = (char*)leash_gc_realloc(s, n + 1024);
    assert(r != NULL);
    assert(leash_str_bsize(r) == (int64_t)n);
    for (size_t i = 0; i < n; i++) assert(r[i] == 'q');
    /* The old (still shared) buffer must stay readable. */
    assert(s[0] == 'q');
    assert(s[n - 1] == 'q');
    assert(s[n] == '\0');
    leasharena_exit();
}

/* Self-append of a large string whose buffer lives in a dedicated sole
 * block. Growing that block uses realloc, which may MOVE it; the piece
 * being appended aliases the string (`s = s + s`), so reading it after the
 * move is a use-after-free. A barrier after the sole block makes realloc
 * move under ASan. */
static void test_self_append_sole_string(void) {
    leash_gc_init();

    leasharena_enter();
    size_t n = 40 * 1024;
    char* s = (char*)leash_gc_alloc_string_cap(n, n);
    assert(s != NULL);
    memset(s, 'a', n);
    s[n] = '\0';

    /* Fragment the heap right after the sole block so realloc cannot grow
     * it in place and must relocate (freeing the old payload). */
    void* barrier = malloc(128);
    assert(barrier != NULL);

    char* t = leash_str_append(s, s);
    assert(t != NULL);
    assert(leash_str_bsize(t) == (int64_t)(2 * n));
    for (size_t i = 0; i < 2 * n; i++) assert(t[i] == 'a');
    assert(t[2 * n] == '\0');

    free(barrier);
    leasharena_exit();
}

/* A near-SIZE_MAX allocation request must be rejected, not wrap the bump
 * allocator's pointer arithmetic into a tiny buffer (and a negative-size
 * memset). Fork so the expected abort does not kill the harness. */
static void test_huge_allocation_aborts(void) {
#ifdef __linux__
    leash_gc_init();
    pid_t pid = fork();
    assert(pid >= 0);
    if (pid == 0) {
        leash_gc_malloc((size_t)-8);
        _exit(0);
    }
    int status = 0;
    assert(waitpid(pid, &status, 0) == pid);
    assert(!WIFEXITED(status) || WEXITSTATUS(status) != 0);
#endif
}

/* Narrowing a tip allocation must zero the whole reclaimed tail, so a
 * following no-zero-fill allocation never sees stale old bytes. */
static void test_shrink_zeroes_reclaimed_tail(void) {
    leash_gc_init();

    leasharena_enter();
    size_t old_sz = 40;                       /* 8-aligned: no old padding */
    unsigned char* p = (unsigned char*)leash_gc_malloc(old_sz);
    assert(p != NULL);
    memset(p, 0xAB, old_sz);

    size_t new_sz = 17;                       /* new_adv = 24, pad = 7 */
    unsigned char* q = (unsigned char*)leash_gc_realloc(p, new_sz);
    assert(q == p);                           /* tip shrink stays in place */

    /* The bytes the bump pointer moved back over must all be zero. Read
     * them directly (the invariant the shrink path promises). */
    for (size_t i = new_sz + 7; i < old_sz; i++) {
        /* p + round8(new_sz) .. p + old_sz is the reclaimed region; the
         * first few bytes of it are the next allocation's header, all zero. */
        assert(q[i] == 0);
    }
    leasharena_exit();
}

/* A string with an embedded NUL survives the spawn transfer unchanged.
 * The transfer copy carries its byte length in a prefix; adopting it must
 * not recompute the length with strlen. */
static void test_transfer_string_keeps_embedded_nul(void) {
    leash_gc_init();
    leasharena_enter();

    const char src[4] = {'a', '\0', 'b', 'c'};
    char* s = (char*)leash_gc_alloc_string_cap(4, 4);
    assert(s != NULL);
    memcpy(s, src, 4);
    s[4] = '\0';

    void* block = leasharena_xfer_new(64);
    assert(block != NULL);
    char* cp = (char*)leasharena_xfer_copy_string(block, s);
    assert(cp != NULL);
    void* slot = cp;
    leasharena_adopt_string(&slot);
    assert(leash_str_bsize((const char*)slot) == 4);
    assert(memcmp(slot, src, 4) == 0);

    leasharena_xfer_free(block);
    leasharena_exit();
}

/* The typed root copy must preserve container identity: two aggregate
 * fields holding the same vec must still alias after the copy, and the
 * walk must terminate. `seen` was previously ignored for containers. */
static void test_typed_root_copy_keeps_vec_identity(void) {
    leash_gc_init();
    leasharena_enter();

    /* A frame-local vec. */
    void* v = leash_sh_vec_new(8, 0);
    assert(v != NULL);
    int64_t one = 1;
    leash_sh_vec_pushb(v, &one);

    /* Aggregate body: two pointer fields, both pointing at `v`. */
    void* body[2] = {v, v};

    /* Descriptor: node 0 = aggregate (16-byte body, fields 1..2);
     * nodes 1 and 2 = field entries at offsets 0 and 8, both typed as
     * the vec at node 3 (leaf element). */
    ar_te tree[4];
    tree[0].off = (void*)(uintptr_t)16; tree[0].kind = 4; tree[0].child = 1; tree[0].aux = 2;
    tree[1].off = (void*)(uintptr_t)0;  tree[1].kind = 5; tree[1].child = 3; tree[1].aux = 0;
    tree[2].off = (void*)(uintptr_t)8;  tree[2].kind = 5; tree[2].child = 3; tree[2].aux = 0;
    tree[3].off = (void*)(uintptr_t)0;  tree[3].kind = 2; tree[3].child = -1; tree[3].aux = 0;

    void* slot = body;
    leasharena_root_copy_blob_typed_d(&slot, tree, 0);
    assert(slot != NULL);
    void** fields = (void**)slot;
    assert(fields[0] != NULL);
    /* The two holders must point at the SAME root copy. */
    assert(fields[0] == fields[1]);
    /* And the copy still holds the element. */
    int64_t out = 0;
    leash_sh_vec_get(fields[0], 0, &out);
    assert(out == 1);

    leasharena_exit();
}

/* Escaping a hash with several short keys stages contiguous node images.
 * Each image must be 8-byte aligned (UBSan flags misaligned member reads
 * even where the hardware tolerates them). */
static void test_hash_escape_node_alignment(void) {
    leash_gc_init();
    leasharena_enter();
    void* h = leash_sh_hash_new();
    assert(h != NULL);
    leash_sh_hash_push(h, "a", (void*)"1");
    leash_sh_hash_push(h, "bb", (void*)"2");
    leash_sh_hash_push(h, "ccc", (void*)"3");
    leash_sh_hash_push(h, "dddd", (void*)"4");

    leasharena_enter();
    void* slot = h;
    leasharena_escape_hash(&slot, 1);
    /* The inner frame is gone; the escaped table must still work. */
    void* got = leash_sh_hash_get(slot, "a");
    assert(got && strcmp((const char*)got, "1") == 0);
    got = leash_sh_hash_get(slot, "bb");
    assert(got && strcmp((const char*)got, "2") == 0);
    got = leash_sh_hash_get(slot, "ccc");
    assert(got && strcmp((const char*)got, "3") == 0);
    got = leash_sh_hash_get(slot, "dddd");
    assert(got && strcmp((const char*)got, "4") == 0);
    leasharena_exit();
}

/* A buffer that predates a checkpoint must survive the checkpoint's
 * rewind when it is grown in place.
 *
 * The arena grows a vector's buffer inside the existing allocation when
 * the buffer sits at the bump tip. If the buffer was created before a
 * checkpoint (a loop frame), that growth reaches past the checkpoint's
 * recorded offset, and the checkpoint's rewind would truncate the live
 * buffer: the vector would keep pointing at a buffer whose tail had been
 * reclaimed and poisoned.
 *
 * The compiler's loop analysis keeps loops that grow an outer vector
 * uncheckpointed, so generated code does not hit this shape; the runtime
 * must not depend on that analysis being exhaustive. */
static void test_growth_across_checkpoint(void) {
    leash_gc_init();

    leasharena_enter();                /* function frame */
    void* v = leash_sh_vec_new(8, 0);  /* header + 8-element buffer */
    assert(v != NULL);
    int64_t seed = 1;
    leash_sh_vec_pushb(v, &seed);

    /* The checkpoint lands right after the buffer: the buffer predates
     * it, so growing the buffer crosses the checkpoint. */
    int64_t frame = leasharena_loop_enter();
    for (int i = 0; i < 64; i++) {
        int64_t x = i;
        leash_sh_vec_pushb(v, &x);
        leasharena_loop_reset(frame);
    }

    /* Every element must still be there, in order. */
    assert(leash_sh_vec_size(v) == 65);
    int64_t out = 0;
    for (int i = 0; i < 65; i++) {
        leash_sh_vec_get(v, i, &out);
        assert(out == (i == 0 ? 1 : i - 1));
    }

    leasharena_loop_exit(frame);
    leasharena_exit();
}

/* A pointer that does not belong to this thread's arena must never be
 * reallocated: the size header lives in front of the payload, so the
 * runtime would read memory it does not own. The runtime refuses the
 * call with a diagnostic instead of corrupting the heap. */
static void test_realloc_rejects_foreign_pointer(void) {
#ifdef __linux__
    leash_gc_init();
    void* foreign = malloc(32);
    assert(foreign != NULL);
    memset(foreign, 0xAB, 32);

    pid_t pid = fork();
    assert(pid >= 0);
    if (pid == 0) {
        /* Child: the realloc must not return; it aborts. */
        leash_gc_realloc(foreign, 64);
        _exit(0);
    }
    int status = 0;
    assert(waitpid(pid, &status, 0) == pid);
    free(foreign);
    /* The child must have died on the diagnostic (SIGABRT), not have
     * returned normally. */
    assert(!WIFEXITED(status) || WEXITSTATUS(status) != 0);
#else
    (void)0;
#endif
}

/* A string with spare capacity keeps its pointer even after a newer
 * arena block becomes the bump head. The append fast path must use the
 * capacity recorded in the string header, not the head block. */
static void test_append_char_outside_head(void) {
    leash_gc_init();

    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(3, 16);
    assert(s != NULL);
    memcpy(s, "abc", 4);

    /* Force a new block to become the head. The original string now
     * lives in an older block but still owns 16 bytes of capacity. */
    void* filler = leash_gc_malloc(64 * 1024);
    assert(filler != NULL);

    char* t = leash_str_append_char(s, 'd');
    assert(t == s);
    assert(strcmp(t, "abcd") == 0);
    assert(leash_str_bsize(t) == 4);
    leasharena_exit();
}

/* A string larger than an arena block lives in a dedicated sole block.
 * Growing it past its capacity must extend that block in place instead
 * of allocating a second large buffer. */
static void test_large_string_grows_in_sole_block(void) {
    leash_gc_init();

    leasharena_enter();
    size_t n = 40 * 1024;
    char* s = (char*)leash_gc_alloc_string_cap(n, n);
    assert(s != NULL);
    memset(s, 'a', n);
    s[n] = '\0';

    char* t = leash_str_append_span(s, "bc", 2);
    assert(t != NULL);
    assert(leash_str_bsize(t) == (int64_t)(n + 2));
    assert(t[n] == 'b' && t[n + 1] == 'c' && t[n + 2] == '\0');
    for (size_t i = 0; i < n; i++) assert(t[i] == 'a');
    leasharena_exit();
}

int main(void) {
    test_string_growth_keeps_identity();
    test_repeated_share_keeps_string();
    test_realloc_shared_string();
    test_realloc_shared_sole_string();
    test_self_append_sole_string();
    test_huge_allocation_aborts();
    test_shrink_zeroes_reclaimed_tail();
    test_transfer_string_keeps_embedded_nul();
    test_typed_root_copy_keeps_vec_identity();
    test_hash_escape_node_alignment();
    test_scan_region_verifier_catches_dangling();
    test_append_char_outside_head();
    test_large_string_grows_in_sole_block();
    test_rooted_vec_survives_frame();
    test_rooted_hash_survives_frame();
    test_tip_growth_ignores_strings();
    test_growth_across_checkpoint();
    test_realloc_rejects_foreign_pointer();
    printf("all tests passed\n");
    return 0;
}
