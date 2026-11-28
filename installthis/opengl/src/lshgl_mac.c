/* macOS CGL/AppKit backend for the Leash OpenGL shim.
 *
 * The framework symbols and Objective-C runtime are resolved at load time,
 * so the archive needs no SDK headers or link flags. A CGL pbuffer backs the
 * offscreen renderer; a real NSWindow with an NSOpenGLView backs windows.
 */
#include <stdbool.h>
#include <stdint.h>
#include "lshgl_common.h"

#ifndef NULL
#define NULL ((void*)0)
#endif

/* Declared locally so the archive cross-compiles without an Apple SDK. */
extern void* dlopen(const char*, int);
extern void* dlsym(void*, const char*);
#define RTLD_LAZY 1
#define RTLD_LOCAL 0
typedef struct { long tv_sec; int tv_usec; } LshTimeval;
extern int gettimeofday(LshTimeval*, void*);
extern int usleep(unsigned int);

static void* lsh_memset(void* dst, int c, unsigned long n) {
    unsigned char* p = (unsigned char*)dst;
    while (n > 0) { *p++ = (unsigned char)c; n--; }
    return dst;
}
#define memset lsh_memset

typedef void* id;
typedef void* SEL;
typedef void* Class;
typedef struct { double x, y, width, height; } LshNSRect;
typedef struct { double x, y; } LshCGPoint;

typedef id (*MSG0)(id, SEL);
typedef id (*MSG1)(id, SEL, id);
typedef id (*MSG_BOOL)(id, SEL, bool);
typedef id (*MSG_UINT)(id, SEL, unsigned long);
typedef id (*MSG_USHORT)(id, SEL, unsigned short);
typedef id (*MSG_DOUBLE)(id, SEL, double);
typedef id (*MSG_INITWIN)(id, SEL, LshNSRect, unsigned long, unsigned long, bool);
typedef id (*MSG_INITVIEW)(id, SEL, LshNSRect, id);
typedef id (*MSG_UNTIL)(id, SEL, unsigned long, id, id, bool);
typedef int (*CGLCREATEPBUFFER)(unsigned long, unsigned long, unsigned long,
                                unsigned long, unsigned long, void**);
typedef void (*CGLDESTROYPBUFFER)(void*);
typedef int (*CGLCHOOSEPIXELFORMAT)(const int*, void**, unsigned long*);
typedef void (*CGLDESTROYPIXELFORMAT)(void*);
typedef int (*CGLCREATECONTEXT)(void*, void*, void**);
typedef int (*CGLDESTROYCONTEXT)(void*);
typedef int (*CGLSETCURRENTCONTEXT)(void*);
typedef int (*CGLSETPBUFFER)(void*, void*, unsigned long, unsigned long, unsigned long);
typedef void (*CGBWARPPOINTER)(LshCGPoint);

static void* g_opengl = NULL;
static void* g_appkit = NULL;
static void* g_objc = NULL;
static void* g_cg = NULL;

static MSG0 msg0;
static MSG1 msg1;
static MSG_BOOL msg_bool;
static MSG_UINT msg_uint;
static MSG_USHORT msg_ushort;
static MSG_INITWIN msg_init_win;
static MSG_INITVIEW msg_init_view;
static MSG_UNTIL msg_until;
static id (*objc_msg_send)(id, SEL, ...);
static id (*objc_get_class)(const char*);
static SEL (*sel_register)(const char*);

static CGLCREATEPBUFFER cgl_create_pbuffer;
static CGLDESTROYPBUFFER cgl_destroy_pbuffer;
static CGLCHOOSEPIXELFORMAT cgl_choose_format;
static CGLDESTROYPIXELFORMAT cgl_destroy_format;
static CGLCREATECONTEXT cgl_create_context;
static CGLDESTROYCONTEXT cgl_destroy_context;
static CGLSETCURRENTCONTEXT cgl_set_current;
static CGLSETPBUFFER cgl_set_pbuffer;
static CGBWARPPOINTER cg_warp_pointer;

