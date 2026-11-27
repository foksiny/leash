/* Native OpenGL binding for Leash.
 *
 * Loads GL, EGL and X11 at runtime with dlopen so the Leash module needs only
 * the small static shim (no system development packages or link flags).
 * Rendering can target either a surfaceless EGL framebuffer (headless) or a
 * real double-buffered X11/GLX window (interactive).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <dlfcn.h>

typedef void* EGLDisplay;
typedef void* EGLContext;
typedef void* EGLConfig;
typedef int EGLint;
typedef unsigned int EGLBoolean;
typedef int EGLenum;
typedef void (*PFNGLVIEWPORT)(int, int, int, int);
typedef void (*PFNGLGENTEXTURES)(int, unsigned int*);
typedef void (*PFNGLBINDTEXTURE)(unsigned int, unsigned int);
typedef void (*PFNGLTEXIMAGE2D)(unsigned int, int, int, int, int, int, unsigned int, unsigned int, const void*);
typedef void (*PFNGLTEXPARAMETERI)(unsigned int, unsigned int, int);
typedef void (*PFNGLGENFRAMEBUFFERS)(int, unsigned int*);
typedef void (*PFNGLBINDFRAMEBUFFER)(unsigned int, unsigned int);
typedef void (*PFNGLFRAMEBUFFERTEXTURE2D)(unsigned int, unsigned int, unsigned int, unsigned int, int);
typedef unsigned int (*PFNGLCHECKFRAMEBUFFERSTATUS)(unsigned int);
typedef void (*PFNGLCLEARCOLOR)(float, float, float, float);
typedef void (*PFNGLCLEAR)(unsigned int);
typedef void (*PFNGLREADPIXELS)(int, int, int, int, unsigned int, unsigned int, void*);
typedef unsigned int (*PFNGLGETERROR)(void);
typedef const unsigned char* (*PFNGLGETSTRING)(unsigned int);
typedef void (*PFNGLBEGIN)(unsigned int);
typedef void (*PFNGLEND)(void);
typedef void (*PFNGLCOLOR4F)(float, float, float, float);
typedef void (*PFNGLVERTEX2F)(float, float);
typedef void (*PFNGLMATRIXMODE)(unsigned int);
typedef void (*PFNGLLOADIDENTITY)(void);
typedef void (*PFNGLORTHO)(double, double, double, double, double, double);
typedef void (*PFNGLSWAPBUFFERS)(void*, unsigned long);
typedef void (*PFNGLVERTEX3F)(float, float, float);
typedef void (*PFNGLNORMAL3F)(float, float, float);
typedef void (*PFNGLTRANSLATEF)(float, float, float);
typedef void (*PFNGLROTATEF)(float, float, float, float);
typedef void (*PFNGLSCALEF)(float, float, float);
typedef void (*PFNGLFRUSTUM)(double, double, double, double, double, double);
typedef void (*PFNGLENABLE)(unsigned int);
typedef void (*PFNGLDISABLE)(unsigned int);
typedef void (*PFNGLDEPTHFUNC)(unsigned int);
typedef void (*PFNGLDEPTHMASK)(unsigned char);
typedef void (*PFNGLBLENDFUNC)(unsigned int, unsigned int);
typedef void (*PFNGLLINEWIDTH)(float);
typedef void (*PFNGLSHADEMODEL)(unsigned int);
typedef void (*PFNGLCOLORMATERIAL)(unsigned int, unsigned int);
typedef void (*PFNGLLIGHTFV)(unsigned int, unsigned int, const float*);
typedef void (*PFNGLLIGHTMODELFV)(unsigned int, const float*);
typedef void (*PFNGLFOGF)(unsigned int, float);
typedef void (*PFNGLFOGFV)(unsigned int, const float*);
typedef void (*PFNGLNEWLIST)(unsigned int, unsigned int);
typedef void (*PFNGLENDLIST)(void);
typedef void (*PFNGLCALLLIST)(unsigned int);
typedef void (*PFNGLDELETELISTS)(unsigned int, int);
typedef unsigned int (*PFNGLGENLISTS)(int);
typedef void (*PFNGLGENRENDERBUFFERS)(int, unsigned int*);
typedef void (*PFNGLBINDRENDERBUFFER)(unsigned int, unsigned int);
typedef void (*PFNGLRENDERBUFFERSTORAGE)(unsigned int, unsigned int, int, int);
typedef void (*PFNGLFRAMEBUFFERRENDERBUFFER)(unsigned int, unsigned int, unsigned int, unsigned int);
typedef void (*PFNGLDELETERENDERBUFFERS)(int, const unsigned int*);
typedef void (*PFNGLDELETEFRAMEBUFFERS)(int, const unsigned int*);
typedef void (*PFNGLDELETETEXTURES)(int, const unsigned int*);
typedef void* (*PFNGLXGETPROCADDRESSARB)(const unsigned char*);
typedef void (*PFNGLXSWAPINTERVALEXT)(void*, unsigned long, int);
typedef int (*PFNGLXSWAPINTERVALMESA)(unsigned int);
typedef int (*PFNGLXSWAPINTERVALSGI)(int);

#define EGL_NO_DISPLAY ((EGLDisplay)0)
#define EGL_NO_CONTEXT ((EGLContext)0)
#define EGL_DEFAULT_DISPLAY ((void*)0)
#define EGL_OPENGL_API 0x30A2
#define EGL_OPENGL_BIT 0x0008
#define EGL_RENDERABLE_TYPE 0x3040
#define EGL_NONE 0x3038
#define EGL_PLATFORM_SURFACELESS_MESA 0x30FD
#define GL_VERSION 0x1F02
#define GL_RENDERER 0x1F01
#define GL_VENDOR 0x1F00
#define GL_COLOR_BUFFER_BIT 0x00004000
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define GL_TEXTURE_2D 0x0DE1
#define GL_RGBA8 0x8058
#define GL_TEXTURE_MIN_FILTER 0x2801
#define GL_TEXTURE_MAG_FILTER 0x2800
#define GL_NEAREST 0x2600
#define GL_FRAMEBUFFER 0x8D40
#define GL_COLOR_ATTACHMENT0 0x8CE0
#define GL_FRAMEBUFFER_COMPLETE 0x8CD5
#define GL_PROJECTION 0x1701
#define GL_MODELVIEW 0x1700
#define GL_DEPTH_BUFFER_BIT 0x0100
#define GL_DEPTH_TEST 0x0B71
#define GL_LEQUAL 0x0203
#define GL_FOG 0x0B60
#define GL_FOG_START 0x0B63
#define GL_FOG_END 0x0B64
#define GL_FOG_MODE 0x0B65
#define GL_FOG_COLOR 0x0B66
#define GL_LINEAR 0x2601
#define GL_LIGHTING 0x0B50
#define GL_LIGHT0 0x4000
#define GL_COLOR_MATERIAL 0x0B57
#define GL_AMBIENT 0x1200
#define GL_DIFFUSE 0x1201
#define GL_SPECULAR 0x1202
#define GL_POSITION 0x1203
#define GL_LIGHT_MODEL_AMBIENT 0x0B53
#define GL_FRONT_AND_BACK 0x0408
#define GL_AMBIENT_AND_DIFFUSE 0x1602
#define GL_FLAT 0x1D00
#define GL_BLEND 0x0BE2
#define GL_SRC_ALPHA 0x0302
#define GL_ONE_MINUS_SRC_ALPHA 0x0303
#define GL_RENDERBUFFER 0x8D41
#define GL_DEPTH_ATTACHMENT 0x8D00
#define GL_DEPTH_COMPONENT24 0x81A6

/* Xlib ABI structs. Xlib is opened dynamically, so the layouts are declared
 * here; they are the exact public layouts of XSetWindowAttributes,
 * XVisualInfo and XEvent on both 32-bit and 64-bit X11. */
