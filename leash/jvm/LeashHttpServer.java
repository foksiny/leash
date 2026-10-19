// Leash JVM httpserver shim: implements the @from("liblshhttpd.a") surface
// for the --target jvm backend over jdk.httpserver (com.sun.net.httpserver).
//
// Contract parity with installthis/httpserver/src/lshhttpd.c:
//   * same LSHD_E_* error codes and strerror() strings,
//   * one server at a time (re-entrant serve() fails with LSHD_E_ARGS),
//   * max_requests < 0 serves forever; >= 0 stops after that many
//     requests and returns the count (LSHD_E_ABORTED when shutdown()
//     fires before the first request completes),
//   * handler result clamped to 200..599 (else 500),
//   * set_body() replaces (last setter wins), add_header()/set_header()
//     append "Name: value" lines; control-character lines are dropped,
//   * Content-Length / Connection / Transfer-Encoding are owned by the
//     server: handler copies are skipped,
//   * "Server: leash-httpd/1.2" on every response,
//   * request headers reach the handler LF-normalized, request line
//     excluded, mirroring the native engine's header block.
//
// Differences from the native engine (inherent to jdk.httpserver):
//   * HTTP/1.1 only — no h2/h2c (the native listener ALPN-negotiates h2),
//   * the JVM engine rides the JDK's own Date header and keep-alive
//     handling, so response header order differs slightly.
//
// Requires Java 11+ at runtime, like the bundled LeashHttp client shim.

import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;

public final class LeashHttpServer {

    // Error codes (mirror lshhttpd.c's enum).
    private static final int E_ARGS    = -1;
    private static final int E_SOCKET  = -2;
    private static final int E_BIND    = -3;
    private static final int E_LISTEN  = -4;
    private static final int E_ACCEPT  = -5;
    private static final int E_MEMORY  = -6;
    private static final int E_ABORTED = -7;
    private static final int E_TLS     = -8;

    private static final String VERSION = "leash-httpd/1.2";
    private static final int MAX_BODY_BYTES = 64 * 1024 * 1024;

    // Single-server state (the native engine allows one at a time too).
    private static volatile boolean running = false;
    private static volatile boolean stopFlag = false;
    private static final Object LOCK = new Object();

    // Per-thread response state for the handler currently running
    // (jdk.httpserver serves exchanges sequentially on its dispatcher
    // thread by default, matching the native engine's single listener).
    private static final ThreadLocal<StringBuilder> respHeaders =
            new ThreadLocal<StringBuilder>() {
                protected StringBuilder initialValue() { return new StringBuilder(); }
            };
    private static final ThreadLocal<StringBuilder> respBody =
            new ThreadLocal<StringBuilder>() {
                protected StringBuilder initialValue() { return new StringBuilder(); }
            };
    private static volatile String detail = "";

    private LeashHttpServer() { }

    // ------------------------------------------------------------------
    // Public surface (names and shapes fixed by httpserver.lsh's @from)
    // ------------------------------------------------------------------

    public static String lshhttpd_version() {
        return VERSION;
    }

    public static String lshhttpd_strerror(int code) {
        String base;
        switch (code) {
            case E_ARGS:    base = "invalid arguments"; break;
            case E_SOCKET:  base = "could not create socket"; break;
            case E_BIND:    base = "could not bind the port"; break;
            case E_LISTEN:  base = "could not listen on the port"; break;
            case E_ACCEPT:  base = "connection error"; break;
            case E_MEMORY:  base = "out of memory or size limit reached"; break;
            case E_ABORTED: base = "server stopped before serving any request"; break;
            case E_TLS:     base = "TLS setup failed"; break;
            default:
                if (code >= 0) return "no error";
                base = "unknown error";
                break;
        }
        String d = detail;
        if (d != null && d.length() > 0) {
            return base + " (" + d + ")";
        }
        return base;
    }

    public static void lshhttpd_shutdown() {
        stopFlag = true;
        synchronized (LOCK) {
            LOCK.notifyAll();
        }
    }

    public static void lshhttpd_set_body(String body) {
        StringBuilder b = respBody.get();
        b.setLength(0); /* last setter wins */
        if (body != null && body.length() > 0) {
            b.append(body);
        }
    }

    public static void lshhttpd_add_header(String name, String value) {
        if (name == null || name.length() == 0) { return; }
        if (unsafe(name) || (value != null && unsafe(value))) {
            detail = "header dropped: illegal control character in name or value";
            return;
        }
        StringBuilder h = respHeaders.get();
        h.append(name).append(": ");
        if (value != null) { h.append(value); }
        h.append('\n');
    }

