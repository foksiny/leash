/* Platform-neutral immediate-mode GL wrappers, shared by all backends.
 * Included by each backend after it defines lshgl_gl_symbol(). */
#define sym_gl(name) lshgl_gl_symbol(name)

/* Offscreen RGBA8 + depth24 framebuffer with pixel readback. Every backend
 * makes a context current before calling this. */
void lshgl_setup_framebuffer(void) {
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
    if (!gen_tex || !bind_tex || !tex_image || !tex_param || !gen_fb ||
        !bind_fb || !fb_tex || !check_fb) return;
    gen_tex(1, &g_tex);
    bind_tex(GL_TEXTURE_2D, g_tex);
    tex_image(GL_TEXTURE_2D, 0, GL_RGBA8, g_fb_w, g_fb_h, 0, GL_RGBA,
              GL_UNSIGNED_BYTE, NULL);
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
        lshgl_teardown_framebuffer();
    }
}

void lshgl_teardown_framebuffer(void) {
    PFNGLDELETEFRAMEBUFFERS del_fb = (PFNGLDELETEFRAMEBUFFERS)sym_gl("glDeleteFramebuffers");
    PFNGLDELETERENDERBUFFERS del_rb = (PFNGLDELETERENDERBUFFERS)sym_gl("glDeleteRenderbuffers");
    PFNGLDELETETEXTURES del_tex = (PFNGLDELETETEXTURES)sym_gl("glDeleteTextures");
    if (g_fbo && del_fb) del_fb(1, &g_fbo);
    if (g_depth_rb && del_rb) del_rb(1, &g_depth_rb);
    if (g_tex && del_tex) del_tex(1, &g_tex);
    g_fbo = 0;
    g_tex = 0;
    g_depth_rb = 0;
}

const char* lshgl_version(void) {
    PFNGLGETSTRING f = (PFNGLGETSTRING)sym_gl("glGetString");
    const unsigned char* s = f ? f(GL_VERSION) : NULL;
    return s ? (const char*)s : "";
}

const char* lshgl_renderer(void) {
    PFNGLGETSTRING f = (PFNGLGETSTRING)sym_gl("glGetString");
    const unsigned char* s = f ? f(GL_RENDERER) : NULL;
    return s ? (const char*)s : "";
}

const char* lshgl_vendor(void) {
    PFNGLGETSTRING f = (PFNGLGETSTRING)sym_gl("glGetString");
    const unsigned char* s = f ? f(GL_VENDOR) : NULL;
    return s ? (const char*)s : "";
}

void lshgl_viewport(int w, int h) {
    PFNGLVIEWPORT f = (PFNGLVIEWPORT)sym_gl("glViewport");
    if (f) f(0, 0, w, h);
}

void lshgl_clear_color(double r, double g, double b, double a) {
    PFNGLCLEARCOLOR f = (PFNGLCLEARCOLOR)sym_gl("glClearColor");
    if (f) f((float)r, (float)g, (float)b, (float)a);
}