typedef struct {
    unsigned long background_pixmap;
    unsigned long background_pixel;
    unsigned long border_pixmap;
    unsigned long border_pixel;
    int bit_gravity;
    int win_gravity;
    int backing_store;
    unsigned long backing_planes;
    unsigned long backing_pixel;
    int save_under;
    long event_mask;
    long do_not_propagate_mask;
    int override_redirect;
    unsigned long colormap;
    unsigned long cursor;
} LshXWindowAttributes;

typedef struct {
    void* visual;
    unsigned long visualid;
    int screen;
    int depth;
    int visual_class;
    unsigned long red_mask;
    unsigned long green_mask;
    unsigned long blue_mask;
    int colormap_size;
    int bits_per_rgb;
} LshXVisualInfo;

typedef struct {
    unsigned long pixel;
    unsigned short red, green, blue;
    char flags;
    char pad;
} LshXColor;

typedef struct {
    char* res_name;
    char* res_class;
} LshXClassHint;

typedef union {
    long pad[24]; /* XEvent is a union of 24 longs on every supported ABI. */
    struct {
        int type;
        unsigned long serial;
        int send_event;
        void* display;
        unsigned long window;
        unsigned long root;
        unsigned long subwindow;
        unsigned long time;
        int x, y, x_root, y_root;
        unsigned int state;
        unsigned int keycode;
        int same_screen;
    } key;
    struct {
        int type;
        unsigned long serial;
        int send_event;
        void* display;
        unsigned long window;
        unsigned long root;
        unsigned long subwindow;
        unsigned long time;
        int x, y, x_root, y_root;
        unsigned int state;
        unsigned int button;
        int same_screen;
    } button;
    struct {
        int type;
        unsigned long serial;
        int send_event;
        void* display;
        unsigned long window;
        unsigned long root;
        unsigned long subwindow;
        unsigned long time;
        int x, y, x_root, y_root;
        unsigned int state;
        char is_hint;
        int same_screen;
    } motion;
    struct {
        int type;
        unsigned long serial;
        int send_event;
        void* display;
        unsigned long window;
        unsigned long message_type;
        int format;
        long data[5];
    } client;
    struct {
        int type;
        unsigned long serial;
        int send_event;
        void* display;
        unsigned long window;
        unsigned long parent;
        int x, y, width, height, border_width;
        unsigned long above;
        int override_redirect;
    } configure;
} LshXEvent;

typedef void* Display;
typedef unsigned long Window;
typedef unsigned long Colormap;
typedef unsigned long Atom;
typedef void* GLXContext;

typedef Display (*FN_XOPENDISPLAY)(const char*);
typedef int (*FN_XCLOSEDISPLAY)(Display*);
typedef int (*FN_XDEFAULTSCREEN)(Display*);
typedef Window (*FN_XROOTWINDOW)(Display*, int);
typedef Colormap (*FN_XCREATECOLORMAP)(Display*, Window, void*, int);
typedef int (*FN_XFREE)(void*);
typedef int (*FN_XFREECOLORMAP)(Display*, Colormap);
typedef Window (*FN_XCREATEWINDOW)(Display*, Window, int, int, unsigned int,
                                   unsigned int, unsigned int, int, unsigned int,
                                   void*, unsigned long, LshXWindowAttributes*);
typedef int (*FN_XMAPWINDOW)(Display*, Window);
typedef int (*FN_XSTORENAME)(Display*, Window, const char*);
typedef int (*FN_XFLUSH)(Display*);
typedef int (*FN_XSYNC)(Display*, int);
typedef int (*FN_XPENDING)(Display*);
typedef int (*FN_XNEXTEVENT)(Display*, LshXEvent*);
typedef int (*FN_XPUTBACKEVENT)(Display*, LshXEvent*);
typedef int (*FN_XGETINPUTFOCUS)(Display*, Window*, int*);
typedef int (*FN_XDESTROYWINDOW)(Display*, Window);
typedef Atom (*FN_XINTERNATOM)(Display*, const char*, int);
typedef int (*FN_XSETWMPROTOCOLS)(Display*, Window, Atom*, int);
typedef unsigned char (*FN_XKEYSYMTOKEYCODE)(Display*, unsigned long);
typedef int (*FN_XERRORHANDLER)(Display*, void*);
typedef FN_XERRORHANDLER (*FN_XSETERRORHANDLER)(FN_XERRORHANDLER);
typedef int (*FN_XGRABPOINTER)(Display*, Window, int, unsigned int, int, int,
                               Window, unsigned long, unsigned long);
typedef int (*FN_XUNGRABPOINTER)(Display*, unsigned long);
typedef int (*FN_XWARPPOINTER)(Display*, Window, Window, int, int,
                               unsigned int, unsigned int, int, int);
typedef unsigned long (*FN_XCREATEBITMAPFROMDATA)(Display*, unsigned long,
                                                  const char*, unsigned int,
                                                  unsigned int);
typedef unsigned long (*FN_XCREATEPIXMAPCURSOR)(Display*, unsigned long,
                                                unsigned long, LshXColor*,
                                                LshXColor*, unsigned int,
                                                unsigned int);
typedef int (*FN_XDEFINECURSOR)(Display*, Window, unsigned long);
typedef int (*FN_XFREECURSOR)(Display*, unsigned long);
typedef int (*FN_XFREEPIXMAP)(Display*, unsigned long);
typedef int (*FN_XSETINPUTFOCUS)(Display*, Window, int, unsigned long);
typedef int (*FN_XSETCLASSHINT)(Display*, Window, LshXClassHint*);

typedef struct {
    int type;
    Display* display;
    unsigned long resourceid;
    unsigned long serial;
    unsigned char error_code;
    unsigned char request_code;
    unsigned char minor_code;
    unsigned char other_code;
} LshXErrorEvent;

/* X11 event types. */
#define X_KEYPRESS 2
#define X_KEYRELEASE 3
#define X_BUTTON_PRESS 4
#define X_BUTTON_RELEASE 5
#define X_MOTION_NOTIFY 6
#define X_FOCUSIN 9
#define X_FOCUSOUT 10
#define X_CONFIGURE_NOTIFY 22
#define X_CLIENT_MESSAGE 33

/* GLX attribute names and window masks. */
#define GLX_RGBA 4
#define GLX_DOUBLEBUFFER 5
#define GLX_RED_SIZE 8
#define GLX_GREEN_SIZE 9
#define GLX_BLUE_SIZE 10
#define GLX_ALPHA_SIZE 11
#define GLX_DEPTH_SIZE 12
#define CW_EVENT_MASK (1L << 11)
#define CW_COLORMAP (1L << 13)
#define FOCUS_CHANGE_MASK (1L << 21)
#define STRUCTURE_NOTIFY_MASK (1L << 17)
#define KEY_PRESS_MASK (1L << 0)
#define KEY_RELEASE_MASK (1L << 1)
#define BUTTON_PRESS_MASK (1L << 2)
#define BUTTON_RELEASE_MASK (1L << 3)
#define POINTER_MOTION_MASK (1L << 6)
#define GRAB_MODE_ASYNC 1
#define REVERT_TO_POINTER_ROOT 1
#define INPUT_OUTPUT 1

typedef LshXVisualInfo* (*FN_GLXCHOOSEVISUAL)(Display*, int, int*);
typedef GLXContext (*FN_GLXCREATECONTEXT)(Display*, LshXVisualInfo*, GLXContext, int);
typedef int (*FN_GLXMAKECURRENT)(Display*, unsigned long, GLXContext);
typedef void (*FN_GLXDESTROYCONTEXT)(Display*, GLXContext);

static void* g_egl = NULL;
static void* g_gl = NULL;
static void* g_x11 = NULL;
static EGLDisplay g_dpy = EGL_NO_DISPLAY;
static EGLContext g_ctx = EGL_NO_CONTEXT;
static unsigned int g_fbo = 0;
static unsigned int g_tex = 0;
static unsigned int g_depth_rb = 0;
static int g_fb_w = 64;
static int g_fb_h = 64;