static void* g_context = NULL;
static void* g_pixel_format = NULL;
static void* g_pbuffer = NULL;
static unsigned int g_fbo = 0;
static unsigned int g_tex = 0;
static unsigned int g_depth_rb = 0;
static int g_fb_w = 64;
static int g_fb_h = 64;

static id g_app = NULL;
static id g_window = NULL;
static id g_view = NULL;
static id g_gl_context = NULL;
static int g_win_w = 0;
static int g_win_h = 0;
static bool g_window_mode = false;
static bool g_should_close = false;

static unsigned char g_key_down[256];
static unsigned char g_key_pressed[256];
static bool g_pause_requested = false;
static unsigned char g_mouse_down[8];
static unsigned char g_mouse_pressed[8];
static bool g_mouse_captured = false;
static int g_mouse_dx = 0;
static int g_mouse_dy = 0;

static const unsigned long g_keysyms[] = {
    0x057, 0x061, 0x073, 0x064, 0x071, 0x065, 0x066, 0x067, 0x072,
    0x020, 0xFF1B, 0xFFE1,
    0x043, 0xFFBE, 0xFFBD, 0xFFB3, 0xFFC2,
    0x031, 0x032, 0x033, 0x034, 0x035, 0x036, 0x037, 0x038, 0x039
};
/* macOS virtual keycodes in the same order. */
static const unsigned short g_vk[] = {
    13, 0, 1, 2, 12, 14, 3, 5, 15,
    49, 53, 56, 8, 122, 120, 99, 96, 18, 19, 20, 21, 23, 22, 26, 28, 25
};
#define KEY_COUNT ((int)(sizeof(g_keysyms) / sizeof(g_keysyms[0])))

static int keycode_for(unsigned long keysym) {
    int i = 0;
    while (i < KEY_COUNT) {
        if (g_keysyms[i] == keysym) return i;
        i = i + 1;
    }
    return -1;
}

void* lshgl_gl_symbol(const char* name) {
    return g_opengl ? dlsym(g_opengl, name) : NULL;
}

static SEL sel(const char* name) { return sel_register(name); }
static id send0(id obj, const char* name) { return msg0(obj, sel(name)); }
static id send1(id obj, const char* name, id a) { return msg1(obj, sel(name), a); }
static id send_bool(id obj, const char* name, bool a) { return msg_bool(obj, sel(name), a); }
static id send_uint(id obj, const char* name, unsigned long a) { return msg_uint(obj, sel(name), a); }

static id ns_string(const char* text) {
    Class cls = objc_get_class("NSString");
    return msg1((id)cls, sel("stringWithUTF8String:"), (id)text);
}

