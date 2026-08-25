/*
 * lshhttp.c — native HTTP/HTTPS client shim for the Leash `http` stdlib package.
 *
 * One translation unit, two backends selected by platform:
 *
 *   POSIX (Linux/macOS/BSD): BSD sockets + OpenSSL for TLS.
 *   Windows (_WIN32):        WinHTTP (handles both HTTP and HTTPS).
 *
 * Returned strings are allocated with leash_gc_alloc_string() so they are
 * ordinary garbage-collected Leash strings — callers never free anything.
 *
 * Build (see README.md in this directory):
 *   Linux/macOS:  sh build.sh            (produces ../<platform>/liblshhttp.a)
 *   Windows:      build.bat              (produces ..\win\liblshhttp.a)
 */

#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Strings handed back to Leash come from the Leash runtime allocator. */
extern void *leash_gc_alloc_string(long long len);

#ifdef _WIN32
# ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
# endif
# include <windows.h>
# include <winhttp.h>
#else
# include <sys/types.h>
# include <sys/socket.h>
# include <sys/time.h>
# include <netdb.h>
# include <netinet/in.h>
# include <netinet/tcp.h>
# include <poll.h>
# include <fcntl.h>
# include <unistd.h>
# include <errno.h>
# include <limits.h>
# include <openssl/ssl.h>
# include <openssl/err.h>
# include <openssl/x509v3.h>
#endif

#if defined(_MSC_VER)
# define LSH_THREADLOCAL __declspec(thread)
#elif defined(__GNUC__)
# define LSH_THREADLOCAL __thread
#else
# define LSH_THREADLOCAL
#endif

#define LSH_HTTP_VERSION_STR "leash-http/1.0"
#define LSH_MAX_HEADER_BYTES (1024L * 1024L)          /* 1 MiB header block   */
#define LSH_MAX_BODY_BYTES  (512LL * 1024L * 1024L)   /* 512 MiB body cap     */
#define LSH_DETAIL_LEN 512

/* Error codes returned instead of an HTTP status. */
enum {
    LSH_E_URL       = -1,  /* malformed or unsupported URL          */
    LSH_E_DNS       = -2,  /* name resolution failed                */
    LSH_E_CONNECT   = -3,  /* connection refused/reset/timed out    */
    LSH_E_TLS       = -4,  /* TLS handshake failed                  */
    LSH_E_SEND      = -5,  /* failed sending the request            */
    LSH_E_RECV      = -6,  /* failed receiving the response         */
    LSH_E_TIMEOUT   = -7,  /* operation exceeded timeout_ms         */
    LSH_E_PROTO     = -8,  /* malformed HTTP response               */
    LSH_E_REDIRECT  = -9,  /* too many redirects                    */
    LSH_E_MEMORY    = -10, /* out of memory / size cap hit          */
    LSH_E_FILE      = -11  /* cannot open destination file          */
};

static LSH_THREADLOCAL char lsh_detail[LSH_DETAIL_LEN] = "";

static void lsh_set_detail(const char *fmt, const char *a, const char *b) {
    snprintf(lsh_detail, sizeof(lsh_detail), fmt, a ? a : "", b ? b : "");
}

static void lsh_clear_detail(void) {
    if (lsh_detail[0] != '\0') lsh_detail[0] = '\0';
}

#ifdef _WIN32
#define LSH_MAYBE_UNUSED __attribute__((unused))
#else
#define LSH_MAYBE_UNUSED
#endif

static LSH_MAYBE_UNUSED long long lsh_now_ms(void) {
#ifdef _WIN32
    return (long long)GetTickCount64();
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
#endif
}

#ifndef _WIN32
static long long lsh_deadline_left(long long deadline) {
    long long left = deadline - lsh_now_ms();
    return left > 0 ? left : 0;
}
#endif /* !_WIN32 */

/* Growable byte buffer used for the header block and in-memory bodies. */
typedef struct {
    char *p;
    size_t len;
    size_t cap;
} lsh_buf;

static void buf_init(lsh_buf *b) {
    b->p = NULL;
    b->len = 0;
    b->cap = 0;
}

static void buf_free(lsh_buf *b) {
    free(b->p);
    buf_init(b);
}

static int buf_reserve(lsh_buf *b, size_t extra) {
    if (b->len + extra <= b->cap) return 1;
    size_t ncap = b->cap ? b->cap : 4096;
    while (ncap < b->len + extra) ncap *= 2;
    char *np = (char *)realloc(b->p, ncap);
    if (!np) return 0;
    b->p = np;
    b->cap = ncap;
    return 1;
}

static int buf_append(lsh_buf *b, const char *data, size_t n) {
    if (!buf_reserve(b, n)) return 0;
    memcpy(b->p + b->len, data, n);
    b->len += n;
    return 1;
}

static char *lsh_gc_string(const char *data, size_t len) {
    if (len > 0x7fffffffLL) return NULL;
    char *out = (char *)leash_gc_alloc_string((long long)len);
    if (!out) return NULL;
    if (len) memcpy(out, data, len);
    return out; /* alloc_string zeroes, so out[len] == '\0' */
}

/* ------------------------------------------------------------------ */
/* URL parsing                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
    char scheme[16];
    char host[280];
    char port[16];
    int  port_num;
    char path[2400];
} lsh_url;

#ifndef _WIN32
static int lsh_is_ip_literal(const char *h) {
    for (; *h; h++) {
        if ((*h >= '0' && *h <= '9') || *h == '.' || *h == ':') continue;
        return 0;
    }
    return 1;
}
#endif /* !_WIN32 */