static Display g_xdpy = NULL;
static Window g_xwin = 0;
static Colormap g_xcmap = 0;
static GLXContext g_glx = NULL;
static Atom g_wm_delete = 0;
static Atom g_wm_protocols = 0;
static int g_win_w = 0;
static int g_win_h = 0;
static bool g_window_mode = false;
static bool g_should_close = false;

/* Relative mouse capture state for first-person controls. */
static bool g_mouse_captured = false;
static bool g_window_focused = false;
static unsigned char g_mouse_down[8];
static unsigned char g_mouse_pressed[8];
static int g_mouse_dx = 0;
static int g_mouse_dy = 0;
static int g_mouse_cx = 0;
static int g_mouse_cy = 0;
static unsigned long g_blank_cursor = 0;

static FN_XOPENDISPLAY x_open_display;
static FN_XCLOSEDISPLAY x_close_display;
static FN_XDEFAULTSCREEN x_default_screen;
static FN_XROOTWINDOW x_root_window;
static FN_XCREATECOLORMAP x_create_colormap;
static FN_XFREE x_free;
static FN_XFREECOLORMAP x_free_colormap;
static FN_XCREATEWINDOW x_create_window;
static FN_XMAPWINDOW x_map_window;
static FN_XSTORENAME x_store_name;
static FN_XFLUSH x_flush;
static FN_XSYNC x_sync;
static FN_XPENDING x_pending;
static FN_XNEXTEVENT x_next_event;
static FN_XPUTBACKEVENT x_put_back_event;
static FN_XGETINPUTFOCUS x_get_input_focus;
static FN_XDESTROYWINDOW x_destroy_window;
static FN_XINTERNATOM x_intern_atom;
static FN_XSETWMPROTOCOLS x_set_wm_protocols;
static FN_XKEYSYMTOKEYCODE x_keysym_to_keycode;
static FN_XSETERRORHANDLER x_set_error_handler;
static FN_XGRABPOINTER x_grab_pointer;
static FN_XUNGRABPOINTER x_ungrab_pointer;
static FN_XWARPPOINTER x_warp_pointer;
static FN_XCREATEBITMAPFROMDATA x_create_bitmap_from_data;
static FN_XCREATEPIXMAPCURSOR x_create_pixmap_cursor;
static FN_XDEFINECURSOR x_define_cursor;
static FN_XFREECURSOR x_free_cursor;
static FN_XFREEPIXMAP x_free_pixmap;
static FN_XSETINPUTFOCUS x_set_input_focus;
static FN_XSETCLASSHINT x_set_class_hint;
static FN_GLXCHOOSEVISUAL glx_choose_visual;
static FN_GLXCREATECONTEXT glx_create_context;
static FN_GLXMAKECURRENT glx_make_current;
static FN_GLXDESTROYCONTEXT glx_destroy_context;

/* X11 keysyms used by the Leash API; positions are stable. */
static const unsigned long g_keysyms[] = {
    0x057,  /* W */
    0x061,  /* A */
    0x073,  /* S */
    0x064,  /* D */
    0x071,  /* Q */
    0x065,  /* E */
    0x066,  /* F */
    0x067,  /* G */
    0x072,  /* R */
    0x020,  /* space */
    0xFF1B, /* Escape */
    0xFFE1, /* left Shift */
    0x031, 0x032, 0x033, 0x034, 0x035, /* 1-5 */
    0x036, 0x037, 0x038, 0x039            /* 6-9 */
};
#define KEY_COUNT ((int)(sizeof(g_keysyms) / sizeof(g_keysyms[0])))
static unsigned char g_keycode[KEY_COUNT];
static unsigned char g_key_down[256];
static unsigned char g_key_pressed[256];

typedef EGLDisplay (*FN_GETDISPLAY)(void*);
typedef EGLDisplay (*FN_GETPLATFORMDISPLAY)(EGLenum, void*, const int*);
typedef EGLBoolean (*FN_INITIALIZE)(EGLDisplay, int*, int*);
typedef EGLBoolean (*FN_CHOOSECONFIG)(EGLDisplay, const EGLint*, EGLConfig*, EGLint, EGLint*);
typedef EGLBoolean (*FN_BINDAPI)(EGLenum);
typedef EGLContext (*FN_CREATECONTEXT)(EGLDisplay, EGLConfig, EGLContext, const EGLint*);
typedef EGLBoolean (*FN_MAKECURRENT)(EGLDisplay, void*, void*, EGLContext);
typedef EGLBoolean (*FN_DESTROYCONTEXT)(EGLDisplay, EGLContext);
typedef EGLBoolean (*FN_TERMINATE)(EGLDisplay);
typedef void* (*FN_GETPROC)(const char*);

static void* sym_egl(const char* name) { return dlsym(g_egl, name); }
static void* sym_gl(const char* name) { return dlsym(g_gl, name); }
static void destroy_headless_context(void);

static bool load_x11(void) {
    if (!g_x11) {
        g_x11 = dlopen("libX11.so.6", RTLD_LAZY | RTLD_LOCAL);
        if (!g_x11) g_x11 = dlopen("libX11.so", RTLD_LAZY | RTLD_LOCAL);
    }
    if (!g_x11) return false;
    if (!g_gl) {
        g_gl = dlopen("libGL.so.1", RTLD_LAZY | RTLD_LOCAL);
        if (!g_gl) g_gl = dlopen("libGL.so", RTLD_LAZY | RTLD_LOCAL);
    }
    if (!g_gl) return false;
    x_open_display = (FN_XOPENDISPLAY)dlsym(g_x11, "XOpenDisplay");
    x_close_display = (FN_XCLOSEDISPLAY)dlsym(g_x11, "XCloseDisplay");
    x_default_screen = (FN_XDEFAULTSCREEN)dlsym(g_x11, "XDefaultScreen");
    x_root_window = (FN_XROOTWINDOW)dlsym(g_x11, "XRootWindow");
    x_create_colormap = (FN_XCREATECOLORMAP)dlsym(g_x11, "XCreateColormap");
    x_free = (FN_XFREE)dlsym(g_x11, "XFree");
    x_free_colormap = (FN_XFREECOLORMAP)dlsym(g_x11, "XFreeColormap");
    x_create_window = (FN_XCREATEWINDOW)dlsym(g_x11, "XCreateWindow");
    x_map_window = (FN_XMAPWINDOW)dlsym(g_x11, "XMapWindow");
    x_store_name = (FN_XSTORENAME)dlsym(g_x11, "XStoreName");
    x_flush = (FN_XFLUSH)dlsym(g_x11, "XFlush");
    x_sync = (FN_XSYNC)dlsym(g_x11, "XSync");
    x_pending = (FN_XPENDING)dlsym(g_x11, "XPending");
    x_next_event = (FN_XNEXTEVENT)dlsym(g_x11, "XNextEvent");
    x_put_back_event = (FN_XPUTBACKEVENT)dlsym(g_x11, "XPutBackEvent");
    x_get_input_focus = (FN_XGETINPUTFOCUS)dlsym(g_x11, "XGetInputFocus");
    x_destroy_window = (FN_XDESTROYWINDOW)dlsym(g_x11, "XDestroyWindow");
    x_intern_atom = (FN_XINTERNATOM)dlsym(g_x11, "XInternAtom");
    x_set_wm_protocols = (FN_XSETWMPROTOCOLS)dlsym(g_x11, "XSetWMProtocols");
    x_keysym_to_keycode = (FN_XKEYSYMTOKEYCODE)dlsym(g_x11, "XKeysymToKeycode");
    x_set_error_handler = (FN_XSETERRORHANDLER)dlsym(g_x11, "XSetErrorHandler");
    x_grab_pointer = (FN_XGRABPOINTER)dlsym(g_x11, "XGrabPointer");
    x_ungrab_pointer = (FN_XUNGRABPOINTER)dlsym(g_x11, "XUngrabPointer");
    x_warp_pointer = (FN_XWARPPOINTER)dlsym(g_x11, "XWarpPointer");
    x_create_bitmap_from_data = (FN_XCREATEBITMAPFROMDATA)dlsym(g_x11, "XCreateBitmapFromData");
    x_create_pixmap_cursor = (FN_XCREATEPIXMAPCURSOR)dlsym(g_x11, "XCreatePixmapCursor");
    x_define_cursor = (FN_XDEFINECURSOR)dlsym(g_x11, "XDefineCursor");
    x_free_cursor = (FN_XFREECURSOR)dlsym(g_x11, "XFreeCursor");
    x_free_pixmap = (FN_XFREEPIXMAP)dlsym(g_x11, "XFreePixmap");
    x_set_input_focus = (FN_XSETINPUTFOCUS)dlsym(g_x11, "XSetInputFocus");
    x_set_class_hint = (FN_XSETCLASSHINT)dlsym(g_x11, "XSetClassHint");
    if (!x_open_display || !x_close_display || !x_default_screen || !x_root_window ||
        !x_create_colormap || !x_create_window || !x_map_window || !x_store_name ||
        !x_flush || !x_pending || !x_next_event || !x_destroy_window ||
        !x_intern_atom || !x_set_wm_protocols || !x_keysym_to_keycode) return false;
    if (!glx_choose_visual) glx_choose_visual = (FN_GLXCHOOSEVISUAL)sym_gl("glXChooseVisual");
    if (!glx_create_context) glx_create_context = (FN_GLXCREATECONTEXT)sym_gl("glXCreateContext");
    if (!glx_make_current) glx_make_current = (FN_GLXMAKECURRENT)sym_gl("glXMakeCurrent");
    if (!glx_destroy_context) glx_destroy_context = (FN_GLXDESTROYCONTEXT)sym_gl("glXDestroyContext");
    return glx_choose_visual && glx_create_context && glx_make_current && glx_destroy_context;
}

