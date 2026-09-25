# httpserver — native HTTP/1.1 server for Leash

A native HTTP/1.1 server shim (`lshhttpd`) bound through Leash's `@from` FFI
directive. It completes the web stack started by the `http` client package:
both sides of the wire are now native Leash, with no external dependencies.

| Platform   | Backend |
|------------|---------|
| Linux      | BSD sockets (`linux/liblshhttpd.a`, prebuilt) |
| Linux ARM64| BSD sockets (`arm64/liblshhttpd.a` — rebuild, see below) |
| Windows    | Winsock2 (`win/liblshhttpd.a`, prebuilt) |
| macOS      | BSD sockets (`macos/liblshhttpd.a` — rebuild) |

## Usage

```leash
use httpserver::httpserver::*;

fnc handle(method string, path string, query string, headers string, body string) : int {
    if path == "/" {
        return HttpServer.reply(200, "text/html", "<h1>Hello from Leash!</h1>");
    }
    if HttpServer.path_matches(path, "/api/data") {
        q: HttpQuery = HttpServer.parse_query(query);
        return HttpServer.reply(200, "application/json",
            "{\"echo\": \"" + q.get("q") + "\"}");
    }
    return HttpServer.reply(404, "text/plain", "not found: " + path);
}

fnc main() : void {
    show("listening on http://localhost:8080 …");
    HttpServer.serve(8080, &handle);   // blocks until shutdown()
}
```

Run with the stdlib on the import path:

```bash
leash run main.lsh --other-imports installthis
```

## Handler contract

```
fnc handle(method string, path string, query string, headers string, body string) : int
```

| Argument | Contents |
|----------|----------|
| `method`  | HTTP verb: `GET`, `POST`, `PUT`, `DELETE`, `HEAD`, … |
| `path`    | URL path, e.g. `/hello` (query string is excluded) |
| `query`   | raw query string without the leading `?` (`""` when absent) |
| `headers` | request headers, one `Name: value` per LF-separated line |
| `body`    | request body (`""` when there is none; GET/HEAD have no body) |

Return the **HTTP status code** (200, 404, …). The response body and extra
headers are handed to the server through `HttpServer.set_body()`,
`HttpServer.add_header()`/`add_header_line()`, or in one line with
`HttpServer.reply(status, content_type, body)`.

`HttpServer.reply()` returns the status it was given, so it can be the
handler's return value: `return HttpServer.reply(200, "text/plain", "hi");`

## API

### `HttpServer` (all static)

| Function | Description |
|----------|-------------|
| `serve(port, handler)` | Blocks until `shutdown()`; returns requests served, or a negative error code |
| `serve_ex(port, max_requests, handler)` | Like `serve`, but exits cleanly after `max_requests` — handy for tests |
| `shutdown()` | Stop a running server. From inside a handler: after the current response. From another thread: unblocks `accept()` immediately |
| `set_body(body)` | Set the response body for the current request |
| `add_header(name, value)` / `add_header_line(line)` | Append response headers (`Content-Length`/`Connection` are managed by the server) |
| `set_content_type(ct)` | Shortcut for `add_header("Content-Type", ct)` |
| `reply(status, ct, body)` / `reply_raw(status, body)` | One-liner responses usable as handler return values |
| `parse_query(q)` → `HttpQuery` | Split `a=1&b=2` into a query object (`q.get("a")`, `q.has("b")`, `q.count()`) |
| `path_matches(path, prefix)` | Prefix matcher for simple routing |
| `version()` / `strerror(code)` | Diagnostics |

### Behaviour notes

- **Multiplexed keep-alive (v1.1):** one `poll()`/`select()` slice watches
  the listen socket and every open client connection at once. Connections
  stay open across requests — HTTP/1.1 keep-alive by default, HTTP/1.0
  only with `Connection: keep-alive`, and `Connection: close` always
  honored. Pipelined requests on one socket are answered in order. The
  handler still runs on the thread that called `serve()` (GC-visible
  stack, same as v1.0), and a slow client no longer blocks the others.
- **Connection hygiene:** up to 128 concurrent connections; an idle
  keep-alive connection closes after 60 s of silence, a request whose
  bytes stall mid-flight after the 30 s I/O budget; the per-connection
  buffer is capped at 64 KiB of head + 16 MiB of body.
- **Binds all interfaces** (`0.0.0.0`): `localhost` shortcuts work, but so do
  network callers. For anything beyond toy/demo servers, put it behind a
  reverse proxy (nginx, Caddy) that handles TLS; `lshhttpd` itself is
  plain HTTP/1.1.
- `HEAD` requests run the handler but suppress the body (with a correct
  `Content-Length`).
- Malformed request lines are answered with `400 Bad Request` without
  invoking the handler; any `Transfer-Encoding` (including chunked, any
  capitalisation) gets `501 Not Implemented`; a second, conflicting
  `Content-Length` header or a negative one is `400 Bad Request`; request
  heads are capped at 64 KiB (`431`), request bodies at 16 MiB (`413`), and
  per-client I/O times out after 30 s.
- The parser is strict on purpose: a header line must be
  `name: value` (no whitespace before the colon), the version must be
  `HTTP/1.x`, control bytes in the head are rejected, and a head that mixes
  CRLF lines with a bare-LF terminator is rejected. A uniformly LF-only
  head is still accepted. These rules are what keep a request
  unambiguous when it passes through a proxy.
- `add_header()` / `set_header()` drop any header whose name or value
  contains a control character (CR, LF, NUL, ...). Handlers that echo
  request data into a header therefore cannot be used to inject extra
  header lines or split the response.
- Handler strings handed in by the server are GC-managed copies — they can
  be stored beyond the callback (vectors, globals).
- `serve()` is not re-entrant: exactly one HTTP server at a time per process
  (nested or concurrent calls report `invalid arguments`).

### Known limitations (beta)

- **Memory ceiling is per connection AND server-wide.** With the default
  caps a single connection can hold roughly 2x (64 KiB head + 16 MiB body)
  in its accumulator, up to 128 connections are accepted at once, and the
  server-wide buffered-bytes ceiling (`LSHD_MAX_GLOBAL_BYTES`, 64 MiB
  default) bounds the total — new bytes simply wait in the kernel until
  live requests are consumed. Lower `LSHD_MAX_GLOBAL_BYTES` /
  `LSHD_MAX_BODY_BYTES` / `LSHD_MAX_CONNS` (compile-time) for untrusted
  deployments, or put a reverse proxy in front.
- **Never-awaited futures stay rooted until exit** (async/await side, not
  an httpserver concern — listed here for completeness).

## Rebuilding the native library

Prebuilt archives ship for Linux (x86-64) and Windows. To rebuild after
modifying `src/lshhttpd.c`:

```bash
cd src && sh build.sh                                    # Linux -> ../linux/
cd src && build.bat                                      # Windows -> ..\win\
cd src && sh build.sh                                    # macOS  -> ../macos/
cd src && TARGET_DIR=arm64 CC=aarch64-linux-gnu-gcc sh build.sh
                                                         # Linux ARM64 cross
```

Outputs land in `../linux/`, `../win/`, `../macos/` or `../arm64/`.
