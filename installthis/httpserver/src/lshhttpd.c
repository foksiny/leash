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

#ifndef _WIN32
# include <openssl/ssl.h>
# include <openssl/err.h>
# include <signal.h>
#endif

#include "lshhpack.h"

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

#define LSH_HTTPD_VERSION_STR "leash-httpd/1.2"
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
    LSHD_E_ABORTED = -7,  /* stop() requested before serving any request    */
    LSHD_E_TLS     = -8   /* TLS setup/handshake failure (POSIX only)       */
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

/* SIGPIPE immunity lives in the Leash runtime (leash_spipe_hold/drop in
 * gc.c) so all shims share one process-global refcount. */
void leash_spipe_hold(void);
void leash_spipe_drop(void);
#define lshd_spipe_hold leash_spipe_hold
#define lshd_spipe_drop leash_spipe_drop

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
    void     *tls;         /* SSL* when serving TLS, else NULL (POSIX only) */
    int       tls_hs;      /* TLS handshake still in progress                */
    int       is_h2;       /* HTTP/2 framing active on this connection       */
    void     *h2;          /* lshd_h2ctx* when is_h2                         */
    int       want_out;    /* poll POLLOUT interest (h2/handshake/TLS)       */
    lshd_buf  acc;         /* request-byte accumulator (pipelining-friendly) */
    lshd_buf  out;         /* pending response bytes (non-blocking write)    */
    size_t    out_off;     /* bytes of `out` already written to the socket   */
    long long out_start;   /* when the CURRENT pending response began        */
    int       out_close;   /* drop the connection once `out` fully drains    */
    long long last_ms;     /* timestamp of the most recent accepted byte     */
    long long req_start;   /* when the CURRENT (incomplete) request began   */
    int       has_bytes;   /* seen any bytes on this connection yet          */
    int       deferred;    /* pipelined requests left for the next slice     */
    int       h2checked;   /* protocol decided (h2 promoted or 1.1 pinned)    */
    size_t    h2_out_off;  /* flush offset for the h2 output buffer          */
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
        if (c->tls) {
            int r = SSL_write((SSL *)c->tls, c->out.p + c->out_off,
                              (int)(c->out.len - c->out_off));
            if (r > 0) {
                c->out_off += (size_t)r;
                c->last_ms = lshd_now_ms();
                continue;
            }
            int e = SSL_get_error((SSL *)c->tls, r);
            if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) {
                c->want_out = 1;
                break;
            }
            lshd_set_detail("TLS write failed");
            return LSHD_E_ACCEPT;
        }
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
        if (c->tls) {
            int r = SSL_read((SSL *)c->tls, chunk, (int)sizeof(chunk));
            if (r > 0) {
                if (!buf_append(&c->acc, chunk, (size_t)r))
                    return LSHD_E_MEMORY;
                g_total_buf_bytes += (long long)r;
                c->last_ms = lshd_now_ms();
                c->has_bytes = 1;
                pulled += (size_t)r;
                continue;
            }
            if (r == 0) return 1;
            int e = SSL_get_error((SSL *)c->tls, r);
            if (e == SSL_ERROR_ZERO_RETURN) return 1;
            if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) {
                c->want_out = 1;
                return 0;
            }
            lshd_set_detail("TLS read failed");
            return LSHD_E_ACCEPT;
        }
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

/* Forward declarations (defined with the H2 engine below). */
static void lshd_conn_free(lshd_conn *c);
static int h2d_maybe_promote(lshd_conn *c);
typedef struct lshd_h2ctx lshd_h2ctx;
static int h2d_pump(lshd_conn *c, lshd_h2ctx *h, lshhttpd_handler_t handler,
                    long long *served_delta, long long max_requests,
                    long long served_so_far, int *must_stop);

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
static int lshd_call_handler(lshhttpd_handler_t handler,
                              const char *method, const char *path,
                              const char *query, const char *hdrs,
                              const char *body, int *status_out,
                              char **hdr_out, char **body_out);

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

    int status = 0;
    char *hdrbuf = NULL;
    char *bodybuf = NULL;
    int drc = lshd_call_handler(handler, req->method, req->path, req->query,
                                req->headers, req->body ? req->body : "",
                                &status, &hdrbuf, &bodybuf);
    if (drc != 0) {
        lshd_request_free(req);
        return drc;
    }

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
    /* HTTP/2 upgrade check first (cleartext preface or TLS+preface). */
    if (!c->is_h2 && !c->h2checked && c->tls_hs == 0 && c->acc.len > 0) {
        int pr = h2d_maybe_promote(c);
        if (pr < 0) return pr;
        if (!c->is_h2 && !c->h2checked) return 0; /* need more bytes */
    }
    if (c->is_h2) {
        lshd_h2ctx *h = (lshd_h2ctx *)c->h2;
        int rc = h2d_pump(c, h, handler, served_delta, max_requests,
                          served_so_far, must_stop);
        if (rc == 1) return 1; /* EOF-drained: close */
        return rc;
    }
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
/* HTTP/2 + TLS serving (h2c prior knowledge, h2 over TLS via ALPN).    */
/* Cleartext H2 works on every platform; TLS serving is POSIX-only.     */
/* ------------------------------------------------------------------ */

#ifndef _WIN32
static SSL_CTX *g_tls_ctx = NULL;
static int g_tls_mode = 0;

/* ALPN callback: serve h2 when the client offers it, else http/1.1. */
static int lshd_alpn_cb(SSL *ssl, const unsigned char **out,
                        unsigned char *outlen, const unsigned char *in,
                        unsigned int inlen, void *arg) {
    (void)ssl;
    (void)arg;
    for (unsigned int i = 0; i < inlen;) {
        unsigned int len = in[i];
        if (len + 1 > inlen - i) break;
        if (len == 2 && memcmp(in + i + 1, "h2", 2) == 0) {
            *out = in + i + 1;
            *outlen = 2;
            return SSL_TLSEXT_ERR_OK;
        }
        i += len + 1;
    }
    static const unsigned char http11[] = "http/1.1";
    *out = http11;
    *outlen = 8;
    return SSL_TLSEXT_ERR_OK;
}
#endif