static FN_XERRORHANDLER x_prev_error_handler = NULL;
static bool x_error_handler_installed = false;

/* Xlib's default error handler exits the process on a BadMatch from a GLX
 * visual mismatch. Report the error through the return path instead. */
static int ignore_x_error(Display* dpy, void* err) {
    (void)dpy;
    (void)err;
    return 0;
}

static void release_mouse_capture(void) {
    if (g_xdpy) {
        if (g_mouse_captured && x_ungrab_pointer) x_ungrab_pointer(g_xdpy, 0);
        if (g_blank_cursor && x_define_cursor && g_xwin)
            x_define_cursor(g_xdpy, g_xwin, 0);
        if (g_blank_cursor && x_free_cursor)
            x_free_cursor(g_xdpy, g_blank_cursor);
    }
    g_blank_cursor = 0;
    g_mouse_captured = false;
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    memset(g_mouse_down, 0, sizeof(g_mouse_down));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
}

static void release_window_resources(void) {
    release_mouse_capture();
    if (g_xdpy) {
        if (g_xcmap && x_free_colormap) x_free_colormap(g_xdpy, g_xcmap);
        x_close_display(g_xdpy);
    }
    g_xdpy = NULL;
    g_xwin = 0;
    g_xcmap = 0;
    g_glx = NULL;
    g_wm_delete = 0;
    g_wm_protocols = 0;
    g_window_mode = false;
    if (x_error_handler_installed && x_set_error_handler) {
        x_set_error_handler(x_prev_error_handler);
        x_error_handler_installed = false;
    }
}

static int keycode_for(unsigned long keysym) {
    int i = 0;
    while (i < KEY_COUNT) {
        if (g_keysyms[i] == keysym) return g_keycode[i];
        i = i + 1;
    }
    return 0;
}

void* lshgl_open_window(int w, int h, const char* title) {
    if (g_xwin != 0) return (void*)g_xwin;
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    if (w > 8192) w = 8192;
    if (h > 8192) h = 8192;
    /* A program may have created the offscreen context before opening a
     * window. Drop it so only one GL context stays current. */
    if (g_ctx != EGL_NO_CONTEXT) destroy_headless_context();
    if (!load_x11()) return NULL;
    g_xdpy = x_open_display(NULL);
    if (!g_xdpy) return NULL;
    if (x_set_error_handler && !x_error_handler_installed) {
        x_prev_error_handler = x_set_error_handler(ignore_x_error);
        x_error_handler_installed = true;
    }
    int screen = x_default_screen(g_xdpy);
    /* A depth buffer is required for the 3D scene; alpha is not. */
    int attrs[] = { GLX_RGBA, GLX_DOUBLEBUFFER,
                    GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8,
                    GLX_DEPTH_SIZE, 24, 0 };
    LshXVisualInfo* vi = glx_choose_visual(g_xdpy, screen, attrs);
    if (!vi) {
        release_window_resources();
        return NULL;
    }
    Window root = x_root_window(g_xdpy, screen);
    g_xcmap = x_create_colormap(g_xdpy, root, vi->visual, 0);
    LshXWindowAttributes wa;
    memset(&wa, 0, sizeof(wa));
    wa.background_pixel = 0;
    wa.border_pixel = 0;
    wa.colormap = g_xcmap;
    /* Focus changes clear held keys; buttons and motion feed the mouse API. */
    wa.event_mask = KEY_PRESS_MASK | KEY_RELEASE_MASK |
                    BUTTON_PRESS_MASK | BUTTON_RELEASE_MASK |
                    POINTER_MOTION_MASK | STRUCTURE_NOTIFY_MASK |
                    FOCUS_CHANGE_MASK;
    g_xwin = x_create_window(g_xdpy, root, 40, 40, (unsigned int)w, (unsigned int)h, 0,
                             vi->depth, INPUT_OUTPUT, vi->visual,
                             CW_COLORMAP | CW_EVENT_MASK, &wa);
    if (g_xwin == 0) {
        release_window_resources();
        return NULL;
    }
    if (title && *title) x_store_name(g_xdpy, g_xwin, title);
    if (x_set_class_hint) {
        LshXClassHint hint;
        hint.res_name = (char*)"leash";
        hint.res_class = (char*)"Leash";
        x_set_class_hint(g_xdpy, g_xwin, &hint);
    }
    g_wm_delete = x_intern_atom(g_xdpy, "WM_DELETE_WINDOW", 0);
    g_wm_protocols = x_intern_atom(g_xdpy, "WM_PROTOCOLS", 0);
    if (g_wm_delete) x_set_wm_protocols(g_xdpy, g_xwin, &g_wm_delete, 1);
    g_glx = glx_create_context(g_xdpy, vi, NULL, 1);  /* prefer direct rendering */
    if (!g_glx) g_glx = glx_create_context(g_xdpy, vi, NULL, 0);  /* indirect fallback */
    if (x_free) x_free(vi);
    if (!g_glx || !glx_make_current(g_xdpy, g_xwin, g_glx)) {
        if (g_glx) glx_destroy_context(g_xdpy, g_glx);
        x_destroy_window(g_xdpy, g_xwin);
        release_window_resources();
        return NULL;
    }
    int i = 0;
    while (i < KEY_COUNT) {
        g_keycode[i] = x_keysym_to_keycode(g_xdpy, g_keysyms[i]);
        i = i + 1;
    }
    memset(g_key_down, 0, sizeof(g_key_down));
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
    memset(g_mouse_down, 0, sizeof(g_mouse_down));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    g_mouse_captured = false;
    g_window_focused = false;
    g_window_mode = true;
    g_should_close = false;
    g_win_w = w;
    g_win_h = h;
    g_mouse_cx = w / 2;
    g_mouse_cy = h / 2;
    /* A previous headless context may have bound its own framebuffer. */
    g_fbo = 0;
    g_tex = 0;
    PFNGLVIEWPORT viewport = (PFNGLVIEWPORT)sym_gl("glViewport");
    if (viewport) viewport(0, 0, w, h);
    PFNGLCLEARCOLOR clear_color_fn = (PFNGLCLEARCOLOR)sym_gl("glClearColor");
    if (clear_color_fn) clear_color_fn(0.05f, 0.07f, 0.12f, 1.0f);
    x_map_window(g_xdpy, g_xwin);
    if (x_set_input_focus) x_set_input_focus(g_xdpy, g_xwin, REVERT_TO_POINTER_ROOT, 0);
    x_flush(g_xdpy);
    return (void*)g_xwin;
}

