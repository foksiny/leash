/* Standalone test for leasharena_checked(), the guard emitted before
 * container field access in arena IR.
 *
 * A live allocation must pass through unchanged, NULL must pass through
 * (optional containers), and a foreign or interior pointer must abort
 * before generated code can dereference it. The abort cases run in forked
 * children so the harness can assert the signal and keep going. */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../leash/gc.h"

static int aborts(void* p) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return 0;
    }
    if (pid == 0) {
        leasharena_checked(p);
        _exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) return 0;
    return WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT;
}

int main(void) {
    setenv("LEASH_ARENA_POISON", "0", 1);
    leash_gc_init();

    char* live = (char*)leash_gc_malloc(64);
    if (!live) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }
    if (leasharena_checked(live) != live) {
        fprintf(stderr, "live allocation rejected\n");
        return 1;
    }
    if (leasharena_checked(NULL) != NULL) {
        fprintf(stderr, "NULL was not passed through\n");
        return 1;
    }

    int marker = 0;
    if (!aborts(&marker)) {
        fprintf(stderr, "foreign stack pointer did not abort\n");
        return 1;
    }
    if (!aborts(live + 8)) {
        fprintf(stderr, "interior pointer did not abort\n");
        return 1;
    }

    puts("checked guard ok");
    return 0;
}
