#!/usr/bin/env python3
"""End-to-end test for the built-in OpenGL stdlib module."""
import ctypes
import ctypes.util
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

XVFB = shutil.which("xvfb-run")

C_VOID, C_ULONG, C_UINT, C_INT = (ctypes.c_void_p, ctypes.c_ulong,
                                  ctypes.c_uint, ctypes.c_int)

XErrorFn = ctypes.CFUNCTYPE(C_INT, C_VOID, ctypes.c_void_p)
XIOErrorFn = ctypes.CFUNCTYPE(C_INT, C_VOID)
_x_bad_match = [False]


def _x_error_handler(dpy, event):
    _x_bad_match[0] = True
    return 0


def _x_io_error_handler(dpy):
    # The test program may finish (and its Xvfb exit) while we are still
    # synthesizing events; do not let libX11 kill the test process.
    return 0


_X_ERROR_HANDLER = XErrorFn(_x_error_handler)
_X_IO_ERROR_HANDLER = XIOErrorFn(_x_io_error_handler)


class XClientMessage(ctypes.Structure):
    _fields_ = [("type", C_INT), ("serial", C_ULONG), ("send_event", C_INT),
                ("display", C_VOID), ("window", C_ULONG),
                ("message_type", C_ULONG), ("format", C_INT),
                ("data", ctypes.c_long * 5)]


def x11_window(display, title):
    """Open a display and return (libX11, display, window) for `title`."""
    name = ctypes.util.find_library("X11")
    if not name:
        return None
    x11 = ctypes.CDLL(name)
    x11.XOpenDisplay.restype = C_VOID
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCloseDisplay.argtypes = [C_VOID]
    x11.XDefaultRootWindow.restype = C_ULONG
    x11.XDefaultRootWindow.argtypes = [C_VOID]
    x11.XQueryTree.restype = C_INT
    x11.XQueryTree.argtypes = [C_VOID, C_ULONG, ctypes.POINTER(C_ULONG),
                               ctypes.POINTER(C_ULONG),
                               ctypes.POINTER(ctypes.POINTER(C_ULONG)),
                               ctypes.POINTER(C_UINT)]
    x11.XFetchName.restype = C_INT
    x11.XFetchName.argtypes = [C_VOID, C_ULONG, ctypes.POINTER(ctypes.c_char_p)]
    x11.XFree.argtypes = [C_VOID]
    x11.XSync.argtypes = [C_VOID, C_INT]
    dpy = x11.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        return None

    def find(window):
        root, parent = C_ULONG(), C_ULONG()
        kids, count = ctypes.POINTER(C_ULONG)(), C_UINT()
        if not x11.XQueryTree(dpy, window, ctypes.byref(root),
                              ctypes.byref(parent), ctypes.byref(kids),
                              ctypes.byref(count)):
            return 0
        found = 0
        for i in range(count.value):
            wname = ctypes.c_char_p()
            if x11.XFetchName(dpy, kids[i], ctypes.byref(wname)) and wname.value:
                if title.encode() in wname.value:
                    found = kids[i]
                x11.XFree(wname)
            if not found:
                found = find(kids[i])
            if found:
                break
        if kids:
            x11.XFree(kids)
        return found

    win = find(x11.XDefaultRootWindow(dpy))
    if not win:
        x11.XCloseDisplay(dpy)
        return None
    return x11, dpy, win


