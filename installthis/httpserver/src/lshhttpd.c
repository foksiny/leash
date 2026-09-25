/*
 * lshhttpd.c — native HTTP/1.1 server shim for the Leash `httpserver` stdlib package.
 *
 * One translation unit, two backends selected by platform:
 *
 *   POSIX (Linux/macOS/BSD): BSD sockets
 *   Windows (_WIN32):        Winsock2
 *
 * Serving model (v1.1): a multiplexed event loop on the thread that calls
 * lshhttpd_serve(). A single poll()/select() slice watches the listen socket
 * and every open client connection — read-readiness AND writability — so
 * complete requests are dispatched to the Leash handler callback ON THAT
 * THREAD (so the garbage collector sees every handler local) and slow
 * readers cannot wedge the server. Client sockets are kept open across
 * requests (HTTP/1.1 keep-alive), with pipelining on a single connection
 * supported naturally by the byte-buffered framing. Responses are written
 * non-blockingly: a response that would block is buffered per connection
 * and drained on POLLOUT while other connections keep being served.
 *
 * Idle keep-alive connections close after 60 s of silence; a connection
 * parked mid-request gets the 30 s per-request I/O budget instead, and a
 * response still draining after its 30 s write deadline is reaped.
 * Buffered bytes (request accumulators + pending responses) are bounded
 * per connection AND by a server-wide ceiling (LSHD_MAX_GLOBAL_BYTES).
 * lshhttpd_shutdown() may be called from the handler (shuts down after the
 * current response) or from another thread (unblocks accept/poll
 * immediately).
 *
 * Strings returned by handlers are copied into malloc'd C buffers right
 * after the callback returns, so they stay valid while being written out.
 *
 * Build (see build.sh / build.bat in this directory):
 *   sh build.sh                                        # -> ../linux/liblshhttpd.a
 *   TARGET_DIR=linux-arm CC=aarch64-linux-gnu-gcc sh build.sh
 *   build.bat                                          # -> ..\win\liblshhttpd.a
 */

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <stdatomic.h>

/* Strings handed back to Leash come from the Leash runtime allocator. */
extern void *leash_gc_alloc_string(long long len);

#ifdef _WIN32
# ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
# endif
# ifndef _WIN32_WINNT
#  define _WIN32_WINNT 0x0600
# endif
# ifndef FD_SETSIZE
#  define FD_SETSIZE 512
# endif
# include <winsock2.h>
# include <ws2tcpip.h>
 typedef SOCKET lsh_fd_t;
# define LSHD_INVALID_FD INVALID_SOCKET
#else
# include <sys/types.h>
# include <sys/socket.h>
# include <netinet/in.h>
# include <netinet/tcp.h>
# include <arpa/inet.h>
# include <poll.h>
# include <fcntl.h>
# include <unistd.h>
# include <errno.h>
 typedef int lsh_fd_t;
# define LSHD_INVALID_FD (-1)
#endif

#define LSH_HTTPD_VERSION_STR "leash-httpd/1.1"
#define LSHD_MAX_HEAD_BYTES  (64L * 1024L)            /* request head cap        */
#define LSHD_MAX_LINE_BYTES  4096                     /* request line cap        */
#define LSHD_MAX_BODY_BYTES  (16LL * 1024LL * 1024LL) /* request body cap        */
#define LSHD_IO_TIMEOUT_MS   30000                    /* per-client I/O timeout  */
#define LSHD_ACCEPT_POLL_MS  250
#define LSHD_MAX_CONNS       128
#define LSHD_POLL_SLICE_MS   250                        /* poll slice                             */
#define LSHD_IDLE_TIMEOUT_MS 60000                      /* idle keep-alive cutoff                 */
#define LSHD_SLOW_REQ_MS     LSHD_IO_TIMEOUT_MS         /* slow request bytes get 30 s windows    */
#define LSHD_MAX_CONN_ACC    (LSHD_MAX_HEAD_BYTES + LSHD_MAX_BODY_BYTES)
/* Server-wide buffered-bytes ceiling (request accumulators + pending
 * response bytes). Per-connection caps alone let 128 connections hold
 * ~2 GiB; this bounds the total memory the server will buffer at once.
 * Redefine at compile time for tighter deployments. */
#ifndef LSHD_MAX_GLOBAL_BYTES
#  define LSHD_MAX_GLOBAL_BYTES (64LL * 1024LL * 1024LL)  /* 64 MiB total */
#endif

/* Error codes returned by lshhttpd_serve(). */
enum {
    LSHD_E_ARGS    = -1,  /* bad port / null handler                        */
    LSHD_E_SOCKET  = -2,  /* socket() or WSAStartup failed                  */
    LSHD_E_BIND    = -3,  /* bind() failed (port in use?)                   */
    LSHD_E_LISTEN  = -4,  /* listen() failed                                */
    LSHD_E_ACCEPT  = -5,  /* accept()/recv()/send() failure                 */
    LSHD_E_MEMORY  = -6,  /* out of memory / size cap hit                   */
    LSHD_E_ABORTED = -7   /* stop() requested before serving any request    */
};

/* Handler installed by Leash.  Returns the HTTP status code to reply with
 * (200, 404, ...). The response body and extra headers are handed back
 * through lshhttpd_set_body()/lshhttpd_add_header(), which copy the data
 * into C-owned buffers immediately.  All strings passed in are NUL-termi-
 * nated Leash GC strings, so handlers may keep references to them. */
typedef int (*lshhttpd_handler_t)(
    const char *method,
    const char *path,
    const char *query,
    const char *headers,
    const char *body
);
static long long lshd_now_ms(void);



static char lshd_detail[256] = "";
static _Atomic int lshd_stop_flag = 0;
static _Atomic lsh_fd_t lshd_listen_fd = LSHD_INVALID_FD;
static _Atomic int lshd_running = 0;  /* one serve() at a time */

static void lshd_set_detail(const char *msg) {
    snprintf(lshd_detail, sizeof(lshd_detail), "%s", msg);
}

static void lshd_clear_detail(void) {
    lshd_detail[0] = '\0';
}

/* ------------------------------------------------------------------ */
/* Socket helpers (both backends)                                      */
/* ------------------------------------------------------------------ */

static void lshd_close_fd(lsh_fd_t fd) {
    if (fd == LSHD_INVALID_FD) return;
#ifdef _WIN32
    closesocket(fd);
#else
    close(fd);
#endif
}

/* Wait for writability on fd for at most `ms` (EINTR-safe). */
static int lshd_wait_writable(lsh_fd_t fd, long ms) {
#ifdef _WIN32
    fd_set wfds;
    struct timeval tv;
    int r;
    FD_ZERO(&wfds);
    FD_SET(fd, &wfds);
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    do {
        r = select(0, NULL, &wfds, NULL, &tv);
    } while (r < 0 && WSAGetLastError() == WSAEINTR);
    return r > 0 ? 1 : 0;
#else
    struct pollfd pfd;
    int r;
    pfd.fd = fd;
    pfd.events = POLLOUT;
    do {
        pfd.revents = 0;
        r = poll(&pfd, 1, (int)ms);
    } while (r < 0 && errno == EINTR);
    return r > 0 ? 1 : 0;
#endif
}

/* MSG_NOSIGNAL exists on Linux but not on the BSDs/macOS; there SO_NOSIGPIPE
 * is set on the accepted socket (see lshd_accept) and flag 0 is correct. */
