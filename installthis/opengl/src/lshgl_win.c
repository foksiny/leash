/* Win32/WGL backend for the Leash OpenGL shim.
 *
 * All system libraries are loaded at runtime with LoadLibraryA, so the
 * archive needs no import libraries or linker flags beyond kernel32.
 * Provides both an offscreen framebuffer context and a real double-buffered
 * window with keyboard and relative-mouse input.
 */
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "lshgl_common.h"

#ifndef GET_X_LPARAM
#define GET_X_LPARAM(lp) ((int)(short)LOWORD(lp))
#define GET_Y_LPARAM(lp) ((int)(short)HIWORD(lp))
#endif

typedef HGLRC (*FN_WGLCREATECONTEXT)(HDC);
typedef BOOL (*FN_WGLDELETECONTEXT)(HGLRC);
typedef BOOL (*FN_WGLMAKECURRENT)(HDC, HGLRC);
typedef PROC (*FN_WGLGETPROCADDRESS)(LPCSTR);
typedef int (*FN_CHOOSEPIXELFORMAT)(HDC, const PIXELFORMATDESCRIPTOR*);
typedef BOOL (*FN_SETPixelFormat)(HDC, int, const PIXELFORMATDESCRIPTOR*);
typedef BOOL (*FN_SWAPBUFFERS)(HDC);
typedef int (*FN_GETTICKCOUNT)(void);

static HMODULE g_opengl = NULL;
static HMODULE g_user32 = NULL;
static HMODULE g_gdi32 = NULL;
static FN_WGLCREATECONTEXT wgl_create;
static FN_WGLDELETECONTEXT wgl_delete;
static FN_WGLMAKECURRENT wgl_make_current;
static FN_WGLGETPROCADDRESS wgl_get_proc;
static FN_CHOOSEPIXELFORMAT choose_pixel_format;
static FN_SETPixelFormat set_pixel_format;
static FN_SWAPBUFFERS swap_buffers_fn;
static FN_GETTICKCOUNT get_tick_count;

static HWND g_hwnd = NULL;
static HDC g_hdc = NULL;
static HGLRC g_hglrc = NULL;
static unsigned int g_fbo = 0;
static unsigned int g_tex = 0;
static unsigned int g_depth_rb = 0;
static int g_fb_w = 64;
static int g_fb_h = 64;

static int g_win_w = 0;
static int g_win_h = 0;
static bool g_window_mode = false;
static bool g_should_close = false;

static unsigned char g_key_down[256];
static unsigned char g_key_pressed[256];
static unsigned char g_mouse_down[8];
static unsigned char g_mouse_pressed[8];
static bool g_mouse_captured = false;
static int g_mouse_dx = 0;
static int g_mouse_dy = 0;
static int g_mouse_cx = 0;
static int g_mouse_cy = 0;

/* Keysyms exported by the Leash Gl API; indices are stable. */
static const unsigned long g_keysyms[] = {
    0x057, 0x061, 0x073, 0x064, 0x071, 0x065, 0x066, 0x067, 0x072,
    0x020, 0xFF1B, 0xFFE1,
    0x031, 0x032, 0x033, 0x034, 0x035, 0x036, 0x037, 0x038, 0x039
};
#define KEY_COUNT ((int)(sizeof(g_keysyms) / sizeof(g_keysyms[0])))

/* Windows virtual-key codes matching g_keysyms by index. */
static const int g_vk[] = {
    'W', 'A', 'S', 'D', 'Q', 'E', 'F', 'G', 'R',
    VK_SPACE, VK_ESCAPE, VK_SHIFT,
    '1', '2', '3', '4', '5', '6', '7', '8', '9'
};

static int keycode_for(unsigned long keysym) {
    int i = 0;
    while (i < KEY_COUNT) {
        if (g_keysyms[i] == keysym) return i;
        i = i + 1;
    }
    return -1;
}

void* lshgl_gl_symbol(const char* name) {
    if (!g_opengl) return NULL;
    void* fn = (void*)GetProcAddress(g_opengl, name);
    if (fn) return fn;
    if (wgl_get_proc) return (void*)wgl_get_proc(name);
    return NULL;
}

static bool load_system(void) {
    if (!g_opengl) g_opengl = LoadLibraryA("opengl32.dll");
    if (!g_user32) g_user32 = LoadLibraryA("user32.dll");
    if (!g_gdi32) g_gdi32 = LoadLibraryA("gdi32.dll");
    if (!g_opengl || !g_user32 || !g_gdi32) return false;
    wgl_create = (FN_WGLCREATECONTEXT)GetProcAddress(g_opengl, "wglCreateContext");
    wgl_delete = (FN_WGLDELETECONTEXT)GetProcAddress(g_opengl, "wglDeleteContext");
    wgl_make_current = (FN_WGLMAKECURRENT)GetProcAddress(g_opengl, "wglMakeCurrent");
    wgl_get_proc = (FN_WGLGETPROCADDRESS)GetProcAddress(g_opengl, "wglGetProcAddress");
    choose_pixel_format = (FN_CHOOSEPIXELFORMAT)GetProcAddress(g_gdi32, "ChoosePixelFormat");
    set_pixel_format = (FN_SETPixelFormat)GetProcAddress(g_gdi32, "SetPixelFormat");
    swap_buffers_fn = (FN_SWAPBUFFERS)GetProcAddress(g_gdi32, "SwapBuffers");
    get_tick_count = (FN_GETTICKCOUNT)GetProcAddress(g_user32, "GetTickCount");
    return wgl_create && wgl_delete && wgl_make_current && wgl_get_proc &&
           choose_pixel_format && set_pixel_format && swap_buffers_fn;
}