void lshgl_close_window(void) {
    if (g_xdpy == NULL) {
        g_window_mode = false;
        release_mouse_capture();
        return;
    }
    release_mouse_capture();
    if (g_glx) {
        glx_make_current(g_xdpy, 0, NULL);
        glx_destroy_context(g_xdpy, g_glx);
        g_glx = NULL;
    }
    if (g_xwin) {
        x_destroy_window(g_xdpy, g_xwin);
        g_xwin = 0;
    }
    release_window_resources();
    g_should_close = false;
    memset(g_key_down, 0, sizeof(g_key_down));
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
}

void lshgl_poll_events(void) {
    if (!g_window_mode || !g_xdpy || !x_pending || !x_next_event) return;
    memset(g_key_pressed, 0, sizeof(g_key_pressed));
    memset(g_mouse_pressed, 0, sizeof(g_mouse_pressed));
    while (x_pending(g_xdpy) > 0) {
        LshXEvent ev;
        memset(&ev, 0, sizeof(ev));
        x_next_event(g_xdpy, &ev);
        if (ev.key.type == X_KEYPRESS || ev.key.type == X_KEYRELEASE) {
            unsigned int code = ev.key.keycode;
            if (code < 256) {
                if (ev.key.type == X_KEYPRESS) {
                    g_key_down[code] = 1;
                    g_key_pressed[code] = 1;
                } else {
                    g_key_down[code] = 0;
                }
            }
        } else if (ev.key.type == X_BUTTON_PRESS || ev.key.type == X_BUTTON_RELEASE) {
            unsigned int button = ev.button.button;
            if (button < 8) {
                if (ev.key.type == X_BUTTON_PRESS) {
                    g_mouse_down[button] = 1;
                    g_mouse_pressed[button] = 1;
                } else {
                    g_mouse_down[button] = 0;
                }
            }
        } else if (ev.key.type == X_MOTION_NOTIFY) {
            if (g_mouse_captured) {
                int dx = ev.motion.x - g_mouse_cx;
                int dy = ev.motion.y - g_mouse_cy;
                if (dx != 0 || dy != 0) {
                    g_mouse_dx += dx;
                    g_mouse_dy += dy;
                    if (x_warp_pointer)
                        x_warp_pointer(g_xdpy, 0, g_xwin, 0, 0, 0, 0,
                                       g_mouse_cx, g_mouse_cy);
                }
            }
        } else if (ev.key.type == X_CLIENT_MESSAGE) {
            /* ICCCM: a WM_DELETE_WINDOW request has message_type
             * WM_PROTOCOLS and the protocol atom in data[0]. Accept a direct
             * WM_DELETE_WINDOW message too, for simple test harnesses. */
            bool delete_request =
                (g_wm_protocols && ev.client.message_type == g_wm_protocols &&
                 g_wm_delete && ev.client.data[0] == (long)g_wm_delete) ||
                (g_wm_delete && ev.client.message_type == g_wm_delete);
            if (delete_request) g_should_close = true;
        } else if (ev.key.type == X_CONFIGURE_NOTIFY) {
            if (ev.configure.width > 0 && ev.configure.height > 0 &&
                (ev.configure.width != g_win_w || ev.configure.height != g_win_h)) {
                g_win_w = ev.configure.width;
                g_win_h = ev.configure.height;
                g_mouse_cx = g_win_w / 2;
                g_mouse_cy = g_win_h / 2;
                PFNGLVIEWPORT viewport = (PFNGLVIEWPORT)sym_gl("glViewport");
                if (viewport) viewport(0, 0, g_win_w, g_win_h);
                if (g_mouse_captured && x_warp_pointer)
                    x_warp_pointer(g_xdpy, 0, g_xwin, 0, 0, 0, 0,
                                   g_mouse_cx, g_mouse_cy);
            }
        } else if (ev.key.type == X_FOCUSIN) {
            g_window_focused = true;
        } else if (ev.key.type == X_FOCUSOUT) {
            memset(g_key_down, 0, sizeof(g_key_down));
            /* Release the grab unless focus is still on this window or on
             * PointerRoot (X servers without a WM deliver an initial
             * FocusOut before the window ever receives FocusIn). */
            if (g_mouse_captured) {
                bool keep = false;
                Window focus = 0;
                int revert = 0;
                if (x_get_input_focus &&
                    x_get_input_focus(g_xdpy, &focus, &revert) &&
                    (focus == g_xwin || focus == 1)) {
                    keep = true;
                }
                if (!keep) release_mouse_capture();
            }
            g_window_focused = false;
        }
    }
}

bool lshgl_should_close(void) { return g_should_close; }
void lshgl_request_close(void) { g_should_close = true; }
int lshgl_window_width(void) { return g_win_w; }
int lshgl_window_height(void) { return g_win_h; }

bool lshgl_key_down(int keysym) {
    int code = keycode_for((unsigned long)keysym);
    return code != 0 && g_key_down[code] != 0;
}

bool lshgl_key_pressed(int keysym) {
    int code = keycode_for((unsigned long)keysym);
    return code != 0 && g_key_pressed[code] != 0;
}

static void create_blank_cursor(void) {
    if (g_blank_cursor || !g_xdpy || !g_xwin) return;
    if (!x_create_bitmap_from_data || !x_create_pixmap_cursor || !x_define_cursor)
        return;
    static const char empty_bits[1] = { 0 };
    unsigned long pixmap = x_create_bitmap_from_data(g_xdpy, g_xwin, empty_bits, 1, 1);
    if (!pixmap) return;
    LshXColor black;
    memset(&black, 0, sizeof(black));
    unsigned long cursor = x_create_pixmap_cursor(g_xdpy, pixmap, pixmap,
                                                  &black, &black, 0, 0);
    if (x_free_pixmap) x_free_pixmap(g_xdpy, pixmap);
    if (!cursor) return;
    g_blank_cursor = cursor;
    x_define_cursor(g_xdpy, g_xwin, cursor);
}

void lshgl_mouse_capture(int on) {
    if (!on) {
        release_mouse_capture();
        return;
    }
    if (!g_window_mode || !g_xdpy || !g_xwin || g_mouse_captured) return;
    g_mouse_cx = g_win_w / 2;
    g_mouse_cy = g_win_h / 2;
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    create_blank_cursor();
    if (x_grab_pointer) {
        unsigned int mask = KEY_PRESS_MASK | KEY_RELEASE_MASK |
                            BUTTON_PRESS_MASK | BUTTON_RELEASE_MASK |
                            POINTER_MOTION_MASK;
        /* A failed grab is not fatal: motion inside the window still works. */
        x_grab_pointer(g_xdpy, g_xwin, 0, mask, GRAB_MODE_ASYNC,
                       GRAB_MODE_ASYNC, g_xwin, 0, 0);
    }
    g_mouse_captured = true;
    if (x_warp_pointer)
        x_warp_pointer(g_xdpy, 0, g_xwin, 0, 0, 0, 0, g_mouse_cx, g_mouse_cy);
    if (x_sync) x_sync(g_xdpy, 0);
    /* Discard only the MotionNotify produced by our own recentre; other
     * queued events (keys, buttons, close requests) must be kept. */
    while (x_pending && x_next_event && x_pending(g_xdpy) > 0) {
        LshXEvent ev;
        memset(&ev, 0, sizeof(ev));
        x_next_event(g_xdpy, &ev);
        if (ev.key.type == X_MOTION_NOTIFY && ev.motion.x == g_mouse_cx &&
            ev.motion.y == g_mouse_cy) {
            continue;
        }
        /* Put this event back and stop draining; continuing would pick the
         * same event up again forever. poll_events() handles the rest. */
        if (x_put_back_event) x_put_back_event(g_xdpy, &ev);
        break;
    }
    g_mouse_dx = 0;
    g_mouse_dy = 0;
    if (x_flush) x_flush(g_xdpy);
}

