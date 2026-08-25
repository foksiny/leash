# http — native HTTP/HTTPS client for Leash

Native HTTP/1.1 client with HTTPS (TLS) support, implemented as a small C
shim bound through Leash's `@from` FFI directive. Both HTTP and HTTPS are
supported on every platform:

| Platform | Backend |
|----------|---------|
| Linux    | BSD sockets + OpenSSL (statically bundled in `linux/liblshhttp.a`) |
| Windows  | WinHTTP (`win/liblshhttp.a`, no external dependencies) |
| macOS    | BSD sockets + OpenSSL (build once, see below) |

## Usage

```leash
use http::http::*;

fnc main() : void {
    r: HttpResponse = Http.get("https://example.com/");
    if r.success() {
        show("status: ", r.status);
        show("server: ", r.header("Server"));
        show("body:   ", r.body);
    } else {
        show("request failed: ", r.err);
    }
}
```

Run with the stdlib on the import path:

```bash
leash run main.lsh --other-imports installthis
```

## API

### `Http` (all static)

| Function | Description |
|----------|-------------|
| `get(url)` / `delete(url)` / `head(url)` | Simple requests without a body |
| `post(url, body)` / `put(url, body)` / `patch(url, body)` | Requests with a body |
| `request(method, url, body, headers)` | Full control; `headers` is LF-separated extra headers |
| `request_ex(method, url, body, headers, timeout_ms, max_redirects, verify_tls)` | Full control + tuning |
| `download(url, path)` | Streams any response straight to a file (binary-safe) |
| `download_ex(url, path, timeout_ms, max_redirects, verify_tls)` | Same, with tuning |
| `form_encode(keys, vals)` | Percent-encodes key/value vectors into a form body |
| `json_header()` / `form_content_type()` | Ready-made Content-Type header lines |
| `version()` / `strerror(code)` | Diagnostics |

Defaults: 30 s timeout, up to 10 redirects followed, TLS certificate
verification on.

### `HttpResponse`

| Member | Description |
|--------|-------------|
| `ok: bool` | True when a response was received at all |
| `success(): bool` | True for 2xx statuses |
| `status: int` | HTTP status code (e.g. 200), or a negative error code |
| `body: string` | Response body (text; use `Http.download` for binary data) |
| `header(name): string` | Case-insensitive header lookup, "" when absent |
| `headers: string` | Raw response head, one header per line |
| `err: string` | Human-readable failure reason ("") on success |

Negative status codes map to failures such as DNS errors (-2), connection
failures (-3), TLS errors (-4), timeouts (-7) and too many redirects (-9);
pass them to `Http.strerror()` for a description.

## Rebuilding the native library

Prebuilt archives ship for Linux and Windows. To rebuild after modifying
`src/lshhttp.c`:

```bash
# Linux (bundles OpenSSL static libs)
cd src && sh build.sh

# Windows (MinGW-w64)
cd src && build.bat

# macOS (Homebrew OpenSSL)
cd src && OPENSSL_ROOT=$(brew --prefix openssl) sh build.sh
```

Outputs land in `../linux/`, `../win/` or `../macos/` respectively.
