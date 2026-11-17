/*
 * win_shim.c — tiny Windows-native stand-ins for the POSIX utilities the
 * self-hosted compiler shells out to via exec()/system().
 *
 * Built with MinGW-w64 and shipped in the Windows bundle's bin/ directory
 * under the names realpath.exe, test.exe, rm.exe, mkdir.exe, stat.exe,
 * date.exe, uname.exe, cp.exe, find.exe and clang.exe. cmd.exe finds them
 * on PATH, so the compiler's POSIX command strings work unchanged.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <process.h>
#include <direct.h>
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static int ieq(const char* a, const char* b) {
    while (*a && *b) {
        if ((*a >= 'A' && *a <= 'Z' ? *a + 32 : *a) !=
            (*b >= 'A' && *b <= 'Z' ? *b + 32 : *b)) return 0;
        a++; b++;
    }
    return *a == *b;
}

static const char* base_of(const char* p) {
    const char* s = p;
    const char* q;
    for (q = p; *q; q++) if (*q == '\\' || *q == '/') s = q + 1;
    size_t n = strlen(s);
    if (n > 4 && ieq(s + n - 4, ".exe")) {
        static char buf[256];
        memcpy(buf, s, n - 4); buf[n - 4] = 0;
        return buf;
    }
    return s;
}

static int is_dash(const char* s) { return s[0] == '-' && s[1]; }

static int cmd_realpath(int argc, char** argv) {
    const char* p = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-m") == 0) continue;
        if (!is_dash(argv[i])) { p = argv[i]; break; }
    }
    if (!p) return 2;
    char buf[MAX_PATH];
    if (!_fullpath(buf, p, MAX_PATH)) return 1;
    puts(buf);
    return 0;
}

static int cmd_test(int argc, char** argv) {
    const char* p = NULL;
    int mode = 0; /* -L symlink, -d dir, -e/-f exists */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-L") == 0 || strcmp(argv[i], "-h") == 0) mode = 1;
        else if (strcmp(argv[i], "-d") == 0) mode = 2;
        else if (!is_dash(argv[i])) p = argv[i];
    }
    if (!p) return 2;
    DWORD a = GetFileAttributesA(p);
    if (a == INVALID_FILE_ATTRIBUTES) return 1;
    if (mode == 1) return (a & FILE_ATTRIBUTE_REPARSE_POINT) ? 0 : 1;
    if (mode == 2) return (a & FILE_ATTRIBUTE_DIRECTORY) ? 0 : 1;
    return 0;
}

static int cmd_rm(int argc, char** argv) {
    for (int i = 1; i < argc; i++) {
        if (is_dash(argv[i])) continue;
        (void)DeleteFileA(argv[i]);
    }
    return 0;
}

static int mkdirs(const char* path) {
    if (!path || !*path) return -1;
    size_t n = strlen(path);
    char* buf = (char*)malloc(n + 1);
    if (!buf) return -1;
    memcpy(buf, path, n + 1);
    for (size_t i = 1; i <= n; i++) {
        if (buf[i] == '/' || buf[i] == '\\' || i == n) {
            char c = buf[i]; buf[i] = 0;
            if (buf[0] && _mkdir(buf) != 0 && errno != EEXIST) {
                /* EEXIST is fine; ignore a drive root like "Z:" too. */
                if (!(c == ':')) { free(buf); return -1; }
            }
            buf[i] = c;
        }
    }
    free(buf);
    return 0;
}

static int cmd_mkdir(int argc, char** argv) {
    for (int i = 1; i < argc; i++) {
        if (is_dash(argv[i])) continue;
        if (mkdirs(argv[i]) != 0) return 1;
    }
    return 0;
}

static int cmd_stat(int argc, char** argv) {
    const char* p = NULL;
    for (int i = 1; i < argc; i++) {
        if (is_dash(argv[i]) || argv[i][0] == '\'') continue; /* -c 'fmt' */
        if (!p) p = argv[i];
    }
    if (!p) return 2;
    struct _stat st;
    if (_stat(p, &st) != 0) return 1;
    printf("%lld_%lld\n", (long long)st.st_size, (long long)st.st_mtime);
    return 0;
}

static int cmd_date(int argc, char** argv) {
    /* `date '+%s%N'` is used for unique temp names; `date '+%Y-%m-%d %H:%M:%S'`
     * for the run banner. cmd strips single quotes, so detect the format. */
    for (int i = 1; i < argc; i++) {
        if (strstr(argv[i], "%Y")) {
            time_t t = time(NULL);
            struct tm* lt = localtime(&t);
            char buf[256], fmt[256];
            const char* s = argv[i];
            if (*s == '+') s++;
            snprintf(fmt, sizeof(fmt), "%s", s);
            strftime(buf, sizeof(buf), fmt, lt);
            puts(buf);
            return 0;
        }
    }
    unsigned long long ns = (unsigned long long)time(NULL) * 1000000000ULL +
                            (unsigned long long)GetTickCount() * 1000000ULL;
    printf("%llu\n", ns);
    return 0;
}

static int cmd_uname(int argc, char** argv) {
    (void)argc; (void)argv;
    puts("x86_64");
    return 0;
}