def send_x11_key(title, keysym, display, release=True):
    """Send a real X11 key event (optionally held) to a titled window."""
    name = ctypes.util.find_library("Xtst")
    if not name:
        return False
    found = x11_window(display, title)
    if not found:
        return False
    x11, dpy, win = found
    xtst = ctypes.CDLL(name)
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XKeysymToKeycode.argtypes = [C_VOID, C_ULONG]
    x11.XSetInputFocus.argtypes = [C_VOID, C_ULONG, C_INT, C_ULONG]
    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XWarpPointer.argtypes = [C_VOID, C_ULONG, C_ULONG, C_INT, C_INT,
                                 C_UINT, C_UINT, C_INT, C_INT]
    x11.XFlush.argtypes = [C_VOID]
    xtst.XTestFakeKeyEvent.restype = C_INT
    xtst.XTestFakeKeyEvent.argtypes = [C_VOID, C_UINT, C_INT, C_ULONG]
    code = x11.XKeysymToKeycode(dpy, keysym)
    # The window may not be viewable on the first attempt. Trap BadMatch and
    # report failure so window_session retries instead of losing the key.
    _x_bad_match[0] = False
    x11.XSetErrorHandler(_X_ERROR_HANDLER)
    x11.XSetInputFocus(dpy, win, 1, 0)  # RevertToPointerRoot, CurrentTime
    x11.XSync(dpy, 0)
    x11.XSetErrorHandler(None)
    if _x_bad_match[0]:
        x11.XCloseDisplay(dpy)
        return False
    x11.XWarpPointer(dpy, 0, win, 0, 0, 0, 0, 120, 80)
    x11.XFlush(dpy)
    xtst.XTestFakeKeyEvent(dpy, code, 1, 0)
    if release:
        xtst.XTestFakeKeyEvent(dpy, code, 0, 0)
    x11.XSync(dpy, 0)
    x11.XCloseDisplay(dpy)
    return True


def send_wm_delete(title, display):
    """Deliver an ICCCM WM_DELETE_WINDOW request, as a window manager does.

    The message_type must be WM_PROTOCOLS and data[0] must contain the
    WM_DELETE_WINDOW protocol atom. This is what `wmctrl -c` sends.
    """
    found = x11_window(display, title)
    if not found:
        return False
    x11, dpy, win = found
    x11.XInternAtom.restype = C_ULONG
    x11.XInternAtom.argtypes = [C_VOID, ctypes.c_char_p, C_INT]
    x11.XSendEvent.restype = C_INT
    x11.XSendEvent.argtypes = [C_VOID, C_ULONG, C_INT, C_ULONG, C_VOID]
    protocols = x11.XInternAtom(dpy, b"WM_PROTOCOLS", 0)
    delete = x11.XInternAtom(dpy, b"WM_DELETE_WINDOW", 0)
    ev = XClientMessage(33, 0, 1, dpy, win, protocols, 32,
                        (delete, 0, 0, 0, 0))
    ok = x11.XSendEvent(dpy, win, 0, 0, ctypes.byref(ev))
    x11.XSync(dpy, 0)
    x11.XCloseDisplay(dpy)
    return bool(ok)


def send_mouse_motion(title, dx, dy, display):
    """Warp the pointer relative to the window centre a few times."""
    found = x11_window(display, title)
    if not found:
        return False
    name = ctypes.util.find_library("Xtst")
    if not name:
        return False
    x11, dpy, win = found
    xtst = ctypes.CDLL(name)
    x11.XFlush.argtypes = [C_VOID]
    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XSetIOErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XGetGeometry.argtypes = [C_VOID, C_ULONG, ctypes.POINTER(C_ULONG),
                                 ctypes.POINTER(C_INT), ctypes.POINTER(C_INT),
                                 ctypes.POINTER(C_UINT), ctypes.POINTER(C_UINT),
                                 ctypes.POINTER(C_UINT), ctypes.POINTER(C_UINT)]
    xtst.XTestFakeMotionEvent.argtypes = [C_VOID, C_INT, C_INT, C_INT, C_ULONG]
    root = C_ULONG()
    gx, gy = C_INT(), C_INT()
    gw, gh, bw, depth = C_UINT(), C_UINT(), C_UINT(), C_UINT()
    x11.XGetGeometry(dpy, win, ctypes.byref(root), ctypes.byref(gx),
                     ctypes.byref(gy), ctypes.byref(gw), ctypes.byref(gh),
                     ctypes.byref(bw), ctypes.byref(depth))
    centre_x = gx.value + gw.value // 2
    centre_y = gy.value + gh.value // 2
    x11.XSetErrorHandler(_X_ERROR_HANDLER)
    x11.XSetIOErrorHandler(_X_IO_ERROR_HANDLER)
    for _ in range(3):
        xtst.XTestFakeMotionEvent(dpy, -1, centre_x, centre_y, 0)
        x11.XFlush(dpy)
        time.sleep(0.1)
        xtst.XTestFakeMotionEvent(dpy, -1, centre_x + dx, centre_y + dy, 0)
        x11.XFlush(dpy)
        time.sleep(0.1)
    x11.XSetIOErrorHandler(None)
    x11.XSetErrorHandler(None)
    x11.XCloseDisplay(dpy)
    return True


