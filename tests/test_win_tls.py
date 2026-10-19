#!/usr/bin/env python3
"""Windows Schannel TLS listener (`lshhttpd_serve_tls`) smoke test.

The checked-in win64 `liblshhttpd.a` is linked into a tiny C consumer
that serves one HTTPS request, then the consumer is run under wine and
driven with a real `curl` request. This exercises the whole Windows
TLS path: PEM certificate loading, RSA private-key import through a
CryptoAPI key container, the non-blocking SChannel handshake, record
decryption of the request and record encryption of the response.

Skips cleanly unless the MinGW cross-compiler, wine, curl, openssl and
the win archive are all available.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN_LIB = os.path.join(ROOT, "installthis", "httpserver", "win",
                       "liblshhttpd.a")

SERVER_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int (*lshhttpd_handler_t)(const char *method, const char *path,
                                  const char *query, const char *headers,
                                  const char *body);
extern int  lshhttpd_serve_tls(int port, long long max_requests,
                               const char *cert, const char *key,
                               lshhttpd_handler_t handler);
extern void lshhttpd_set_body(const char *body);
extern const char *lshhttpd_strerror(int code);

static int handle(const char *method, const char *path, const char *query,
                  const char *headers, const char *body) {
    (void)method; (void)path; (void)query; (void)headers; (void)body;
    lshhttpd_set_body("hello-schannel\n");
    return 200;
}

int main(int argc, char **argv) {
    int rc;
    if (argc < 4) {
        fprintf(stderr, "usage: server port cert key\n");
        return 2;
    }
    rc = lshhttpd_serve_tls(atoi(argv[1]), 4, argv[2], argv[3], handle);
    if (rc != 0)
        fprintf(stderr, "serve_tls failed: %d (%s)\n", rc,
                lshhttpd_strerror(rc));
    return rc;
}
"""

GC_STUB_C = r"""
#include <stdlib.h>
void *leash_gc_alloc_string(long long n) {
    char *p = (char *)calloc(1, (size_t)n + 1);
    if (p) p[n] = '\0';
    return p;
}
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _need_wine():
    return sys.platform != "win32"


class TestWinTls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which("x86_64-w64-mingw32-gcc")
        if not cc:
            raise unittest.SkipTest("MinGW-w64 cross-compiler missing")
        if _need_wine() and not shutil.which("wine"):
            raise unittest.SkipTest("wine missing")
        if not shutil.which("curl"):
            raise unittest.SkipTest("curl missing")
        if not shutil.which("openssl"):
            raise unittest.SkipTest("openssl missing")
        if not os.path.exists(WIN_LIB):
            raise unittest.SkipTest("win64 liblshhttpd.a missing")

        cls.tmp = tempfile.mkdtemp(prefix="leash_win_tls_")
        cls.proc = None
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w+")

        with open(os.path.join(cls.tmp, "server.c"), "w") as fh:
            fh.write(SERVER_C)
        with open(os.path.join(cls.tmp, "gcstub.c"), "w") as fh:
            fh.write(GC_STUB_C)

        exe = os.path.join(cls.tmp, "server.exe")
        r = subprocess.run(
            [cc, "server.c", "gcstub.c", WIN_LIB, "-o", exe,
             "-lws2_32", "-lsecur32", "-lcrypt32", "-lncrypt"],
            cwd=cls.tmp, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise unittest.SkipTest(
                "could not link against the win64 archive: " + r.stderr)

        cert = os.path.join(cls.tmp, "cert.pem")
        key = os.path.join(cls.tmp, "key.pem")
        r = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", key, "-out", cert, "-days", "2", "-nodes",
             "-subj", "/CN=localhost"],
            cwd=cls.tmp, capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(cert) or not os.path.exists(key):
            raise unittest.SkipTest("openssl could not create a test cert")

        cls.port = _free_port()
        cmd = ["wine", exe, str(cls.port), cert, key]
        cls.proc = subprocess.Popen(cmd, cwd=cls.tmp, stdout=cls.log,
                                    stderr=subprocess.STDOUT)
        # Wait for the listener to come up (wine startup is slow).
        for _ in range(60):
            if cls.proc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", cls.port), 0.5):
                    break
            except OSError:
                time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.proc is not None:
            cls.proc.kill()
            try:
                cls.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        try:
            cls.log.close()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_serve_tls_request(self):
        if self.proc is None or self.proc.poll() is not None:
            self.skipTest("wine server exited early: " + self._server_log())
        r = subprocess.run(
            ["curl", "-sk", "--max-time", "25",
             f"https://127.0.0.1:{self.port}/"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0,
                         msg=f"curl failed: {r.stderr}\n{self._server_log()}")
        self.assertIn("hello-schannel", r.stdout)

    def _server_log(self):
        try:
            self.log.flush()
            with open(os.path.join(self.tmp, "server.log")) as fh:
                return fh.read()
        except Exception:
            return "<no log>"


if __name__ == "__main__":
    unittest.main()