static int lsh_parse_url(const char *url, lsh_url *u) {
    memset(u, 0, sizeof(*u));
    if (!url) return 0;

    if (strncmp(url, "https://", 8) == 0) {
        strcpy(u->scheme, "https");
        url += 8;
        u->port_num = 443;
        strcpy(u->port, "443");
    } else if (strncmp(url, "http://", 7) == 0) {
        strcpy(u->scheme, "http");
        url += 7;
        u->port_num = 80;
        strcpy(u->port, "80");
    } else {
        return 0;
    }

    /* Strip any userinfo component (user:pass@host). */
    const char *slash = strchr(url, '/');
    const char *hostend = slash ? slash : url + strlen(url);
    const char *at = NULL;
    const char *q;
    for (q = url; q < hostend; q++) {
        if (*q == '@') at = q;
    }
    if (at) url = at + 1;
    hostend = slash ? slash : url + strlen(url);

    /* Split host[:port], honouring [ipv6]:port literals. */
    char hostpart[320];
    size_t hl = (size_t)(hostend - url);
    if (hl == 0 || hl >= sizeof(hostpart)) return 0;
    memcpy(hostpart, url, hl);
    hostpart[hl] = '\0';

    if (hostpart[0] == '[') {
        char *close = strchr(hostpart, ']');
        if (!close) return 0;
        size_t inner = (size_t)(close - hostpart - 1);
        if (inner >= sizeof(u->host)) return 0;
        memcpy(u->host, hostpart + 1, inner);
        u->host[inner] = '\0';
        if (close[1] == ':') {
            const char *p = close + 2;
            if (*p == '\0' || (size_t)strlen(p) >= sizeof(u->port)) return 0;
            for (const char *c = p; *c; c++) {
                if (*c < '0' || *c > '9') return 0;
            }
            strcpy(u->port, p);
            u->port_num = atoi(p);
        }
    } else {
        char *colon = strrchr(hostpart, ':');
        if (colon) {
            size_t hn = (size_t)(colon - hostpart);
            if (hn == 0 || hn >= sizeof(u->host)) return 0;
            memcpy(u->host, hostpart, hn);
            u->host[hn] = '\0';
            const char *p = colon + 1;
            if (*p == '\0' || (size_t)strlen(p) >= sizeof(u->port)) return 0;
            for (const char *c = p; *c; c++) {
                if (*c < '0' || *c > '9') return 0;
            }
            strcpy(u->port, p);
            u->port_num = atoi(p);
        } else {
            if (strlen(hostpart) >= sizeof(u->host)) return 0;
            strcpy(u->host, hostpart);
        }
    }

    if (u->port_num <= 0 || u->port_num > 65535) return 0;
    if (u->host[0] == '\0') return 0;

    if (slash) {
        if (strlen(slash) >= sizeof(u->path)) return 0;
        strcpy(u->path, slash);
    } else {
        strcpy(u->path, "/");
    }
    return 1;
}

/* Resolve `loc` against `base` into `out`. Supports absolute URLs,
 * scheme-relative "//host/path" and root/relative paths. */
static int lsh_resolve_location(const lsh_url *base, const char *loc, lsh_url *out) {
    if (strncmp(loc, "http://", 7) == 0 || strncmp(loc, "https://", 8) == 0) {
        return lsh_parse_url(loc, out);
    }
    if (loc[0] == '/' && loc[1] == '/') {
        char abs[2800];
        snprintf(abs, sizeof(abs), "%s:%s", base->scheme, loc);
        return lsh_parse_url(abs, out);
    }

    *out = *base;
    if (loc[0] == '/') {
        if (strlen(loc) >= sizeof(out->path)) return 0;
        strcpy(out->path, loc);
        return 1;
    }

    /* Relative path: replace everything after the last '/'. */
    char dir[2400];
    const char *last = strrchr(base->path, '/');
    if (!last) {
        strcpy(dir, "/");
    } else {
        size_t dl = (size_t)(last - base->path) + 1;
        if (dl >= sizeof(dir)) return 0;
        memcpy(dir, base->path, dl);
        dir[dl] = '\0';
    }
    size_t need = strlen(dir) + strlen(loc) + 1;
    if (need >= sizeof(out->path)) return 0;
    strcpy(out->path, dir);
    strcat(out->path, loc);

    /* Collapse "./" and "../" segments (query string preserved verbatim). */
    char pathcopy[2400];
    char query[1200] = "";
    strcpy(pathcopy, out->path);
    char *qmark = strchr(pathcopy, '?');
    if (qmark) {
        strncpy(query, qmark, sizeof(query) - 1);
        *qmark = '\0';
    }
    {
        char *sptr = NULL;
        char *segm = strtok_r(pathcopy, "/", &sptr);
        char *kept[128];
        int kn = 0;
        while (segm) {
            if (strcmp(segm, ".") == 0) {
                /* skip */
            } else if (strcmp(segm, "..") == 0) {
                if (kn > 0) kn--;
            } else if (kn < 128) {
                kept[kn++] = segm;
            }
            segm = strtok_r(NULL, "/", &sptr);
        }
        char rebuilt[2400];
        size_t off = (size_t)snprintf(rebuilt, sizeof(rebuilt), "/");
        for (int i = 0; i < kn && off < sizeof(rebuilt); i++) {
            off += (size_t)snprintf(rebuilt + off, sizeof(rebuilt) - off, "%s%s",
                                    kept[i], (i + 1 < kn) ? "/" : "");
        }
        if (query[0]) strncat(rebuilt, query, sizeof(rebuilt) - strlen(rebuilt) - 1);
        strcpy(out->path, rebuilt);
    }
    return 1;
}

/* ------------------------------------------------------------------ */
/* Shared response plumbing                                           */
/* ------------------------------------------------------------------ */

typedef struct {
    FILE *file;      /* when downloading */
    lsh_buf mem;     /* otherwise        */
} body_sink;

static int sink_write(body_sink *s, const char *data, size_t n, long long total_so_far) {
    if ((long long)(total_so_far) > LSH_MAX_BODY_BYTES) return 0;
    if (s->file) {
        return fwrite(data, 1, n, s->file) == n;
    }
    return buf_append(&s->mem, data, n);
}

/* Extract status code from a status line like "HTTP/1.1 200 OK". */
#ifndef _WIN32
static int lsh_status_from_line(const char *line) {
    if (strncmp(line, "HTTP/", 5) != 0) return 0;
    const char *p = line + 5;
    while (*p && *p != ' ') p++;
    if (*p != ' ') return 0;
    p++;
    if (*p < '0' || *p > '9') return 0;
    return atoi(p);
}
#endif /* !_WIN32 */

/* Copy header block (without status line) normalizing line endings to '\n'. */
static char *lsh_normalize_headers(const char *raw, size_t raw_len) {
    /* raw starts right after the status line's CRLF and ends before the blank
     * line. Replace CRLF (and lone LF) with LF. */
    lsh_buf nb;
    buf_init(&nb);
    for (size_t i = 0; i < raw_len; i++) {
        char c = raw[i];
        if (c == '\r') continue;
        if (!buf_append(&nb, &c, 1)) {
            buf_free(&nb);
            return NULL;
        }
    }
    char *out = lsh_gc_string(nb.p ? nb.p : "", nb.len);
    buf_free(&nb);
    return out;
}

#ifndef _WIN32
static int lsh_header_find(const char *headers, const char *name, char *dst, size_t dstcap) {
    size_t nlen = strlen(name);
    const char *p = headers;
    while (p && *p) {
        const char *eol = strchr(p, '\n');
        size_t linelen = eol ? (size_t)(eol - p) : strlen(p);
        if (linelen > nlen && p[nlen] == ':' &&
            strncasecmp(p, name, nlen) == 0) {
            const char *v = p + nlen + 1;
            while (linelen > (size_t)(v - p) && (*v == ' ' || *v == '\t')) {
                v++;
            }
            size_t vl = linelen - (size_t)(v - p);
            while (vl > 0 && (v[vl - 1] == ' ' || v[vl - 1] == '\t')) vl--;
            if (vl >= dstcap) vl = dstcap - 1;
            memcpy(dst, v, vl);
            dst[vl] = '\0';
            return 1;
        }
        p = eol ? eol + 1 : NULL;
    }
    return 0;
}


