/* Gen-validating container handles and copy-on-write for vec/hash.
 *
 * The emitted arena IR registers a container header, then dereferences
 * the generation handle on every access. A shared container must yield
 * a private copy with a fresh generation when a writer asks for COW.
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../leash/gc.h"

void* leash_sh_vec_new(int64_t elem_size, int64_t scan);
void leash_sh_vec_share(void* p);
void* leash_sh_hash_new(void);
void leash_sh_hash_push(void* p, const char* key, void* val);
void leash_sh_hash_share(void* p);

static int aborts(void (*body)(uint32_t), uint32_t h) {
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 0; }
    if (pid == 0) { body(h); _exit(0); }
    int status = 0;
    return waitpid(pid, &status, 0) == pid &&
           WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT;
}
static void require_call(uint32_t h) { leasharena_handle_require(h); }

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();

    /* Vec: stable handle, generation-checked dereference. */
    void* v = leash_sh_vec_new(8, 0);
    uint32_t vh = leasharena_container_handle(v);
    if (vh == 0xFFFFFFFFu || leasharena_handle_require(vh) != v) {
        fprintf(stderr, "vec handle deref failed\n"); return 1;
    }
    if (leasharena_container_handle(v) != vh) {
        fprintf(stderr, "vec handle registry is not stable\n"); return 1;
    }

    /* COW: a shared vec gets a private copy with a new handle, and the
     * old handle is retired (a stale dereference must abort). */
    leash_sh_vec_share(v);
    uint32_t vh2 = leasharena_container_cow(vh, 0);
    void* v2 = leasharena_handle_require(vh2);
    if (vh2 == vh || v2 == v || vh2 == 0xFFFFFFFFu) {
        fprintf(stderr, "vec COW did not produce a private copy\n"); return 1;
    }
    if (!aborts(require_call, vh)) {
        fprintf(stderr, "retired vec handle did not abort\n"); return 1;
    }

    /* Root copy: the new container is staged in the root arena and
     * reached through its own generation handle. */
    uint32_t rh = leasharena_root_copy_vec_h(v2);
    void* rv = leasharena_handle_require(rh);
    if (rh == 0xFFFFFFFFu || !rv || rv == v2) {
        fprintf(stderr, "vec root copy handle failed\n"); return 1;
    }

    /* Hash: same registry, COW and root-copy guarantees. */
    void* h = leash_sh_hash_new();
    leash_sh_hash_push(h, "k", (void*)42);
    uint32_t hh = leasharena_container_handle(h);
    if (hh == 0xFFFFFFFFu || leasharena_handle_require(hh) != h) {
        fprintf(stderr, "hash handle deref failed\n"); return 1;
    }
    leash_sh_hash_share(h);
    uint32_t hh2 = leasharena_container_cow(hh, 1);
    void* h2 = leasharena_handle_require(hh2);
    if (hh2 == hh || h2 == h || hh2 == 0xFFFFFFFFu) {
        fprintf(stderr, "hash COW did not produce a private copy\n"); return 1;
    }
    uint32_t rh2 = leasharena_root_copy_hash_h(h2);
    if (rh2 == 0xFFFFFFFFu || !leasharena_handle_require(rh2)) {
        fprintf(stderr, "hash root copy handle failed\n"); return 1;
    }

    puts("container handles ok");
    return 0;
}