/* Call the Leash handler with GC-owned strings; snapshot the response.
 * Shared by the HTTP/1.1 and HTTP/2 dispatch paths. Returns 0 with
 * status/hdr/body out-params set (malloc'd, caller frees), or a
 * negative LSHD_E_* code. */
static int lshd_call_handler(lshhttpd_handler_t handler,
                              const char *method, const char *path,
                              const char *query, const char *hdrs,
                              const char *body, int *status_out,
                              char **hdr_out, char **body_out) {
    char *g_method = lshd_to_gc_string(method, 0);
    char *g_path = lshd_to_gc_string(path, 0);
    char *g_query = lshd_to_gc_string(query, 0);
    char *g_hdrs = lshd_to_gc_string(hdrs, 0);
    char *g_body = lshd_to_gc_string(body ? body : "", 0);
    if (!g_method || !g_path || !g_query || !g_hdrs || !g_body) {
        return LSHD_E_MEMORY;
    }
    lshd_resp_reset();
    int status = handler(g_method, g_path, g_query, g_hdrs, g_body);
    char *hdrbuf = (char *)malloc(g_resp_headers.len + 1);
    char *bodybuf = (char *)malloc(g_resp_body.len + 1);
    if (!hdrbuf || !bodybuf) {
        free(hdrbuf);
        free(bodybuf);
        return LSHD_E_MEMORY;
    }
    memcpy(hdrbuf, g_resp_headers.p ? g_resp_headers.p : "",
           g_resp_headers.len);
    hdrbuf[g_resp_headers.len] = '\0';
    memcpy(bodybuf, g_resp_body.p ? g_resp_body.p : "", g_resp_body.len);
    bodybuf[g_resp_body.len] = '\0';
    if (status < 200 || status > 599) status = 500;
    *status_out = status;
    *hdr_out = hdrbuf;
    *body_out = bodybuf;
    return 0;
}

/* ------------------------------------------------------------------ */
/* HTTP/2 connection engine (server side).                               */
/* Sequential-friendly but stream-correct: up to H2D_MAX_STREAMS open    */
/* streams per connection, each dispatched when its END_STREAM arrives. */
/* ------------------------------------------------------------------ */

#define H2D_FTYPE_DATA 0
#define H2D_FTYPE_HEADERS 1
#define H2D_FTYPE_PRIORITY 2
#define H2D_FTYPE_RST 3
#define H2D_FTYPE_SETTINGS 4
#define H2D_FTYPE_PUSH 5
#define H2D_FTYPE_PING 6
#define H2D_FTYPE_GOAWAY 7
#define H2D_FTYPE_WINDOW 8
#define H2D_FTYPE_CONT 9

#define H2D_FLAG_END_STREAM 0x1
#define H2D_FLAG_END_HEADERS 0x4
#define H2D_FLAG_ACK 0x1
#define H2D_FLAG_PADDED 0x8
#define H2D_FLAG_PRIORITY 0x20

#define H2D_MAX_STREAMS 16
#define H2D_MAX_HDR_BYTES (64 * 1024)
#define H2D_MAX_OUT (16 * 1024 * 1024)
#define H2D_PUMP_FRAMES 32   /* frames processed per poll slice */
#define H2D_PUMP_DISPATCH 8  /* handlers run per poll slice */

typedef struct {
    unsigned int id;
    int used;
    int headers_done;
    lshd_buf hblk;      /* HEADERS/CONTINUATION accumulator */
    int in_cont;        /* inside a continuation run */
    lshd_buf data;      /* DATA accumulator */
    int recv_window;    /* our receive window top-ups */
    int send_window;    /* peer's window for our response */
    char *method;
    char *path;
    char *query;
    lshd_buf hdrs;      /* LF lines for the handler (built at dispatch) */
    int has_host;
} h2d_stream;

typedef struct lshd_h2ctx {
    lshd_buf in;         /* raw inbound bytes */
    lshd_buf out;        /* raw outbound bytes */
    int got_client_settings;
    unsigned int peer_max_frame;  /* default 16384 */
    unsigned int init_window;     /* peer SETTINGS initial window */
    int conn_send_window;         /* connection-level send window */
    int conn_recv_window;         /* connection-level receive window */
    hpk_dyn dyn;                  /* HPACK decode table */
    hpk_hnode ht[600];
    h2d_stream streams[H2D_MAX_STREAMS];
    unsigned int last_stream;     /* highest stream id seen */
    int peer_goaway;              /* client sent GOAWAY */
    int goaway_sent;
    int closing;                  /* GOAWAY queued: drain, no new streams */
} lshd_h2ctx;

static void h2d_stream_clear(h2d_stream *st) {
    buf_free(&st->hblk);
    buf_free(&st->data);
    buf_free(&st->hdrs);
    free(st->method);
    free(st->path);
    free(st->query);
    memset(st, 0, sizeof(*st));
    buf_init(&st->hblk);
    buf_init(&st->data);
    buf_init(&st->hdrs);
}

static lshd_h2ctx *h2d_ctx_new(void) {
    lshd_h2ctx *h = (lshd_h2ctx *)calloc(1, sizeof(lshd_h2ctx));
    if (!h) return NULL;
    buf_init(&h->in);
    buf_init(&h->out);
    h->peer_max_frame = 16384;
    h->init_window = 65535;
    h->conn_send_window = 65535;
    h->conn_recv_window = 65535;
    hpk_dyn_init(&h->dyn, 4096);
    hpk_huff_build(h->ht);
    for (int i = 0; i < H2D_MAX_STREAMS; i++) {
        buf_init(&h->streams[i].hblk);
        buf_init(&h->streams[i].data);
        buf_init(&h->streams[i].hdrs);
        h->streams[i].recv_window = 65535;
        h->streams[i].send_window = 65535;
    }
    return h;
}

static void h2d_ctx_free(lshd_h2ctx *h) {
    if (!h) return;
    buf_free(&h->in);
    buf_free(&h->out);
    hpk_dyn_free(&h->dyn);
    for (int i = 0; i < H2D_MAX_STREAMS; i++) {
        buf_free(&h->streams[i].hblk);
        buf_free(&h->streams[i].data);
        buf_free(&h->streams[i].hdrs);
        free(h->streams[i].method);
        free(h->streams[i].path);
        free(h->streams[i].query);
    }
    free(h);
}