#ifndef MSG_NOSIGNAL
#  define MSG_NOSIGNAL 0
#endif

/* ------------------------------------------------------------------ */
/* Growable buffer                                                      */
/* ------------------------------------------------------------------ */

typedef struct {
    char *p;
    size_t len;
    size_t cap;
} lshd_buf;

static void buf_init(lshd_buf *b) {
    b->p = NULL;
    b->len = 0;
    b->cap = 0;
}

static void buf_free(lshd_buf *b) {
    free(b->p);
    buf_init(b);
}

static int buf_append(lshd_buf *b, const char *data, size_t n) {
    /* Overflow guard: without it `len + n` could wrap and the doubling
     * loop would settle on a capacity smaller than the payload. */
    if (n > SIZE_MAX - b->len) return 0;
    size_t need = b->len + n;
    if (need > b->cap) {
        size_t ncap = b->cap ? b->cap : 4096;
        while (ncap < need) {
            if (ncap > SIZE_MAX / 2) { ncap = need; break; }
            ncap *= 2;
        }
        char *np = (char *)realloc(b->p, ncap);
        if (!np) return 0;
        b->p = np;
        b->cap = ncap;
    }
    memcpy(b->p + b->len, data, n);
    b->len = need;
    return 1;
}

/* ------------------------------------------------------------------ */
/* HTTP text helpers                                                    */
/* ------------------------------------------------------------------ */

static int lshd_ieq(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        char ca = a[i], cb = b[i];
        if (ca >= 'A' && ca <= 'Z') ca = (char)(ca + 32);
        if (cb >= 'A' && cb <= 'Z') cb = (char)(cb + 32);
        if (ca != cb) return 0;
    }
    return 1;
}

static const char *lshd_status_reason(int code) {
    switch (code) {
    case 200: return "OK";
    case 201: return "Created";
    case 202: return "Accepted";
    case 204: return "No Content";
    case 301: return "Moved Permanently";
    case 302: return "Found";
    case 303: return "See Other";
    case 304: return "Not Modified";
    case 307: return "Temporary Redirect";
    case 308: return "Permanent Redirect";
    case 400: return "Bad Request";
    case 401: return "Unauthorized";
    case 403: return "Forbidden";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 408: return "Request Timeout";
    case 411: return "Length Required";
    case 413: return "Payload Too Large";
    case 414: return "URI Too Long";
    case 415: return "Unsupported Media Type";
    case 422: return "Unprocessable Entity";
    case 429: return "Too Many Requests";
    case 431: return "Request Header Fields Too Large";
    case 500: return "Internal Server Error";
    case 501: return "Not Implemented";
    case 503: return "Service Unavailable";
    default:  return "";
    }
}

/* Returns 1 when the LF-separated header line starts with `name:` (case-
 * insensitive). */
static int lshd_line_is_header(const char *line, size_t linelen, const char *name) {
    size_t n = strlen(name);
    if (linelen <= n) return 0;
    if (line[n] != ':') return 0;
    return lshd_ieq(line, name, n);
}

/* RFC 1123 date for the Date header. */
static void lshd_date_line(char *dst, size_t cap) {
    time_t now = time(NULL);
    struct tm *tmv = gmtime(&now);
    if (!tmv) { dst[0] = '\0'; return; }
    static const char *wd[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
    static const char *mo[] = {"Jan","Feb","Mar","Apr","May","Jun",
                              "Jul","Aug","Sep","Oct","Nov","Dec"};
    snprintf(dst, cap, "Date: %s, %02d %s %04d %02d:%02d:%02d GMT",
             wd[tmv->tm_wday % 7], tmv->tm_mday, mo[tmv->tm_mon % 12],
             tmv->tm_year + 1900, tmv->tm_hour, tmv->tm_min, tmv->tm_sec);
}

/* ------------------------------------------------------------------ */
/* Request snapshot handed to the handler                              */
/* ------------------------------------------------------------------ */

typedef struct {
    char method[16];
    char path[LSHD_MAX_LINE_BYTES];
    char *query;    /* malloc'd, without the leading '?' ("" when absent) */
    char *headers;  /* malloc'd, LF-separated lines, no request line       */
    char *body;     /* malloc'd (never NULL; "" when there is no body)     */
    int   bad;      /* when non-zero: reply with this status, skip handler */
    int   http_minor;     /* 0 = HTTP/1.0, 1 = HTTP/1.1 */
    int   close_after;    /* 1 = close after this response */
} lshd_request;

static char *lshd_strdup(const char *s) {
    size_t n = strlen(s);
    char *out = (char *)malloc(n + 1);
    if (!out) return NULL;
    memcpy(out, s, n + 1);
    return out;
}

static char *lshd_strndup_all(const char *s, size_t n) {
    char *out = (char *)malloc(n + 1);
    if (!out) return NULL;
    memcpy(out, s, n);
    out[n] = '\0';
    return out;
}

static void lshd_request_free(lshd_request *r) {
    free(r->query);
    free(r->headers);
    free(r->body);
    r->query = NULL;
    r->headers = NULL;
    r->body = NULL;
}

/* Copy `data` into a freshly GC-allocated string (what handlers get). */
static char *lshd_to_gc_string(const char *data, size_t len) {
    if (!data) data = "";
    if (len == 0) len = strlen(data);
    char *out = (char *)leash_gc_alloc_string((long long)len);
    if (!out) return NULL;
    if (len) memcpy(out, data, len);
    return out; /* zero-terminated by the allocator */
}

/* ---- per-request response buffers (filled by the handler) ---- */

static lshd_buf g_resp_headers;  /* LF-separated extra header lines   */
static lshd_buf g_resp_body;     /* response body                     */
static int g_resp_inited = 0;

static void lshd_resp_ensure(void) {
    if (!g_resp_inited) {
        buf_init(&g_resp_headers);
        buf_init(&g_resp_body);
        g_resp_inited = 1;
    }
}

/* Reset for the next request — called once before the handler runs. */
static void lshd_resp_reset(void) {
    lshd_resp_ensure();
    g_resp_headers.len = 0;
    g_resp_body.len = 0;
}

void lshhttpd_set_body(const char *body) {
    lshd_resp_ensure();
    g_resp_body.len = 0; /* last setter wins */
    if (body && body[0]) buf_append(&g_resp_body, body, strlen(body));
}

/* True when a header name/value would break the header block: any control
 * character (CR/LF included) can be used to inject extra header lines or
 * split the response ("response splitting"). Handlers sometimes echo
 * request data into headers, so the value is not necessarily trusted. */
static int lshd_header_unsafe(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '\r' || c == '\n' || c == 0) return 1;
        if (c < 0x20 && c != '\t') return 1;
        if (c == 0x7f) return 1;
    }
    return 0;
}

void lshhttpd_add_header(const char *name, const char *value) {
    lshd_resp_ensure();
    if (!name || !name[0]) return;
    size_t nl = strlen(name);
    size_t vl = value ? strlen(value) : 0;
    if (lshd_header_unsafe(name, nl) || (value && lshd_header_unsafe(value, vl))) {
        lshd_set_detail("header dropped: illegal control character in name or value");
        return;
    }
    buf_append(&g_resp_headers, name, nl);
    buf_append(&g_resp_headers, ": ", 2);
    if (value) buf_append(&g_resp_headers, value, vl);
    buf_append(&g_resp_headers, "\n", 1);
}