static int copy_one(const char* src, const char* dst) {
    if (!CopyFileA(src, dst, FALSE)) return 1;
    return 0;
}

static int cmd_cp(int argc, char** argv) {
    const char* src = NULL, *dst = NULL;
    for (int i = 1; i < argc; i++) {
        if (is_dash(argv[i])) continue;
        if (!src) src = argv[i];
        else if (!dst) dst = argv[i];
    }
    if (!src || !dst) return 2;
    return copy_one(src, dst);
}

static int cmd_find(int argc, char** argv) {
    /* Only use: `find <dir> -mindepth 1 -maxdepth 1 -print -quit`. */
    const char* dir = NULL;
    for (int i = 1; i < argc; i++) {
        if (is_dash(argv[i])) continue;
        if (!dir) { dir = argv[i]; break; }
    }
    if (!dir) return 0;
    char pat[MAX_PATH];
    snprintf(pat, sizeof(pat), "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if (strcmp(fd.cFileName, ".") && strcmp(fd.cFileName, "..")) {
            printf("%s\\%s\n", dir, fd.cFileName);
            break;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return 0;
}

/* Response files carry link flags too (--hash-style=gnu, -no-pie, ...).
 * Copy the file with those lines removed and hand clang the rewritten one. */
static const char* rewrite_rsp(const char* path) {
    static char out[MAX_PATH];
    FILE* f = fopen(path, "r");
    if (!f) return path;
    snprintf(out, sizeof(out), "%s.leash", path);
    FILE* g = fopen(out, "w");
    if (!g) { fclose(f); return path; }
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        char t[1024];
        snprintf(t, sizeof(t), "%s", line);
        size_t n = strlen(t);
        while (n && (t[n-1] == '\n' || t[n-1] == '\r')) t[--n] = 0;
        if (strcmp(t, "-Wl,--hash-style=gnu") == 0 ||
            strcmp(t, "-Wl,--build-id=none") == 0 ||
            strcmp(t, "-no-pie") == 0 ||
            strcmp(t, "-pthread") == 0) continue;
        fprintf(g, "%s\n", t);
    }
    fclose(f); fclose(g);
    return out;
}

/* clang.exe wrapper: strip POSIX-only linker flags, use the Windows stubs,
 * add the system libraries the Windows runtime needs, then forward to the
 * real compiler copied to clang.orig.exe by install.bat. */
static int cmd_clang(int argc, char** argv) {
    char self[MAX_PATH];
    GetModuleFileNameA(NULL, self, MAX_PATH);
    char* slash = strrchr(self, '\\');
    if (slash) *slash = 0;
    char real[MAX_PATH];
    snprintf(real, sizeof(real), "%s\\clang.orig.exe", self);

    char** out = (char**)calloc((size_t)argc + 8, sizeof(char*));
    int n = 0;
    out[n++] = argv[0];
    for (int i = 1; i < argc; i++) {
        const char* a = argv[i];
        if (strcmp(a, "-pthread") == 0 || strcmp(a, "-lm") == 0 ||
            strcmp(a, "-ldl") == 0 || strcmp(a, "-no-pie") == 0 ||
            strcmp(a, "-Wl,--hash-style=gnu") == 0 ||
            strcmp(a, "-Wl,--build-id=none") == 0) continue;
        if (strcmp(a, "leash/cross_compile_stubs.c") == 0 ||
            strcmp(a, "leash\\cross_compile_stubs.c") == 0)
            a = "leash/windows_stubs.c";
        if (a[0] == '@') {
            const char* rp = rewrite_rsp(a + 1);
            char* at = (char*)malloc(strlen(rp) + 2);
            sprintf(at, "@%s", rp);
            out[n++] = at;
        } else {
            out[n++] = (char*)a;
        }
    }
    out[n++] = "-lws2_32";
    out[n++] = "-lsecur32";
    out[n++] = "-lcrypt32";
    out[n++] = "-lncrypt";
    out[n] = NULL;

    int rc = _spawnv(_P_WAIT, real, (const char* const*)out);
    free(out);
    if (rc == -1) {
        fprintf(stderr, "leash: cannot run %s (install a Windows clang and re-run install.bat)\n", real);
        return 127;
    }
    return rc;
}

int main(int argc, char** argv) {
    const char* n = base_of(argv[0]);
    if (ieq(n, "realpath")) return cmd_realpath(argc, argv);
    if (ieq(n, "test")) return cmd_test(argc, argv);
    if (ieq(n, "rm")) return cmd_rm(argc, argv);
    if (ieq(n, "mkdir")) return cmd_mkdir(argc, argv);
    if (ieq(n, "stat")) return cmd_stat(argc, argv);
    if (ieq(n, "date")) return cmd_date(argc, argv);
    if (ieq(n, "uname")) return cmd_uname(argc, argv);
    if (ieq(n, "cp")) return cmd_cp(argc, argv);
    if (ieq(n, "find")) return cmd_find(argc, argv);
    if (ieq(n, "clang")) return cmd_clang(argc, argv);
    fprintf(stderr, "leash win_shim: unknown tool '%s'\n", n);
    return 2;
}