static h2d_stream *h2d_find_stream(lshd_h2ctx *h, unsigned int id) {
    for (int i = 0; i < H2D_MAX_STREAMS; i++) {
        if (h->streams[i].used && h->streams[i].id == id)
            return &h->streams[i];
    }
    return NULL;
}

/* TLS-aware raw send/recv for the h2 engine. Returns >0 bytes, 0 for
 * would-block, negative LSHD_E_* on hard errors. */
static int h2d_raw_send(lshd_conn *c, const char *buf, size_t len) {
#ifndef _WIN32
    if (c->tls) {
        SSL *ssl = (SSL *)c->tls;
        int r = SSL_write(ssl, buf, (int)len);
        if (r > 0) return r;
        int e = SSL_get_error(ssl, r);
        if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) {
            c->want_out = 1;
            return 0;
        }
        lshd_set_detail("TLS write failed");
        return LSHD_E_ACCEPT;
    }
#endif
#ifdef _WIN32
    int n = send(c->fd, buf, (int)len, 0);
    if (n > 0) return n;
    int e = WSAGetLastError();
    if (e == WSAEWOULDBLOCK || e == WSAEINTR) return 0;
#else
    ssize_t n = send(c->fd, buf, len, MSG_NOSIGNAL);
    if (n > 0) return (int)n;
    if (errno == EINTR) return 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
#endif
    lshd_set_detail("send failed");
    return LSHD_E_ACCEPT;
}

static int h2d_raw_recv(lshd_conn *c, char *buf, size_t len) {
#ifndef _WIN32
    if (c->tls) {
        SSL *ssl = (SSL *)c->tls;
        int r = SSL_read(ssl, buf, (int)len);
        if (r > 0) return r;
        if (r == 0) return -100; /* TLS close-notify: treat as EOF */
        int e = SSL_get_error(ssl, r);
        if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) {
            c->want_out = 1;
            return 0;
        }
        if (e == SSL_ERROR_ZERO_RETURN) return -100;
        lshd_set_detail("TLS read failed");
        return LSHD_E_ACCEPT;
    }
#endif
#ifdef _WIN32
    int n = recv(c->fd, buf, (int)len, 0);
    if (n > 0) return n;
    if (n == 0) return -100; /* EOF marker distinct from would-block */
    int e = WSAGetLastError();
    if (e == WSAEWOULDBLOCK || e == WSAEINTR) return 0;
#else
    ssize_t n = recv(c->fd, buf, len, 0);
    if (n > 0) return (int)n;
    if (n == 0) return -100;
    if (errno == EINTR) return 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
#endif
    lshd_set_detail("recv failed");
    return LSHD_E_ACCEPT;
}

/* Queue raw bytes for sending (buffered, non-blocking discipline). */
static int h2d_queue(lshd_conn *c, lshd_h2ctx *h, const void *data,
                     size_t n) {
    if (h->out.len + n > H2D_MAX_OUT) {
        lshd_set_detail("h2 response too large for slow reader");
        return LSHD_E_MEMORY;
    }
    size_t before = h->out.len;
    if (!buf_append(&h->out, (const char *)data, n)) return LSHD_E_MEMORY;
    g_total_buf_bytes += (long long)(h->out.len - before);
    c->want_out = 1;
    return 0;
}

static int h2d_send_frame(lshd_conn *c, lshd_h2ctx *h, unsigned char type,
                          unsigned char flags, unsigned int sid,
                          const unsigned char *payload, unsigned int len) {
    unsigned char hdr[9];
    hdr[0] = (unsigned char)((len >> 16) & 0xff);
    hdr[1] = (unsigned char)((len >> 8) & 0xff);
    hdr[2] = (unsigned char)(len & 0xff);
    hdr[3] = type;
    hdr[4] = flags;
    hdr[5] = (unsigned char)((sid >> 24) & 0x7f);
    hdr[6] = (unsigned char)((sid >> 16) & 0xff);
    hdr[7] = (unsigned char)((sid >> 8) & 0xff);
    hdr[8] = (unsigned char)(sid & 0xff);
    if (h2d_queue(c, h, hdr, 9) != 0) return LSHD_E_MEMORY;
    if (len > 0 && h2d_queue(c, h, payload, len) != 0) return LSHD_E_MEMORY;
    return 0;
}

/* Flush queued h2 bytes. Returns 1 drained, 0 would-block, <0 fatal. */
static int h2d_flush(lshd_conn *c, lshd_h2ctx *h, size_t *off) {
    while (*off < h->out.len) {
        int n = h2d_raw_send(c, h->out.p + *off, h->out.len - *off);
        if (n > 0) {
            *off += (size_t)n;
            c->last_ms = lshd_now_ms();
            g_total_buf_bytes -= (long long)n;
            continue;
        }
        if (n == 0) {
            c->want_out = 1;
            return 0;
        }
        return n;
    }
    if (h->out.len > 0) {
        buf_free(&h->out);
        buf_init(&h->out);
        *off = 0;
    }
    c->want_out = 0;
    return 1;
}

static int h2d_send_rst(lshd_conn *c, lshd_h2ctx *h, unsigned int sid,
                        unsigned int code) {
    unsigned char p[4];
    p[0] = (unsigned char)((code >> 24) & 0xff);
    p[1] = (unsigned char)((code >> 16) & 0xff);
    p[2] = (unsigned char)((code >> 8) & 0xff);
    p[3] = (unsigned char)(code & 0xff);
    return h2d_send_frame(c, h, H2D_FTYPE_RST, 0, sid, p, 4);
}

static int h2d_send_window(lshd_conn *c, lshd_h2ctx *h, unsigned int sid,
                           unsigned int incr) {
    unsigned char p[4];
    p[0] = (unsigned char)((incr >> 24) & 0x7f);
    p[1] = (unsigned char)((incr >> 16) & 0xff);
    p[2] = (unsigned char)((incr >> 8) & 0xff);
    p[3] = (unsigned char)(incr & 0xff);
    return h2d_send_frame(c, h, H2D_FTYPE_WINDOW, 0, sid, p, 4);
}

/* Decode a completed header block into stream fields. Returns 0 ok,
 * <0 stream error (caller sends RST_STREAM). */