/* ------------------------------------------------------------------
 * Request assembly (POSIX backend builds its own request wire format)
 * ------------------------------------------------------------------ */

static int lsh_build_request(lsh_buf *req, const char *method, const lsh_url *u,
                             const char *headers, const char *body) {
    buf_init(req);
    char line[2800];

    snprintf(line, sizeof(line), "%s %s HTTP/1.1\r\n", method, u->path);
    if (!buf_append(req, line, strlen(line))) return 0;

    int default_port = (strcmp(u->scheme, "https") == 0 && u->port_num == 443) ||
                       (strcmp(u->scheme, "http") == 0 && u->port_num == 80);
    if (default_port) {
        snprintf(line, sizeof(line), "Host: %s\r\n", u->host);
    } else {
        snprintf(line, sizeof(line), "Host: %s:%s\r\n", u->host, u->port);
    }
    if (!buf_append(req, line, strlen(line))) return 0;

    snprintf(line, sizeof(line),
             "User-Agent: %s\r\n"
             "Accept: */*\r\n"
             "Connection: close\r\n",
             LSH_HTTP_VERSION_STR);
    if (!buf_append(req, line, strlen(line))) return 0;

    if (headers && headers[0]) {
        /* Accept LF separated custom headers; emit each with CRLF. */
        const char *p = headers;
        while (p && *p) {
            const char *eol = strchr(p, '\n');
            size_t ll = eol ? (size_t)(eol - p) : strlen(p);
            while (ll > 0 && (p[ll - 1] == '\r')) ll--;
            if (ll > 0 && !(ll == 0)) {
                if (!buf_append(req, p, ll)) return 0;
                if (!buf_append(req, "\r\n", 2)) return 0;
            }
            p = eol ? eol + 1 : NULL;
        }
    }

    if (body && body[0]) {
        snprintf(line, sizeof(line), "Content-Length: %lld\r\n", (long long)strlen(body));
        if (!buf_append(req, line, strlen(line))) return 0;
    }

    if (!buf_append(req, "\r\n", 2)) return 0;
    if (body && body[0] && !buf_append(req, body, strlen(body))) return 0;
    return 1;
}

#endif /* !_WIN32 */

/* ------------------------------------------------------------------ */
/* POSIX backend                                                      */
/* ------------------------------------------------------------------ */

#ifndef _WIN32

struct lsh_conn {
    int fd;
    SSL *ssl;
    SSL_CTX *ctx;
};

static void conn_close(struct lsh_conn *c) {
    if (!c) return;
    if (c->ssl) {
        SSL_shutdown(c->ssl);
        SSL_free(c->ssl);
        c->ssl = NULL;
    }
    if (c->ctx) {
        SSL_CTX_free(c->ctx);
        c->ctx = NULL;
    }
    if (c->fd >= 0) {
        close(c->fd);
        c->fd = -1;
    }
}

static int wait_fd(int fd, short events, long long deadline) {
    long long left = lsh_deadline_left(deadline);
    if (left <= 0) return 0;
    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = events;
    pfd.revents = 0;
    int r = poll(&pfd, 1, (int)left);
    return r > 0 && (pfd.revents & (events | POLLERR | POLLHUP));
}

static int tcp_connect(const lsh_url *u, long long deadline,
                       struct lsh_conn *c) {
    struct addrinfo hints, *res = NULL, *rp;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    int gai = getaddrinfo(u->host, u->port, &hints, &res);
    if (gai != 0) {
        lsh_set_detail("name resolution failed for '%s' (%s)", u->host, gai_strerror(gai));
        return LSH_E_DNS;
    }

    c->fd = -1;
    int err = LSH_E_CONNECT;
    for (rp = res; rp; rp = rp->ai_next) {
        int fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;

        int flags = fcntl(fd, F_GETFL, 0);
        if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);

        int rc = connect(fd, rp->ai_addr, rp->ai_addrlen);
        if (rc != 0) {
            if (errno == EINPROGRESS) {
                if (!wait_fd(fd, POLLOUT, deadline)) {
                    close(fd);
                    err = lsh_deadline_left(deadline) <= 0 ? LSH_E_TIMEOUT : LSH_E_CONNECT;
                    continue;
                }
                int soerr = 0;
                socklen_t slen = sizeof(soerr);
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &slen);
                if (soerr != 0) {
                    close(fd);
                    lsh_set_detail("connect to '%s' failed (%s)", u->host, strerror(soerr));
                    continue;
                }
            } else {
                close(fd);
                lsh_set_detail("connect to '%s' failed (%s)", u->host, strerror(errno));
                continue;
            }
        }

        flags = fcntl(fd, F_GETFL, 0);
        if (flags >= 0) fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);

        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        c->fd = fd;
        err = 0;
        break;
    }
    freeaddrinfo(res);
    if (err != 0 && c->fd < 0) {
        if (err == LSH_E_CONNECT && lsh_detail[0] == '\0') {
            lsh_set_detail("could not reach '%s'", u->host, NULL);
        }
    }
    return err;
}

/* Returns 1 on progress, 0 on want-read/write handled, -1 on fatal. */
static int tls_wait(SSL *ssl, int fd, int ret, long long deadline) {
    int err = SSL_get_error(ssl, ret);
    if (err == SSL_ERROR_WANT_READ) return wait_fd(fd, POLLIN, deadline) ? 0 : -1;
    if (err == SSL_ERROR_WANT_WRITE) return wait_fd(fd, POLLOUT, deadline) ? 0 : -1;
    return -1;
}