/* Append a raw header line ("Header-Name: value"). LF terminator added. */
void lshhttpd_set_header(const char *line) {
    lshd_resp_ensure();
    if (!line || !line[0]) return;
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) n--;
    if (lshd_header_unsafe(line, n)) {
        lshd_set_detail("header dropped: illegal control character in line");
        return;
    }
    buf_append(&g_resp_headers, line, n);
    buf_append(&g_resp_headers, "\n", 1);
}

static int lshd_is_head_method(const char *method) {
    return strlen(method) == 4 && memcmp(method, "HEAD", 4) == 0;
}

/* ------------------------------------------------------------------ */
/* Multiplexed keep-alive connection table                             */
/* ------------------------------------------------------------------ */

typedef struct {
    lsh_fd_t  fd;
    lshd_buf  acc;         /* request-byte accumulator (pipelining-friendly) */
    lshd_buf  out;         /* pending response bytes (non-blocking write)    */
    size_t    out_off;     /* bytes of `out` already written to the socket   */
    long long out_start;   /* when the CURRENT pending response began        */
    int       out_close;   /* drop the connection once `out` fully drains    */
    long long last_ms;     /* timestamp of the most recent accepted byte     */
    long long req_start;   /* when the CURRENT (incomplete) request began   */
    int       has_bytes;   /* seen any bytes on this connection yet          */
    int       deferred;    /* pipelined requests left for the next slice     */
} lshd_conn;

static lshd_conn *g_conns[LSHD_MAX_CONNS];
static int        g_nconns = 0;
/* Set when a connection left pipelined requests behind after hitting the
 * per-slice dispatch budget: the next poll must not block on I/O. */
static int        g_pending_work = 0;
/* Server-wide count of bytes currently buffered in request accumulators
 * and pending response buffers. Bounded by LSHD_MAX_GLOBAL_BYTES: the
 * per-connection caps alone let 128 connections hold ~2 GiB, and this
 * counter is what keeps the total (not just each connection) in check. */
static long long  g_total_buf_bytes = 0;

/* ------------------------------------------------------------------ */
/* Response writing                                                    */
/* ------------------------------------------------------------------ */

/* Build one response into the connection's pending-output buffer and try
 * to flush it without blocking the serving loop. Pipelined responses
 * queue in order in `c->out`; when the socket would block, the remaining
 * bytes stay buffered and the event loop drains them on POLLOUT — other
 * connections keep being served meanwhile. The newly buffered bytes are
 * accounted against the server-wide LSHD_MAX_GLOBAL_BYTES ceiling.
 * `close_conn` selects the Connection header and is remembered in
 * `c->out_close` until the write fully drains (the connection drops then).
 * Returns 0 on success (flushed or buffered), negative LSHD_E_* error. */
static int lshd_write_response(lshd_conn *c, int status,
                               const char *hdr, const char *body,
                               int head_only, int close_conn) {
    size_t body_len = body ? strlen(body) : 0;

    lshd_buf out;
    buf_init(&out);
    char line[256];

    const char *reason = lshd_status_reason(status);
    if (reason[0]) {
        snprintf(line, sizeof(line), "HTTP/1.1 %d %s\r\n", status, reason);
    } else {
        snprintf(line, sizeof(line), "HTTP/1.1 %d\r\n", status);
    }
    if (!buf_append(&out, line, strlen(line))) goto oom;

    char datebuf[64];
    lshd_date_line(datebuf, sizeof(datebuf));
    if (datebuf[0]) {
        if (!buf_append(&out, datebuf, strlen(datebuf))) goto oom;
        if (!buf_append(&out, "\r\n", 2)) goto oom;
    }
    snprintf(line, sizeof(line), "Server: %s\r\n", LSH_HTTPD_VERSION_STR);
    if (!buf_append(&out, line, strlen(line))) goto oom;

    {
        const char *conn_line = close_conn ? "Connection: close\r\n"
                                           : "Connection: keep-alive\r\n";
        if (!buf_append(&out, conn_line, strlen(conn_line))) goto oom;
    }

    /* Handler-provided headers: normalize LF to CRLF and skip framing
     * headers the server owns (Content-Length / Connection /
     * Transfer-Encoding) so the wire stays well-formed. Lines carrying a
     * stray control character are dropped — nothing but the vetted header
     * APIs can reach here, but a split response is worth a second gate. */
    if (hdr && hdr[0]) {
        const char *p = hdr;
        while (p && *p) {
            const char *eol = strchr(p, '\n');
            size_t ll = eol ? (size_t)(eol - p) : strlen(p);
            while (ll > 0 && (p[ll - 1] == '\r' || p[ll - 1] == ' ' || p[ll - 1] == '\t')) ll--;
            if (ll > 0 &&
                !lshd_header_unsafe(p, ll) &&
                !lshd_line_is_header(p, ll, "Content-Length") &&
                !lshd_line_is_header(p, ll, "Connection") &&
                !lshd_line_is_header(p, ll, "Transfer-Encoding")) {
                if (!buf_append(&out, p, ll)) goto oom;
                if (!buf_append(&out, "\r\n", 2)) goto oom;
            }
            p = eol ? eol + 1 : NULL;
        }
    }

    snprintf(line, sizeof(line), "Content-Length: %llu\r\n",
             (unsigned long long)body_len);
    if (!buf_append(&out, line, strlen(line))) goto oom;
    if (!buf_append(&out, "\r\n", 2)) goto oom;

    if (!head_only && body_len > 0) {
        if (!buf_append(&out, body, body_len)) goto oom;
    }

    if (c->out.len == 0) {
        /* First byte of a fresh response: start the write deadline. A
         * pipelined response appended onto an undrained one keeps the
         * earlier deadline. */
        c->out_start = lshd_now_ms();
        c->out_close = 0;
    }

    /* One accounted append into the connection's pending-output buffer. */
    size_t added = out.len;
    if (added > 0 && !buf_append(&c->out, out.p, out.len)) {
        buf_free(&out);
        goto oom;
    }
    buf_free(&out);
    g_total_buf_bytes += (long long)added;

    return 0;

oom:
    buf_free(&out);
    return LSHD_E_MEMORY;
}

/* Non-blocking flush of pending response bytes. Returns:
 *   1  all pending bytes written (out buffer reset for the next response)
 *   0  would block — remaining bytes stay buffered; poll watches POLLOUT
 *  <0  hard send error (caller drops the connection)
 * Every byte sent is un-accounted from the server-wide buffer ceiling. */
static int lshd_conn_flush(lshd_conn *c) {
    size_t before = c->out.len - c->out_off;
    while (c->out_off < c->out.len) {
#ifdef _WIN32
        int n = send(c->fd, c->out.p + c->out_off, (int)(c->out.len - c->out_off), 0);
        if (n > 0) {
            c->out_off += (size_t)n;
            c->last_ms = lshd_now_ms();
            continue;
        }
        int err = WSAGetLastError();
        if (err == WSAEINTR) continue;
        if (err == WSAEWOULDBLOCK) break;
        lshd_set_detail("send failed");
        return LSHD_E_ACCEPT;
#else
        ssize_t n = send(c->fd, c->out.p + c->out_off, c->out.len - c->out_off, MSG_NOSIGNAL);
        if (n > 0) {
            c->out_off += (size_t)n;
            c->last_ms = lshd_now_ms();
            continue;
        }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        lshd_set_detail("send failed");
        return LSHD_E_ACCEPT;
#endif
    }
    size_t after = c->out.len - c->out_off;
    if (after != before) g_total_buf_bytes -= (long long)(before - after);
    if (c->out_off >= c->out.len) {
        c->out.len = 0;
        c->out_off = 0;
        return 1;
    }
    return 0;
}