static int h2d_decode_headers(lshd_h2ctx *h, h2d_stream *st) {
    hpk_hdrs hdrs;
    hpk_hdrs_init(&hdrs);
    if (!hpk_decode_block((const unsigned char *)st->hblk.p, st->hblk.len,
                          &h->dyn, h->ht, &hdrs)) {
        hpk_hdrs_free(&hdrs);
        return -1;
    }
    for (int i = 0; i < hdrs.n; i++) {
        const char *nm = hdrs.v[i].name;
        const char *vv = hdrs.v[i].value;
        if (nm[0] == ':') {
            if (strcmp(nm, ":method") == 0) {
                free(st->method);
                st->method = lshd_strndup_all(vv, strlen(vv));
            } else if (strcmp(nm, ":path") == 0) {
                /* split path?query */
                const char *qm = strchr(vv, '?');
                free(st->path);
                free(st->query);
                if (qm) {
                    st->path = lshd_strndup_all(vv, (size_t)(qm - vv));
                    st->query = lshd_strndup_all(qm + 1, strlen(qm + 1));
                } else {
                    st->path = lshd_strndup_all(vv, strlen(vv));
                    st->query = lshd_strndup_all("", 0);
                }
            } else if (strcmp(nm, ":authority") == 0) {
                if (!st->has_host) {
                    buf_append(&st->hdrs, "host: ", 6);
                    buf_append(&st->hdrs, vv, strlen(vv));
                    buf_append(&st->hdrs, "\n", 1);
                    st->has_host = 1;
                }
            }
            /* :scheme ignored (cleartext vs TLS is transport, not the
             * handler's business). */
            continue;
        }
        /* Connection-specific fields have no h2 meaning; drop them so
         * handlers see the same shape over both protocols. */
        if (strcmp(nm, "connection") == 0 || strcmp(nm, "keep-alive") == 0 ||
            strcmp(nm, "upgrade") == 0 || strcmp(nm, "transfer-encoding") == 0 ||
            strcmp(nm, "te") == 0) {
            continue;
        }
        if (strcmp(nm, "host") == 0) st->has_host = 1;
        buf_append(&st->hdrs, nm, strlen(nm));
        buf_append(&st->hdrs, ": ", 2);
        buf_append(&st->hdrs, vv, strlen(vv));
        buf_append(&st->hdrs, "\n", 1);
    }
    hpk_hdrs_free(&hdrs);
    if (!st->method || !st->path) return -1;
    return 0;
}

/* Encode + queue a response HEADERS [+DATA]. HEAD requests and 1xx/204/304
 * carry no DATA (END_STREAM rides on HEADERS). */
static int h2d_respond(lshd_conn *c, lshd_h2ctx *h, unsigned int sid,
                       int status, const char *hdr, const char *body,
                       int head_only) {
    hpk_buf blk;
    blk.p = NULL;
    blk.len = 0;
    blk.cap = 0;
    /* :status via indexed static when possible, else literal. */
    int sidx = 0;
    if (status == 200) sidx = 8;
    else if (status == 204) sidx = 9;
    else if (status == 206) sidx = 10;
    else if (status == 304) sidx = 11;
    else if (status == 400) sidx = 12;
    else if (status == 404) sidx = 13;
    else if (status == 500) sidx = 14;
    if (sidx) {
        if (!hpk_put_int(&blk, (unsigned)sidx, 0x80, 7)) goto oom;
    } else {
        char scode[16];
        snprintf(scode, sizeof(scode), "%d", status);
        if (!hpk_enc_field(&blk, 8, NULL, scode)) goto oom;
    }
    size_t body_len = body ? strlen(body) : 0;
    /* content-length first (helps HEAD + clients that want it). */
    {
        char cl[32];
        snprintf(cl, sizeof(cl), "%llu", (unsigned long long)body_len);
        if (!hpk_enc_field(&blk, 28, NULL, cl)) goto oom;
    }
    /* server identity, mirroring the 1.1 Server line. */
    if (!hpk_enc_field(&blk, 54, NULL, LSH_HTTPD_VERSION_STR)) goto oom;
    if (hdr && hdr[0]) {
        const char *p = hdr;
        while (p && *p) {
            const char *eol = strchr(p, '\n');
            size_t ll = eol ? (size_t)(eol - p) : strlen(p);
            while (ll > 0 && (p[ll-1] == '\r' || p[ll-1] == ' ' ||
                              p[ll-1] == '\t')) ll--;
            if (ll > 0) {
                char line[4096];
                if (ll >= sizeof(line)) {
                    p = eol ? eol + 1 : NULL;
                    continue;
                }
                memcpy(line, p, ll);
                line[ll] = '\0';
                char *colon = strchr(line, ':');
                if (colon && colon != line && !lshd_header_unsafe(line, ll)) {
                    *colon = '\0';
                    char *val = colon + 1;
                    while (*val == ' ' || *val == '\t') val++;
                    for (char *q = line; *q; q++) {
                        if (*q >= 'A' && *q <= 'Z')
                            *q = (char)(*q + ('a' - 'A'));
                    }
                    if (strcmp(line, "content-length") != 0 &&
                        strcmp(line, "connection") != 0 &&
                        strcmp(line, "keep-alive") != 0 &&
                        strcmp(line, "transfer-encoding") != 0 &&
                        strcmp(line, "upgrade") != 0 &&
                        strcmp(line, "server") != 0) {
                        int exact = 0;
                        unsigned int si = hpk_static_find(line, val, &exact);
                        if (exact) {
                            if (!hpk_put_int(&blk, si, 0x80, 7)) goto oom;
                        } else {
                            if (!hpk_enc_field(&blk, si, si ? NULL : line,
                                               val)) goto oom;
                        }
                    }
                }
            }
            p = eol ? eol + 1 : NULL;
        }
    }
    {
        /* Queue HEADERS, chunked at peer_max_frame with CONTINUATION. */
        size_t off = 0;
        int first = 1;
        int no_body = head_only || body_len == 0 || status == 204 ||
                      status == 304 || (status >= 100 && status < 200);
        while (off < blk.len) {
            size_t chunk = blk.len - off;
            if (chunk > h->peer_max_frame) chunk = h->peer_max_frame;
            unsigned char flags = 0;
            if (off + chunk >= blk.len) flags |= H2D_FLAG_END_HEADERS;
            if (first && no_body && (flags & H2D_FLAG_END_HEADERS))
                flags |= H2D_FLAG_END_STREAM;
            int rc;
            if (first) {
                rc = h2d_send_frame(c, h, H2D_FTYPE_HEADERS, flags, sid,
                                    blk.p + off, (unsigned)chunk);
            } else {
                rc = h2d_send_frame(c, h, H2D_FTYPE_CONT, flags, sid,
                                    blk.p + off, (unsigned)chunk);
            }
            if (rc != 0) {
                free(blk.p);
                return rc;
            }
            off += chunk;
            first = 0;
        }
        free(blk.p);
        if (no_body) return 0;
        /* DATA chunked at peer_max_frame; stream/connection windows are
         * 64K+ by default and responses under the per-conn cap fit, but
         * hold politely if the peer shrank its window. */
        size_t sent = 0;
        while (sent < body_len) {
            size_t chunk = body_len - sent;
            if (chunk > h->peer_max_frame) chunk = h->peer_max_frame;
            h2d_stream *st = h2d_find_stream(h, sid);
            int sw = st ? st->send_window : 65535;
            if ((int)chunk > h->conn_send_window ||
                (int)chunk > sw) {
                /* Window exhausted: leave the rest queued at the stream
                 * level is complex; responses here are < 16MB and peers
                 * open 64K+ windows, so treat exhaustion as fatal. */
                lshd_set_detail("h2 send window exhausted");
                return LSHD_E_ACCEPT;
            }
            unsigned char flags =
                (sent + chunk >= body_len) ? H2D_FLAG_END_STREAM : 0;
            int rc = h2d_send_frame(c, h, H2D_FTYPE_DATA, flags, sid,
                                    (const unsigned char *)body + sent,
                                    (unsigned)chunk);
            if (rc != 0) return rc;
            sent += chunk;
            h->conn_send_window -= (int)chunk;
            if (st) st->send_window -= (int)chunk;
        }
    }
    return 0;

oom:
    free(blk.p);
    return LSHD_E_MEMORY;
}