static int tls_connect(const lsh_url *u, int verify, long long deadline,
                       struct lsh_conn *c) {
    c->ctx = SSL_CTX_new(TLS_client_method());
    if (!c->ctx) {
        lsh_set_detail("TLS context allocation failed (%s)", ERR_reason_error_string(ERR_get_error()), NULL);
        return LSH_E_TLS;
    }
    SSL_CTX_set_min_proto_version(c->ctx, TLS1_VERSION);
    if (verify) {
        SSL_CTX_set_verify(c->ctx, SSL_VERIFY_PEER, NULL);
        if (!SSL_CTX_set_default_verify_paths(c->ctx)) {
            /* System store unavailable; certificate validation will likely
             * fail later, which surfaces as a TLS error. */
            lsh_set_detail("system CA store unavailable (%s)", ERR_reason_error_string(ERR_get_error()), NULL);
        }
    } else {
        SSL_CTX_set_verify(c->ctx, SSL_VERIFY_NONE, NULL);
    }

    c->ssl = SSL_new(c->ctx);
    if (!c->ssl) {
        lsh_set_detail("TLS session allocation failed", NULL, NULL);
        return LSH_E_TLS;
    }
    SSL_set_fd(c->ssl, c->fd);

    if (!lsh_is_ip_literal(u->host)) {
        SSL_ctrl(c->ssl, SSL_CTRL_SET_TLSEXT_HOSTNAME, TLSEXT_NAMETYPE_host_name,
                 (void *)u->host);
    }
    if (verify) {
        if (SSL_set1_host(c->ssl, u->host) != 1) {
            lsh_set_detail("invalid TLS hostname '%s'", u->host, NULL);
            return LSH_E_TLS;
        }
    }

    for (;;) {
        int r = SSL_connect(c->ssl);
        if (r == 1) return 0;
        int w = tls_wait(c->ssl, c->fd, r, deadline);
        if (w < 0) {
            unsigned long e = ERR_get_error();
            const char *reason = e ? ERR_reason_error_string(e) : NULL;
            if (!reason) reason = "unknown TLS failure";
            if (lsh_deadline_left(deadline) <= 0 &&
                SSL_get_error(c->ssl, r) == SSL_ERROR_SYSCALL) {
                lsh_set_detail("TLS handshake with '%s' timed out (%s)", u->host,
                               verify ? "verification enabled" : "verification disabled");
                return LSH_E_TIMEOUT;
            }
            lsh_set_detail("TLS handshake with '%s' failed (%s)", u->host, reason);
            return LSH_E_TLS;
        }
    }
}

static int conn_write_all(struct lsh_conn *c, const char *data, size_t len,
                          long long deadline) {
    size_t off = 0;
    while (off < len) {
        ssize_t n;
        if (c->ssl) {
            int r = SSL_write(c->ssl, data + off, (int)(len - off));
            if (r > 0) {
                off += (size_t)r;
                continue;
            }
            int w = tls_wait(c->ssl, c->fd, r, deadline);
            if (w < 0) return LSH_E_SEND;
        } else {
            n = send(c->fd, data + off, len - off, MSG_NOSIGNAL);
            if (n > 0) {
                off += (size_t)n;
                continue;
            }
            if (n < 0 && errno == EINTR) continue;
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (!wait_fd(c->fd, POLLOUT, deadline)) {
                    return lsh_deadline_left(deadline) <= 0 ? LSH_E_TIMEOUT : LSH_E_SEND;
                }
                continue;
            }
            lsh_set_detail("send failed (%s)", strerror(errno), NULL);
            return LSH_E_SEND;
        }
    }
    (void)0;
    return 0;
}

/* Read some bytes; returns count, 0 on clean EOF, negative error. */
static int conn_read_some(struct lsh_conn *c, char *dst, size_t cap,
                          long long deadline) {
    for (;;) {
        if (c->ssl) {
            int r = SSL_read(c->ssl, dst, (int)cap);
            if (r > 0) return r;
            if (SSL_get_error(c->ssl, r) == SSL_ERROR_ZERO_RETURN) return 0;
            int w = tls_wait(c->ssl, c->fd, r, deadline);
            if (w < 0) {
                if (lsh_deadline_left(deadline) <= 0) return LSH_E_TIMEOUT;
                return LSH_E_RECV;
            }
            continue;
        }
        ssize_t n = recv(c->fd, dst, cap, 0);
        if (n > 0) return (int)n;
        if (n == 0) return 0;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            if (!wait_fd(c->fd, POLLIN, deadline)) {
                return lsh_deadline_left(deadline) <= 0 ? LSH_E_TIMEOUT : LSH_E_RECV;
            }
            continue;
        }
        lsh_set_detail("recv failed (%s)", strerror(errno), NULL);
        return LSH_E_RECV;
    }
}