bool lshgl_mouse_captured(void) { return g_mouse_captured; }

int lshgl_mouse_dx(void) {
    int value = g_mouse_dx;
    g_mouse_dx = 0;
    return value;
}

int lshgl_mouse_dy(void) {
    int value = g_mouse_dy;
    g_mouse_dy = 0;
    return value;
}

bool lshgl_mouse_down(int button) {
    return button > 0 && button < 8 && g_mouse_down[button] != 0;
}

bool lshgl_mouse_pressed(int button) {
    return button > 0 && button < 8 && g_mouse_pressed[button] != 0;
}

static void apply_swap_interval(int interval) {
    if (!g_window_mode || !g_xdpy || !g_xwin || !g_gl) return;
    PFNGLXGETPROCADDRESSARB get_proc =
        (PFNGLXGETPROCADDRESSARB)sym_gl("glXGetProcAddressARB");
    if (!get_proc)
        get_proc = (PFNGLXGETPROCADDRESSARB)sym_gl("glXGetProcAddress");
    if (!get_proc) return;
    PFNGLXSWAPINTERVALEXT ext = (PFNGLXSWAPINTERVALEXT)
        get_proc((const unsigned char*)"glXSwapIntervalEXT");
    if (ext) {
        ext(g_xdpy, g_xwin, interval);
        return;
    }
    PFNGLXSWAPINTERVALMESA mesa = (PFNGLXSWAPINTERVALMESA)
        get_proc((const unsigned char*)"glXSwapIntervalMESA");
    if (mesa) {
        mesa((unsigned int)interval);
        return;
    }
    PFNGLXSWAPINTERVALSGI sgi = (PFNGLXSWAPINTERVALSGI)
        get_proc((const unsigned char*)"glXSwapIntervalSGI");
    if (sgi) sgi(interval);
}

/* Disabling VSync lets a game measure and display its uncapped frame rate. */
void lshgl_set_vsync(int enabled) {
    apply_swap_interval(enabled ? 1 : 0);
}

void lshgl_swap_buffers(void) {
    if (!g_window_mode || !g_xdpy || !g_xwin || !g_gl) return;
    PFNGLSWAPBUFFERS swap = (PFNGLSWAPBUFFERS)sym_gl("glXSwapBuffers");
    if (swap) swap(g_xdpy, g_xwin);
}

int64_t lshgl_time_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (int64_t)ts.tv_sec * 1000 + (int64_t)ts.tv_nsec / 1000000;
}

void lshgl_sleep_ms(int ms) {
    if (ms < 0) ms = 0;
    if (ms > 1000) ms = 1000;
    struct timespec req;
    req.tv_sec = ms / 1000;
    req.tv_nsec = (long)(ms % 1000) * 1000000L;
    while (nanosleep(&req, &req) == -1 && errno == EINTR) { }
}