def resize_x11_window(title, width, height, display):
    found = x11_window(display, title)
    if not found:
        return False
    x11, dpy, win = found
    x11.XResizeWindow.argtypes = [C_VOID, C_ULONG, C_UINT, C_UINT]
    x11.XResizeWindow(dpy, win, width, height)
    x11.XSync(dpy, 0)
    x11.XCloseDisplay(dpy)
    return True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")
STD = os.path.join(ROOT, "installthis")
ARCHIVE = os.path.join(STD, "opengl", "linux", "liblshgl.a")


class OpenGLModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(LEASHC):
            raise unittest.SkipTest("bin/leashc missing")
        if not os.path.exists(ARCHIVE):
            raise unittest.SkipTest("OpenGL shim not built (run sh installthis/opengl/src/build.sh)")
        if shutil.which("ldconfig") and not subprocess.run(
                ["sh", "-c", "ldconfig -p | grep -q libGL.so.1"],
                capture_output=True).returncode == 0:
            raise unittest.SkipTest("libGL not available")

    def test_headless_clear_and_readback(self):
        src = """use opengl::opengl::*;
fnc main() {
    ctx: pointer<void> = Gl.init();
    show(Gl.version());
    Gl.viewport(16, 16);
    Gl.clear_color(1.0, 0.0, 0.0, 1.0);
    Gl.clear();
    show(Gl.pixel(8, 8));
    show(Gl.error());
    Gl.clear_color(0.0, 1.0, 0.0, 1.0);
    Gl.clear();
    show(Gl.pixel(8, 8));
    Gl.close(ctx);
}
"""
        with tempfile.TemporaryDirectory(prefix="leash_opengl_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            r = subprocess.run([LEASHC, "run", path, "--other-imports", STD],
                               cwd=ROOT, capture_output=True, text=True,
                               errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = [l for l in r.stdout.splitlines()
                     if not l.startswith("--- Executed at")]
            self.assertGreaterEqual(len(lines), 4)
            self.assertRegex(lines[0], r"^\d+\.\d+")  # any GL implementation
            self.assertEqual(lines[1], "4278190335")  # 0xFF0000FF
            self.assertEqual(lines[2], "0")
            self.assertEqual(lines[3], "16711935")    # 0x00FF00FF

    def test_headless_perspective_and_lighting(self):
        """A lit 3D quad must survive the perspective projection."""
        src = """use opengl::opengl::*;
fnc main() {
    ctx: pointer<void> = Gl.init();
    Gl.viewport(64, 64);
    Gl.clear_color(0.2, 0.4, 0.8, 1.0);
    Gl.clear();
    Gl.matrix(Gl.projection());
    Gl.identity();
    Gl.frustum(-0.056016, 0.056016, -0.056016, 0.056016, 0.08, 180.0);
    Gl.matrix(Gl.modelview());
    Gl.identity();
    Gl.enable(Gl.depth_test());
    Gl.lighting(true);
    Gl.light_ambient(0.3, 0.3, 0.3, 1.0);
    Gl.light_directional(0.3, 0.8, 0.5, 1.0, 1.0, 1.0);
    Gl.color(1.0, 0.0, 0.0, 1.0);
    Gl.normal3(0.0, 0.0, 1.0);
    Gl.begin(Gl.quads());
    Gl.vertex3(-0.5, -0.5, -5.0);
    Gl.vertex3(0.5, -0.5, -5.0);
    Gl.vertex3(0.5, 0.5, -5.0);
    Gl.vertex3(-0.5, 0.5, -5.0);
    Gl.end();
    px: int<64> = Gl.pixel(32, 32);
    show("r=", (px >> 24) & 255, " g=", (px >> 16) & 255,
         " err=", Gl.error());
    Gl.close(ctx);
}
"""
        with tempfile.TemporaryDirectory(prefix="leash_opengl_3d_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            r = subprocess.run([LEASHC, "run", path, "--other-imports", STD],
                               cwd=ROOT, capture_output=True, text=True,
                               errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            m = re.search(r"r=(\d+) g=(\d+) err=(\d+)", r.stdout)
            self.assertIsNotNone(m, r.stdout + r.stderr)
            self.assertGreater(int(m.group(1)), 80)   # diffuse light present
            self.assertLess(int(m.group(2)), 80)      # red material tracked
            self.assertEqual(int(m.group(3)), 0)

    def test_all_targets_expose_the_same_api(self):
        src = """use opengl::opengl::*;
fnc main() {
    Gl.init();
    Gl.viewport(16, 16);
    Gl.clear_color(0.0, 0.0, 1.0, 1.0);
    Gl.clear();
    Gl.matrix(Gl.projection());
    Gl.identity();
    Gl.ortho(-1.0, 1.0, -1.0, 1.0, -1.0, 1.0);
    Gl.matrix(Gl.modelview());
    Gl.identity();
    Gl.color(1.0, 0.0, 0.0, 1.0);
    Gl.begin(Gl.quads());
    Gl.vertex3(-0.5, -0.5, 0.0);
    Gl.vertex3(0.5, -0.5, 0.0);
    Gl.vertex3(0.5, 0.5, 0.0);
    Gl.vertex3(-0.5, 0.5, 0.0);
    Gl.end();
    show(Gl.pixel(8, 8));
}
"""
        targets = ("linux64", "linux32", "linux-arm", "win64",
                   "macos", "macos-arm", "jvm")
        with tempfile.TemporaryDirectory(prefix="leash_gl_targets_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            for target in targets:
                with self.subTest(target=target):
                    r = subprocess.run([LEASHC, "check", path, "--target", target,
                                        "--other-imports", STD],
                                       cwd=ROOT, capture_output=True, text=True,
                                       errors="replace", timeout=180)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_jvm_software_fallback(self):
        if shutil.which("java") is None:
            self.skipTest("java not available")
        src = """use opengl::opengl::*;
fnc main() {
    Gl.init();
    Gl.viewport(16, 16);
    Gl.clear_color(0.0, 0.0, 1.0, 1.0);
    Gl.clear();
    Gl.matrix(Gl.projection());
    Gl.identity();
    Gl.ortho(-1.0, 1.0, -1.0, 1.0, -1.0, 1.0);
    Gl.matrix(Gl.modelview());
    Gl.identity();
    Gl.color(1.0, 0.0, 0.0, 1.0);
    Gl.begin(Gl.quads());
    Gl.vertex3(-0.5, -0.5, 0.0);
    Gl.vertex3(0.5, -0.5, 0.0);
    Gl.vertex3(0.5, 0.5, 0.0);
    Gl.vertex3(-0.5, 0.5, 0.0);
    Gl.end();
    show("center=", Gl.pixel(8, 8), " corner=", Gl.pixel(1, 1));
}
"""
        with tempfile.TemporaryDirectory(prefix="leash_gl_jvm_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            compiled = subprocess.run([LEASHC, "compile", path, "to",
                                       os.path.join(td, "jvmgl"), "--target", "jvm",
                                       "--other-imports", STD],
                                      cwd=ROOT, capture_output=True, text=True,
                                      errors="replace", timeout=180)
            self.assertEqual(compiled.returncode, 0,
                             compiled.stdout + compiled.stderr)
            r = subprocess.run(["java", "-cp", td, "jvmgl"], cwd=td,
                               capture_output=True, text=True, errors="replace",
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("center=-16776961 corner=65535", r.stdout)

    @unittest.skipUnless(XVFB, "xvfb-run not available")
    def test_real_window_render_and_lifecycle(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "Leash GL window test");
    show("opened=", w != null);
    show("size=", Gl.width(), "x", Gl.height());
    Gl.viewport(240, 160);
    Gl.clear_color(0.0, 0.0, 1.0, 1.0);
    Gl.clear();
    Gl.matrix(Gl.projection());
    Gl.identity();
    Gl.ortho(-1.0, 1.0, -1.0, 1.0, -1.0, 1.0);
    Gl.matrix(Gl.modelview());
    Gl.identity();
    Gl.color(1.0, 0.0, 0.0, 1.0);
    Gl.begin(Gl.quads());
    Gl.vertex2(-0.5, -0.5);
    Gl.vertex2(0.5, -0.5);
    Gl.vertex2(0.5, 0.5);
    Gl.vertex2(-0.5, 0.5);
    Gl.end();
    show("pixel=", Gl.pixel(120, 80));
    Gl.swap_buffers();
    Gl.poll_events();
    show("close_before=", Gl.should_close());
    show("key=", Gl.key_down(Gl.key_w()));
    Gl.request_close();
    show("close_after=", Gl.should_close());
    Gl.close(w);
    show("done");
}
"""
        with tempfile.TemporaryDirectory(prefix="leash_gl_window_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            r = subprocess.run([XVFB, "-a", LEASHC, "run", path,
                                "--other-imports", STD],
                               cwd=ROOT, capture_output=True, text=True,
                               errors="replace", timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = [l for l in r.stdout.splitlines()
                     if not l.startswith("--- Executed at")]
            self.assertIn("opened=true", lines)
            self.assertIn("size=240x160", lines)
            self.assertIn("pixel=4278190335", lines)  # red quad on blue clear
            self.assertIn("close_before=false", lines)
            self.assertIn("key=false", lines)
            self.assertIn("close_after=true", lines)
            self.assertIn("done", lines)

    @unittest.skipUnless(XVFB, "xvfb-run not available")
    def window_session(self, src, title, action):
        """Run a window program under Xvfb and apply an X11 action to it."""
        with tempfile.TemporaryDirectory(prefix="leash_gl_window_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            display_file = os.path.join(td, "display")
            proc = subprocess.Popen(
                [XVFB, "-a", "sh", "-c",
                 "echo \"$DISPLAY|$XAUTHORITY\" > \"$1\"; shift; exec \"$@\"", "sh",
                 display_file, LEASHC, "run", path, "--other-imports", STD],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, start_new_session=True)
            old_xauth = os.environ.get("XAUTHORITY")
            sent = False
            try:
                display = ""
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline and proc.poll() is None:
                    if os.path.exists(display_file):
                        parts = (open(display_file, encoding="utf-8")
                                 .read().strip().split("|", 1))
                        if len(parts) == 2:
                            display, xauthority = parts
                            os.environ["XAUTHORITY"] = xauthority
                    if display and action(display, title):
                        sent = True
                        break
                    time.sleep(0.2)
                out, _ = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                out = ""
            finally:
                if old_xauth is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = old_xauth
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate(timeout=30)
            return sent, out, proc.returncode

    @unittest.skipUnless(XVFB and ctypes.util.find_library("Xtst"),
                         "xvfb-run or libXtst not available")
    def test_window_keyboard_event(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "leash-key-test");
    i: int = 0;
    while i < 4000 {
        Gl.poll_events();
        if Gl.key_pressed(Gl.key_w()) {
            show("key_seen=true");
            stop;
        }
        Gl.sleep_ms(10);
        i = i + 1;
    }
    show("key_seen=false");
    Gl.close(w);
}
"""
        sent, out, rc = self.window_session(
            src, "leash-key-test",
            lambda display, title: send_x11_key(title, 0x057, display))
        self.assertTrue(sent, "could not find the Leash test window")
        self.assertIn("key_seen=true", out)
        self.assertEqual(rc, 0, out)

    @unittest.skipUnless(XVFB and ctypes.util.find_library("Xtst"),
                         "xvfb-run or libXtst not available")
    def test_window_held_vs_pressed(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "leash-held-test");
    polls: int = 0;
    first: bool = false;
    second: bool = true;
    i: int = 0;
    while i < 4000 {
        Gl.poll_events();
        if Gl.key_down(Gl.key_w()) {
            if polls == 0 {
                first = Gl.key_pressed(Gl.key_w());
                polls = 1;
            } else {
                second = Gl.key_pressed(Gl.key_w());
                polls = 2;
                stop;
            }
        }
        Gl.sleep_ms(10);
        i = i + 1;
    }
    show("held=", polls >= 2, " first_edge=", first, " second_edge=", second);
    Gl.close(w);
}
"""
        sent, out, rc = self.window_session(
            src, "leash-held-test",
            lambda display, title: send_x11_key(
                title, 0x057, display, release=False))
        self.assertTrue(sent, "could not find the Leash test window")
        self.assertIn("held=true first_edge=true second_edge=false", out)
        self.assertEqual(rc, 0, out)

    @unittest.skipUnless(XVFB and ctypes.util.find_library("Xtst"),
                         "xvfb-run or libXtst not available")
    def test_window_mouse_capture_and_dx(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "leash-mouse-test");
    Gl.mouse_capture(true);
    i: int = 0;
    seen: int = 0;
    seen_y: int = 0;
    while i < 400 {
        Gl.poll_events();
        if Gl.mouse_captured() {
            dx: int = Gl.mouse_dx();
            dy: int = Gl.mouse_dy();
            if dx != 0 && seen == 0 {
                seen = dx;
                seen_y = dy;
            }
        }
        Gl.sleep_ms(10);
        i = i + 1;
    }
    show("moved=", seen, ",", seen_y, " captured=", Gl.mouse_captured());
    Gl.close(w);
}
"""
        sent, out, rc = self.window_session(
            src, "leash-mouse-test",
            lambda display, title: send_mouse_motion(title, 80, 0, display))
        self.assertTrue(sent, "could not find the Leash test window")
        m = re.search(r"moved=(-?\d+),(-?\d+) captured=(true|false)", out)
        self.assertIsNotNone(m, out)
        self.assertGreater(int(m.group(1)), 0)  # rightward motion is positive
        self.assertEqual(m.group(3), "true")
        self.assertEqual(rc, 0, out)

    @unittest.skipUnless(XVFB, "xvfb-run not available")
    def test_window_manager_close(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "leash-close-test");
    i: int = 0;
    while !Gl.should_close() && i < 4000 {
        Gl.poll_events();
        Gl.sleep_ms(10);
        i = i + 1;
    }
    show("wm_closed=", Gl.should_close());
    Gl.close(w);
}
"""
        sent, out, rc = self.window_session(
            src, "leash-close-test",
            lambda display, title: send_wm_delete(title, display))
        self.assertTrue(sent, "could not find the Leash test window")
        self.assertIn("wm_closed=true", out)
        self.assertEqual(rc, 0, out)

    @unittest.skipUnless(XVFB, "xvfb-run not available")
    def test_window_resize(self):
        src = """use opengl::opengl::*;
fnc main() {
    w := Gl.open_window(240, 160, "leash-resize-test");
    i: int = 0;
    while i < 4000 {
        Gl.poll_events();
        if Gl.width() != 240 {
            show("size_after=", Gl.width(), "x", Gl.height());
            stop;
        }
        Gl.sleep_ms(10);
        i = i + 1;
    }
    Gl.close(w);
}
"""
        sent, out, rc = self.window_session(
            src, "leash-resize-test",
            lambda display, title: resize_x11_window(title, 320, 200, display))
        self.assertTrue(sent, "could not find the Leash test window")
        self.assertIn("size_after=320x200", out)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
