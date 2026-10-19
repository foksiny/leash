// Leash JVM http shim: implements the @from("liblshhttp.a") surface for
// the --target jvm backend over java.net.http (JDK 11+).
//
// Contract parity with installthis/http/src/lshhttp.c:
//   * same LSH_E_* error codes and strerror() strings,
//   * same redirect rules (301/302/303/307/308; 303 and POST-on-301/302
//     rewrite to GET with an empty body; max_redirects < 0 -> 10),
//   * User-Agent "leash-http/1.1", Accept: */*, custom headers as
//     LF-separated "Name: value" lines,
//   * response headers normalized to LF-separated "Name: value" lines
//     (status line excluded), body as the platform string.
//
// Out-params ride as one-element Object[] boxes: the compiler lowers
// `&local` to a fresh box, passes it, and copies element 0 back after
// the call, mirroring the C char** / int* out-parameters.
//
// Differences from the native engine (inherent to java.net.http):
//   * no `Connection: close` hop header (the JDK pools connections),
//   * response header order follows the JDK's map iteration,
//   * h2c prior knowledge is not available: *_h2 negotiates h2 over TLS
//     via ALPN and reports proto=1 (HTTP/1.1) on cleartext URLs.
//
// Requires Java 11+ at runtime (other JVM-target programs keep the
// Java 5 class-file floor the backend emits).

import java.io.File;
import java.io.FileOutputStream;
import java.net.ConnectException;
import java.net.URI;
import java.net.UnknownHostException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509ExtendedTrustManager;

public final class LeashHttp {

    // Error codes (mirror lshhttp.c's enum).
    private static final int E_URL      = -1;
    private static final int E_DNS      = -2;
    private static final int E_CONNECT  = -3;
    private static final int E_TLS      = -4;
    private static final int E_SEND     = -5;
    private static final int E_RECV     = -6;
    private static final int E_TIMEOUT  = -7;
    private static final int E_PROTO    = -8;
    private static final int E_REDIRECT = -9;
    private static final int E_MEMORY   = -10;
    private static final int E_FILE     = -11;

    private static final String VERSION = "leash-http/1.1";

    // Last error detail (C: __thread char lsh_detail[512]).
    private static volatile String detail = "";

    private LeashHttp() { }

    // ------------------------------------------------------------------
    // Public surface (names and shapes fixed by http.lsh's @from block)
    // ------------------------------------------------------------------

    public static String lsh_http_version() {
        return VERSION;
    }

    public static String lsh_http_strerror(int code) {
        String base;
        switch (code) {
            case E_URL:      base = "invalid or unsupported URL"; break;
            case E_DNS:      base = "could not resolve host"; break;
            case E_CONNECT:  base = "could not connect"; break;
            case E_TLS:      base = "TLS error"; break;
            case E_SEND:     base = "failed to send request"; break;
            case E_RECV:     base = "failed to receive response"; break;
            case E_TIMEOUT:  base = "timed out"; break;
            case E_PROTO:    base = "malformed HTTP response"; break;
            case E_REDIRECT: base = "too many redirects"; break;
            case E_MEMORY:   base = "out of memory or size limit reached"; break;
            case E_FILE:     base = "cannot open destination file"; break;
            default:
                if (code >= 100) return "no error";
                base = "unknown error";
                break;
        }
        String d = detail;
        if (d != null && d.length() > 0) {
            return base + " (" + d + ")";
        }
        return base;
    }

    public static int lsh_http_request(String method, String url, String headers,
                                       String body, long timeout_ms, int max_redirects,
                                       int verify_tls, Object[] out_headers,
                                       Object[] out_body) {
        return perform(method, url, headers, body, timeout_ms, max_redirects,
                       verify_tls, false, null, out_headers, out_body, null);
    }

    public static int lsh_http_request_h2(String method, String url, String headers,
                                          String body, long timeout_ms, int max_redirects,
                                          int verify_tls, Object[] out_headers,
                                          Object[] out_body, Object[] out_proto) {
        return perform(method, url, headers, body, timeout_ms, max_redirects,
                       verify_tls, true, null, out_headers, out_body, out_proto);
    }

    public static int lsh_http_download(String url, String dest_path, long timeout_ms,
                                        int max_redirects, int verify_tls) {
        return perform("GET", url, "", "", timeout_ms, max_redirects,
                       verify_tls, false, dest_path, null, null, null);
    }

    public static int lsh_http_download_h2(String url, String dest_path, long timeout_ms,
                                           int max_redirects, int verify_tls,
                                           Object[] out_proto) {
        return perform("GET", url, "", "", timeout_ms, max_redirects,
                       verify_tls, true, dest_path, null, null, out_proto);
    }

    // ------------------------------------------------------------------
    // Core driver
    // ------------------------------------------------------------------