/* Dispatch one completed stream through the Leash handler. */
static int h2d_dispatch(lshd_conn *c, lshd_h2ctx *h, h2d_stream *st,
                        lshhttpd_handler_t handler, long long *served_delta) {
    if (h2d_decode_headers(h, st) != 0) {
        h2d_send_rst(c, h, st->id, 1); /* PROTOCOL_ERROR */
        h2d_stream_clear(st);
        return 0;
    }
    /* NUL-terminate the handler inputs (accumulators are raw byte bufs). */
    if (!buf_append(&st->hdrs, "", 1) || !buf_append(&st->data, "", 1)) {
        h2d_send_rst(c, h, st->id, 1);
        h2d_stream_clear(st);
        return LSHD_E_MEMORY;
    }
    /* The terminators are framing, not payload: keep them out of the
     * server-wide byte accounting. */
    g_total_buf_bytes -= 2;
    int is_head = (strcmp(st->method, "HEAD") == 0);
    int status = 0;
    char *rhdr = NULL;
    char *rbody = NULL;
    int rc = lshd_call_handler(handler, st->method, st->path, st->query,
                               st->hdrs.p ? st->hdrs.p : "",
                               st->data.p ? st->data.p : "", &status, &rhdr,
                               &rbody);
    if (rc != 0) {
        h2d_send_rst(c, h, st->id, 1);
        h2d_stream_clear(st);
        return rc;
    }
    rc = h2d_respond(c, h, st->id, status, rhdr, rbody, is_head);
    free(rhdr);
    free(rbody);
    h2d_stream_clear(st);
    if (rc != 0) return rc;
    (*served_delta)++;
    return 0;
}

/* Process inbound frames + dispatch ready streams. Bounded per slice.
 * Returns 0 keep, 1 drop connection, <0 fatal. */