static bool set_window_pixel_format(HDC dc) {
    PIXELFORMATDESCRIPTOR pfd;
    memset(&pfd, 0, sizeof(pfd));
    pfd.nSize = sizeof(pfd);
    pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 24;
    pfd.cDepthBits = 24;
    pfd.iLayerType = PFD_MAIN_PLANE;
    int fmt = choose_pixel_format(dc, &pfd);
    return fmt != 0 && set_pixel_format(dc, fmt, &pfd);
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_CLOSE) {
        g_should_close = true;
        return 0;
    }
    if (msg == WM_SIZE) {
        int w = LOWORD(lp);
        int h = HIWORD(lp);
        if (w > 0 && h > 0) {
            g_win_w = w;
            g_win_h = h;
            g_mouse_cx = w / 2;
            g_mouse_cy = h / 2;
            PFNGLVIEWPORT viewport = (PFNGLVIEWPORT)lshgl_gl_symbol("glViewport");
            if (viewport) viewport(0, 0, w, h);
        }
        return 0;
    }
    if (msg == WM_KEYDOWN || msg == WM_KEYUP) {
        int i = 0;
        while (i < KEY_COUNT) {
            if ((int)wp == g_vk[i]) {
                unsigned char down = msg == WM_KEYDOWN ? 1 : 0;
                g_key_down[i] = down;
                if (down) g_key_pressed[i] = 1;
                return 0;
            }
            i = i + 1;
        }
        return 0;
    }
    if (msg == WM_LBUTTONDOWN || msg == WM_LBUTTONUP ||
        msg == WM_RBUTTONDOWN || msg == WM_RBUTTONUP ||
        msg == WM_MBUTTONDOWN || msg == WM_MBUTTONUP) {
        int button = (msg == WM_LBUTTONDOWN || msg == WM_LBUTTONUP) ? 1 :
                     (msg == WM_RBUTTONDOWN || msg == WM_RBUTTONUP) ? 2 : 3;
        unsigned char down = (msg == WM_LBUTTONDOWN || msg == WM_RBUTTONDOWN ||
                              msg == WM_MBUTTONDOWN) ? 1 : 0;
        g_mouse_down[button] = down;
        if (down) g_mouse_pressed[button] = 1;
        return 0;
    }
    if (msg == WM_MOUSEMOVE && g_mouse_captured) {
        int x = GET_X_LPARAM(lp);
        int y = GET_Y_LPARAM(lp);
        g_mouse_dx += x - g_mouse_cx;
        g_mouse_dy += y - g_mouse_cy;
        POINT centre;
        centre.x = g_mouse_cx;
        centre.y = g_mouse_cy;
        ClientToScreen(hwnd, &centre);
        SetCursorPos(centre.x, centre.y);
        return 0;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

static HWND create_native_window(int w, int h, const char* title, bool hidden) {
    HINSTANCE inst = GetModuleHandleA(NULL);
    WNDCLASSA wc;
    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = inst;
    wc.lpszClassName = hidden ? "LshGlOffscreen" : "LshGlWindow";
    RegisterClassA(&wc);
    DWORD style = hidden ? 0 : (WS_OVERLAPPEDWINDOW | WS_VISIBLE);
    return CreateWindowExA(0, wc.lpszClassName, title ? title : "Leash",
                           style, hidden ? 0 : 40, hidden ? 0 : 40,
                           w, h, NULL, NULL, inst, NULL);
}

void* lshgl_create_context(void) {
    if (g_hwnd && g_hglrc) return (void*)g_hglrc;
    if (!load_system()) return NULL;
    g_hwnd = create_native_window(g_fb_w, g_fb_h, "Leash offscreen", true);
    if (!g_hwnd) return NULL;
    g_hdc = GetDC(g_hwnd);
    if (!g_hdc || !set_window_pixel_format(g_hdc)) {
        if (g_hdc) ReleaseDC(g_hwnd, g_hdc);
        DestroyWindow(g_hwnd);
        g_hwnd = NULL;
        g_hdc = NULL;
        return NULL;
    }
    g_hglrc = wgl_create(g_hdc);
    if (!g_hglrc || !wgl_make_current(g_hdc, g_hglrc)) {
        if (g_hglrc) wgl_delete(g_hglrc);
        ReleaseDC(g_hwnd, g_hdc);
        DestroyWindow(g_hwnd);
        g_hwnd = NULL;
        g_hdc = NULL;
        g_hglrc = NULL;
        return NULL;
    }
    lshgl_setup_framebuffer();
    return (void*)g_hglrc;
}

void lshgl_destroy_context(void* ctx) {
    (void)ctx;
    if (g_window_mode) lshgl_close_window();
    lshgl_teardown_framebuffer();
    if (g_hglrc) {
        wgl_make_current(NULL, NULL);
        wgl_delete(g_hglrc);
        g_hglrc = NULL;
    }
    if (g_hdc && g_hwnd) ReleaseDC(g_hwnd, g_hdc);
    if (g_hwnd) DestroyWindow(g_hwnd);
    g_hdc = NULL;
    g_hwnd = NULL;
}

void* lshgl_open_window(int w, int h, const char* title) {
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    if (!load_system()) return NULL;
    if (g_hwnd && g_hglrc) lshgl_destroy_context(NULL);
    g_hwnd = create_native_window(w, h, title, false);
    if (!g_hwnd) return NULL;
    g_hdc = GetDC(g_hwnd);
    if (!g_hdc || !set_window_pixel_format(g_hdc)) {
        lshgl_destroy_context(NULL);
        return NULL;
    }
    g_hglrc = wgl_create(g_hdc);
    if (!g_hglrc || !wgl_make_current(g_hdc, g_hglrc)) {
        lshgl_destroy_context(NULL);
        return NULL;
    }
    g_win_w = w;
    g_win_h = h;
    g_mouse_cx = w / 2;
    g_mouse_cy = h / 2;
    g_window_mode = true;
    g_should_close = false;
    memset(g_key_down, 0, sizeof(g_key_down));
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
    memset(g_mouse_down, 0, sizeof(g_mouse_down));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    PFNGLVIEWPORT viewport = (PFNGLVIEWPORT)lshgl_gl_symbol("glViewport");
    if (viewport) viewport(0, 0, w, h);
    ShowWindow(g_hwnd, SW_SHOW);
    SetForegroundWindow(g_hwnd);
    return (void*)g_hwnd;
}

void lshgl_close_window(void) {
    g_mouse_captured = false;
    if (g_hglrc) {
        wgl_make_current(NULL, NULL);
        wgl_delete(g_hglrc);
        g_hglrc = NULL;
    }
    if (g_hdc && g_hwnd) ReleaseDC(g_hwnd, g_hdc);
    if (g_hwnd) DestroyWindow(g_hwnd);
    g_hdc = NULL;
    g_hwnd = NULL;
    g_window_mode = false;
    g_should_close = false;
}

void lshgl_poll_events(void) {
    if (!g_window_mode) return;
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
    MSG msg;
    while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
}

bool lshgl_should_close(void) { return g_should_close; }
void lshgl_request_close(void) { g_should_close = true; }
int lshgl_window_width(void) { return g_win_w; }
int lshgl_window_height(void) { return g_win_h; }
bool lshgl_key_down(int keysym) {
    int k = keycode_for((unsigned long)keysym);
    return k >= 0 && g_key_down[k] != 0;
}
bool lshgl_key_pressed(int keysym) {
    int k = keycode_for((unsigned long)keysym);
    return k >= 0 && g_key_pressed[k] != 0;
}
void lshgl_mouse_capture(int on) {
    if (!g_window_mode || !g_hwnd) return;
    if (!on) {
        if (g_mouse_captured) ReleaseCapture();
        ShowCursor(TRUE);
        g_mouse_captured = false;
        return;
    }
    if (g_mouse_captured) return;
    SetCapture(g_hwnd);
    ShowCursor(FALSE);
    g_mouse_cx = g_win_w / 2;
    g_mouse_cy = g_win_h / 2;
    POINT centre;
    centre.x = g_mouse_cx;
    centre.y = g_mouse_cy;
    ClientToScreen(g_hwnd, &centre);
    SetCursorPos(centre.x, centre.y);
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    g_mouse_captured = true;
}
bool lshgl_mouse_captured(void) { return g_mouse_captured; }
int lshgl_mouse_dx(void) { int v = g_mouse_dx; g_mouse_dx = 0; return v; }
int lshgl_mouse_dy(void) { int v = g_mouse_dy; g_mouse_dy = 0; return v; }
bool lshgl_mouse_down(int button) { return button > 0 && button < 8 && g_mouse_down[button]; }
bool lshgl_mouse_pressed(int button) { return button > 0 && button < 8 && g_mouse_pressed[button]; }

void lshgl_set_vsync(int enabled) {
    if (!wgl_get_proc) return;
    typedef int (*FN_SWAPINTERVAL)(int);
    FN_SWAPINTERVAL fn = (FN_SWAPINTERVAL)wgl_get_proc("wglSwapIntervalEXT");
    if (fn) fn(enabled ? 1 : 0);
}
void lshgl_swap_buffers(void) {
    if (g_window_mode && g_hdc && swap_buffers_fn) swap_buffers_fn(g_hdc);
}
int64_t lshgl_time_ms(void) { return get_tick_count ? (int64_t)get_tick_count() : 0; }
void lshgl_sleep_ms(int ms) {
    if (ms < 0) ms = 0;
    if (ms > 1000) ms = 1000;
    Sleep((DWORD)ms);
}

#include "lshgl_gl.inc"