static bool load_frameworks(void) {
    if (!g_opengl) g_opengl = dlopen("/System/Library/Frameworks/OpenGL.framework/OpenGL",
                                     RTLD_LAZY | RTLD_LOCAL);
    if (!g_appkit) g_appkit = dlopen("/System/Library/Frameworks/AppKit.framework/AppKit",
                                     RTLD_LAZY | RTLD_LOCAL);
    if (!g_objc) g_objc = dlopen("/usr/lib/libobjc.A.dylib", RTLD_LAZY | RTLD_LOCAL);
    if (!g_cg) g_cg = dlopen("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices",
                             RTLD_LAZY | RTLD_LOCAL);
    if (!g_opengl || !g_appkit || !g_objc) return false;
    objc_msg_send = (id (*)(id, SEL, ...))dlsym(g_objc, "objc_msgSend");
    objc_get_class = (id (*)(const char*))dlsym(g_objc, "objc_getClass");
    sel_register = (SEL (*)(const char*))dlsym(g_objc, "sel_registerName");
    msg0 = (MSG0)objc_msg_send;
    msg1 = (MSG1)objc_msg_send;
    msg_bool = (MSG_BOOL)objc_msg_send;
    msg_uint = (MSG_UINT)objc_msg_send;
    msg_ushort = (MSG_USHORT)objc_msg_send;
    msg_init_win = (MSG_INITWIN)objc_msg_send;
    msg_init_view = (MSG_INITVIEW)objc_msg_send;
    msg_until = (MSG_UNTIL)objc_msg_send;
    if (!objc_msg_send || !objc_get_class || !sel_register) return false;
    cgl_create_pbuffer = (CGLCREATEPBUFFER)dlsym(g_opengl, "CGLCreatePBuffer");
    cgl_destroy_pbuffer = (CGLDESTROYPBUFFER)dlsym(g_opengl, "CGLDestroyPBuffer");
    cgl_choose_format = (CGLCHOOSEPIXELFORMAT)dlsym(g_opengl, "CGLChoosePixelFormat");
    cgl_destroy_format = (CGLDESTROYPIXELFORMAT)dlsym(g_opengl, "CGLDestroyPixelFormat");
    cgl_create_context = (CGLCREATECONTEXT)dlsym(g_opengl, "CGLCreateContext");
    cgl_destroy_context = (CGLDESTROYCONTEXT)dlsym(g_opengl, "CGLDestroyContext");
    cgl_set_current = (CGLSETCURRENTCONTEXT)dlsym(g_opengl, "CGLSetCurrentContext");
    cgl_set_pbuffer = (CGLSETPBUFFER)dlsym(g_opengl, "CGLSetPBuffer");
    if (g_cg) cg_warp_pointer = (CGBWARPPOINTER)dlsym(g_cg, "CGWarpMouseCursorPosition");
    return cgl_create_context && cgl_destroy_context && cgl_set_current;
}

void* lshgl_create_context(void) {
    if (g_window && g_gl_context) return g_gl_context;
    if (g_context) return g_context;
    if (!load_frameworks()) return NULL;
    if (!g_app) {
        Class app_cls = objc_get_class("NSApplication");
        g_app = send0((id)app_cls, "sharedApplication");
        if (g_app) send_uint(g_app, "setActivationPolicy:", 0);
    }
    int attrs[] = { 8, 32, 11, 8, 12, 24, 5, 0 };
    unsigned long count = 0;
    if (!cgl_choose_format || cgl_choose_format(attrs, &g_pixel_format, &count) != 0) return NULL;
    void* ctx = NULL;
    if (!cgl_create_context(g_pixel_format, NULL, &ctx) || !ctx) return NULL;
    if (!cgl_set_current(ctx)) return NULL;
    g_context = ctx;
    if (cgl_create_pbuffer && cgl_set_pbuffer) {
        void* pbuf = NULL;
        if (cgl_create_pbuffer((unsigned long)g_fb_w, (unsigned long)g_fb_h,
                               0x0DE1, 0x1908, 0, &pbuf) == 0 && pbuf) {
            cgl_set_pbuffer(ctx, pbuf, 0, 0, 0);
            g_pbuffer = pbuf;
        }
    }
    lshgl_setup_framebuffer();
    return g_context;
}

void lshgl_destroy_context(void* ctx) {
    (void)ctx;
    if (g_window_mode) lshgl_close_window();
    lshgl_teardown_framebuffer();
    if (g_context) {
        cgl_set_current(NULL);
        cgl_destroy_context(g_context);
        g_context = NULL;
    }
    if (g_pbuffer && cgl_destroy_pbuffer) cgl_destroy_pbuffer(g_pbuffer);
    if (g_pixel_format && cgl_destroy_format) cgl_destroy_format(g_pixel_format);
    g_pbuffer = NULL;
    g_pixel_format = NULL;
}

