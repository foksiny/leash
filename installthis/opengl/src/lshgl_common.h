/* Shared OpenGL typedefs and constants for every Leash GL shim backend. */
#ifndef LSHGL_COMMON_H
#define LSHGL_COMMON_H
#include <stdbool.h>
#include <stdint.h>

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


/* Implemented by each platform backend: resolve a core or extension GL entry
 * point in the currently active context. */
void* lshgl_gl_symbol(const char* name);

/* Offscreen framebuffer setup/teardown shared by all backends. */
void lshgl_setup_framebuffer(void);
void lshgl_teardown_framebuffer(void);

/* Public shim API (implemented once per platform). */
void* lshgl_create_context(void);
void lshgl_destroy_context(void* ctx);
void* lshgl_open_window(int w, int h, const char* title);
void lshgl_close_window(void);
void lshgl_poll_events(void);
bool lshgl_should_close(void);
void lshgl_request_close(void);
int lshgl_window_width(void);
int lshgl_window_height(void);
bool lshgl_key_down(int keysym);
bool lshgl_key_pressed(int keysym);
bool lshgl_pause_requested(void);
void lshgl_mouse_capture(int on);
bool lshgl_mouse_captured(void);
int lshgl_mouse_dx(void);
int lshgl_mouse_dy(void);
int lshgl_mouse_x(void);
int lshgl_mouse_y(void);
bool lshgl_mouse_down(int button);
bool lshgl_mouse_pressed(int button);
void lshgl_set_vsync(int enabled);
void lshgl_swap_buffers(void);
int64_t lshgl_time_ms(void);
void lshgl_sleep_ms(int ms);
const char* lshgl_version(void);
const char* lshgl_renderer(void);
const char* lshgl_vendor(void);
void lshgl_viewport(int w, int h);
void lshgl_clear_color(double r, double g, double b, double a);
void lshgl_clear(void);
int64_t lshgl_read_pixel(int x, int y);
int lshgl_error(void);
void lshgl_begin(int mode);
void lshgl_end(void);
void lshgl_color(double r, double g, double b, double a);
void lshgl_vertex2(double x, double y);
void lshgl_vertex3(double x, double y, double z);
void lshgl_normal3(double x, double y, double z);
void lshgl_matrix(int mode);
void lshgl_identity(void);
void lshgl_ortho(double l, double r, double b, double t, double n, double f);
void lshgl_frustum(double l, double r, double b, double t, double n, double f);
void lshgl_translate(double x, double y, double z);
void lshgl_rotate(double angle, double x, double y, double z);
void lshgl_scale(double x, double y, double z);
void lshgl_enable(int cap);
void lshgl_disable(int cap);
void lshgl_depth_func(int func);
void lshgl_depth_mask(int on);
void lshgl_blend(int on);
void lshgl_line_width(double width);
void lshgl_shade_flat(void);
void lshgl_shade_smooth(void);
void lshgl_lighting(int on);
void lshgl_light_directional(double x, double y, double z, double r, double g, double b);
void lshgl_light_ambient(double r, double g, double b, double a);
void lshgl_fog(double start, double end, double r, double g, double b);
void lshgl_no_fog(void);
int lshgl_new_list(void);
void lshgl_end_list(void);
void lshgl_call_list(int id);
void lshgl_delete_list(int id);
int lshgl_texture_create(int w, int h, const char* rgba);
void lshgl_texture_bind(int id);
void lshgl_tex_coord2(double u, double v);

#endif