static int h2d_pump(lshd_conn *c, lshd_h2ctx *h, lshhttpd_handler_t handler,
                    long long *served_delta, long long max_requests,
                    long long served_so_far, int *must_stop) {
    char chunk[16384];
    /* 1. pull available bytes (bounded). */
    for (int k = 0; k < 4; k++) {
        int n = h2d_raw_recv(c, chunk, sizeof(chunk));
        if (n > 0) {
            if (!buf_append(&h->in, chunk, (size_t)n)) return LSHD_E_MEMORY;
            g_total_buf_bytes += (long long)n;
            c->last_ms = lshd_now_ms();
            c->has_bytes = 1;
            if ((size_t)n < sizeof(chunk)) break;
            continue;
        }
        if (n == -100) {
            /* EOF: dispatch nothing new; close when drained. */
            if (h->in.len > 0) break;
            return 1;
        }
        if (n < 0) return n;
        break; /* would-block */
    }
    /* 2. process complete frames. */
    int frames = 0;
    int dispatched = 0;
    while (h->in.len >= 9 && frames < H2D_PUMP_FRAMES) {
        unsigned char *p = (unsigned char *)h->in.p;
        unsigned int len = ((unsigned int)p[0] << 16) |
                           ((unsigned int)p[1] << 8) | (unsigned int)p[2];
        if (len > 16777215) return LSHD_E_ACCEPT;
        if (h->in.len < 9 + len) break; /* incomplete */
        unsigned char type = p[3];
        unsigned char flags = p[4];
        unsigned int sid = (((unsigned int)p[5] << 24) |
                            ((unsigned int)p[6] << 16) |
                            ((unsigned int)p[7] << 8) | (unsigned int)p[8]) &
                           0x7fffffff;
        unsigned char *pl = p + 9;
        frames++;

        if (type == H2D_FTYPE_SETTINGS) {
            if (!(flags & H2D_FLAG_ACK)) {
                if (len % 6 != 0) return LSHD_E_ACCEPT;
                for (unsigned int i = 0; i < len; i += 6) {
                    unsigned int id = ((unsigned int)pl[i] << 8) | pl[i + 1];
                    unsigned int val = ((unsigned int)pl[i+2] << 24) |
                                       ((unsigned int)pl[i+3] << 16) |
                                       ((unsigned int)pl[i+4] << 8) | pl[i+5];
                    if (id == 1) {
                        if (val > 16384) val = 16384;
                        h->dyn.max = val;
                    } else if (id == 3) {
                        h->init_window = val;
                    } else if (id == 5) {
                        if (val < 16384) val = 16384;
                        if (val > 16777215) val = 16777215;
                        h->peer_max_frame = val;
                    }
                }
                h->got_client_settings = 1;
                h2d_send_frame(c, h, H2D_FTYPE_SETTINGS, H2D_FLAG_ACK, 0,
                               NULL, 0);
            }
        } else if (type == H2D_FTYPE_PING) {
            if (!(flags & H2D_FLAG_ACK)) {
                if (len != 8) return LSHD_E_ACCEPT;
                h2d_send_frame(c, h, H2D_FTYPE_PING, H2D_FLAG_ACK, 0, pl, 8);
            }
        } else if (type == H2D_FTYPE_WINDOW) {
            if (len != 4) return LSHD_E_ACCEPT;
            unsigned int incr = (((unsigned int)pl[0] << 24) |
                                 ((unsigned int)pl[1] << 16) |
                                 ((unsigned int)pl[2] << 8) | pl[3]) &
                                0x7fffffff;
            if (incr == 0) return LSHD_E_ACCEPT;
            if (sid == 0) {
                h->conn_send_window += (int)incr;
            } else {
                h2d_stream *st = h2d_find_stream(h, sid);
                if (st) st->send_window += (int)incr;
            }
        } else if (type == H2D_FTYPE_GOAWAY) {
            h->peer_goaway = 1;
        } else if (type == H2D_FTYPE_HEADERS) {
            if (sid == 0 || (sid & 1) == 0) return LSHD_E_ACCEPT;
            if (sid <= h->last_stream && !h2d_find_stream(h, sid))
                return LSHD_E_ACCEPT; /* reuse of closed stream */
            h2d_stream *st = h2d_find_stream(h, sid);
            if (!st) {
                if (h->peer_goaway) {
                    /* ignore */
                } else {
                    int slot = -1;
                    for (int i = 0; i < H2D_MAX_STREAMS; i++) {
                        if (!h->streams[i].used) {
                            slot = i;
                            break;
                        }
                    }
                    if (slot < 0) {
                        h2d_send_rst(c, h, sid, 7); /* REFUSED_STREAM */
                    } else {
                        st = &h->streams[slot];
                        st->used = 1;
                        st->id = sid;
                        st->send_window = (int)h->init_window;
                        if (sid > h->last_stream) h->last_stream = sid;
                    }
                }
            }
            if (st) {
                size_t skip = 0;
                if (flags & H2D_FLAG_PADDED) {
                    if (len < 1 || (size_t)pl[0] + 1 > len)
                        return LSHD_E_ACCEPT;
                    skip = 1;
                    len = (unsigned int)(len - 1 - pl[0]);
                }
                if (flags & H2D_FLAG_PRIORITY) {
                    if (len < 5) return LSHD_E_ACCEPT;
                    skip += 5;
                    len -= 5;
                }
                if (st->hblk.len + len > H2D_MAX_HDR_BYTES)
                    return LSHD_E_ACCEPT;
                if (!buf_append(&st->hblk, (const char *)pl + skip, len))
                    return LSHD_E_MEMORY;
                g_total_buf_bytes += (long long)len;
                st->in_cont = !(flags & H2D_FLAG_END_HEADERS);
                if ((flags & H2D_FLAG_END_HEADERS) &&
                    (flags & H2D_FLAG_END_STREAM)) {
                    if (h->closing || (max_requests >= 0 &&
                        served_so_far + *served_delta >= max_requests)) {
                        h2d_send_rst(c, h, sid, 7);
                        h2d_stream_clear(st);
                    } else if (dispatched < H2D_PUMP_DISPATCH) {
                        int rc = h2d_dispatch(c, h, st, handler,
                                              served_delta);
                        dispatched++;
                        if (rc != 0) return rc;
                    }
                }
            }
        } else if (type == H2D_FTYPE_CONT) {
            h2d_stream *st = h2d_find_stream(h, sid);
            if (!st || !st->in_cont) return LSHD_E_ACCEPT;
            if (st->hblk.len + len > H2D_MAX_HDR_BYTES) return LSHD_E_ACCEPT;
            if (!buf_append(&st->hblk, (const char *)pl, len))
                return LSHD_E_MEMORY;
            g_total_buf_bytes += (long long)len;
            if (flags & H2D_FLAG_END_HEADERS) {
                st->in_cont = 0;
                if (flags & H2D_FLAG_END_STREAM) {
                    if (h->closing || (max_requests >= 0 &&
                        served_so_far + *served_delta >= max_requests)) {
                        h2d_send_rst(c, h, sid, 7);
                        h2d_stream_clear(st);
                    } else if (dispatched < H2D_PUMP_DISPATCH) {
                        int rc = h2d_dispatch(c, h, st, handler,
                                              served_delta);
                        dispatched++;
                        if (rc != 0) return rc;
                    }
                }
            }
        } else if (type == H2D_FTYPE_DATA) {
            h2d_stream *st = h2d_find_stream(h, sid);
            if (!st) return LSHD_E_ACCEPT;
            size_t skip = 0;
            if (flags & H2D_FLAG_PADDED) {
                if (len < 1 || (size_t)pl[0] + 1 > len)
                    return LSHD_E_ACCEPT;
                skip = 1;
                len = (unsigned int)(len - 1 - pl[0]);
            }
            if (st->data.len + len > (size_t)(16 * 1024 * 1024))
                return LSHD_E_ACCEPT;
            if (len > 0) {
                if (!buf_append(&st->data, (const char *)pl + skip, len))
                    return LSHD_E_MEMORY;
                g_total_buf_bytes += (long long)len;
                h->conn_recv_window -= (int)len;
                st->recv_window -= (int)len;
                if (h->conn_recv_window < 32768) {
                    h2d_send_window(c, h, 0,
                                    (unsigned)(65535 - h->conn_recv_window));
                    h->conn_recv_window = 65535;
                }
                if (st->recv_window < 32768) {
                    h2d_send_window(c, h, sid,
                                    (unsigned)(65535 - st->recv_window));
                    st->recv_window = 65535;
                }
            }
            if (flags & H2D_FLAG_END_STREAM) {
                if (h->closing || (max_requests >= 0 &&
                    served_so_far + *served_delta >= max_requests)) {
                    h2d_send_rst(c, h, sid, 7);
                    h2d_stream_clear(st);
                } else if (dispatched < H2D_PUMP_DISPATCH) {
                    int rc = h2d_dispatch(c, h, st, handler, served_delta);
                    dispatched++;
                    if (rc != 0) return rc;
                }
            }
        } else if (type == H2D_FTYPE_RST) {
            h2d_stream *st = h2d_find_stream(h, sid);
            if (st) h2d_stream_clear(st);
        }
        /* PRIORITY / PUSH cap: ignored. Unknown types: ignored. */

        /* consume the frame */
        size_t used = 9 + (((unsigned int)p[0] << 16) |
                           ((unsigned int)p[1] << 8) | (unsigned int)p[2]);
        memmove(h->in.p, h->in.p + used, h->in.len - used);
        h->in.len -= used;
        g_total_buf_bytes -= (long long)used;
    }
    /* 3. flush queued output (offset persists on the connection). */
    {
        int fr = h2d_flush(c, h, &c->h2_out_off);
        if (fr < 0) return fr;
    }
    if (lshd_stop_flag) *must_stop = 1;
    return 0;
}