void* lshgl_create_context(void) {
    if (g_xwin != 0) return (void*)g_xwin;
    if (g_ctx != EGL_NO_CONTEXT) return (void*)g_ctx;
    if (!g_egl) g_egl = dlopen("libEGL.so.1", RTLD_LAZY | RTLD_LOCAL);
    if (!g_egl) g_egl = dlopen("libEGL.so", RTLD_LAZY | RTLD_LOCAL);
    if (!g_egl) return NULL;
    if (!g_gl) {
        g_gl = dlopen("libGL.so.1", RTLD_LAZY | RTLD_LOCAL);
        if (!g_gl) g_gl = dlopen("libGL.so", RTLD_LAZY | RTLD_LOCAL);
    }
    if (!g_gl) return NULL;

    FN_GETPROC get_proc = (FN_GETPROC)sym_egl("eglGetProcAddress");
    FN_GETPLATFORMDISPLAY get_platform = NULL;
    if (get_proc) get_platform = (FN_GETPLATFORMDISPLAY)get_proc("eglGetPlatformDisplayEXT");
    FN_GETDISPLAY get_display = (FN_GETDISPLAY)sym_egl("eglGetDisplay");
    FN_INITIALIZE init = (FN_INITIALIZE)sym_egl("eglInitialize");
    FN_CHOOSECONFIG choose = (FN_CHOOSECONFIG)sym_egl("eglChooseConfig");
    FN_BINDAPI bind_api = (FN_BINDAPI)sym_egl("eglBindAPI");
    FN_CREATECONTEXT create = (FN_CREATECONTEXT)sym_egl("eglCreateContext");
    FN_MAKECURRENT make_current = (FN_MAKECURRENT)sym_egl("eglMakeCurrent");
    if (!get_display || !init || !choose || !bind_api || !create || !make_current) return NULL;

    /* Try Mesa's surfaceless platform first, then the native display. */
    EGLDisplay platform_dpy = EGL_NO_DISPLAY;
    if (get_platform)
        platform_dpy = get_platform(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (platform_dpy != EGL_NO_DISPLAY && init(platform_dpy, NULL, NULL)) {
        g_dpy = platform_dpy;
    } else {
        g_dpy = get_display(EGL_DEFAULT_DISPLAY);
        if (g_dpy == EGL_NO_DISPLAY || !init(g_dpy, NULL, NULL)) {
            g_dpy = EGL_NO_DISPLAY;
            return NULL;
        }
    }

    EGLint attrs[] = { EGL_RENDERABLE_TYPE, (EGLint)EGL_OPENGL_BIT, EGL_NONE };
    EGLConfig cfg = NULL;
    EGLint n = 0;
    if (!choose(g_dpy, attrs, &cfg, 1, &n) || n < 1) {
        destroy_headless_context();
        return NULL;
    }
    if (!bind_api(EGL_OPENGL_API)) {
        destroy_headless_context();
        return NULL;
    }
    g_ctx = create(g_dpy, cfg, EGL_NO_CONTEXT, NULL);
    if (g_ctx == EGL_NO_CONTEXT) {
        destroy_headless_context();
        return NULL;
    }
    if (!make_current(g_dpy, NULL, NULL, g_ctx)) {
        destroy_headless_context();
        return NULL;
    }

    /* Surfaceless EGL has no default framebuffer: render into a texture. */
    PFNGLGENTEXTURES gen_tex = (PFNGLGENTEXTURES)sym_gl("glGenTextures");
    PFNGLBINDTEXTURE bind_tex = (PFNGLBINDTEXTURE)sym_gl("glBindTexture");
    PFNGLTEXIMAGE2D tex_image = (PFNGLTEXIMAGE2D)sym_gl("glTexImage2D");
    PFNGLTEXPARAMETERI tex_param = (PFNGLTEXPARAMETERI)sym_gl("glTexParameteri");
    PFNGLGENFRAMEBUFFERS gen_fb = (PFNGLGENFRAMEBUFFERS)sym_gl("glGenFramebuffers");
    PFNGLBINDFRAMEBUFFER bind_fb = (PFNGLBINDFRAMEBUFFER)sym_gl("glBindFramebuffer");
    PFNGLFRAMEBUFFERTEXTURE2D fb_tex = (PFNGLFRAMEBUFFERTEXTURE2D)sym_gl("glFramebufferTexture2D");
    PFNGLCHECKFRAMEBUFFERSTATUS check_fb = (PFNGLCHECKFRAMEBUFFERSTATUS)sym_gl("glCheckFramebufferStatus");
    PFNGLGENRENDERBUFFERS gen_rb = (PFNGLGENRENDERBUFFERS)sym_gl("glGenRenderbuffers");
    PFNGLBINDRENDERBUFFER bind_rb = (PFNGLBINDRENDERBUFFER)sym_gl("glBindRenderbuffer");
    PFNGLRENDERBUFFERSTORAGE rb_storage = (PFNGLRENDERBUFFERSTORAGE)sym_gl("glRenderbufferStorage");
    PFNGLFRAMEBUFFERRENDERBUFFER fb_rb = (PFNGLFRAMEBUFFERRENDERBUFFER)sym_gl("glFramebufferRenderbuffer");
    if (!gen_tex || !bind_tex || !tex_image || !tex_param || !gen_fb || !bind_fb || !fb_tex || !check_fb)
        return (void*)g_ctx;
    gen_tex(1, &g_tex);
    bind_tex(GL_TEXTURE_2D, g_tex);
    tex_image(GL_TEXTURE_2D, 0, GL_RGBA8, g_fb_w, g_fb_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    tex_param(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    tex_param(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    gen_fb(1, &g_fbo);
    bind_fb(GL_FRAMEBUFFER, g_fbo);
    fb_tex(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, g_tex, 0);
    if (gen_rb && bind_rb && rb_storage && fb_rb) {
        gen_rb(1, &g_depth_rb);
        bind_rb(GL_RENDERBUFFER, g_depth_rb);
        rb_storage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, g_fb_w, g_fb_h);
        fb_rb(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, g_depth_rb);
    }
    if (check_fb(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        PFNGLDELETEFRAMEBUFFERS del_fb =
            (PFNGLDELETEFRAMEBUFFERS)sym_gl("glDeleteFramebuffers");
        PFNGLDELETERENDERBUFFERS del_rb =
            (PFNGLDELETERENDERBUFFERS)sym_gl("glDeleteRenderbuffers");
        PFNGLDELETETEXTURES del_tex =
            (PFNGLDELETETEXTURES)sym_gl("glDeleteTextures");
        if (g_fbo && del_fb) del_fb(1, &g_fbo);
        if (g_depth_rb && del_rb) del_rb(1, &g_depth_rb);
        if (g_tex && del_tex) del_tex(1, &g_tex);
        g_fbo = 0;
        g_tex = 0;
        g_depth_rb = 0;
    }
    return (void*)g_ctx;
}

static void destroy_headless_context(void) {
    if (g_dpy == EGL_NO_DISPLAY || !g_egl) return;
    if (g_gl) {
        PFNGLDELETEFRAMEBUFFERS del_fb =
            (PFNGLDELETEFRAMEBUFFERS)sym_gl("glDeleteFramebuffers");
        PFNGLDELETERENDERBUFFERS del_rb =
            (PFNGLDELETERENDERBUFFERS)sym_gl("glDeleteRenderbuffers");
        PFNGLDELETETEXTURES del_tex =
            (PFNGLDELETETEXTURES)sym_gl("glDeleteTextures");
        if (g_fbo && del_fb) del_fb(1, &g_fbo);
        if (g_depth_rb && del_rb) del_rb(1, &g_depth_rb);
        if (g_tex && del_tex) del_tex(1, &g_tex);
    }
    FN_MAKECURRENT make_current = (FN_MAKECURRENT)sym_egl("eglMakeCurrent");
    FN_DESTROYCONTEXT destroy = (FN_DESTROYCONTEXT)sym_egl("eglDestroyContext");
    FN_TERMINATE terminate = (FN_TERMINATE)sym_egl("eglTerminate");
    if (make_current) make_current(g_dpy, NULL, NULL, EGL_NO_CONTEXT);
    if (destroy && g_ctx != EGL_NO_CONTEXT) destroy(g_dpy, g_ctx);
    if (terminate) terminate(g_dpy);
    g_ctx = EGL_NO_CONTEXT;
    g_dpy = EGL_NO_DISPLAY;
    g_fbo = 0;
    g_tex = 0;
    g_depth_rb = 0;
}

void lshgl_destroy_context(void* ctx) {
    (void)ctx;
    if (g_window_mode) {
        lshgl_close_window();
        /* A program may have called init() before open_window(). */
        destroy_headless_context();
        return;
    }
    destroy_headless_context();
}

const char* lshgl_version(void) {
    PFNGLGETSTRING f = g_gl ? (PFNGLGETSTRING)sym_gl("glGetString") : NULL;
    const unsigned char* s = f ? f(GL_VERSION) : NULL;
    return s ? (const char*)s : "";
}

const char* lshgl_renderer(void) {
    PFNGLGETSTRING f = g_gl ? (PFNGLGETSTRING)sym_gl("glGetString") : NULL;
    const unsigned char* s = f ? f(GL_RENDERER) : NULL;
    return s ? (const char*)s : "";
}

const char* lshgl_vendor(void) {
    PFNGLGETSTRING f = g_gl ? (PFNGLGETSTRING)sym_gl("glGetString") : NULL;
    const unsigned char* s = f ? f(GL_VENDOR) : NULL;
    return s ? (const char*)s : "";
}

void lshgl_viewport(int w, int h) {
    PFNGLVIEWPORT f = g_gl ? (PFNGLVIEWPORT)sym_gl("glViewport") : NULL;
    if (f) f(0, 0, w, h);
}

void lshgl_clear_color(double r, double g, double b, double a) {
    PFNGLCLEARCOLOR f = g_gl ? (PFNGLCLEARCOLOR)sym_gl("glClearColor") : NULL;
    if (f) f((float)r, (float)g, (float)b, (float)a);
}

void lshgl_clear(void) {
    PFNGLCLEAR f = g_gl ? (PFNGLCLEAR)sym_gl("glClear") : NULL;
    if (f) f(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}

int64_t lshgl_read_pixel(int x, int y) {
    unsigned char px[4] = {0, 0, 0, 0};
    PFNGLREADPIXELS f = g_gl ? (PFNGLREADPIXELS)sym_gl("glReadPixels") : NULL;
    if (f) f(x, y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    return (int64_t)(((uint32_t)px[0] << 24) | ((uint32_t)px[1] << 16) |
                     ((uint32_t)px[2] << 8) | (uint32_t)px[3]);
}

int lshgl_error(void) {
    PFNGLGETERROR f = g_gl ? (PFNGLGETERROR)sym_gl("glGetError") : NULL;
    return f ? (int)f() : 1;
}

void lshgl_begin(int mode) {
    PFNGLBEGIN f = g_gl ? (PFNGLBEGIN)sym_gl("glBegin") : NULL;
    if (f) f((unsigned int)mode);
}

void lshgl_end(void) {
    PFNGLEND f = g_gl ? (PFNGLEND)sym_gl("glEnd") : NULL;
    if (f) f();
}

void lshgl_color(double r, double g, double b, double a) {
    PFNGLCOLOR4F f = g_gl ? (PFNGLCOLOR4F)sym_gl("glColor4f") : NULL;
    if (f) f((float)r, (float)g, (float)b, (float)a);
}

void lshgl_vertex2(double x, double y) {
    PFNGLVERTEX2F f = g_gl ? (PFNGLVERTEX2F)sym_gl("glVertex2f") : NULL;
    if (f) f((float)x, (float)y);
}

void lshgl_matrix(int mode) {
    PFNGLMATRIXMODE f = g_gl ? (PFNGLMATRIXMODE)sym_gl("glMatrixMode") : NULL;
    if (f) f((unsigned int)mode);
}

void lshgl_identity(void) {
    PFNGLLOADIDENTITY f = g_gl ? (PFNGLLOADIDENTITY)sym_gl("glLoadIdentity") : NULL;
    if (f) f();
}

void lshgl_ortho(double l, double r, double b, double t, double n, double f) {
    PFNGLORTHO fn = g_gl ? (PFNGLORTHO)sym_gl("glOrtho") : NULL;
    if (fn) fn(l, r, b, t, n, f);
}

void lshgl_frustum(double l, double r, double b, double t, double n, double f) {
    /* Build the frustum matrix explicitly. Calling glFrustum through dlsym is
     * unreliable on some Mesa/libGLdispatch builds, while glLoadMatrixf is
     * always dispatched correctly. Column-major, replaces the current matrix. */
    typedef void (*PFNGLLOADMATRIXD)(const double*);
    PFNGLLOADMATRIXD load = g_gl ? (PFNGLLOADMATRIXD)sym_gl("glLoadMatrixd") : NULL;
    if (!load || l == r || b == t || n <= 0.0 || f <= n) return;
    double m[16] = {
        2.0 * n / (r - l), 0.0, 0.0, 0.0,
        0.0, 2.0 * n / (t - b), 0.0, 0.0,
        (r + l) / (r - l), (t + b) / (t - b),
        -(f + n) / (f - n), -1.0,
        0.0, 0.0, -2.0 * f * n / (f - n), 0.0
    };
    load(m);
}

void lshgl_vertex3(double x, double y, double z) {
    PFNGLVERTEX3F f = g_gl ? (PFNGLVERTEX3F)sym_gl("glVertex3f") : NULL;
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_normal3(double x, double y, double z) {
    PFNGLNORMAL3F f = g_gl ? (PFNGLNORMAL3F)sym_gl("glNormal3f") : NULL;
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_translate(double x, double y, double z) {
    PFNGLTRANSLATEF f = g_gl ? (PFNGLTRANSLATEF)sym_gl("glTranslatef") : NULL;
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_rotate(double angle, double x, double y, double z) {
    PFNGLROTATEF f = g_gl ? (PFNGLROTATEF)sym_gl("glRotatef") : NULL;
    if (f) f((float)angle, (float)x, (float)y, (float)z);
}

void lshgl_scale(double x, double y, double z) {
    PFNGLSCALEF f = g_gl ? (PFNGLSCALEF)sym_gl("glScalef") : NULL;
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_enable(int cap) {
    PFNGLENABLE f = g_gl ? (PFNGLENABLE)sym_gl("glEnable") : NULL;
    if (f) f((unsigned int)cap);
}

void lshgl_disable(int cap) {
    PFNGLDISABLE f = g_gl ? (PFNGLDISABLE)sym_gl("glDisable") : NULL;
    if (f) f((unsigned int)cap);
}

void lshgl_depth_func(int func) {
    PFNGLDEPTHFUNC f = g_gl ? (PFNGLDEPTHFUNC)sym_gl("glDepthFunc") : NULL;
    if (f) f((unsigned int)func);
}

void lshgl_depth_mask(int on) {
    PFNGLDEPTHMASK f = g_gl ? (PFNGLDEPTHMASK)sym_gl("glDepthMask") : NULL;
    if (f) f(on ? 1 : 0);
}

void lshgl_blend(int on) {
    PFNGLENABLE enable = g_gl ? (PFNGLENABLE)sym_gl("glEnable") : NULL;
    PFNGLDISABLE disable = g_gl ? (PFNGLDISABLE)sym_gl("glDisable") : NULL;
    PFNGLBLENDFUNC blend = g_gl ? (PFNGLBLENDFUNC)sym_gl("glBlendFunc") : NULL;
    if (blend) blend(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    if (on && enable) enable(GL_BLEND);
    else if (!on && disable) disable(GL_BLEND);
}

void lshgl_line_width(double width) {
    PFNGLLINEWIDTH f = g_gl ? (PFNGLLINEWIDTH)sym_gl("glLineWidth") : NULL;
    if (f) f((float)width);
}

void lshgl_shade_flat(void) {
    PFNGLSHADEMODEL f = g_gl ? (PFNGLSHADEMODEL)sym_gl("glShadeModel") : NULL;
    if (f) f(GL_FLAT);
}

void lshgl_shade_smooth(void) {
    PFNGLSHADEMODEL f = g_gl ? (PFNGLSHADEMODEL)sym_gl("glShadeModel") : NULL;
    if (f) f(0x1D01);  /* GL_SMOOTH */
}

void lshgl_lighting(int on) {
    PFNGLENABLE enable = g_gl ? (PFNGLENABLE)sym_gl("glEnable") : NULL;
    PFNGLDISABLE disable = g_gl ? (PFNGLDISABLE)sym_gl("glDisable") : NULL;
    PFNGLCOLORMATERIAL color_material =
        g_gl ? (PFNGLCOLORMATERIAL)sym_gl("glColorMaterial") : NULL;
    PFNGLSHADEMODEL shade = g_gl ? (PFNGLSHADEMODEL)sym_gl("glShadeModel") : NULL;
    if (!on) {
        if (disable) disable(GL_LIGHTING);
        return;
    }
    if (enable) {
        enable(GL_LIGHTING);
        enable(GL_LIGHT0);
        enable(GL_COLOR_MATERIAL);
    }
    if (color_material) color_material(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE);
    if (shade) shade(GL_FLAT);
}

void lshgl_light_directional(double x, double y, double z,
                             double r, double g, double b) {
    PFNGLLIGHTFV f = g_gl ? (PFNGLLIGHTFV)sym_gl("glLightfv") : NULL;
    if (!f) return;
    float position[4] = { (float)x, (float)y, (float)z, 0.0f };
    float diffuse[4] = { (float)r, (float)g, (float)b, 1.0f };
    float specular[4] = { 0.0f, 0.0f, 0.0f, 1.0f };
    f(GL_LIGHT0, GL_POSITION, position);
    f(GL_LIGHT0, GL_DIFFUSE, diffuse);
    f(GL_LIGHT0, GL_SPECULAR, specular);
}

void lshgl_light_ambient(double r, double g, double b, double a) {
    PFNGLLIGHTMODELFV f = g_gl ? (PFNGLLIGHTMODELFV)sym_gl("glLightModelfv") : NULL;
    if (!f) return;
    float ambient[4] = { (float)r, (float)g, (float)b, (float)a };
    f(GL_LIGHT_MODEL_AMBIENT, ambient);
}

void lshgl_fog(double start, double end, double r, double g, double b) {
    PFNGLFOGF fogf = g_gl ? (PFNGLFOGF)sym_gl("glFogf") : NULL;
    PFNGLFOGFV fogfv = g_gl ? (PFNGLFOGFV)sym_gl("glFogfv") : NULL;
    PFNGLENABLE enable = g_gl ? (PFNGLENABLE)sym_gl("glEnable") : NULL;
    if (!fogf || !fogfv) return;
    float color[4] = { (float)r, (float)g, (float)b, 1.0f };
    fogf(GL_FOG_MODE, GL_LINEAR);
    fogf(GL_FOG_START, (float)start);
    fogf(GL_FOG_END, (float)end);
    fogfv(GL_FOG_COLOR, color);
    if (enable) enable(GL_FOG);
}

void lshgl_no_fog(void) {
    PFNGLDISABLE f = g_gl ? (PFNGLDISABLE)sym_gl("glDisable") : NULL;
    if (f) f(GL_FOG);
}

int lshgl_new_list(void) {
    PFNGLGENLISTS gen = g_gl ? (PFNGLGENLISTS)sym_gl("glGenLists") : NULL;
    PFNGLNEWLIST begin = g_gl ? (PFNGLNEWLIST)sym_gl("glNewList") : NULL;
    if (!gen || !begin) return 0;
    unsigned int id = gen(1);
    if (id == 0) return 0;
    begin(id, 0x1300);  /* GL_COMPILE */
    return (int)id;
}

void lshgl_end_list(void) {
    PFNGLENDLIST f = g_gl ? (PFNGLENDLIST)sym_gl("glEndList") : NULL;
    if (f) f();
}

void lshgl_call_list(int id) {
    PFNGLCALLLIST f = g_gl ? (PFNGLCALLLIST)sym_gl("glCallList") : NULL;
    if (f && id != 0) f((unsigned int)id);
}

void lshgl_delete_list(int id) {
    PFNGLDELETELISTS f = g_gl ? (PFNGLDELETELISTS)sym_gl("glDeleteLists") : NULL;
    if (f && id != 0) f((unsigned int)id, 1);
}