    public static void lshhttpd_set_header(String line) {
        if (line == null || line.length() == 0) { return; }
        int n = line.length();
        while (n > 0 && (line.charAt(n - 1) == '\n' || line.charAt(n - 1) == '\r')) {
            n--;
        }
        if (unsafe(line, n)) {
            detail = "header dropped: illegal control character in line";
            return;
        }
        StringBuilder h = respHeaders.get();
        h.append(line, 0, n).append('\n');
    }

    public static int lshhttpd_serve(int port, long max_requests,
                                     LshHttpHandler handler) {
        return serveImpl(port, max_requests, handler, null, null);
    }

    public static int lshhttpd_serve_tls(int port, long max_requests,
                                         String cert, String key,
                                         LshHttpHandler handler) {
        if (cert == null || cert.length() == 0 || key == null || key.length() == 0) {
            detail = "TLS serving needs a certificate and key file";
            return E_TLS;
        }
        return serveImpl(port, max_requests, handler, cert, key);
    }

    // ------------------------------------------------------------------
    // Core driver
    // ------------------------------------------------------------------

    private static int serveImpl(int port, long max_requests,
                                 LshHttpHandler handler,
                                 String certPath, String keyPath) {
        detail = "";
        if (running) {
            detail = "serve() is not re-entrant - only one server at a time";
            return E_ARGS;
        }
        if (handler == null) {
            detail = "no request handler provided";
            return E_ARGS;
        }
        if (port <= 0 || port > 65535) {
            detail = "invalid port";
            return E_ARGS;
        }

        SSLContext ssl = null;
        if (certPath != null) {
            try {
                ssl = buildSslContext(certPath, keyPath);
            } catch (Exception e) {
                detail = clip(e.getMessage() == null ? e.toString() : e.getMessage(), 200);
                return E_TLS;
            }
        }

        HttpServer server;
        try {
            if (ssl != null) {
                HttpsServer hs = HttpsServer.create(new InetSocketAddress(port), 64);
                hs.setHttpsConfigurator(new HttpsConfigurator(ssl));
                server = hs;
            } else {
                server = HttpServer.create(new InetSocketAddress(port), 64);
            }
        } catch (java.net.BindException e) {
            detail = "bind failed - is the port already in use?";
            return E_BIND;
        } catch (Exception e) {
            detail = clip(e.getMessage() == null ? e.toString() : e.getMessage(), 200);
            return E_SOCKET;
        }

        final long max = max_requests;
        final long[] served = { 0 };
        final long[] inFlight = { 0 };

        server.createContext("/", (HttpExchange ex) -> {
            try {
                synchronized (LOCK) {
                    inFlight[0] = inFlight[0] + 1;
                }
                serveOne(ex, handler);
            } finally {
                synchronized (LOCK) {
                    served[0] = served[0] + 1;
                    inFlight[0] = inFlight[0] - 1;
                    // Wake the accept loop: quota met, or shutdown()
                    // requested (from this handler or another thread).
                    if (stopFlag || (max >= 0 && served[0] >= max)) {
                        LOCK.notifyAll();
                    }
                }
            }
        });

        running = true;
        stopFlag = false;
        server.start();
        try {
            synchronized (LOCK) {
                // Drain-aware stop: shutdown() from inside a handler
                // stops AFTER the current response (native parity), so
                // only exit once no exchange is in flight.
                while (!stopFlag && (max < 0 || served[0] < max)) {
                    LOCK.wait(200);
                }
                while (inFlight[0] > 0) {
                    LOCK.wait(100);
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        server.stop(0);
        running = false;

        if (served[0] == 0 && stopFlag) {
            detail = "shutdown() requested";
            return E_ABORTED;
        }
        return (int) served[0];
    }

    private static void serveOne(HttpExchange ex, LshHttpHandler handler)
            throws java.io.IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        if (path == null || path.length() == 0) { path = "/"; }
        String query = ex.getRequestURI().getRawQuery();
        if (query == null) { query = ""; }
        String headers = normalizeRequestHeaders(ex);
        String body = readBody(ex.getRequestBody());

        StringBuilder h = respHeaders.get();
        StringBuilder b = respBody.get();
        h.setLength(0);
        b.setLength(0);
        detail = "";

        int status;
        try {
            status = handler.handle(method, path, query, headers, body);
        } catch (RuntimeException e) {
            // A Leash-level runtime error in the handler: reply 500 and
            // keep serving (the native engine would abort the process;
            // dropping the connection silently helps nobody).
            System.err.println("leash: httpserver handler raised: " + e);
            status = 500;
        } catch (ThreadDeath | OutOfMemoryError fatal) {
            throw fatal;
        } catch (Error e) {
            // Linkage/verify problems in the generated adapter or the
            // compiled handler: same contract — reply 500, keep serving.
            System.err.println("leash: httpserver handler error: " + e);
            status = 500;
        }
        if (status < 200 || status > 599) { status = 500; }

        byte[] bodyBytes = b.toString().getBytes(StandardCharsets.UTF_8);
        try {
            ex.getResponseHeaders().add("Server", VERSION);
            appendResponseHeaders(ex, h.toString());
        } catch (RuntimeException e) {
            System.err.println("leash: httpserver bad response header: " + e);
        }

        boolean headOnly = method.equals("HEAD");
        try {
            if (headOnly) {
                ex.sendResponseHeaders(status, -1);
                ex.close();
                return;
            }
            if (bodyBytes.length == 0) {
                ex.sendResponseHeaders(status, -1);
                ex.close();
                return;
            }
            ex.sendResponseHeaders(status, bodyBytes.length);
            ex.getResponseBody().write(bodyBytes);
            ex.close();
        } catch (java.io.IOException e) {
            // Client went away mid-response: nothing to salvage.
            throw e;
        }
    }

    // Request headers, LF-normalized "Name: value" per line, request
    // line excluded — one line per received value.
    private static String normalizeRequestHeaders(HttpExchange ex) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, List<String>> e : ex.getRequestHeaders().entrySet()) {
            String name = e.getKey();
            for (String v : e.getValue()) {
                sb.append(name).append(": ").append(v).append('\n');
            }
        }
        return sb.toString();
    }