/* Send our connection preface (empty SETTINGS) + queue a GOAWAY. */
static int h2d_send_settings(lshd_conn *c, lshd_h2ctx *h) {
    return h2d_send_frame(c, h, H2D_FTYPE_SETTINGS, 0, 0, NULL, 0);
}

static int h2d_send_goaway(lshd_conn *c, lshd_h2ctx *h) {
    unsigned char p[8];
    unsigned int last = h->last_stream & 0x7fffffff;
    p[0] = (unsigned char)((last >> 24) & 0x7f);
    p[1] = (unsigned char)((last >> 16) & 0xff);
    p[2] = (unsigned char)((last >> 8) & 0xff);
    p[3] = (unsigned char)(last & 0xff);
    p[4] = p[5] = p[6] = p[7] = 0; /* NO_ERROR */
    h->goaway_sent = 1;
    return h2d_send_frame(c, h, H2D_FTYPE_GOAWAY, 0, 0, p, 8);
}

/* Promote a fresh connection to H2 after seeing the client preface.
 * Moves any bytes past the 24-byte magic into the h2 input buffer. */
static int h2d_promote(lshd_conn *c) {
    lshd_h2ctx *h = h2d_ctx_new();
    if (!h) return LSHD_E_MEMORY;
    if (c->acc.len > 24) {
        if (!buf_append(&h->in, c->acc.p + 24, c->acc.len - 24)) {
            h2d_ctx_free(h);
            return LSHD_E_MEMORY;
        }
    }
    g_total_buf_bytes -= (long long)c->acc.len;
    buf_free(&c->acc);
    buf_init(&c->acc);
    c->h2 = h;
    c->is_h2 = 1;
    return h2d_send_settings(c, h);
}

/* Called with fresh inbound bytes on a connection whose protocol is not
 * yet known. Returns 1 when promoted to h2, 0 when still undecided or
 * plain HTTP/1.1 (c->acc holds the bytes either way), <0 fatal. */
static int h2d_maybe_promote(lshd_conn *c) {
    /* Called only while !h2checked, i.e. before any 1.1 request was
     * extracted from this connection (read_avail sets has_bytes on any
     * bytes, so it cannot tell a preface apart — the byte prefix can). */
    if (c->acc.len == 0) return 0;
    if (c->acc.len >= 3 && memcmp(c->acc.p, "PRI", 3) != 0) {
        c->h2checked = 1; /* definitely HTTP/1.1 */
        return 0;
    }
    if (c->acc.len < 24) return 0; /* wait for the full magic */
    if (memcmp(c->acc.p, "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n", 24) == 0) {
        c->h2checked = 1;
        int rc = h2d_promote(c);
        return rc != 0 ? rc : 1;
    }
    /* Starts with PRI but is not the preface: leave it to the 1.1
     * parser, which answers 400. */
    c->h2checked = 1;
    return 0;
}

#ifndef _WIN32
/* Drive one TLS handshake step. Returns 1 done, 0 want I/O, <0 fatal. */
static int lshd_tls_accept_step(lshd_conn *c) {
    SSL *ssl = (SSL *)c->tls;
    int r = SSL_accept(ssl);
    if (r == 1) {
        c->tls_hs = 0;
        c->want_out = 0;
        /* ALPN decides the framing: h2 needs the preface dance, http/1.1
         * flows through the existing engine over TLS. */
        const unsigned char *sel = NULL;
        unsigned len = 0;
        SSL_get0_alpn_selected(ssl, &sel, &len);
        (void)sel;
        (void)len;
        return 1;
    }
    int e = SSL_get_error(ssl, r);
    if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) {
        c->want_out = 1;
        return 0;
    }
    lshd_set_detail("TLS handshake failed");
    return LSHD_E_ACCEPT;
}

/* Create the TLS server context for serve_tls. */
static int lshd_tls_ctx_new(const char *cert, const char *key) {
    if (g_tls_ctx) return 0;
    g_tls_ctx = SSL_CTX_new(TLS_server_method());
    if (!g_tls_ctx) {
        lshd_set_detail("TLS context allocation failed");
        return LSHD_E_TLS;
    }
    SSL_CTX_set_min_proto_version(g_tls_ctx, TLS1_2_VERSION);
    if (SSL_CTX_use_certificate_file(g_tls_ctx, cert, SSL_FILETYPE_PEM) != 1) {
        lshd_set_detail("cannot load TLS certificate");
        return LSHD_E_TLS;
    }
    if (SSL_CTX_use_PrivateKey_file(g_tls_ctx, key, SSL_FILETYPE_PEM) != 1) {
        lshd_set_detail("cannot load TLS private key");
        return LSHD_E_TLS;
    }
    SSL_CTX_set_alpn_select_cb(g_tls_ctx, lshd_alpn_cb, NULL);
    return 0;
}

static void lshd_tls_ctx_free(void) {
    if (g_tls_ctx) {
        SSL_CTX_free(g_tls_ctx);
        g_tls_ctx = NULL;
    }
    g_tls_mode = 0;
}
#endif