void* lshgl_open_window(int w, int h, const char* title) {
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    if (!load_frameworks()) return NULL;
    if (g_context) lshgl_destroy_context(NULL);
    if (!g_app) {
        Class app_cls = objc_get_class("NSApplication");
        g_app = send0((id)app_cls, "sharedApplication");
        if (g_app) send_uint(g_app, "setActivationPolicy:", 0);
    }
    LshNSRect frame = { 40, 40, (double)w, (double)h };
    Class win_cls = objc_get_class("NSWindow");
    id win = send0((id)win_cls, "alloc");
    if (!win) return NULL;
    unsigned long mask = 1 | 2 | 4 | 8;  /* titled, closable, mini, resizable */
    win = msg_init_win(win, sel("initWithContentRect:styleMask:backing:defer:"),
                       frame, mask, 2, false);
    if (!win) return NULL;
    if (title) send1(win, "setTitle:", ns_string(title));
    int attrs[] = { 8, 32, 11, 8, 12, 24, 5, 0 };
    id pf = msg1((id)objc_get_class("NSOpenGLPixelFormat"),
                 sel("alloc"), NULL);
    /* initWithAttributes: takes the C array directly. */
    typedef id (*MSG_ATTRS)(id, SEL, const int*);
    pf = ((MSG_ATTRS)objc_msg_send)(pf, sel("initWithAttributes:"), attrs);
    LshNSRect vframe = { 0, 0, (double)w, (double)h };
    id view = msg_init_view(send0((id)objc_get_class("NSOpenGLView"), "alloc"),
                            sel("initWithFrame:pixelFormat:"), vframe, pf);
    if (!view) { send1(win, "release", NULL); return NULL; }
    send1(win, "setContentView:", view);
    id ctx = send0(view, "openGLContext");
    send0(ctx, "makeCurrentContext");
    send1(win, "makeKeyAndOrderFront:", NULL);
    send_bool(g_app, "activateIgnoringOtherApps:", true);
    g_window = win;
    g_view = view;
    g_gl_context = ctx;
    g_win_w = w;
    g_win_h = h;
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
    return g_gl_context;
}

void lshgl_close_window(void) {
    if (g_mouse_captured) {
        Class cursor_cls = objc_get_class("NSCursor");
        send0((id)cursor_cls, "unhide");
    }
    g_mouse_captured = false;
    if (g_gl_context) send0(g_gl_context, "clearCurrentContext");
    if (g_window) send1(g_window, "close", NULL);
    g_window = NULL;
    g_view = NULL;
    g_gl_context = NULL;
    g_window_mode = false;
    g_should_close = false;
}

void lshgl_poll_events(void) {
    if (!g_window_mode || !g_app) return;
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
    id mode = ns_string("kCFRunLoopDefaultMode");
    while (true) {
        id ev = msg_until(g_app, sel("nextEventMatchingMask:untilDate:inMode:dequeue:"),
                          ~(unsigned long)0, NULL, mode, true);
        if (!ev) break;
        typedef unsigned long (*MSG_TYPE)(id, SEL);
        unsigned long type = ((MSG_TYPE)objc_msg_send)(ev, sel("type"));
        if (type == 10 || type == 11) {
            typedef unsigned short (*MSG_KEYCODE)(id, SEL);
            unsigned short code = ((MSG_KEYCODE)objc_msg_send)(ev, sel("keyCode"));
            if (type == 10 && code == 53) g_pause_requested = true;  /* Escape */
            int i = 0;
            while (i < KEY_COUNT) {
                if (g_vk[i] == code) {
                    unsigned char down = type == 10 ? 1 : 0;
                    g_key_down[i] = down;
                    if (down) g_key_pressed[i] = 1;
                    break;
                }
                i = i + 1;
            }
        } else if (type == 1 || type == 2 || type == 3 || type == 4 || type == 25 || type == 26) {
            int button = 0;
            if (type == 1 || type == 2) button = 1;
            else if (type == 3 || type == 4) button = 2;
            else button = 3;
            unsigned char down = (type == 1 || type == 3 || type == 25) ? 1 : 0;
            g_mouse_down[button] = down;
            if (down) g_mouse_pressed[button] = 1;
        } else if (type == 5 && g_mouse_captured) {
            typedef double (*MSG_F64)(id, SEL);
            MSG_F64 f64 = (MSG_F64)objc_msg_send;
            g_mouse_dx += (int)f64(ev, sel("deltaX"));
            g_mouse_dy -= (int)f64(ev, sel("deltaY"));
        }
        send1(g_app, "sendEvent:", ev);
    }
    if (g_window && !send_bool(g_window, "isVisible", false)) g_should_close = true;
}