    // Handler-provided header lines: LF -> separate adds, trailing CR and
    // horizontal space trimmed, control-character lines dropped, and the
    // framing headers the server owns are skipped.
    private static void appendResponseHeaders(HttpExchange ex, String hdr) {
        int start = 0;
        int n = hdr.length();
        while (start <= n) {
            int eol = hdr.indexOf('\n', start);
            int end = eol < 0 ? n : eol;
            String line = hdr.substring(start, end);
            while (line.endsWith("\r") || line.endsWith(" ") || line.endsWith("\t")) {
                line = line.substring(0, line.length() - 1);
            }
            if (line.length() > 0 && !unsafe(line)) {
                int colon = line.indexOf(':');
                String name = colon > 0 ? line.substring(0, colon).trim() : "";
                if (!name.equalsIgnoreCase("Content-Length")
                        && !name.equalsIgnoreCase("Connection")
                        && !name.equalsIgnoreCase("Transfer-Encoding")) {
                    ex.getResponseHeaders().add(name, line.substring(colon + 1).trim());
                }
            }
            if (eol < 0) { break; }
            start = eol + 1;
        }
    }

    private static String readBody(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[16384];
        int total = 0;
        for (;;) {
            int r = in.read(chunk);
            if (r < 0) { break; }
            if (r == 0) { continue; }
            total += r;
            if (total > MAX_BODY_BYTES) { break; }
            buf.write(chunk, 0, r);
        }
        in.close();
        return new String(buf.toByteArray(), StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------------
    // TLS material (PEM cert + PKCS#8 or PKCS#1 RSA key)
    // ------------------------------------------------------------------

    private static SSLContext buildSslContext(String certPath, String keyPath)
            throws Exception {
        byte[] certPem = readFile(certPath);
        byte[] keyPem = readFile(keyPath);

        CertificateFactory cf = CertificateFactory.getInstance("X.509");
        List<Certificate> chain = new ArrayList<Certificate>();
        int pos = 0;
        while (true) {
            int b = indexOf(certPem, "-----BEGIN CERTIFICATE-----", pos);
            if (b < 0) { break; }
            int e = indexOf(certPem, "-----END CERTIFICATE-----", b);
            if (e < 0) { break; }
            byte[] der = b64(certPem, b + "-----BEGIN CERTIFICATE-----".length(), e);
            chain.add(cf.generateCertificate(new java.io.ByteArrayInputStream(der)));
            pos = e + 1;
        }
        if (chain.isEmpty()) {
            throw new IllegalArgumentException("no CERTIFICATE block found in " + certPath);
        }

        PrivateKey key = readPrivateKey(keyPem, keyPath);
        KeyStore ks = KeyStore.getInstance("PKCS12");
        ks.load(null, null);
        ks.setKeyEntry("leash", key, "leash".toCharArray(),
                chain.toArray(new Certificate[0]));

        KeyManagerFactory kmf = KeyManagerFactory.getInstance("SunX509");
        kmf.init(ks, "leash".toCharArray());
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(kmf.getKeyManagers(), null, new SecureRandom());
        return ctx;
    }

    private static PrivateKey readPrivateKey(byte[] pem, String path) throws Exception {
        int b = indexOf(pem, "-----BEGIN PRIVATE KEY-----", 0);
        if (b >= 0) {
            int e = indexOf(pem, "-----END PRIVATE KEY-----", b);
            if (e < 0) { throw new IllegalArgumentException("unterminated PRIVATE KEY block in " + path); }
            byte[] der = b64(pem, b + "-----BEGIN PRIVATE KEY-----".length(), e);
            return KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(der));
        }
        b = indexOf(pem, "-----BEGIN RSA PRIVATE KEY-----", 0);
        if (b >= 0) {
            int e = indexOf(pem, "-----END RSA PRIVATE KEY-----", b);
            if (e < 0) { throw new IllegalArgumentException("unterminated RSA PRIVATE KEY block in " + path); }
            byte[] pkcs1 = b64(pem, b + "-----BEGIN RSA PRIVATE KEY-----".length(), e);
            byte[] pkcs8 = wrapPkcs1InPkcs8(pkcs1);
            return KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(pkcs8));
        }
        throw new IllegalArgumentException("no PRIVATE KEY block found in " + path
                + " (PKCS#8 'BEGIN PRIVATE KEY' or PKCS#1 'BEGIN RSA PRIVATE KEY' required)");
    }