static long long lshd_now_ms(void) {
#ifdef _WIN32
    return (long long)GetTickCount64();
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
#endif
}

/* ------------------------------------------------------------------ */
/* Byte read done inside the multiplex loop. Returns:
 *   0  OK (may be 0 bytes — would-block)
 *   1  peer closed cleanly
 *  <0  hard recv error
 * Reads at most LSHD_DRAIN_BUDGET bytes per call so one chatty writer
 * cannot monopolize the serving thread between polls (R5), refuses
 * to buffer beyond LSHD_MAX_CONN_ACC per connection (R3), and stops
 * pulling when the server-wide LSHD_MAX_GLOBAL_BYTES ceiling is reached
 * (R4) — buffered bytes free up as requests are consumed and the next
 * slice reads again, so nothing is lost. */
#define LSHD_DRAIN_BUDGET (64 * 16384)   /* max bytes pulled per poll slice */
/* Max pipelined requests dispatched from one connection per poll slice. */
#define LSHD_DISPATCH_BUDGET 32
static int lshd_conn_read_avail(lshd_conn *c) {
    char chunk[16384];
    size_t pulled = 0;
    for (;;) {
        if (c->acc.len >= LSHD_MAX_CONN_ACC) {
            /* Per-connection fence exceeded: the parser's 431/413 will
             * fire on the next extract; stop buffering more right now. */
            return 0;
        }
        if (g_total_buf_bytes >= LSHD_MAX_GLOBAL_BYTES) {
            /* Server-wide fence exceeded: bytes stay in the kernel until
             * live requests are consumed and memory frees up. */
            return 0;
        }
        if (pulled >= LSHD_DRAIN_BUDGET) {
            return 0; /* be fair: let other connections take a slice */
        }
        if (c->acc.len == 0) {
            c->req_start = lshd_now_ms(); /* a fresh request is arriving */
        }
#ifdef _WIN32
        int n = recv(c->fd, chunk, (int)sizeof(chunk), 0);
        if (n > 0) {
            if (!buf_append(&c->acc, chunk, (size_t)n)) return LSHD_E_MEMORY;
            g_total_buf_bytes += (long long)n;
            c->last_ms = lshd_now_ms();
            c->has_bytes = 1;
            pulled += (size_t)n;
            continue;
        }
        if (n == 0) return 1;
        int e = WSAGetLastError();
        if (e == WSAEWOULDBLOCK) return 0;
        if (e == WSAEINTR) continue;
        lshd_set_detail("recv failed");
        return LSHD_E_ACCEPT;
#else
        ssize_t n = recv(c->fd, chunk, sizeof(chunk), 0);
        if (n > 0) {
            if (!buf_append(&c->acc, chunk, (size_t)n)) return LSHD_E_MEMORY;
            g_total_buf_bytes += (long long)n;
            c->last_ms = lshd_now_ms();
            c->has_bytes = 1;
            pulled += (size_t)n;
            continue;
        }
        if (n == 0) return 1;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
        lshd_set_detail("recv failed");
        return LSHD_E_ACCEPT;
#endif
    }
}

static void lshd_conn_free(lshd_conn *c) {
    if (!c) return;
    /* Un-account whatever is still buffered on this connection (request
     * accumulator + undrained response bytes). */
    g_total_buf_bytes -= (long long)(c->acc.len + (c->out.len - c->out_off));
    lshd_close_fd(c->fd);
    buf_free(&c->acc);
    buf_free(&c->out);
    free(c);
}

/* Remove table entry i (swap-with-last). */
static void lshd_conn_drop(int i) {
    lshd_conn_free(g_conns[i]);
    g_conns[i] = g_conns[g_nconns - 1];
    g_conns[g_nconns - 1] = NULL;
    g_nconns--;
}

/* ------------------------------------------------------------------ */
/* Buffer-driven request parsing (the heart of keep-alive)             */
/* ------------------------------------------------------------------ */

enum {
    LSHD_X_INCOMPLETE = 0,   /* request not fully buffered yet             */
    LSHD_X_READY      = 1,   /* req filled; *consumed set                  */
    LSHD_X_BAD        = 2    /* protocol parse error; req->bad is the code  */
};

/* Case-insensitive, comma-separated token scan over a header value
 * ("close", "keep-alive", ...). Scans exact tokens only. */
static int lshd_has_token(const char *hdr, const char *token) {
    size_t tl = strlen(token);
    while (*hdr) {
        while (*hdr == ' ' || *hdr == '\t' || *hdr == ',') hdr++;
        if (!*hdr) break;
        size_t j = 0;
        while (hdr[j] && hdr[j] != ',' && hdr[j] != ' ' && hdr[j] != '\t') j++;
        if (j == tl) {
            size_t k;
            int ok = 1;
            for (k = 0; k < tl; k++) {
                char a = hdr[k];
                char b = token[k];
                if (a >= 'A' && a <= 'Z') a = (char)(a + 32);
                if (a != b) { ok = 0; break; }
            }
            if (ok) return 1;
        }
        hdr += j;
    }
    return 0;
}

/* Parse the request at the front of conn->acc. *consumed is only set on
 * READY (whole request) or on BAD (whole offending span gets dropped). */
