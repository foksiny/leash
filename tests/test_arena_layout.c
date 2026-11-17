/* Adversarial stress harness for the arena memory LAYOUT.
 *
 * The generated code hands plain C pointers to the runtime, so a stale or
 * forged pointer can reach the header readers. Every reader must treat a
 * header as untrusted: validate its tag, prove the header lies inside the
 * owning block, and bound the size/length by that block before copying.
 *
 * The cases below drive each of those rules with a deliberately malformed
 * header and require either a clean abort (strict paths) or a safe fallback
 * (lookup paths that legitimately accept non-arena pointers). Running the
 * harness under ASan/UBSan catches any residual out-of-bounds access.
 *
 * Build & run:  python3 tests/test_arena_layout.py
 */
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

/* Runtime helpers not part of the public gc.h surface. */
void leasharena_escape_slice(void* agg, int64_t elem_size, int64_t nframes);

/* Run `fn` in a child; it must abort (never return, never OOB). */
static void expect_abort(const char* name, void (*fn)(void)) {
#ifdef __linux__
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); exit(2); }
    if (pid == 0) {
        fn();
        fprintf(stderr, "FAIL: %s returned instead of aborting\n", name);
        _exit(0);
    }
    int st = 0;
    if (waitpid(pid, &st, 0) != pid) { perror("waitpid"); exit(2); }
    if (WIFEXITED(st) && WEXITSTATUS(st) == 0) {
        fprintf(stderr, "FAIL: %s did not abort\n", name);
        exit(2);
    }
    printf("ok: %s aborted\n", name);
#else
    (void)name; (void)fn;
#endif
}

/* A self-consistent string header advertising 1 MB inside a 32 KB block.
 * The old validator trusted (total, len) after only matching the magic and
 * read s[len] out of bounds. The new one bounds total by the block. */
static void case_forged_string_lookup(void) {
    leash_gc_init();
    leasharena_enter();
    char* buf = (char*)leash_gc_malloc(64);
    char* s = buf + 16;
    uint64_t total = 1u << 20;          /* 1 MB claimed */
    uint64_t len = total - 20;          /* self-consistent */
    *(uint64_t*)(void*)(s - 16) = (((uint64_t)0x4C53) << 48) | total;
    *(uint64_t*)(void*)(s - 8) = len;
    memset(s, 'A', 8);
    s[8] = '\0';
    /* Must fall back to strlen (8) rather than read s[len]. Safe outcome,
     * so this case runs in-process; ASan is the OOB detector. */
    if (leash_str_bsize(s) != 8) {
        fprintf(stderr, "FAIL: forged string lookup did not fall back\n");
        exit(2);
    }
    leasharena_exit();
}

/* Same forged header, but on a strict path (realloc) that must diagnose. */
static void case_forged_string_realloc(void) {
    leash_gc_init();
    leasharena_enter();
    char* buf = (char*)leash_gc_malloc(64);
    char* s = buf + 16;
    uint64_t total = 1u << 20;
    *(uint64_t*)(void*)(s - 16) = (((uint64_t)0x4C53) << 48) | total;
    *(uint64_t*)(void*)(s - 8) = 4;
    memset(s, 'B', 5);
    s[4] = '\0';
    (void)leash_gc_realloc(s, 8);        /* must abort: corrupt header */
}

/* An interior pointer into an ordinary allocation: ptr-8 is payload, with
 * no generic header tag. Must be rejected structurally. */
static void case_interior_pointer_realloc(void) {
    leash_gc_init();
    leasharena_enter();
    char* buf = (char*)leash_gc_malloc(64);
    memset(buf, 0, 64);                  /* interior word = 0, no tag */
    (void)leash_gc_realloc(buf + 16, 8);
}

/* A realloc size near SIZE_MAX used to wrap (n+7)&~7 into a tiny/shrunk
 * capacity while the header advertised the huge size. */
static void case_oversized_realloc(void) {
    leash_gc_init();
    leasharena_enter();
    void* p = leash_gc_malloc(8);
    (void)leash_gc_realloc(p, SIZE_MAX - 4);
}

/* len * elem_size wraps to a tiny buffer that a memcpy then overruns. */
static void case_slice_overflow(void) {
    leash_gc_init();
    leasharena_enter();
    struct slice_view { int64_t len; void* data; };
    static struct slice_view sv;
    sv.len = (int64_t)1 << 40;
    sv.data = (void*)0x1;                /* never dereferenced: guard hits first */
    leasharena_escape_slice(&sv, (int64_t)1 << 40, 0);
}

/* A valid string still grows in place and keeps its identity. */
static void case_valid_string_growth(void) {
    leash_gc_init();
    leasharena_enter();
    char* s = (char*)leash_gc_alloc_string_cap(4, 4);
    if (!s) exit(2);
    memcpy(s, "abcd", 5);
    for (int i = 0; i < 64; i++) {
        char* t = leash_str_append(s, "x");
        if (t != s) {
            fprintf(stderr, "FAIL: in-place string growth moved the buffer\n");
            exit(2);
        }
        s = t;
    }
    if (leash_str_bsize(s) != 68 || strcmp(s + 67, "x")) {
        fprintf(stderr, "FAIL: grown string is corrupt\n");
        exit(2);
    }
    leasharena_exit();
}

int main(void) {
    case_forged_string_lookup();
    printf("ok: forged string lookup is bounded\n");

    expect_abort("forged string realloc", case_forged_string_realloc);
    expect_abort("interior pointer realloc", case_interior_pointer_realloc);
    expect_abort("oversized realloc", case_oversized_realloc);
    expect_abort("slice size overflow", case_slice_overflow);

    case_valid_string_growth();
    printf("ok: valid string growth unaffected\n");

    printf("all tests passed\n");
    return 0;
}