    // Minimal PKCS#8 (PrivateKeyInfo) wrapper around a PKCS#1
    // RSAPrivateKey: SEQUENCE { version 0, algorithm rsaEncryption, OCTET
    // STRING <pkcs1 bytes> } — the same DER walk the native engines use.
    private static byte[] wrapPkcs1InPkcs8(byte[] pkcs1) {
        byte[] version = { 0x02, 0x01, 0x00 };              // INTEGER 0
        byte[] algId = { 0x30, 0x0d, 0x06, 0x09, 0x2a,      // SEQUENCE { rsaEncryption,
                (byte) 0x86, 0x48, (byte) 0x86, (byte) 0xf7,
                0x0d, 0x01, 0x01, 0x01, 0x05, 0x00 };       // NULL params }
        byte[] content = concat(version, concat(algId, derLenPrefixed(0x04, pkcs1)));
        return derLenPrefixed(0x30, content);
    }

    private static byte[] derLenPrefixed(int tag, byte[] content) {
        int n = content.length;
        if (n < 0x80) {
            byte[] out = new byte[2 + n];
            out[0] = (byte) tag;
            out[1] = (byte) n;
            System.arraycopy(content, 0, out, 2, n);
            return out;
        }
        int lenBytes = 0;
        int tmp = n;
        while (tmp > 0) { lenBytes++; tmp >>>= 8; }
        byte[] out = new byte[2 + lenBytes + n];
        out[0] = (byte) tag;
        out[1] = (byte) (0x80 | lenBytes);
        for (int i = 0; i < lenBytes; i++) {
            out[2 + i] = (byte) ((n >>> (8 * (lenBytes - 1 - i))) & 0xff);
        }
        System.arraycopy(content, 0, out, 2 + lenBytes, n);
        return out;
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    private static byte[] b64(byte[] src, int from, int to) {
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        for (int i = from; i < to; i++) {
            char c = (char) (src[i] & 0xff);
            if (c == '\r' || c == '\n' || c == ' ' || c == '\t') { continue; }
            body.write(src[i]);
        }
        return Base64.getDecoder().decode(body.toByteArray());
    }

    private static int indexOf(byte[] hay, String needle, int from) {
        byte[] n = needle.getBytes(StandardCharsets.US_ASCII);
        outer:
        for (int i = from; i + n.length <= hay.length; i++) {
            for (int j = 0; j < n.length; j++) {
                if (hay[i + j] != n[j]) { continue outer; }
            }
            return i;
        }
        return -1;
    }

    private static byte[] readFile(String path) throws Exception {
        FileInputStream f = new FileInputStream(path);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int r;
            while ((r = f.read(buf)) > 0) {
                out.write(buf, 0, r);
            }
            return out.toByteArray();
        } finally {
            f.close();
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    // True when the string would break the header block: CR/LF, NUL or any
    // control character other than TAB can inject or split headers.
    private static boolean unsafe(String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\r' || c == '\n' || c == 0) { return true; }
            if (c < 0x20 && c != '\t') { return true; }
            if (c == 0x7f) { return true; }
        }
        return false;
    }

    private static boolean unsafe(String s, int n) {
        for (int i = 0; i < n; i++) {
            char c = s.charAt(i);
            if (c == '\r' || c == '\n' || c == 0) { return true; }
            if (c < 0x20 && c != '\t') { return true; }
            if (c == 0x7f) { return true; }
        }
        return false;
    }

    private static String clip(String s, int max) {
        if (s == null) { return ""; }
        if (s.length() <= max) { return s; }
        return s.substring(0, max);
    }
}