    private static int perform(String method0, String url0, String headers, String body,
                               long timeout_ms, int max_redirects, int verify_tls,
                               boolean prefer_h2, String dest_path,
                               Object[] out_headers, Object[] out_body,
                               Object[] out_proto) {
        detail = "";
        if (out_headers != null) { out_headers[0] = null; }
        if (out_body != null) { out_body[0] = null; }
        if (out_proto != null) { out_proto[0] = null; }

        if (method0 == null || url0 == null) {
            detail = "null method or URL";
            return E_URL;
        }
        String method = method0;
        String url = url0;
        if (timeout_ms <= 0) { timeout_ms = 30000; }
        if (max_redirects < 0) { max_redirects = 10; }
        if (headers == null) { headers = ""; }
        if (body == null) { body = ""; }

        // The C engine opens (and truncates) the destination up front so a
        // bad path fails fast with LSH_E_FILE before any network work.
        if (dest_path != null) {
            try {
                File f = new File(dest_path);
                FileOutputStream fo = new FileOutputStream(f);
                fo.close();
            } catch (Exception e) {
                detail = "cannot open '" + dest_path + "' for writing";
                return E_FILE;
            }
        }

        HttpClient client;
        try {
            HttpClient.Builder b = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofMillis(timeout_ms))
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .version(prefer_h2 ? HttpClient.Version.HTTP_2
                                       : HttpClient.Version.HTTP_1_1);
            if (verify_tls == 0) {
                b.sslContext(trustAll());
            }
            client = b.build();
        } catch (Exception e) {
            detail = "cannot build client: " + e.getMessage();
            return E_TLS;
        }

        String currentHost = "";
        for (int hop = 0; ; hop++) {
            Url u = parseUrl(url);
            if (u == null) {
                detail = "unsupported or malformed URL: " + clip(url, 96);
                return E_URL;
            }
            currentHost = u.host;

            URI uri;
            try {
                String authority = u.host.indexOf(':') >= 0
                        ? "[" + u.host + "]:" + u.port
                        : u.host + ":" + u.port;
                uri = URI.create(u.scheme + "://" + authority + u.path);
            } catch (Exception e) {
                detail = "unsupported or malformed URL: " + clip(url, 96);
                return E_URL;
            }

            HttpRequest.Builder rb;
            try {
                rb = HttpRequest.newBuilder(uri)
                        .header("User-Agent", VERSION)
                        .header("Accept", "*/*")
                        .timeout(Duration.ofMillis(timeout_ms));
                appendCustomHeaders(rb, headers);
                if (body.length() > 0) {
                    rb.method(method, HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8));
                } else if (method.equals("GET") || method.equals("HEAD")
                        || method.equals("DELETE") || method.equals("TRACE")
                        || method.equals("OPTIONS")) {
                    rb.method(method, HttpRequest.BodyPublishers.noBody());
                } else {
                    rb.method(method, HttpRequest.BodyPublishers.noBody());
                }
            } catch (IllegalArgumentException e) {
                detail = "bad request: " + e.getMessage();
                return E_URL;
            }

            int status;
            HttpResponse<java.nio.file.Path> fileResp = null;
            HttpResponse<String> textResp = null;
            try {
                if (dest_path != null) {
                    fileResp = client.send(rb.build(), HttpResponse.BodyHandlers.ofFile(java.nio.file.Paths.get(dest_path)));
                    status = fileResp.statusCode();
                } else {
                    textResp = client.send(rb.build(), HttpResponse.BodyHandlers.ofString());
                    status = textResp.statusCode();
                }
            } catch (java.net.http.HttpConnectTimeoutException e) {
                detail = "connect " + e.getMessage();
                return E_TIMEOUT;
            } catch (java.net.http.HttpTimeoutException e) {
                detail = e.getMessage();
                return E_TIMEOUT;
            } catch (UnknownHostException e) {
                detail = "name resolution failed for '" + currentHost + "'";
                return E_DNS;
            } catch (ConnectException e) {
                if (isDnsFailure(e)) {
                    detail = "name resolution failed for '" + currentHost + "'";
                    return E_DNS;
                }
                detail = e.getMessage();
                return E_CONNECT;
            } catch (javax.net.ssl.SSLException e) {
                detail = clip(e.getMessage(), 200);
                return E_TLS;
            } catch (java.io.IOException e) {
                // A malformed status line / response surfaces as an
                // IOException from the wire-level parser.
                Throwable cause = e.getCause();
                String msg = e.getMessage() == null ? "" : e.getMessage();
                if (cause instanceof javax.net.ssl.SSLException) {
                    detail = clip(cause.getMessage(), 200);
                    return E_TLS;
                }
                if (isDnsFailure(e)) {
                    detail = "name resolution failed for '" + currentHost + "'";
                    return E_DNS;
                }
                if (hasCause(e, UnknownHostException.class)) {
                    detail = "name resolution failed for '" + currentHost + "'";
                    return E_DNS;
                }
                if (hasCause(e, java.net.SocketTimeoutException.class)) {
                    detail = clip(causeMessage(e), 200);
                    return E_TIMEOUT;
                }
                if (msg != null && msg.toLowerCase().contains("invalid status line")) {
                    detail = clip(msg, 200);
                    return E_PROTO;
                }
                detail = clip(msg, 200);
                return E_RECV;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                detail = "interrupted";
                return E_RECV;
            } catch (IllegalArgumentException e) {
                detail = clip(e.getMessage(), 200);
                return E_URL;
            }