static int lsh_socket_timeout(int fd, long long ms) {
    struct timeval tv;
    tv.tv_sec = (long)(ms / 1000);
    tv.tv_usec = (long)((ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    return 0;
}

/* Read the full response head + body from `conn` into sink.
 * On success returns HTTP status and fills hdr_raw (malloc'd, caller frees)
 * with the raw header block (status line excluded). */
static int read_response_head_and_body(struct lsh_conn *c, long long deadline,
                                       body_sink *sink, char **hdr_raw,
                                       size_t *hdr_len_out, int *is_chunked,
                                       long long *content_length) {
    lsh_buf acc;
    buf_init(&acc);
    *hdr_raw = NULL;
    *is_chunked = 0;
    *content_length = -1;

    static const char term[] = "\r\n\r\n";
    size_t head_end = 0;
    int head_found = 0;

    char chunk[16384];
    for (;;) {
        /* Search for terminator in newly accumulated data. */
        if (acc.len >= 4) {
            size_t start = acc.len > 20000 ? acc.len - 20000 : 0;
            for (size_t i = start; i + 4 <= acc.len; i++) {
                if (memcmp(acc.p + i, term, 4) == 0) {
                    head_end = i;
                    head_found = 1;
                    break;
                }
            }
        }
        if (head_found) break;
        if (acc.len > LSH_MAX_HEADER_BYTES) {
            lsh_set_detail("response header block exceeds %s bytes", "1048576", NULL);
            buf_free(&acc);
            return LSH_E_PROTO;
        }
        int n = conn_read_some(c, chunk, sizeof(chunk), deadline);
        if (n < 0) {
            buf_free(&acc);
            return n;
        }
        if (n == 0) {
            lsh_set_detail("connection closed before response head completed", NULL, NULL);
            buf_free(&acc);
            return LSH_E_PROTO;
        }
        if (!buf_append(&acc, chunk, (size_t)n)) {
            buf_free(&acc);
            return LSH_E_MEMORY;
        }
    }

    /* Locate end of status line. */
    const char *base = acc.p;
    const char *sl_end = memchr(acc.p, '\n', head_end);
    if (!sl_end) {
        buf_free(&acc);
        return LSH_E_PROTO;
    }
    int status = lsh_status_from_line(base);
    if (status < 100) {
        lsh_set_detail("malformed status line: %.32s", base, NULL);
        buf_free(&acc);
        return LSH_E_PROTO;
    }

    size_t hdr_len = head_end - ((size_t)(sl_end - base) + 1);
    const char *hdr_start = sl_end + 1;

    /* Parse framing headers (case-insensitive scan of individual lines). */
    char framedup[LSH_MAX_HEADER_BYTES > 65536 ? 65536 : LSH_MAX_HEADER_BYTES];
    size_t dup_len = hdr_len < sizeof(framedup) - 1 ? hdr_len : sizeof(framedup) - 1;
    memcpy(framedup, hdr_start, dup_len);
    framedup[dup_len] = '\0';

    char *saveptr = NULL;
    char *linep = strtok_r(framedup, "\r\n", &saveptr);
    long long clen = -1;
    int chunked = 0;
    while (linep) {
        char *colon = strchr(linep, ':');
        if (colon) {
            *colon = '\0';
            char *val = colon + 1;
            while (*val == ' ' || *val == '\t') val++;
            if (strncasecmp(linep, "Content-Length", 14) == 0 && linep[14] == '\0') {
                clen = strtoll(val, NULL, 10);
            } else if (strncasecmp(linep, "Transfer-Encoding", 17) == 0 &&
                       linep[17] == '\0') {
                if (strstr(val, "chunked")) chunked = 1;
            }
        }
        linep = strtok_r(NULL, "\r\n", &saveptr);
    }

    *hdr_raw = (char *)malloc(hdr_len + 1);
    if (!*hdr_raw) {
        buf_free(&acc);
        return LSH_E_MEMORY;
    }
    memcpy(*hdr_raw, hdr_start, hdr_len);
    (*hdr_raw)[hdr_len] = '\0';
    *hdr_len_out = hdr_len;
    *is_chunked = chunked;
    *content_length = clen;

    /* Any bytes after the head belong to the body. */
    size_t body_off = head_end + 4;
    size_t avail = acc.len - body_off;
    if (avail > 0) {
        if (!sink_write(sink, acc.p + body_off, avail, (long long)avail)) {
            free(*hdr_raw);
            *hdr_raw = NULL;
            buf_free(&acc);
            return LSH_E_MEMORY;
        }
    }
    buf_free(&acc);
    return status;
}

static int pump_body_fixed(struct lsh_conn *c, long long deadline, body_sink *sink,
                           long long already, long long total) {
    char chunk[16384];
    long long got = already;
    while (got < total) {
        long long want = total - got;
        size_t take = want < (long long)sizeof(chunk) ? (size_t)want : sizeof(chunk);
        int n = conn_read_some(c, chunk, take, deadline);
        if (n < 0) return n;
        if (n == 0) {
            lsh_set_detail("connection closed mid-body (%lld/%lld bytes)", "", NULL);
            return LSH_E_PROTO;
        }
        if (!sink_write(sink, chunk, (size_t)n, got + n)) return LSH_E_MEMORY;
        got += n;
    }
    return 0;
}


static int pump_body_chunked(struct lsh_conn *c, long long deadline, body_sink *sink,
                             char *pending, size_t pending_len) {
    /* Streaming chunk decoder fed by `pending` bytes already consumed past
     * the header block plus further socket reads. */
    lsh_buf in;
    buf_init(&in);
    if (pending_len && !buf_append(&in, pending, pending_len)) {
        buf_free(&in);
        return LSH_E_MEMORY;
    }

    size_t pos = 0;
    long long total_written = 0;
    enum { PS_SIZE, PS_EXT, PS_CRLF, PS_DATA, PS_CRLF_AFTER } st = PS_SIZE;
    long long chunk_rem = 0;
    int done = 0;
    char linebuf[64];
    size_t linelen = 0;

    while (!done) {
        if (st == PS_DATA) {
            /* Bulk payload copy: push through as much as the buffer holds. */
            if (chunk_rem > 0) {
                size_t avail_in = in.len - pos;
                long long take = (long long)avail_in;
                if (take > chunk_rem) take = chunk_rem;
                if (take > 0) {
                    if (!sink_write(sink, in.p + pos, (size_t)take,
                                    total_written + take)) {
                        buf_free(&in);
                        return LSH_E_MEMORY;
                    }
                    total_written += take;
                    pos += (size_t)take;
                    chunk_rem -= take;
                }
                if (chunk_rem > 0 && pos >= in.len) {
                    char more[16384];
                    int n = conn_read_some(c, more, sizeof(more), deadline);
                    if (n <= 0) {
                        buf_free(&in);
                        return n < 0 ? n : LSH_E_PROTO;
                    }
                    pos = 0;
                    buf_free(&in);
                    buf_init(&in);
                    if (!buf_append(&in, more, (size_t)n)) {
                        buf_free(&in);
                        return LSH_E_MEMORY;
                    }
                }
                continue;
            }
            st = PS_CRLF_AFTER;
        }

        if (st == PS_CRLF_AFTER) {
            /* Consume the CRLF that follows each chunk's payload. */
            if (pos >= in.len) {
                char more[4096];
                int n = conn_read_some(c, more, sizeof(more), deadline);
                if (n <= 0) {
                    buf_free(&in);
                    return n < 0 ? n : LSH_E_PROTO;
                }
                pos = 0;
                buf_free(&in);
                buf_init(&in);
                if (!buf_append(&in, more, (size_t)n)) {
                    buf_free(&in);
                    return LSH_E_MEMORY;
                }
                continue;
            }
            char b = in.p[pos++];
            if (b == '\n') {
                st = PS_SIZE;
                linelen = 0;
            }
            /* any stray \r is skipped */
            continue;
        }

        /* Framing phase: byte-at-a-time (size line / CRLF). */
        if (pos >= in.len) {
            char more[16384];
            int n = conn_read_some(c, more, sizeof(more), deadline);
            if (n < 0) {
                buf_free(&in);
                return n;
            }
            if (n == 0) {
                lsh_set_detail("connection closed mid-chunk", NULL, NULL);
                buf_free(&in);
                return LSH_E_PROTO;
            }
            pos = 0;
            buf_free(&in);
            buf_init(&in);
            if (!buf_append(&in, more, (size_t)n)) {
                buf_free(&in);
                return LSH_E_MEMORY;
            }
            continue;
        }

        char ch = in.p[pos++];
        if (st == PS_SIZE || st == PS_EXT) {
            if (ch == ';') {
                st = PS_EXT; /* ignore chunk extensions */
                linelen = 0;
            } else if (ch == '\r') {
                linebuf[linelen] = '\0';
                chunk_rem = strtoll(linebuf, NULL, 16);
                linelen = 0;
                st = PS_CRLF;
            } else if (ch == '\n') { /* tolerate LF-only framing */
                linebuf[linelen] = '\0';
                chunk_rem = strtoll(linebuf, NULL, 16);
                linelen = 0;
                if (chunk_rem == 0) done = 1;
                else st = PS_DATA;
            } else if (st == PS_SIZE && linelen + 1 < sizeof(linebuf)) {
                linebuf[linelen++] = ch;
            }
        } else if (st == PS_CRLF) {
            if (ch == '\n') {
                if (chunk_rem == 0) done = 1;
                else st = PS_DATA;
            }
        }
    }

    buf_free(&in);
    return 0;
}

#endif /* !_WIN32 */

/* ------------------------------------------------------------------ */
/* Public API                                                         */
/* ------------------------------------------------------------------ */

const char *lsh_http_version(void) {
    return LSH_HTTP_VERSION_STR;
}

const char *lsh_http_strerror(int code) {
    const char *base;
    switch (code) {
    case LSH_E_URL:      base = "invalid or unsupported URL"; break;
    case LSH_E_DNS:      base = "could not resolve host"; break;
    case LSH_E_CONNECT:  base = "could not connect"; break;
    case LSH_E_TLS:      base = "TLS error"; break;
    case LSH_E_SEND:     base = "failed to send request"; break;
    case LSH_E_RECV:     base = "failed to receive response"; break;
    case LSH_E_TIMEOUT:  base = "timed out"; break;
    case LSH_E_PROTO:    base = "malformed HTTP response"; break;
    case LSH_E_REDIRECT: base = "too many redirects"; break;
    case LSH_E_MEMORY:   base = "out of memory or size limit reached"; break;
    case LSH_E_FILE:     base = "cannot open destination file"; break;
    default:
        if (code >= 100) return "no error";
        base = "unknown error";
        break;
    }
    char msg[768];
    if (lsh_detail[0]) {
        snprintf(msg, sizeof(msg), "%s (%s)", base, lsh_detail);
    } else {
        snprintf(msg, sizeof(msg), "%s", base);
    }
    char *out = (char *)leash_gc_alloc_string((long long)strlen(msg));
    if (out) strcpy(out, msg);
    return out;
}

/* Core request driver shared by both backends through `perform`. */

#ifdef _WIN32
static int win_perform(const char *method, const char *url, const char *headers,
                       const char *body, long long timeout_ms, int max_redirects,
                       int verify_tls, body_sink *sink, char **out_headers);
#else
static int posix_perform(const char *method, const char *url, const char *headers,
                         const char *body, long long timeout_ms, int max_redirects,
                         int verify_tls, body_sink *sink, char **out_headers);
#endif

static int lsh_perform(const char *method, const char *url, const char *headers,
                       const char *body, long long timeout_ms, int max_redirects,
                       int verify_tls, body_sink *sink, char **out_headers) {
    lsh_clear_detail();
#ifdef _WIN32
    return win_perform(method, url, headers, body, timeout_ms, max_redirects,
                       verify_tls, sink, out_headers);
#else
    return posix_perform(method, url, headers, body, timeout_ms, max_redirects,
                         verify_tls, sink, out_headers);
#endif
}

#ifndef _WIN32

static int posix_perform(const char *method0, const char *url0, const char *headers,
                         const char *body, long long timeout_ms, int max_redirects,
                         int verify_tls, body_sink *sink, char **out_headers) {
    char method[16];
    char url[2800];
    snprintf(method, sizeof(method), "%s", method0);
    snprintf(url, sizeof(url), "%s", url0);

    if (timeout_ms <= 0) timeout_ms = 30000;
    if (max_redirects < 0) max_redirects = 10;

    for (int hop = 0;; hop++) {
        lsh_url u;
        if (!lsh_parse_url(url, &u)) {
            lsh_set_detail("unsupported or malformed URL: %.96s", url, NULL);
            return LSH_E_URL;
        }
        if (strcmp(u.scheme, "https") != 0 && strcmp(u.scheme, "http") != 0) {
            lsh_set_detail("unsupported scheme '%s'", u.scheme, NULL);
            return LSH_E_URL;
        }

        long long deadline = lsh_now_ms() + timeout_ms;
        struct lsh_conn conn;
        conn.fd = -1;
        conn.ssl = NULL;
        conn.ctx = NULL;

        int rc = tcp_connect(&u, deadline, &conn);
        if (rc != 0) return rc;

        if (strcmp(u.scheme, "https") == 0) {
            rc = tls_connect(&u, verify_tls, deadline, &conn);
            if (rc != 0) {
                conn_close(&conn);
                return rc;
            }
        }
        lsh_socket_timeout(conn.fd, lsh_deadline_left(deadline));

        lsh_buf req;
        if (!lsh_build_request(&req, method, &u, headers, body)) {
            buf_free(&req);
            conn_close(&conn);
            return LSH_E_MEMORY;
        }
        rc = conn_write_all(&conn, req.p, req.len, deadline);
        buf_free(&req);
        if (rc != 0) {
            conn_close(&conn);
            return rc;
        }

        char *hdr_raw = NULL;
        size_t hdr_len = 0;
        int chunked = 0;
        long long clen = -1;

        /* The response head plus any body bytes that arrive with it are
         * decoded into a temporary memory sink so redirects stay clean; on
         * the final hop those bytes are forwarded to the caller's sink. */
        body_sink tmp_sink;
        buf_init(&tmp_sink.mem);
        tmp_sink.file = NULL;
        int status = read_response_head_and_body(&conn, deadline, &tmp_sink,
                                                 &hdr_raw, &hdr_len, &chunked, &clen);
        if (status < 0) {
            buf_free(&tmp_sink.mem);
            free(hdr_raw);
            conn_close(&conn);
            return status;
        }

        if (status == 301 || status == 302 || status == 303 || status == 307 ||
            status == 308) {
            char loc[2600];
            if (hop >= max_redirects) {
                lsh_set_detail("more than %d redirects", "", NULL);
                free(hdr_raw);
                buf_free(&tmp_sink.mem);
                conn_close(&conn);
                return LSH_E_REDIRECT;
            }
            if (!lsh_header_find(hdr_raw, "Location", loc, sizeof(loc)) ||
                loc[0] == '\0') {
                lsh_set_detail("%d redirect without Location header", "", NULL);
                free(hdr_raw);
                buf_free(&tmp_sink.mem);
                conn_close(&conn);
                return LSH_E_PROTO;
            }
            lsh_url next;
            if (!lsh_resolve_location(&u, loc, &next)) {
                lsh_set_detail("bad redirect Location: %.96s", loc, NULL);
                free(hdr_raw);
                buf_free(&tmp_sink.mem);
                conn_close(&conn);
                return LSH_E_URL;
            }
            int next_default_port =
                (next.port_num == 443 && strcmp(next.scheme, "https") == 0) ||
                (next.port_num == 80 && strcmp(next.scheme, "http") == 0);
            snprintf(url, sizeof(url), "%s://%s%s%s%s", next.scheme, next.host,
                     next_default_port ? "" : ":",
                     next_default_port ? "" : next.port, next.path);

            if (status == 303 ||
                ((status == 301 || status == 302) && strcmp(method, "POST") == 0)) {
                strcpy(method, "GET");
                body = "";
            }
            free(hdr_raw);
            buf_free(&tmp_sink.mem);
            conn_close(&conn);
            continue;
        }

        /* Final response: finish reading the body. */
        int rc2 = 0;
        if (chunked) {
            rc2 = pump_body_chunked(&conn, deadline, sink,
                                    tmp_sink.mem.p ? tmp_sink.mem.p : "",
                                    tmp_sink.mem.len);
        } else {
            /* Plain body data that arrived with the head goes straight to
             * the caller's sink before reading any further bytes. */
            if (tmp_sink.mem.len > 0 &&
                !sink_write(sink, tmp_sink.mem.p, tmp_sink.mem.len,
                            (long long)tmp_sink.mem.len)) {
                rc2 = LSH_E_MEMORY;
            }
            if (rc2 == 0) {
                if (clen >= 0) {
                    rc2 = pump_body_fixed(&conn, deadline, sink,
                                          (long long)tmp_sink.mem.len, clen);
                } else {
                    /* Read until EOF. */
                    char more[16384];
                    long long tot = (long long)tmp_sink.mem.len;
                    for (;;) {
                        int n = conn_read_some(&conn, more, sizeof(more), deadline);
                        if (n < 0) {
                            rc2 = n;
                            break;
                        }
                        if (n == 0) break;
                        if (!sink_write(sink, more, (size_t)n, tot + n)) {
                            rc2 = LSH_E_MEMORY;
                            break;
                        }
                        tot += n;
                    }
                }
            }
        }
        buf_free(&tmp_sink.mem);
        conn_close(&conn);
        if (rc2 != 0) {
            free(hdr_raw);
            return rc2;
        }

        if (out_headers) {
            *out_headers = lsh_normalize_headers(hdr_raw, hdr_len);
            if (!*out_headers) {
                free(hdr_raw);
                return LSH_E_MEMORY;
            }
        }
        free(hdr_raw);
        return status;
    }
}

#else /* _WIN32 */

static wchar_t *utf8_to_wide(const char *s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (n <= 0) return NULL;
    wchar_t *w = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, (size_t)n * sizeof(wchar_t));
    if (!w) return NULL;
    MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}

static char *wide_to_utf8(const wchar_t *w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    if (n <= 0) return NULL;
    char *s = (char *)HeapAlloc(GetProcessHeap(), 0, (size_t)n);
    if (!s) return NULL;
    WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
    return s;
}

static int win_perform(const char *method0, const char *url0, const char *headers,
                       const char *body, long long timeout_ms, int max_redirects,
                       int verify_tls, body_sink *sink, char **out_headers) {
    char method[16];
    char url[2800];
    snprintf(method, sizeof(method), "%s", method0);
    snprintf(url, sizeof(url), "%s", url0);

    if (timeout_ms <= 0) timeout_ms = 30000;
    if (max_redirects < 0) max_redirects = 10;

    static int session_ready = 0;
    static HINTERNET g_session = NULL;
    if (!session_ready) {
        g_session = WinHttpOpen(L"leash-http/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        session_ready = 1;
    }
    if (!g_session) {
        lsh_set_detail("WinHttpOpen failed (error %lu)", "", NULL);
        return LSH_E_CONNECT;
    }

    for (int hop = 0;; hop++) {
        lsh_url u;
        if (!lsh_parse_url(url, &u)) {
            lsh_set_detail("unsupported or malformed URL: %.96s", url, NULL);
            return LSH_E_URL;
        }
        int secure = strcmp(u.scheme, "https") == 0;
        if (!secure && strcmp(u.scheme, "http") != 0) {
            lsh_set_detail("unsupported scheme '%s'", u.scheme, NULL);
            return LSH_E_URL;
        }

        wchar_t *whost = utf8_to_wide(u.host);
        wchar_t *wpath = utf8_to_wide(u.path);
        wchar_t *wmethod = utf8_to_wide(method);
        wchar_t *whdr = headers && headers[0] ? utf8_to_wide(headers) : NULL;
        wchar_t *wbody_hdr = NULL;
        if (!whost || !wpath || !wmethod) {
            if (whost) HeapFree(GetProcessHeap(), 0, whost);
            if (wpath) HeapFree(GetProcessHeap(), 0, wpath);
            if (wmethod) HeapFree(GetProcessHeap(), 0, wmethod);
            if (whdr) HeapFree(GetProcessHeap(), 0, whdr);
            return LSH_E_MEMORY;
        }

        HINTERNET conn = WinHttpConnect(g_session, whost, (INTERNET_PORT)u.port_num, 0);
        if (!conn) {
            lsh_set_detail("WinHttpConnect('%s') failed (error %lu)", u.host, NULL);
            HeapFree(GetProcessHeap(), 0, whost);
            HeapFree(GetProcessHeap(), 0, wpath);
            HeapFree(GetProcessHeap(), 0, wmethod);
            if (whdr) HeapFree(GetProcessHeap(), 0, whdr);
            return LSH_E_CONNECT;
        }

        DWORD flags = secure ? WINHTTP_FLAG_SECURE : 0;
        HINTERNET req = WinHttpOpenRequest(conn, wmethod, wpath, NULL,
                                           WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                           flags);
        HeapFree(GetProcessHeap(), 0, whost);
        HeapFree(GetProcessHeap(), 0, wpath);
        HeapFree(GetProcessHeap(), 0, wmethod);
        if (!req) {
            lsh_set_detail("WinHttpOpenRequest failed (error %lu)", "", NULL);
            WinHttpCloseHandle(conn);
            if (whdr) HeapFree(GetProcessHeap(), 0, whdr);
            return LSH_E_CONNECT;
        }

        WinHttpSetTimeouts(req, (int)timeout_ms, (int)timeout_ms, (int)timeout_ms,
                           (int)timeout_ms);

        DWORD redir_feature = WINHTTP_DISABLE_REDIRECTS;
        WinHttpSetOption(req, WINHTTP_OPTION_DISABLE_FEATURE, &redir_feature,
                         sizeof(redir_feature));

        if (secure && !verify_tls) {
            DWORD secflags = SECURITY_FLAG_IGNORE_UNKNOWN_CA |
                             SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                             SECURITY_FLAG_IGNORE_CERT_DATE_INVALID |
                             SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
            WinHttpSetOption(req, WINHTTP_OPTION_SECURITY_FLAGS, &secflags,
                             sizeof(secflags));
        }

        if (whdr) {
            WinHttpAddRequestHeaders(req, whdr, (DWORD)-1,
                                     WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE);
            HeapFree(GetProcessHeap(), 0, whdr);
            whdr = NULL;
        }

        BOOL ok;
        DWORD total = body ? (DWORD)strlen(body) : 0;
        if (total > 0) {
            char cl[64];
            snprintf(cl, sizeof(cl), "Content-Length: %lu\r\n", total);
            wbody_hdr = utf8_to_wide(cl);
            if (wbody_hdr) {
                WinHttpAddRequestHeaders(req, wbody_hdr, (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD);
                HeapFree(GetProcessHeap(), 0, wbody_hdr);
            }
            ok = WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                    (LPVOID)body, total, total, 0);
        } else {
            ok = WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                    WINHTTP_NO_REQUEST_DATA, 0, 0, 0);
        }
        if (!ok) {
            lsh_set_detail("WinHttpSendRequest failed (error %lu)", GetLastError() ? "" : "", NULL);
            WinHttpCloseHandle(req);
            WinHttpCloseHandle(conn);
            return LSH_E_SEND;
        }
        if (!WinHttpReceiveResponse(req, NULL)) {
            lsh_set_detail("WinHttpReceiveResponse failed (error %lu)", "", NULL);
            WinHttpCloseHandle(req);
            WinHttpCloseHandle(conn);
            return LSH_E_RECV;
        }

        DWORD status = 0, sz = sizeof(status);
        WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                            WINHTTP_HEADER_NAME_BY_INDEX, &status, &sz,
                            WINHTTP_NO_HEADER_INDEX);

        /* Raw headers (includes status line). */
        char *rawhdr_utf8 = NULL;
        DWORD hdrlen = 0;
        WinHttpQueryHeaders(req, WINHTTP_QUERY_RAW_HEADERS_CRLF,
                            WINHTTP_HEADER_NAME_BY_INDEX, NULL, &hdrlen, WINHTTP_NO_HEADER_INDEX);
        if (hdrlen > 0) {
            wchar_t *wraw = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, hdrlen);
            if (wraw) {
                if (WinHttpQueryHeaders(req, WINHTTP_QUERY_RAW_HEADERS_CRLF,
                                        WINHTTP_HEADER_NAME_BY_INDEX, wraw, &hdrlen,
                                        WINHTTP_NO_HEADER_INDEX)) {
                    rawhdr_utf8 = wide_to_utf8(wraw);
                }
                HeapFree(GetProcessHeap(), 0, wraw);
            }
        }

        if (status == 301 || status == 302 || status == 303 || status == 307 ||
            status == 308) {
            WCHAR wloc[2600] = {0};
            DWORD lsz = sizeof(wloc) - sizeof(WCHAR);
            int have_loc = WinHttpQueryHeaders(req, WINHTTP_QUERY_LOCATION,
                                               WINHTTP_HEADER_NAME_BY_INDEX, wloc, &lsz,
                                               WINHTTP_NO_HEADER_INDEX);
            char loc[2600] = {0};
            if (have_loc) {
                char *l8 = wide_to_utf8(wloc);
                if (l8) {
                    snprintf(loc, sizeof(loc), "%s", l8);
                    HeapFree(GetProcessHeap(), 0, l8);
                }
            }
            free(rawhdr_utf8);
            WinHttpCloseHandle(req);
            WinHttpCloseHandle(conn);
            if (hop >= max_redirects) {
                lsh_set_detail("more than %d redirects", "", NULL);
                return LSH_E_REDIRECT;
            }
            if (!loc[0]) {
                lsh_set_detail("%d redirect without Location header", "", NULL);
                return LSH_E_PROTO;
            }
            lsh_url next;
            if (!lsh_resolve_location(&u, loc, &next)) {
                lsh_set_detail("bad redirect Location: %.96s", loc, NULL);
                return LSH_E_URL;
            }
            int defport = (next.port_num == 443 && strcmp(next.scheme, "https") == 0) ||
                          (next.port_num == 80 && strcmp(next.scheme, "http") == 0);
            snprintf(url, sizeof(url), "%s://%s%s%s%s", next.scheme, next.host,
                     defport ? "" : ":", defport ? "" : next.port, next.path);
            if (status == 303 ||
                ((status == 301 || status == 302) && strcmp(method, "POST") == 0)) {
                strcpy(method, "GET");
                body = "";
            }
            continue;
        }

        /* Read body into sink. */
        int rc = 0;
        DWORD avail = 0;
        for (;;) {
            if (!WinHttpQueryDataAvailable(req, &avail)) {
                lsh_set_detail("WinHttpQueryDataAvailable failed (error %lu)", "", NULL);
                rc = LSH_E_RECV;
                break;
            }
            if (avail == 0) break;
            char *buf = (char *)malloc(avail);
            if (!buf) {
                rc = LSH_E_MEMORY;
                break;
            }
            DWORD readn = 0;
            if (!WinHttpReadData(req, buf, avail, &readn)) {
                free(buf);
                lsh_set_detail("WinHttpReadData failed (error %lu)", "", NULL);
                rc = LSH_E_RECV;
                break;
            }
            if (readn == 0) {
                free(buf);
                break;
            }
            if (!sink_write(sink, buf, readn, (long long)readn)) {
                free(buf);
                rc = LSH_E_MEMORY;
                break;
            }
            free(buf);
        }

        if (rc == 0 && out_headers && rawhdr_utf8) {
            /* Skip the status line, normalize CRLF -> LF. */
            const char *nl = strchr(rawhdr_utf8, '\n');
            const char *hs = nl ? nl + 1 : rawhdr_utf8;
            *out_headers = lsh_normalize_headers(hs, strlen(hs));
            if (!*out_headers) rc = LSH_E_MEMORY;
        }
        free(rawhdr_utf8);
        WinHttpCloseHandle(req);
        WinHttpCloseHandle(conn);
        return (int)status;
    }
}

