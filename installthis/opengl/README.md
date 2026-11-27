# opengl

Headless-capable OpenGL bindings for native Leash programs.

```leash
use opengl::opengl::*;

fnc main() {
    ctx: pointer<void> = Gl.init();
    show(Gl.version());
    Gl.viewport(16, 16);
    Gl.clear_color(1.0, 0.0, 0.0, 1.0);
    Gl.clear();
    show(Gl.pixel(8, 8));
    Gl.close(ctx);
}
```

`Gl.init()` creates a surfaceless EGL context, so the module works on a
machine without a window (a display connection is still needed by the
system EGL implementation). Pixel readback uses an offscreen framebuffer.

For an interactive program, open a real X11/GLX window instead:

```leash
use opengl::opengl::*;

fnc main() {
    ctx: pointer<void> = Gl.open_window(960, 600, "Voxel Sandbox");
    if ctx == null { return; }
    while !Gl.should_close() {
        Gl.poll_events();
        if Gl.key_pressed(Gl.key_escape()) { stop; }
        Gl.viewport(Gl.width(), Gl.height());
        Gl.clear_color(0.45, 0.65, 0.95, 1.0);
        Gl.clear();
        Gl.swap_buffers();
    }
    Gl.close(ctx);
}
```

`Gl.open_window()` returns `null` when there is no usable display or GLX
visual, so programs can fall back to `Gl.init()`. `Gl.close(ctx)` releases
either kind of context. Events are drained by `Gl.poll_events()`;
`Gl.should_close()` also reacts to the window manager's close button.
Key state is queried with `Gl.key_down()` / `Gl.key_pressed()` using the
exported X11 keysym helpers (`Gl.key_w()`, `Gl.key_space()`,
`Gl.key_escape()`, ...). `Gl.time_ms()` and `Gl.sleep_ms()` are provided for
frame pacing.

For first-person controls call `Gl.mouse_capture(true)` (relative pointer
mode, released on real focus loss) and read `Gl.mouse_dx()` /
`Gl.mouse_dy()` each frame; a click can re-capture after focus loss. Call
`Gl.set_vsync(false)` before the frame loop for an uncapped frame rate and
use `Gl.time_ms()` to count FPS. 3D rendering uses `Gl.frustum()`,
`Gl.vertex3()`, `Gl.normal3()`, `Gl.translate()` / `Gl.rotate()`,
`Gl.lighting(true)` + `Gl.light_ambient()` / `Gl.light_directional()`,
`Gl.fog()` and compiled display lists (`Gl.new_list()` ...
`Gl.end_list()`, `Gl.call_list(id)`). Important: `Gl.lighting(true)` enables
`GL_LIGHT0` and color material; `Gl.enable(Gl.lighting_cap())` alone only
turns on the lighting model and leaves faces unlit.

The shim loads the system OpenGL, EGL and X11 libraries at runtime with
`dlopen`; no development packages or linker flags are needed. Build the
native archive with `sh src/build.sh`. Unsupported targets get no-op stubs
and a clear message from `Gl.version()`.
