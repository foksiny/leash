/* Standalone test for leasharena_check_native_ptr(), the provenance guard
 * emitted at native (FFI) pointer returns.
 *
 * NULL, root-arena and foreign pointers are stable native handles and must
 * pass through. A pointer into the scoped arena dies with its frame, so the
 * guard must abort before generated code can store it as a native handle.
 * The abort case runs in a forked child so the harness keeps going. */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../leash/gc.h"

static int aborts(void* p) {
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 0; }
    if (pid == 0) {
        leasharena_check_native_ptr(p);
        _exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) return 0;
    return WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT;
}

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();
    leasharena_enter();

    void* frame = leash_gc_malloc(64);
    void* root = leasharena_root_alloc(64);
    void* foreign = malloc(64);
    if (!frame || !root || !foreign) { fprintf(stderr, "allocation failed\n"); return 1; }

    leasharena_check_native_ptr(NULL);
    leasharena_check_native_ptr(root);
    leasharena_check_native_ptr(foreign);

    if (!aborts(frame)) {
        fprintf(stderr, "scoped arena pointer was not rejected\n");
        return 1;
    }

    leasharena_exit();
    free(foreign);
    puts("provenance guard ok");
    return 0;
}