bool lshgl_should_close(void) { return g_should_close; }
void lshgl_request_close(void) { g_should_close = true; }
int lshgl_window_width(void) { return g_win_w; }
int lshgl_window_height(void) { return g_win_h; }
bool lshgl_key_down(int keysym) {
    int k = keycode_for((unsigned long)keysym);
    return k >= 0 && g_key_down[k] != 0;
}
bool lshgl_pause_requested(void) {
    bool v = g_pause_requested;
    g_pause_requested = false;
    return v;
}
bool lshgl_key_pressed(int keysym) {
    int k = keycode_for((unsigned long)keysym);
    return k >= 0 && g_key_pressed[k] != 0;
}

void lshgl_mouse_capture(int on) {
    if (!g_window_mode || !g_window) return;
    Class cursor_cls = objc_get_class("NSCursor");
    if (!on) {
        if (g_mouse_captured) send0((id)cursor_cls, "unhide");
        g_mouse_captured = false;
        return;
    }
    if (g_mouse_captured) return;
    send_bool(g_window, "acceptMouseMovedEvents:", true);
    send0((id)cursor_cls, "hide");
    if (cg_warp_pointer) {
        LshCGPoint p = { (double)g_win_w / 2.0, (double)g_win_h / 2.0 };
        cg_warp_pointer(p);
    }
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    g_mouse_captured = true;
}
bool lshgl_mouse_captured(void) { return g_mouse_captured; }
int lshgl_mouse_dx(void) { int v = g_mouse_dx; g_mouse_dx = 0; return v; }
int lshgl_mouse_dy(void) { int v = g_mouse_dy; g_mouse_dy = 0; return v; }
/* AppKit exposes absolute pointer position as a struct returned via
 * objc_msgSend_stret; the menu also supports keyboard navigation, so the
 * portable fallback reports the window centre here. */
int lshgl_mouse_x(void) { return g_win_w / 2; }
int lshgl_mouse_y(void) { return g_win_h / 2; }

bool lshgl_mouse_down(int button) { return button > 0 && button < 8 && g_mouse_down[button]; }
bool lshgl_mouse_pressed(int button) { return button > 0 && button < 8 && g_mouse_pressed[button]; }

void lshgl_set_vsync(int enabled) {
    typedef int (*FN_CGLSETPARAM)(void*, int, const int*);
    FN_CGLSETPARAM fn = (FN_CGLSETPARAM)lshgl_gl_symbol("CGLSetParameter");
    void* context = g_window_mode ? g_gl_context : g_context;
    int interval = enabled ? 1 : 0;
    if (fn && context) fn(context, 222, &interval);  /* kCGLCPSwapInterval */
}
void lshgl_swap_buffers(void) {
    if (g_window_mode && g_gl_context) send0(g_gl_context, "flushBuffer");
    else {
        typedef int (*FN_FLUSH)(void*);
        FN_FLUSH fn = (FN_FLUSH)lshgl_gl_symbol("CGLFlushDrawable");
        if (fn && g_context) fn(g_context);
    }
}
int64_t lshgl_time_ms(void) {
    LshTimeval tv;
    if (gettimeofday(&tv, NULL) != 0) return 0;
    return (int64_t)tv.tv_sec * 1000 + (int64_t)tv.tv_usec / 1000;
}
void lshgl_sleep_ms(int ms) {
    if (ms < 0) ms = 0;
    if (ms > 1000) ms = 1000;
    usleep((unsigned int)(ms * 1000));
}

#include "lshgl_gl.inc"