void lshgl_clear(void) {
    PFNGLCLEAR f = (PFNGLCLEAR)sym_gl("glClear");
    if (f) f(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}

int64_t lshgl_read_pixel(int x, int y) {
    unsigned char px[4] = {0, 0, 0, 0};
    PFNGLREADPIXELS f = (PFNGLREADPIXELS)sym_gl("glReadPixels");
    if (f) f(x, y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    return (int64_t)(((uint32_t)px[0] << 24) | ((uint32_t)px[1] << 16) |
                     ((uint32_t)px[2] << 8) | (uint32_t)px[3]);
}

int lshgl_error(void) {
    PFNGLGETERROR f = (PFNGLGETERROR)sym_gl("glGetError");
    return f ? (int)f() : 1;
}

void lshgl_begin(int mode) {
    PFNGLBEGIN f = (PFNGLBEGIN)sym_gl("glBegin");
    if (f) f((unsigned int)mode);
}

void lshgl_end(void) {
    PFNGLEND f = (PFNGLEND)sym_gl("glEnd");
    if (f) f();
}

void lshgl_color(double r, double g, double b, double a) {
    PFNGLCOLOR4F f = (PFNGLCOLOR4F)sym_gl("glColor4f");
    if (f) f((float)r, (float)g, (float)b, (float)a);
}

void lshgl_vertex2(double x, double y) {
    PFNGLVERTEX2F f = (PFNGLVERTEX2F)sym_gl("glVertex2f");
    if (f) f((float)x, (float)y);
}

void lshgl_matrix(int mode) {
    PFNGLMATRIXMODE f = (PFNGLMATRIXMODE)sym_gl("glMatrixMode");
    if (f) f((unsigned int)mode);
}

void lshgl_identity(void) {
    PFNGLLOADIDENTITY f = (PFNGLLOADIDENTITY)sym_gl("glLoadIdentity");
    if (f) f();
}

void lshgl_ortho(double l, double r, double b, double t, double n, double f) {
    PFNGLORTHO fn = (PFNGLORTHO)sym_gl("glOrtho");
    if (fn) fn(l, r, b, t, n, f);
}

void lshgl_frustum(double l, double r, double b, double t, double n, double f) {
    /* Build the frustum matrix explicitly. Calling glFrustum through dlsym is
     * unreliable on some Mesa/libGLdispatch builds, while glLoadMatrixf is
     * always dispatched correctly. Column-major, replaces the current matrix. */
    typedef void (*PFNGLLOADMATRIXD)(const double*);
    PFNGLLOADMATRIXD load = (PFNGLLOADMATRIXD)sym_gl("glLoadMatrixd");
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
    PFNGLVERTEX3F f = (PFNGLVERTEX3F)sym_gl("glVertex3f");
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_normal3(double x, double y, double z) {
    PFNGLNORMAL3F f = (PFNGLNORMAL3F)sym_gl("glNormal3f");
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_translate(double x, double y, double z) {
    PFNGLTRANSLATEF f = (PFNGLTRANSLATEF)sym_gl("glTranslatef");
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_rotate(double angle, double x, double y, double z) {
    PFNGLROTATEF f = (PFNGLROTATEF)sym_gl("glRotatef");
    if (f) f((float)angle, (float)x, (float)y, (float)z);
}

void lshgl_scale(double x, double y, double z) {
    PFNGLSCALEF f = (PFNGLSCALEF)sym_gl("glScalef");
    if (f) f((float)x, (float)y, (float)z);
}

void lshgl_enable(int cap) {
    PFNGLENABLE f = (PFNGLENABLE)sym_gl("glEnable");
    if (f) f((unsigned int)cap);
}

void lshgl_disable(int cap) {
    PFNGLDISABLE f = (PFNGLDISABLE)sym_gl("glDisable");
    if (f) f((unsigned int)cap);
}

void lshgl_depth_func(int func) {
    PFNGLDEPTHFUNC f = (PFNGLDEPTHFUNC)sym_gl("glDepthFunc");
    if (f) f((unsigned int)func);
}

void lshgl_depth_mask(int on) {
    PFNGLDEPTHMASK f = (PFNGLDEPTHMASK)sym_gl("glDepthMask");
    if (f) f(on ? 1 : 0);
}

void lshgl_blend(int on) {
    PFNGLENABLE enable = (PFNGLENABLE)sym_gl("glEnable");
    PFNGLDISABLE disable = (PFNGLDISABLE)sym_gl("glDisable");
    PFNGLBLENDFUNC blend = (PFNGLBLENDFUNC)sym_gl("glBlendFunc");
    if (blend) blend(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    if (on && enable) enable(GL_BLEND);
    else if (!on && disable) disable(GL_BLEND);
}

void lshgl_line_width(double width) {
    PFNGLLINEWIDTH f = (PFNGLLINEWIDTH)sym_gl("glLineWidth");
    if (f) f((float)width);
}

void lshgl_shade_flat(void) {
    PFNGLSHADEMODEL f = (PFNGLSHADEMODEL)sym_gl("glShadeModel");
    if (f) f(GL_FLAT);
}

void lshgl_shade_smooth(void) {
    PFNGLSHADEMODEL f = (PFNGLSHADEMODEL)sym_gl("glShadeModel");
    if (f) f(0x1D01);  /* GL_SMOOTH */
}

void lshgl_lighting(int on) {
    PFNGLENABLE enable = (PFNGLENABLE)sym_gl("glEnable");
    PFNGLDISABLE disable = (PFNGLDISABLE)sym_gl("glDisable");
    PFNGLCOLORMATERIAL color_material =
        (PFNGLCOLORMATERIAL)sym_gl("glColorMaterial");
    PFNGLSHADEMODEL shade = (PFNGLSHADEMODEL)sym_gl("glShadeModel");
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
    PFNGLLIGHTFV f = (PFNGLLIGHTFV)sym_gl("glLightfv");
    if (!f) return;
    float position[4] = { (float)x, (float)y, (float)z, 0.0f };
    float diffuse[4] = { (float)r, (float)g, (float)b, 1.0f };
    float specular[4] = { 0.0f, 0.0f, 0.0f, 1.0f };
    f(GL_LIGHT0, GL_POSITION, position);
    f(GL_LIGHT0, GL_DIFFUSE, diffuse);
    f(GL_LIGHT0, GL_SPECULAR, specular);
}

void lshgl_light_ambient(double r, double g, double b, double a) {
    PFNGLLIGHTMODELFV f = (PFNGLLIGHTMODELFV)sym_gl("glLightModelfv");
    if (!f) return;
    float ambient[4] = { (float)r, (float)g, (float)b, (float)a };
    f(GL_LIGHT_MODEL_AMBIENT, ambient);
}

void lshgl_fog(double start, double end, double r, double g, double b) {
    PFNGLFOGF fogf = (PFNGLFOGF)sym_gl("glFogf");
    PFNGLFOGFV fogfv = (PFNGLFOGFV)sym_gl("glFogfv");
    PFNGLENABLE enable = (PFNGLENABLE)sym_gl("glEnable");
    if (!fogf || !fogfv) return;
    float color[4] = { (float)r, (float)g, (float)b, 1.0f };
    fogf(GL_FOG_MODE, GL_LINEAR);
    fogf(GL_FOG_START, (float)start);
    fogf(GL_FOG_END, (float)end);
    fogfv(GL_FOG_COLOR, color);
    if (enable) enable(GL_FOG);
}

void lshgl_no_fog(void) {
    PFNGLDISABLE f = (PFNGLDISABLE)sym_gl("glDisable");
    if (f) f(GL_FOG);
}

int lshgl_new_list(void) {
    PFNGLGENLISTS gen = (PFNGLGENLISTS)sym_gl("glGenLists");
    PFNGLNEWLIST begin = (PFNGLNEWLIST)sym_gl("glNewList");
    if (!gen || !begin) return 0;
    unsigned int id = gen(1);
    if (id == 0) return 0;
    begin(id, 0x1300);  /* GL_COMPILE */
    return (int)id;
}

void lshgl_end_list(void) {
    PFNGLENDLIST f = (PFNGLENDLIST)sym_gl("glEndList");
    if (f) f();
}

void lshgl_call_list(int id) {
    PFNGLCALLLIST f = (PFNGLCALLLIST)sym_gl("glCallList");
    if (f && id != 0) f((unsigned int)id);
}

void lshgl_delete_list(int id) {
    PFNGLDELETELISTS f = (PFNGLDELETELISTS)sym_gl("glDeleteLists");
    if (f && id != 0) f((unsigned int)id, 1);
}

typedef void (*PFNGLPIXELSTOREI)(unsigned int, int);
int lshgl_texture_create(int w, int h, const char* rgba) {
    PFNGLGENTEXTURES gen = (PFNGLGENTEXTURES)sym_gl("glGenTextures");
    PFNGLBINDTEXTURE bind = (PFNGLBINDTEXTURE)sym_gl("glBindTexture");
    PFNGLTEXIMAGE2D image = (PFNGLTEXIMAGE2D)sym_gl("glTexImage2D");
    PFNGLTEXPARAMETERI param = (PFNGLTEXPARAMETERI)sym_gl("glTexParameteri");
    PFNGLPIXELSTOREI store = (PFNGLPIXELSTOREI)sym_gl("glPixelStorei");
    if (!gen || !bind || !image || !param || !rgba || w < 1 || h < 1) return 0;
    unsigned int id = 0;
    gen(1, &id);
    if (id == 0) return 0;
    bind(GL_TEXTURE_2D, id);
    if (store) store(0x0CF5, 1);  /* GL_UNPACK_ALIGNMENT */
    image(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba);
    param(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    param(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    param(GL_TEXTURE_2D, 0x2802, 0x812F);  /* WRAP_S, CLAMP_TO_EDGE */
    param(GL_TEXTURE_2D, 0x2803, 0x812F);  /* WRAP_T, CLAMP_TO_EDGE */
    return (int)id;
}

void lshgl_texture_bind(int id) {
    PFNGLBINDTEXTURE bind = (PFNGLBINDTEXTURE)sym_gl("glBindTexture");
    if (bind) bind(GL_TEXTURE_2D, (unsigned int)id);
}

void lshgl_tex_coord2(double u, double v) {
    typedef void (*PFNGLTEXCOORD2F)(float, float);
    PFNGLTEXCOORD2F fn = (PFNGLTEXCOORD2F)sym_gl("glTexCoord2f");
    if (fn) fn((float)u, (float)v);
}