            if (status == 301 || status == 302 || status == 303 || status == 307
                    || status == 308) {
                if (hop >= max_redirects) {
                    detail = "more than " + max_redirects + " redirects";
                    return E_REDIRECT;
                }
                HttpResponse<?> r = dest_path != null ? fileResp : textResp;
                String loc = r.headers().firstValue("Location").orElse("");
                if (loc.length() == 0) {
                    detail = status + " redirect without Location header";
                    return E_PROTO;
                }
                String next = resolveLocation(u, loc);
                if (next == null) {
                    detail = "bad redirect Location: " + clip(loc, 96);
                    return E_URL;
                }
                url = next;
                if (status == 303
                        || ((status == 301 || status == 302) && method.equals("POST"))) {
                    method = "GET";
                    body = "";
                }
                continue;
            }

            // Final response.
            HttpResponse<?> r = dest_path != null ? fileResp : textResp;
            if (out_proto != null) {
                out_proto[0] = Integer.valueOf(
                        r.version() == HttpClient.Version.HTTP_2 ? 2 : 1);
            }
            if (out_headers != null) {
                out_headers[0] = normalizeHeaders(r);
            }
            if (out_body != null) {
                out_body[0] = textResp != null ? textResp.body() : "";
            }
            return status;
        }
    }

    // ------------------------------------------------------------------
    // URL handling (lsh_parse_url parity)
    // ------------------------------------------------------------------

    private static final class Url {
        String scheme;
        String host;
        int port;
        String path;
    }

    // http://host[:port]/path or https://..., userinfo stripped,
    // [ipv6]:port honoured, port must be 1..65535 digits, path defaults
    // to "/".
    private static Url parseUrl(String url) {
        Url u = new Url();
        String rest;
        if (url.startsWith("https://")) {
            u.scheme = "https";
            u.port = 443;
            rest = url.substring(8);
        } else if (url.startsWith("http://")) {
            u.scheme = "http";
            u.port = 80;
            rest = url.substring(7);
        } else {
            return null;
        }

        int slash = rest.indexOf('/');
        String hostpart = slash >= 0 ? rest.substring(0, slash) : rest;
        // Strip userinfo (user:pass@host) like the C engine.
        int at = hostpart.lastIndexOf('@');
        if (at >= 0) {
            hostpart = hostpart.substring(at + 1);
        }
        if (hostpart.length() == 0 || hostpart.length() >= 320) {
            return null;
        }

        if (hostpart.startsWith("[")) {
            int close = hostpart.indexOf(']');
            if (close < 0) { return null; }
            u.host = hostpart.substring(1, close);
            if (u.host.length() == 0) { return null; }
            if (close + 1 < hostpart.length() && hostpart.charAt(close + 1) == ':') {
                String p = hostpart.substring(close + 2);
                if (!isDigits(p) || p.length() == 0 || p.length() >= 8) { return null; }
                u.port = Integer.parseInt(p);
            } else if (close + 1 != hostpart.length()) {
                return null;
            }
        } else {
            int colon = hostpart.lastIndexOf(':');
            if (colon >= 0) {
                u.host = hostpart.substring(0, colon);
                String p = hostpart.substring(colon + 1);
                if (u.host.length() == 0 || !isDigits(p) || p.length() == 0
                        || p.length() >= 8) {
                    return null;
                }
                u.port = Integer.parseInt(p);
            } else {
                u.host = hostpart;
            }
        }
        if (u.port <= 0 || u.port > 65535 || u.host.length() == 0) {
            return null;
        }
        u.path = slash >= 0 ? rest.substring(slash) : "/";
        return u;
    }

    private static boolean isDigits(String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c < '0' || c > '9') { return false; }
        }
        return true;
    }

    // Absolute URLs pass through; "//host/path" inherits the scheme;
    // "/path" is root-relative; anything else replaces the segment after
    // the last '/'. Returns the next request URL or null.
    private static String resolveLocation(Url base, String loc) {
        if (loc.startsWith("http://") || loc.startsWith("https://")) {
            Url u = parseUrl(loc);
            return u == null ? null : loc;
        }
        if (loc.startsWith("//")) {
            return base.scheme + ":" + loc;
        }
        if (loc.startsWith("/")) {
            return base.scheme + "://" + authority(base) + loc;
        }
        int last = base.path.lastIndexOf('/');
        String dir = last >= 0 ? base.path.substring(0, last + 1) : "/";
        return base.scheme + "://" + authority(base) + dir + loc;
    }

    private static String authority(Url u) {
        boolean def = (u.scheme.equals("https") && u.port == 443)
                || (u.scheme.equals("http") && u.port == 80);
        String host = u.host.indexOf(':') >= 0 ? "[" + u.host + "]" : u.host;
        return def ? host : host + ":" + u.port;
    }

    // ------------------------------------------------------------------
    // Request/response shaping
    // ------------------------------------------------------------------

    // LF-separated "Name: value" lines, empty segments skipped, a line
    // without ':' skipped (the C engine forwards such lines raw; the JDK
    // builder would reject them, so they are dropped instead).
    private static void appendCustomHeaders(HttpRequest.Builder rb, String headers) {
        if (headers == null || headers.length() == 0) { return; }
        int start = 0;
        int n = headers.length();
        while (start <= n) {
            int eol = headers.indexOf('\n', start);
            int end = eol < 0 ? n : eol;
            String line = headers.substring(start, end);
            if (line.endsWith("\r")) { line = line.substring(0, line.length() - 1); }
            int colon = line.indexOf(':');
            if (colon > 0) {
                String name = line.substring(0, colon);
                String value = line.substring(colon + 1);
                try {
                    rb.header(name, value);
                } catch (IllegalArgumentException ignored) {
                    // Restricted header (Host, Content-Length, ...) — the
                    // JDK owns it; skip like the engine owns Content-Length.
                }
            }
            if (eol < 0) { break; }
            start = eol + 1;
        }
    }

    // "Name: value\n" per header value, LF-terminated like the C
    // engine's normalized block (status line excluded).
    private static String normalizeHeaders(HttpResponse<?> resp) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, List<String>> e : resp.headers().map().entrySet()) {
            String name = e.getKey();
            for (String value : e.getValue()) {
                sb.append(name).append(": ").append(value).append('\n');
            }
        }
        return sb.toString();
    }

    // ------------------------------------------------------------------
    // TLS verification opt-out (verify_tls == 0)
    // ------------------------------------------------------------------

    private static SSLContext trustAll() {
        try {
            TrustManager[] tm = new TrustManager[] { new X509ExtendedTrustManager() {
                public void checkClientTrusted(X509Certificate[] c, String a) { }
                public void checkServerTrusted(X509Certificate[] c, String a) { }
                public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                public void checkClientTrusted(X509Certificate[] c, String a, SSLEngine e) { }
                public void checkServerTrusted(X509Certificate[] c, String a, SSLEngine e) { }
                public void checkClientTrusted(X509Certificate[] c, String a, java.net.Socket s) { }
                public void checkServerTrusted(X509Certificate[] c, String a, java.net.Socket s) { }
            } };
            // Also disable the JDK client's built-in hostname verification
            // for this JVM (matches the native engine's verify_tls=0).
            HttpsURLConnection.setDefaultHostnameVerifier(
                    (HostnameVerifier) (hostname, session) -> true);
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, tm, new SecureRandom());
            return ctx;
        } catch (Exception e) {
            return null;
        }
    }

    private static String clip(String s, int max) {
        if (s == null) { return ""; }
        if (s.length() <= max) { return s; }
        return s.substring(0, max);
    }

    // True when `t` or any of its causes is an instance of `kind`.
    private static boolean hasCause(Throwable t, Class<?> kind) {
        Throwable c = t;
        int depth = 0;
        while (c != null && depth < 8) {
            if (kind.isInstance(c)) { return true; }
            c = c.getCause();
            depth++;
        }
        return false;
    }

    // DNS failure: the JDK client wraps an unresolvable host as
    // ConnectException -> UnresolvedAddressException (not the classic
    // UnknownHostException).
    private static boolean isDnsFailure(Throwable t) {
        return hasCause(t, java.nio.channels.UnresolvedAddressException.class)
                || hasCause(t, UnknownHostException.class);
    }

    // Message of the deepest non-null cause (the wrapped reason).
    private static String causeMessage(Throwable t) {
        Throwable c = t;
        String msg = c.getMessage();
        int depth = 0;
        while (c.getCause() != null && c.getCause() != c && depth < 8) {
            c = c.getCause();
            if (c.getMessage() != null) { msg = c.getMessage(); }
            depth++;
        }
        return msg == null ? "" : msg;
    }
}
