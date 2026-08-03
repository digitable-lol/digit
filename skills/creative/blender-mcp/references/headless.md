# Running Blender MCP headless

## Why background mode is not an option

The addon guards its own startup:

```python
if bpy.app.background:
    print("BlenderMCP: cannot start server in background mode ...")
    return
```

Even with the guard removed it would not work. Every command is dispatched
through `bpy.app.timers`, and timers only fire when Blender runs an event loop.
Under `blender -b` there is none, so commands queue and never execute — the
client sees a hang, not an error.

The supported answer is a **virtual display**: run the ordinary GUI build with
nothing to display it on.

## The launcher

```bash
export BLENDER_USER_SCRIPTS="$REPO/blender_scripts"   # project-local addons
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe

setsid nohup xvfb-run -a -s "-screen 0 1920x1080x24" \
  "$BLENDER" --factory-startup --python "$REPO/startup.py" \
  >"$LOG" 2>&1 &
```

- `--factory-startup` keeps the host's real Blender preferences untouched;
  combined with a project-local `BLENDER_USER_SCRIPTS`, the setup is contained.
- `setsid` puts Blender in its own process group, so the launcher can kill the
  whole group — `xvfb-run` spawns children that a bare `kill` would orphan.
- `llvmpipe` is Mesa's software rasteriser, needed when the host has no usable
  3D acceleration. Blender still gets a real OpenGL context.

Readiness is a **port check**, not a sleep:

```bash
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }
```

## The black screenshot

The single most confusing failure, because it does not look like a failure.

Upstream's `get_viewport_screenshot` calls `bpy.ops.screen.screenshot_area()`,
which reads the window's **front buffer** — the framebuffer the compositor
presents. Headless there is no compositor and no buffer swap, so the front
buffer stays at its clear colour. The result is a structurally valid PNG,
entirely black, returned with `"success": True`. An agent cannot tell it has
gone blind, and will keep "fixing" a model it cannot see.

The fix renders the viewport offscreen, never touching the window:

```python
offscreen = gpu.types.GPUOffScreen(width, height)
with offscreen.bind():
    offscreen.draw_view3d(scene, view_layer, space_data, region, view_matrix,
                          projection_matrix, do_color_management=True)
```

### Telling the two apart

Decode the PNG and count distinct pixel values. A working capture of any
non-empty viewport yields hundreds; the broken one yields exactly **1**.

```python
w, h, distinct = read_png(path)
assert len(distinct) > 1, "uniformly black — addon is unpatched"
```

A 400×239 capture of a default scene with one object measured **1071** distinct
values on the patched addon.

## Renderer choice

Measured on 8 CPU cores, no GPU, 480×360 at 16 samples:

| Engine | Time |
|---|---|
| Cycles (CPU) | **1.3 s** |
| EEVEE | 6.0 s |

EEVEE is a rasteriser designed around real GPU hardware; under llvmpipe it is
emulated in software and loses to Cycles, which was written for CPUs all along.
Do not carry the desktop intuition here — measure.

## Diagnostics

```bash
./run.sh status                       # is the port open
./run.sh log                          # Blender's stdout
nc -z 127.0.0.1 9876 && echo READY
ss -tlnp | grep 9876                  # who actually holds the port
```

The startup script prints a line worth grepping for:

```
STARTUP: polyhaven=True telemetry=False running=True port=9876
```

| Symptom | Cause |
|---|---|
| Port never opens, log mentions GL | llvmpipe env vars not exported |
| Port never opens, no log at all | Wrong `$BLENDER` path |
| Opens then dies | `read_factory_settings()` unregistered the addon |
| Opens, tools hang | Blender started with `-b` after all |