#endif /* _WIN32 */

int lsh_http_request(const char *method, const char *url, const char *headers,
                     const char *body, long long timeout_ms, int max_redirects,
                     int verify_tls, char **out_headers, char **out_body) {
    lsh_clear_detail();
    if (out_headers) *out_headers = NULL;
    if (out_body) *out_body = NULL;

    body_sink sink;
    sink.file = NULL;
    buf_init(&sink.mem);

    int status = lsh_perform(method, url, headers, body, timeout_ms, max_redirects,
                             verify_tls, &sink, out_headers);
    if (status < 0) {
        buf_free(&sink.mem);
        if (out_headers) *out_headers = NULL;
        return status;
    }

    if (out_body) {
        *out_body = lsh_gc_string(sink.mem.p ? sink.mem.p : "", sink.mem.len);
        if (!*out_body) {
            buf_free(&sink.mem);
            if (out_headers) *out_headers = NULL;
            return LSH_E_MEMORY;
        }
    }
    buf_free(&sink.mem);
    return status;
}

int lsh_http_download(const char *url, const char *dest_path, long long timeout_ms,
                      int max_redirects, int verify_tls) {
    lsh_clear_detail();
    FILE *f = fopen(dest_path, "wb");
    if (!f) {
        lsh_set_detail("cannot open '%s' for writing", dest_path, NULL);
        return LSH_E_FILE;
    }
    body_sink sink;
    sink.file = f;
    buf_init(&sink.mem);

    int status = lsh_perform("GET", url, "", "", timeout_ms, max_redirects,
                             verify_tls, &sink, NULL);
    int io_ok = (fflush(f) == 0);
    fclose(f);
    buf_free(&sink.mem);
    if (status < 0) return status;
    if (!io_ok) {
        lsh_set_detail("write error while saving to '%s'", dest_path, NULL);
        return LSH_E_FILE;
    }
    return status;
}