static int lshd_conn_extract(lshd_conn *c, lshd_request *req,
                             size_t *consumed) {
    memset(req, 0, sizeof(*req));
    req->bad = 0;
    req->http_minor = 1;
    req->close_after = 0;
    *consumed = 0;

    size_t len = c->acc.len;
    char *base = c->acc.p;

    /* ---- head terminator ---- */
    size_t head_end = 0;
    int term = 0;                         /* 1 = "\r\n\r\n", 2 = "\n\n" */
    size_t i;
    for (i = 0; i + 1 < len; i++) {
        if (i + 3 < len && memcmp(base + i, "\r\n\r\n", 4) == 0) {
            head_end = i;
            term = 1;
            break;
        }
        if (memcmp(base + i, "\n\n", 2) == 0) {
            head_end = i;
            term = 2;
            break;
        }
    }
    if (!term) {
        if (len > LSHD_MAX_HEAD_BYTES) {
            req->bad = 431;
            *consumed = len;
            return LSHD_X_BAD;
        }
        return LSHD_X_INCOMPLETE;
    }

    /* Head complete: enforce the head cap even when the terminator is
     * present (R2 — a 1 MiB head with a terminator must 431, not parse). */
    if (head_end > LSHD_MAX_HEAD_BYTES) {
        req->bad = 431;
        req->close_after = 1;
        *consumed = len;
        return LSHD_X_BAD;
    }

    size_t body_off = head_end + (term == 1 ? 4 : 2);

    /* Mixed line endings in one head (`\r\n` headers under a bare-LF
     * terminator, or the reverse) make the head ambiguous to anything that
     * forwards it — reject rather than guess. Uniform bare-LF heads stay
     * acceptable, as RFC 9112 allows. */
    if (term == 2) {
        size_t k;
        for (k = 0; k < head_end; k++) {
            if (base[k] == '\r') {
                req->bad = 400;
                req->close_after = 1;
                *consumed = body_off;
                return LSHD_X_BAD;
            }
        }
    }

    /* Everything below allocates request fields — from here on, every
     * INCOMPLETE return must free them (see the body-path below). */
    req->query = lshd_strdup("");
    req->headers = lshd_strdup("");
    req->body = lshd_strdup("");
    if (!req->query || !req->headers || !req->body) {
        lshd_request_free(req);
        req->query = NULL;
        req->headers = NULL;
        req->body = NULL;
        req->bad = 500;
        req->close_after = 1;
        return LSHD_X_BAD;
    }

    /* Reject control bytes in the head: NUL silently truncates every later
     * string operation, and the rest have no meaning in a request line or
     * header field. CR, LF and HTAB are the framing exceptions. */
    {
        size_t k;
        for (k = 0; k < head_end; k++) {
            unsigned char c = (unsigned char)base[k];
            if (c < 0x20 && c != '\r' && c != '\n' && c != '\t') {
                req->bad = 400;
                req->close_after = 1;
                *consumed = body_off;
                return LSHD_X_BAD;
            }
            if (c == 0x7f) {
                req->bad = 400;
                req->close_after = 1;
                *consumed = body_off;
                return LSHD_X_BAD;
            }
        }
    }

    /* ---- request line ---- */
    char *nl_mem = (char *)memchr(base, '\n', head_end);
    size_t line_len = nl_mem ? (size_t)(nl_mem - base) : head_end;
    if (line_len && base[line_len - 1] == '\r') line_len--;

    if (line_len == 0) {
        req->bad = 400;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    if (line_len >= LSHD_MAX_LINE_BYTES) {
        req->bad = 414;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }

    char rline_buf[LSHD_MAX_LINE_BYTES];
    memcpy(rline_buf, base, line_len);
    rline_buf[line_len] = '\0';
    char *sp = strchr(rline_buf, ' ');
    char *sp2 = sp ? strchr(sp + 1, ' ') : NULL;
    if (!sp || !sp2) {
        req->bad = 400;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    *sp = '\0';
    *sp2 = '\0';
    const char *method = rline_buf;
    const char *target = sp + 1;
    const char *ver = sp2 + 1;

    if (strlen(method) >= sizeof(req->method)) {
        req->bad = 501;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    strcpy(req->method, method);

    /* HTTP version must be HTTP/1.x — "1.0" and "1.1" are the only
     * materially different ones (keep-alive default); anything else is a
     * malformed request rather than a silent 1.1 guess. */
    if (strncmp(ver, "HTTP/1.", 7) != 0 || ver[7] < '0' || ver[7] > '9' || ver[8] != '\0') {
        req->bad = 400;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    req->http_minor = (ver[7] == '0') ? 0 : 1;

    /* absolute-form target → keep only the path+query part */
    if (strncmp(target, "http://", 7) == 0 || strncmp(target, "https://", 8) == 0) {
        const char *ps = strchr(target + (target[4] == 's' ? 8 : 7), '/');
        target = ps ? ps : "/";
    }
    if (strlen(target) >= sizeof(req->path)) {
        req->bad = 414;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }

    const char *qm = strchr(target, '?');
    if (qm) {
        size_t plen = (size_t)(qm - target);
        memcpy(req->path, target, plen);
        req->path[plen] = '\0';
        free(req->query);
        req->query = lshd_strndup_all(qm + 1, strlen(qm + 1));
        if (!req->query) goto oom;
    } else {
        strcpy(req->path, target);
    }

    /* ---- headers (LF-normalized, without the request line) ---- */
    size_t hdr_start = nl_mem ? (size_t)(nl_mem - base) + 1 : head_end;
    size_t raw_hdr_len = head_end >= hdr_start ? head_end - hdr_start : 0;
    free(req->headers);
    req->headers = (char *)malloc(raw_hdr_len + 1);
    if (!req->headers) goto oom;
    {
        size_t o = 0;
        size_t k;
        for (k = 0; k < raw_hdr_len; k++) {
            char cc = base[hdr_start + k];
            if (cc == '\r') continue;
            req->headers[o++] = cc;
        }
        req->headers[o] = '\0';
    }

    /* ---- framing + keep-alive decisions ---- */
    long long content_length = -1;
    long long prev_cl = -1;
    int saw_te = 0, saw_close = 0, saw_keep_alive = 0;
    {
        const char *p = req->headers;
        while (p && *p) {
            const char *nl = strchr(p, '\n');
            size_t ll = nl ? (size_t)(nl - p) : strlen(p);
            /* Every line must be `field-name ":" OWS value` with no
             * whitespace before the colon (RFC 9112 §5.1). "Content-Length :
             * 5" must not slip past as an unknown header and leave its body
             * to be parsed as the next request. */
            const char *colon = (const char *)memchr(p, ':', ll);
            if (!colon) {
                req->bad = 400;
                req->close_after = 1;
                *consumed = body_off;
                return LSHD_X_BAD;
            }
            if (colon > p && (colon[-1] == ' ' || colon[-1] == '\t')) {
                req->bad = 400;
                req->close_after = 1;
                *consumed = body_off;
                return LSHD_X_BAD;
            }
            if (lshd_line_is_header(p, ll, "Content-Length")) {
                const char *v = p + 15;
                while (*v == ' ' || *v == '\t') v++;
                if (*v < '0' || *v > '9') {
                    /* missing, negative ("+5"/"-0") or garbage value */
                    req->bad = 400;
                    req->close_after = 1;
                    *consumed = body_off;
                    return LSHD_X_BAD;
                }
                char *endp = NULL;
                long long cand = strtoll(v, &endp, 10);
                if (endp == v) {
                    req->bad = 400;
                    req->close_after = 1;
                    *consumed = body_off;
                    return LSHD_X_BAD;
                }
                if (endp) {
                    while (*endp == ' ' || *endp == '\t' || *endp == '\r') endp++;
                    if (*endp != '\0' && *endp != '\n') {
                        req->bad = 400;
                        req->close_after = 1;
                        *consumed = body_off;
                        return LSHD_X_BAD;
                    }
                }
                if (prev_cl >= 0 && cand != prev_cl) {
                    req->bad = 400;
                    req->close_after = 1;
                    *consumed = body_off;
                    return LSHD_X_BAD;
                }
                prev_cl = cand;
                content_length = cand;
            } else if (lshd_line_is_header(p, ll, "Transfer-Encoding")) {
                saw_te = 1;
            } else if (lshd_line_is_header(p, ll, "Connection")) {
                const char *v = p + 11;
                while (*v == ' ' || *v == '\t') v++;
                if (lshd_has_token(v, "close")) saw_close = 1;
                if (lshd_has_token(v, "keep-alive")) saw_keep_alive = 1;
            }
            p = nl ? nl + 1 : NULL;
        }
    }

    if (saw_te) {
        /* this server speaks exactly one request framing: Content-Length */
        req->bad = 501;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    /* content_length == -1 means "no Content-Length header" (GET etc.);
     * explicit negatives like "-5" were already rejected at the digit
     * gate above, so only the sentinel can be -1 here. */
    if (content_length < -1) {
        req->bad = 400;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }
    if (content_length > LSHD_MAX_BODY_BYTES) {
        req->bad = 413;
        req->close_after = 1;
        *consumed = body_off;
        return LSHD_X_BAD;
    }

    /* RFC 9110 keep-alive rules:
     *   HTTP/1.1 → keep-alive unless "Connection: close"
     *   HTTP/1.0 → close unless "Connection: keep-alive" */
    req->close_after = saw_close ||
                       (req->http_minor == 0 && !saw_keep_alive);

    /* ---- body present? ---- */
    size_t want_total = body_off +
                        (size_t)(content_length > 0 ? content_length : 0);
    if (len < want_total) {
        lshd_request_free(req);
        req->query = NULL;
        req->headers = NULL;
        req->body = NULL;
        return LSHD_X_INCOMPLETE;
    }

    free(req->body);
    if (content_length > 0) {
        req->body = lshd_strndup_all(base + body_off, (size_t)content_length);
        if (!req->body) goto oom;
    } else {
        req->body = lshd_strdup("");
        if (!req->body) goto oom;
    }

    *consumed = want_total;
    return LSHD_X_READY;

oom:
    lshd_request_free(req);
    req->query = NULL;
    req->headers = NULL;
    req->body = NULL;
    *consumed = c->acc.len;
    req->bad = 500;
    req->close_after = 1;
    return LSHD_X_BAD;
}

/* ------------------------------------------------------------------ */
/* Per-connection service                                              */
/* ------------------------------------------------------------------ */

/* Run the handler for a decoded request and write one response.
 * Returns 0 on OK; negative LSHD_E_* on I/O error.
 * Sets *stop_after when the handler asked the whole server to stop, and
 * *close_after when the connection must be dropped after this response. */
static int lshd_serve_ready(lshd_conn *c, lshd_request *req,
                            lshhttpd_handler_t handler,
                            int *stop_after, int *close_after) {
    *stop_after = 0;
    *close_after = req->close_after;

    if (req->bad != 0) {
        int status = req->bad;
        lshd_request_free(req);
        int wrc = lshd_write_response(c, status, "Content-Type: text/plain\n",
                                      "malformed request", 0, 1);
        if (wrc != 0) return wrc;
        int frc = lshd_conn_flush(c);
        return frc < 0 ? frc : 0;
    }

    /* Hand GC copies to the handler so they survive arbitrary use. */
    char *g_method = lshd_to_gc_string(req->method, 0);
    char *g_path   = lshd_to_gc_string(req->path, 0);
    char *g_query  = lshd_to_gc_string(req->query, 0);
    char *g_hdrs   = lshd_to_gc_string(req->headers, 0);
    char *g_body   = lshd_to_gc_string(req->body ? req->body : "", 0);
    if (!g_method || !g_path || !g_query || !g_hdrs || !g_body) {
        lshd_request_free(req);
        return LSHD_E_MEMORY;
    }

    lshd_resp_reset();
    int status = handler(g_method, g_path, g_query, g_hdrs, g_body);

    /* Snapshot the response buffers into NUL-terminated copies. */
    char *hdrbuf = (char *)malloc(g_resp_headers.len + 1);
    char *bodybuf = (char *)malloc(g_resp_body.len + 1);
    if (!hdrbuf || !bodybuf) {
        free(hdrbuf);
        free(bodybuf);
        lshd_request_free(req);
        return LSHD_E_MEMORY;
    }
    memcpy(hdrbuf, g_resp_headers.p ? g_resp_headers.p : "", g_resp_headers.len);
    hdrbuf[g_resp_headers.len] = '\0';
    memcpy(bodybuf, g_resp_body.p ? g_resp_body.p : "", g_resp_body.len);
    bodybuf[g_resp_body.len] = '\0';

    if (status < 200 || status > 599) status = 500;

    int head_only = lshd_is_head_method(req->method);
    int close_conn = req->close_after;
    lshd_request_free(req);
    int rc = lshd_write_response(c, status, hdrbuf, bodybuf,
                                 head_only, close_conn);
    free(hdrbuf);
    free(bodybuf);
    if (rc != 0) return rc;

    /* Try to drain the response right away — in the common case it fits
     * the kernel buffer and the write completes synchronously. Only a
     * slow reader leaves bytes buffered for the POLLOUT path. */
    int frc = lshd_conn_flush(c);
    if (frc < 0) return frc;

    if (lshd_stop_flag) *stop_after = 1;
    return 0;
}

/* Consume any complete requests sitting in a connection's accumulator.
 * ` eof` = peer already hung up (close when done). Returns:
 *    0 keep the connection open (incomplete tail stays for the next round)
 *    1 close the connection (only happens on successful paths)
 *    2 keep the connection open; the dispatch budget ran out and buffered
 *      pipelined requests are waiting for the next slice
 *   -1 close after a bad-request response
 *   <0 fatal I/O error — caller drops the connection and maybe stops */
static int lshd_conn_serve(lshd_conn *c, lshhttpd_handler_t handler,
                           int peer_eof, long long *served_delta,
                           long long max_requests, long long served_so_far,
                           int *must_stop) {
    /* One connection may carry a deep pipelined burst (up to the
     * accumulator cap). Dispatch a bounded number per poll slice so a
     * single client cannot starve the others; the rest stays buffered and
     * is served on the next slice (the caller marks the connection
     * `deferred` so it is revisited without waiting for socket data). */
    int dispatched = 0;
    for (;;) {
        if (dispatched >= LSHD_DISPATCH_BUDGET) {
            return (c->acc.len > 0) ? 2 : 0;
        }
        /* Cap check FIRST: once the budget is spent, no further request
         * may be extracted/dispatched from any connection. */
        if (max_requests >= 0 && served_so_far + *served_delta >= max_requests) {
            return 1;
        }
        size_t consumed = 0;
        lshd_request req;
        int xr = lshd_conn_extract(c, &req, &consumed);
        if (xr == LSHD_X_INCOMPLETE) break;

        if (consumed > 0) {
            memmove(c->acc.p, c->acc.p + consumed, c->acc.len - consumed);
            c->acc.len -= consumed;
            g_total_buf_bytes -= (long long)consumed;
            if (c->acc.len == 0) {
                c->req_start = 0; /* request boundary reached */
            }
        }

        if (xr == LSHD_X_BAD) {
            int bad_code = req.bad ? req.bad : 500;
            int wrc = lshd_write_response(c, bad_code, "Content-Type: text/plain\n",
                                          "malformed request",
                                          lshd_is_head_method(req.method), 1);
            lshd_request_free(&req);
            if (wrc == 0) lshd_conn_flush(c);
            return -1;
        }

        /* serve_ready() frees `req` on every path. */
        int stop_after = 0;
        int close_after = 0;
        int rc = lshd_serve_ready(c, &req, handler,
                                  &stop_after, &close_after);
        if (rc != 0) {
            return rc;
        }
        (*served_delta)++;
        dispatched++;
        if (c->out.len > c->out_off) {
            /* The response is still draining (slow reader): the connection
             * stays open and the close decision is remembered until the
             * write finishes — or the write deadline reaps it. The event
             * loop watches POLLOUT and keeps serving other connections
             * meanwhile. */
            c->out_close = close_after || peer_eof;
            if (stop_after) *must_stop = 1;
            return 0;
        }
        if (stop_after) {
            *must_stop = 1;
            return 1;
        }
        if (close_after) {
            return 1; /* client wanted Connection: close */
        }
        if (peer_eof && c->acc.len == 0) {
            return 1; /* peer finished and nothing is buffered */
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Serve loop + public API                                             */
/* ------------------------------------------------------------------ */

const char *lshhttpd_version(void) {
    return LSH_HTTPD_VERSION_STR;
}

void lshhttpd_shutdown(void) {
    lshd_stop_flag = 1;
    /* Wake a blocking accept()/poll() wait happening in serve(). */
    lsh_fd_t lfd = lshd_listen_fd;
    if (lfd != LSHD_INVALID_FD) {
#ifdef _WIN32
        shutdown(lfd, SD_BOTH);
#else
        shutdown(lfd, SHUT_RDWR);
#endif
    }
}

int lshhttpd_serve(int port, long long max_requests, lshhttpd_handler_t handler) {
    lshd_clear_detail();

    /* Re-entrancy guard first: a second serve() call (from a handler or
     * another thread) must not touch the live connection table. */
    if (lshd_running) {
        lshd_set_detail("serve() is not re-entrant — only one server at a time");
        return LSHD_E_ARGS;
    }
    if (!handler) {
        lshd_set_detail("no request handler provided");
        return LSHD_E_ARGS;
    }
    if (port <= 0 || port > 65535) {
        lshd_set_detail("invalid port");
        return LSHD_E_ARGS;
    }

    lshd_running = 1;
    lshd_stop_flag = 0;
    g_nconns = 0;
    g_total_buf_bytes = 0;

    long long served = 0;
    int rc = 0;
    int aborted = 0;
    int accepting = 1;
    int need_stop = 0;

#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        lshd_set_detail("WSAStartup failed");
        lshd_running = 0;
        return LSHD_E_SOCKET;
    }
#endif

    lsh_fd_t lfd = socket(AF_INET, SOCK_STREAM, 0);
    if (lfd == LSHD_INVALID_FD) {
        lshd_set_detail("socket creation failed");
#ifdef _WIN32
        WSACleanup();
#endif
        lshd_running = 0;
        return LSHD_E_SOCKET;
    }

    int one = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof(one));
#ifdef SO_NOSIGPIPE
    /* BSD/macOS: a write to a client that vanished must not raise SIGPIPE
     * and kill the process. The setting is inherited by accepted sockets.
     * Linux uses MSG_NOSIGNAL per send() instead. */
    setsockopt(lfd, SOL_SOCKET, SO_NOSIGPIPE, (const char *)&one, sizeof(one));
#endif

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((unsigned short)port);

    if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        lshd_set_detail("bind failed — is the port already in use?");
        lshd_close_fd(lfd);
#ifdef _WIN32
        WSACleanup();
#endif
        lshd_running = 0;
        return LSHD_E_BIND;
    }
    if (listen(lfd, 64) != 0) {
        lshd_set_detail("listen failed");
        lshd_close_fd(lfd);
#ifdef _WIN32
        WSACleanup();
#endif
        lshd_running = 0;
        return LSHD_E_LISTEN;
    }
    lshd_listen_fd = lfd;
    /* The multiplex loop drains the accept queue until would-block; the
     * listener must itself be non-blocking for that to terminate. */
#ifndef _WIN32
    {
        int lfl = fcntl(lfd, F_GETFL, 0);
        if (lfl >= 0) fcntl(lfd, F_SETFL, lfl | O_NONBLOCK);
    }
#else
    {
        u_long nbm = 1;
        ioctlsocket(lfd, FIONBIO, &nbm);
    }
#endif

    for (;;) {
        if (lshd_stop_flag) {
            if (served == 0) aborted = 1;
            break;
        }
        /* set by conn_serve() when a handler calls lshhttpd_shutdown() */
        if (need_stop) break;

        if (accepting && max_requests >= 0 && served >= max_requests) {
            accepting = 0;
            lshd_listen_fd = LSHD_INVALID_FD;
            lshd_close_fd(lfd);
        }
        if (!accepting && g_nconns == 0) break;

        /* --- poll everything at once ---
         * A zero timeout while `g_pending_work` is set: deferred pipelined
         * requests are buffered in userspace, so waiting for socket
         * readiness would strand them. */
#ifdef _WIN32
        fd_set rfds;
        FD_ZERO(&rfds);
        fd_set wfds;
        FD_ZERO(&wfds);
        if (accepting) {
            FD_SET(lfd, &rfds);
        }
        for (int i = 0; i < g_nconns; i++) {
            FD_SET(g_conns[i]->fd, &rfds);
            if (g_conns[i]->out.len > g_conns[i]->out_off) {
                FD_SET(g_conns[i]->fd, &wfds); /* response draining */
            }
        }
        struct timeval tv;
        if (g_pending_work) {
            tv.tv_sec = 0;
            tv.tv_usec = 0;
        } else {
            tv.tv_sec = LSHD_POLL_SLICE_MS / 1000;
            tv.tv_usec = (LSHD_POLL_SLICE_MS % 1000) * 1000;
        }
        int ready = select(0, &rfds, &wfds, NULL, &tv);
        if (ready == SOCKET_ERROR) {
            if (WSAGetLastError() == WSAEINTR) continue;
            if (lshd_stop_flag) break;
            rc = LSHD_E_ACCEPT;
            lshd_set_detail("select failed");
            break;
        }
        int listen_ready = accepting && ready > 0 && FD_ISSET(lfd, &rfds) != 0;
#else
        struct pollfd pfds_arr[LSHD_MAX_CONNS + 1];
        nfds_t nfds = 0;
        int p_listen = -1;
        if (accepting) {
            p_listen = 0;
            pfds_arr[nfds].fd = lfd;
            pfds_arr[nfds].events = POLLIN;
            pfds_arr[nfds].revents = 0;
            nfds++;
        }
        for (int i = 0; i < g_nconns; i++) {
            pfds_arr[nfds].fd = g_conns[i]->fd;
            pfds_arr[nfds].events = POLLIN;
            /* Watch POLLOUT on connections with a response still draining
             * so the event loop wakes to flush it without busy-spinning. */
            if (g_conns[i]->out.len > g_conns[i]->out_off) {
                pfds_arr[nfds].events |= POLLOUT;
            }
            pfds_arr[nfds].revents = 0;
            nfds++;
        }
        int slice_timeout = g_pending_work ? 0 : LSHD_POLL_SLICE_MS;
        int ready = poll(pfds_arr, nfds, slice_timeout);
        if (ready < 0) {
            if (errno == EINTR) continue;
            if (lshd_stop_flag) break;
            rc = LSHD_E_ACCEPT;
            lshd_set_detail("poll failed");
            break;
        }
        int listen_ready = accepting && ready > 0 &&
                           (pfds_arr[p_listen].revents & (POLLIN | POLLERR | POLLHUP));
#endif

        /* --- accept all pending connections --- */
        if (listen_ready) {
            for (;;) {
                struct sockaddr_in peer;
                int peer_len = sizeof(peer);
                lsh_fd_t client = accept(lfd, (struct sockaddr *)&peer,
#ifdef _WIN32
                                         (int *)&peer_len
#else
                                         (socklen_t *)&peer_len
#endif
                                         );
                if (client == LSHD_INVALID_FD) {
#ifdef _WIN32
                    int aerr = WSAGetLastError();
                    if (aerr == WSAEINTR || aerr == WSAENOTSOCK ||
                        aerr == WSAEWOULDBLOCK) { }
                    break;
#else
                    if (errno == EINTR || errno == EINVAL || errno == EBADF ||
                        errno == EAGAIN || errno == EWOULDBLOCK) { }
                    break;
#endif
                }
                if (g_nconns >= LSHD_MAX_CONNS) {
                    lshd_close_fd(client);
                    continue;
                }
                /* NOTE: client sockets are non-blocking; the I/O deadline
                 * is enforced by poll()/select() waits, not SO_*TIMEO. */
#ifndef _WIN32
                {
                    int fl = fcntl(client, F_GETFL, 0);
                    if (fl >= 0) fcntl(client, F_SETFL, fl | O_NONBLOCK);
                }
#else
                {
                    u_long nb = 1;
                    ioctlsocket(client, FIONBIO, &nb);
                }
#endif
                int one_tcp = 1;
                setsockopt(client, IPPROTO_TCP, TCP_NODELAY,
                           (const char *)&one_tcp, sizeof(one_tcp));
                lshd_conn *cc = (lshd_conn *)calloc(1, sizeof(lshd_conn));
                if (!cc) {
                    lshd_close_fd(client);
                    continue;
                }
                buf_init(&cc->acc);
                buf_init(&cc->out);
                cc->fd = client;
                cc->last_ms = lshd_now_ms();
                cc->req_start = 0;
                cc->has_bytes = 0;
                g_conns[g_nconns++] = cc;
            }
        }

        /* --- serve + drain live connections --- */
        g_pending_work = 0;
        for (int i = 0; i < g_nconns; i++) {
            lshd_conn *cc = g_conns[i];

            /* --- 1. drain pending response bytes (non-blocking write) ---
             * A slow reader no longer blocks the loop: while a response is
             * buffered the event loop polls POLLOUT and flushes it here,
             * while other connections keep being served. Reading from this
             * connection is paused until the write drains. */
            if (cc->out.len > cc->out_off) {
                int write_ready;
#ifdef _WIN32
                write_ready = ready > 0 && FD_ISSET(cc->fd, &wfds) != 0;
#else
                write_ready = 0;
                for (nfds_t j = 0; j < nfds; j++) {
                    if (pfds_arr[j].fd == cc->fd &&
                        (pfds_arr[j].revents & (POLLOUT | POLLERR | POLLHUP)) != 0) {
                        write_ready = 1;
                        break;
                    }
                }
#endif
                if (write_ready) {
                    int frc = lshd_conn_flush(cc);
                    if (frc < 0) {
                        lshd_conn_drop(i);
                        i--;
                        continue;
                    }
                    if (frc == 1 && cc->out_close) {
                        /* Fully drained and the connection was marked for
                         * closing (Connection: close / peer EOF). */
                        lshd_conn_drop(i);
                        i--;
                        continue;
                    }
                }
                if (cc->out.len > cc->out_off) {
                    /* Still draining: skip reading from this connection
                     * until the write finishes. poll() watches POLLOUT; a
                     * stalled reader is reaped by the write deadline in the
                     * sweep below. */
                    continue;
                }
            }

            /* --- 2. read + serve as before --- */
            int live_ready;
#ifdef _WIN32
            live_ready = cc->deferred || (ready > 0 && FD_ISSET(cc->fd, &rfds) != 0);
#else
            live_ready = cc->deferred ? 1 : 0;
            for (nfds_t j = 0; !live_ready && j < nfds; j++) {
                if (pfds_arr[j].fd == cc->fd &&
                    (pfds_arr[j].revents & (POLLIN | POLLERR | POLLHUP)) != 0) {
                    live_ready = 1;
                    break;
                }
            }
#endif
            if (!live_ready) continue;

            int rc_read = lshd_conn_read_avail(cc);
            int peer_eof = (rc_read == 1);
            if (rc_read < 0) {
                if (rc_read == LSHD_E_MEMORY) { rc = LSHD_E_MEMORY; break; }
                lshd_conn_drop(i);
                i--;
                continue;
            }

            long long delta = 0;
            int svc = lshd_conn_serve(cc, handler, peer_eof, &delta,
                                      max_requests, served, &need_stop);
            served += delta;
            if (need_stop) { lshd_stop_flag = 1; }
            if (rc != 0) break;
            if (svc == 2) {
                /* Budget spent with a pipelined backlog left: revisit this
                 * connection on the next slice without waiting for I/O. */
                cc->deferred = 1;
                g_pending_work = 1;
                continue;
            }
            cc->deferred = 0;
            /* drop-or-keep decision. A peer EOF with a response still
             * draining keeps the connection until the write finishes
             * (conn_serve remembered it in out_close). */
            if (svc != 0 || (peer_eof && cc->out.len <= cc->out_off)) {
                lshd_conn_drop(i);
                i--;
                continue;
            }
        }
        if (rc != 0) break;
        if (need_stop) break;

        /* idle sweep: kill keep-alive zombies */
        long long now = lshd_now_ms();
        for (int i = 0; i < g_nconns; i++) {
            lshd_conn *cc = g_conns[i];
            if (cc->out.len > cc->out_off) {
                /* mid-response write: fixed deadline from when the response
                 * began (a stalled reader cannot outlive it) */
                if (now - cc->out_start > LSHD_IO_TIMEOUT_MS) {
                    lshd_conn_drop(i);
                    i--;
                }
                continue;
            }
            if (cc->acc.len > 0 && cc->req_start > 0) {
                /* mid-request: fixed deadline from when the request began
                 * (a 1-byte-per-29s trickle cannot outlive it) */
                if (now - cc->req_start > LSHD_SLOW_REQ_MS) {
                    lshd_conn_drop(i);
                    i--;
                }
                continue;
            }
            if (now - cc->last_ms > LSHD_IDLE_TIMEOUT_MS) {
                lshd_conn_drop(i);
                i--;
            }
        }
    }

    /* Best-effort drain of pending response bytes before the connections
     * close: shutdown()/max_requests semantics promise the in-flight
     * response completes. Bounded by the I/O deadline so a wedged peer
     * cannot hold the exit hostage. */
    for (int i = 0; i < g_nconns; i++) {
        lshd_conn *cc = g_conns[i];
        long long deadline = lshd_now_ms() + LSHD_IO_TIMEOUT_MS;
        while (cc->out.len > cc->out_off && lshd_now_ms() < deadline) {
            if (lshd_conn_flush(cc) != 0) break;   /* 1 = done, <0 = error */
            lshd_wait_writable(cc->fd, 100);       /* short writability wait */
        }
    }

    /* close all live connections (their in-flight response already written) */
    for (int i = 0; i < g_nconns; i++) lshd_conn_free(g_conns[i]);
    g_nconns = 0;

    if (accepting) {
        lshd_listen_fd = LSHD_INVALID_FD;
        lshd_close_fd(lfd);
    }
#ifdef _WIN32
    WSACleanup();
#endif
    lshd_running = 0;

    if (aborted) return LSHD_E_ABORTED;
    if (rc != 0) return rc;
    return (int)served;
}

const char *lshhttpd_strerror(int code) {
    const char *base;
    switch (code) {
    case LSHD_E_ARGS:    base = "invalid arguments"; break;
    case LSHD_E_SOCKET: base = "could not create socket"; break;
    case LSHD_E_BIND:   base = "could not bind the port"; break;
    case LSHD_E_LISTEN: base = "could not listen on the port"; break;
    case LSHD_E_ACCEPT: base = "connection error"; break;
    case LSHD_E_MEMORY: base = "out of memory or size limit reached"; break;
    case LSHD_E_ABORTED: base = "server stopped before serving any request"; break;
    default:
        if (code >= 0) return "no error";
        base = "unknown error";
        break;
    }
    char msg[512];
    if (lshd_detail[0]) {
        snprintf(msg, sizeof(msg), "%s (%s)", base, lshd_detail);
    } else {
        snprintf(msg, sizeof(msg), "%s", base);
    }
    char *out = (char *)leash_gc_alloc_string((long long)strlen(msg));
    if (out) strcpy(out, msg);
    return out;
}