static void lshd_conn_free(lshd_conn *c) {
    if (!c) return;
    /* Un-account whatever is still buffered on this connection (request
     * accumulator + undrained response bytes). */
    g_total_buf_bytes -= (long long)(c->acc.len + (c->out.len - c->out_off));
#ifndef _WIN32
    if (c->tls) {
        SSL_shutdown((SSL *)c->tls);
        SSL_free((SSL *)c->tls);
        c->tls = NULL;
    }
#endif
    if (c->h2) {
        lshd_h2ctx *h = (lshd_h2ctx *)c->h2;
        g_total_buf_bytes -= (long long)(h->in.len + h->out.len);
        for (int i = 0; i < H2D_MAX_STREAMS; i++) {
            if (h->streams[i].used) {
                g_total_buf_bytes -= (long long)(h->streams[i].hblk.len +
                                                 h->streams[i].data.len +
                                                 h->streams[i].hdrs.len);
            }
        }
        h2d_ctx_free(h);
        c->h2 = NULL;
    }
    lshd_close_fd(c->fd);
    buf_free(&c->acc);
    buf_free(&c->out);
    free(c);
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

static int lshd_serve_impl(int port, long long max_requests,
                           lshhttpd_handler_t handler,
                           const char *tls_cert, const char *tls_key) {
    /* The TLS context is prepared by lshhttpd_serve_tls before the loop
     * starts; plain serve() leaves g_tls_ctx NULL (cleartext only). */
    (void)tls_cert;
    (void)tls_key;
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
#ifndef _WIN32
    lshd_spipe_hold();
#endif

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
            if (g_conns[i]->out.len > g_conns[i]->out_off ||
                g_conns[i]->want_out) {
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
            if (g_conns[i]->out.len > g_conns[i]->out_off ||
                g_conns[i]->want_out) {
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
#ifndef _WIN32
                if (g_tls_mode && g_tls_ctx) {
                    SSL *ssl = SSL_new(g_tls_ctx);
                    if (!ssl) {
                        lshd_close_fd(client);
                        free(cc);
                        continue;
                    }
                    SSL_set_fd(ssl, client);
                    SSL_set_accept_state(ssl);
                    cc->tls = ssl;
                    cc->tls_hs = 1;
                    cc->want_out = 1;
                }
#endif
                g_conns[g_nconns++] = cc;
            }
        }

        /* --- serve + drain live connections --- */
        g_pending_work = 0;
        for (int i = 0; i < g_nconns; i++) {
            lshd_conn *cc = g_conns[i];

#ifndef _WIN32
            /* --- 0. TLS handshake drive --- */
            if (cc->tls_hs) {
                int hs = lshd_tls_accept_step(cc);
                if (hs < 0) {
                    lshd_conn_drop(i);
                    i--;
                    continue;
                }
                if (hs == 0) continue; /* want I/O: revisit next slice */
                /* Handshake done: fall through to framing below. */
            }
#endif
            /* --- 0b. HTTP/2 connections run their own pump --- */
            if (cc->is_h2) {
                lshd_h2ctx *h2 = (lshd_h2ctx *)cc->h2;
                if (max_requests >= 0 && served >= max_requests)
                    h2->closing = 1;
                if (need_stop) h2->closing = 1;
                if (h2->closing) {
                    if (!h2->goaway_sent) h2d_send_goaway(cc, h2);
                    h2d_flush(cc, h2, &cc->h2_out_off);
                    if (h2->out.len == 0) {
                        lshd_conn_drop(i);
                        i--;
                    }
                    continue;
                }
                long long delta = 0;
                int hstop = 0;
                int hrc = h2d_pump(cc, h2, handler, &delta, max_requests,
                                   served, &hstop);
                served += delta;
                if (hstop) need_stop = 1;
                if (rc != 0) break;
                if (hrc != 0) {
                    /* EOF-drained or fatal: flush what we can, then drop. */
                    if (!h2->goaway_sent && hrc > 0)
                        h2d_send_goaway(cc, h2);
                    h2d_flush(cc, h2, &cc->h2_out_off);
                    lshd_conn_drop(i);
                    i--;
                    continue;
                }
                continue;
            }

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
        if (cc->is_h2 && cc->h2) {
            lshd_h2ctx *h2 = (lshd_h2ctx *)cc->h2;
            if (!h2->goaway_sent) h2d_send_goaway(cc, h2);
            while (h2->out.len > 0 && lshd_now_ms() < deadline) {
                size_t before = h2->out.len;
                if (h2d_flush(cc, h2, &cc->h2_out_off) < 0) break;
                if (h2->out.len >= before) {
                    lshd_wait_writable(cc->fd, 100);
                }
            }
        }
    }

    /* close all live connections (their in-flight response already written) */
    for (int i = 0; i < g_nconns; i++) lshd_conn_free(g_conns[i]);
    g_nconns = 0;

    if (accepting) {
        lshd_listen_fd = LSHD_INVALID_FD;
        lshd_close_fd(lfd);
    }
#ifndef _WIN32
    lshd_tls_ctx_free();
#endif
#ifdef _WIN32
    WSACleanup();
#endif
    lshd_running = 0;

#ifndef _WIN32
    lshd_spipe_drop();
#endif
    if (aborted) return LSHD_E_ABORTED;
    if (rc != 0) return rc;
    return (int)served;
}

int lshhttpd_serve(int port, long long max_requests,
                   lshhttpd_handler_t handler) {
    return lshd_serve_impl(port, max_requests, handler, NULL, NULL);
}

int lshhttpd_serve_tls(int port, long long max_requests, const char *cert,
                       const char *key, lshhttpd_handler_t handler) {
#ifdef _WIN32
    (void)port;
    (void)max_requests;
    (void)cert;
    (void)key;
    (void)handler;
    lshd_set_detail("TLS serving needs OpenSSL (POSIX builds)");
    return LSHD_E_TLS;
#else
    if (!cert || !cert[0] || !key || !key[0]) {
        lshd_set_detail("TLS serving needs a certificate and key file");
        return LSHD_E_TLS;
    }
    if (lshd_tls_ctx_new(cert, key) != 0) return LSHD_E_TLS;
    g_tls_mode = 1;
    return lshd_serve_impl(port, max_requests, handler, cert, key);
#endif
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
    case LSHD_E_TLS: base = "TLS setup failed"; break;
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
