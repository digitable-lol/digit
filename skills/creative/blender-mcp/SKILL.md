---
name: blender-mcp
description: Drive Blender over MCP to model, render and export 3D assets — including headless on a server with no GPU. Use when the task involves Blender, .blend files, GLB/glTF export, procedural geometry, 3D game assets, renders, or the blender-mcp addon.
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [blender, mcp, 3d, glb, gltf, modelling, rendering, game-assets, headless, xvfb, procedural-geometry]
    related_skills: [touchdesigner-mcp, digitable-workbench, digitable-courses]
---

# Blender over MCP

Drives Blender 4.5 LTS+ through [blender-mcp](https://github.com/ahujasid/blender-mcp).
Headless setup: [digitable-lol/blender-mcp](https://github.com/digitable-lol/blender-mcp).
Course (RU): https://courses.digitable.life/post/3d-pipeline/00-overview/

## CRITICAL RULES

1. **Blender must already be running.** Every tool talks to a socket server
   living *inside* Blender on `127.0.0.1:9876`. No Blender, no tools. Check with
   `./run.sh status` before anything else.
2. **Never call `bpy.ops.wm.read_factory_settings()`.** It resets preferences,
   which unregisters every addon — including the one serving you. You lose the
   connection until someone restarts Blender. To empty a scene use
   `bpy.ops.wm.read_homefile(use_empty=True)` or delete objects directly.
3. **Never reload the file from inside running code.** Reloading invalidates the
   context the script is executing in, and every later `bpy.context` lookup
   fails. Delete objects in a loop instead (see Clearing a scene).
4. **Verify visually, not by status code.** `get_viewport_screenshot` can report
   `"success": True` and hand back a completely black image. Look at the picture.
5. **Trust boundary: `execute_blender_code` runs arbitrary Python** with your
   user's filesystem rights. Keep the socket on localhost. Do not leave MCP
   connected while opening an untrusted `.blend`.

## Architecture

```
Digit -> MCP (stdio) -> blender-mcp server -> TCP 127.0.0.1:9876 -> addon inside Blender
```

The addon dispatches every command through `bpy.app.timers`, which only tick
when Blender has a real event loop. This is why **background mode (`blender -b`)
does not work** — the addon refuses to start, and commands would hang forever.
On a headless host, run the real GUI under `xvfb`.

## Setup

```bash
bash "${DIGIT_SKILL_DIR:-$HOME/.digit/skills/creative/blender-mcp}/scripts/setup.sh"
```

The script checks for Blender, `xvfb` and `uvx`, clones the headless launcher if
it is missing, starts Blender, and verifies the port is listening.

Registering the MCP server (user scope, telemetry off):

```bash
claude mcp add blender --scope user \
  -e DISABLE_TELEMETRY=1 -e BLENDER_MCP_DISABLE_TELEMETRY=1 -e MCP_DISABLE_TELEMETRY=1 \
  -- "$(command -v uvx)" blender-mcp@1.6.4
```

Use the **absolute** `uvx` path — a desktop client may not inherit your
interactive shell's `PATH`.

### Telemetry

Upstream enables it by default on **both** sides. Nearly every tool takes a
`user_prompt` parameter that exists solely to feed it — upstream's own docstrings
say "required for telemetry" — and `execute_blender_code` is decorated
`@rich_telemetry_tool("execute_blender_code", capture_code=True)`, i.e. it ships
your code. Prompts, code and viewport screenshots go to a third-party Supabase.

Disable it in both places: the three `*_DISABLE_TELEMETRY=1` environment
variables above, and `telemetry_consent = False` on the addon preferences. With
telemetry off, `user_prompt` is inert — pass it or don't.

## Workflow

### Step 0: Orient before building

```
get_scene_info(user_prompt="...")              -> what already exists
get_viewport_screenshot(max_size=800)          -> what it looks like
get_object_info(object_name="Cube")            -> one object in detail
```

Never assume the scene is empty. Never assume it is the scene you left.

### Step 1: Clear

```python
# Not read_homefile(): reloading mid-script invalidates the running context.
import bpy
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.lights):
    for d in list(coll):
        if d.users == 0:
            coll.remove(d)
```

### Step 2: Build with modifiers, not hand-placed vertices

Modifiers stay editable and keep topology clean. The pattern that works:
primitive → edit topology in `bmesh` → Solidify/Bevel/Subsurf → shade smooth.

```python
bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=0.04, depth=0.095)
body = bpy.context.active_object
sol = body.modifiers.new("Solidify", 'SOLIDIFY')
sol.thickness = 0.004
sol.offset = -1              # thicken inward, keep the outer silhouette
bev = body.modifiers.new("Bevel", 'BEVEL')
bev.width, bev.segments = 0.0012, 3
bev.limit_method, bev.angle_limit = 'ANGLE', math.radians(35)
```

Set `bevel.limit_method='ANGLE'` — the default bevels every edge including ones
you wanted sharp.

### Step 3: Light and frame before judging

An unlit render is not evidence the model is wrong. Add a sun and a camera, then
screenshot.

### Step 4: Verify visually

```
get_viewport_screenshot(max_size=800)
```

**Look at the returned image.** If it is uniformly black on a headless box, the
addon is unpatched and reading the window's front buffer — see Headless notes.

### Step 5: Export

```python
bpy.ops.export_scene.gltf(
    filepath="/abs/path/asset.glb",
    export_format='GLB',
    export_apply=True,          # apply modifiers
    export_yup=True,            # glTF is Y-up; Blender is Z-up
)
```

Blender's CWD is wherever it was launched from — **always use absolute paths.**

## Tool reference (blender-mcp 1.6.4)

Verified against the pinned wheel; identical in `main` at time of writing.

**Core — these four do almost everything:**

| Tool | Signature |
|------|-----------|
| `get_scene_info` | `(user_prompt)` |
| `get_object_info` | `(object_name, user_prompt='')` |
| `get_viewport_screenshot` | `(max_size=1000, user_prompt='')` |
| `execute_blender_code` | `(code, user_prompt='')` |

**Poly Haven** — free HDRIs, textures and models, no API key. On by default in
the Digitable setup.

| Tool | Signature |
|------|-----------|
| `get_polyhaven_status` | `(user_prompt='')` |
| `get_polyhaven_categories` | `(asset_type='hdris', user_prompt='')` |
| `search_polyhaven_assets` | `(asset_type='all', categories=None, user_prompt='')` |
| `download_polyhaven_asset` | `(asset_id, asset_type, resolution='1k', file_format=None, user_prompt='')` |
| `set_texture` | `(object_name, texture_id, user_prompt='')` |

**Asset generators — all need API keys and are OFF by default.** Their status
tools work regardless; the rest fail without a key.

| Provider | Tools |
|---|---|
| Sketchfab | `get_sketchfab_status`, `search_sketchfab_models`, `get_sketchfab_model_preview`, `download_sketchfab_model` |
| Hyper3D Rodin | `get_hyper3d_status`, `generate_hyper3d_model_via_text`, `generate_hyper3d_model_via_images`, `poll_rodin_job_status`, `import_generated_asset` |
| Hunyuan3D | `get_hunyuan3d_status`, `generate_hunyuan3d_model`, `poll_hunyuan_job_status`, `import_generated_asset_hunyuan` |

Generator jobs are **asynchronous**: generate → poll until done → import. Do not
import before the poll reports completion.

Image-to-3D is a poor fit for stylised game assets — it produces scan-like
geometry with artifacts and no rig. Digitable rejected it for Corners Arena and
hand-modelled instead. Use it for background props, not hero assets.

## Headless notes

- **Background mode does not work.** Run the GUI under
  `xvfb-run -a -s "-screen 0 1920x1080x24"` with `LIBGL_ALWAYS_SOFTWARE=1` and
  `GALLIUM_DRIVER=llvmpipe`.
- **Cycles on CPU beats EEVEE here.** Measured on 8 cores: 480×360 at 16 samples
  took **1.3 s** on Cycles, **6.0 s** on EEVEE. llvmpipe rasterises in software,
  so the usual "EEVEE is the fast one" intuition is inverted.
- **Black screenshots mean an unpatched addon.** Upstream reads the window's
  front buffer, which without a compositor stays black — while still reporting
  success. The Digitable addon renders through `gpu.types.GPUOffScreen` instead.
- **Scene options reset on file load.** `blendermcp_use_polyhaven` and friends
  are per-scene, so opening any `.blend` silently reverts them. The Digitable
  `startup.py` installs a `load_post` handler that re-applies them.

## Export gate for game assets

Before a GLB replaces procedural geometry in a running game:

- [ ] transforms applied, scale consistent, origin deliberate (a character's
      origin belongs at its base, not its centre);
- [ ] mesh, material and animation-clip names are stable — runtimes recolour by
      material-slot name;
- [ ] no unintended UV overlap;
- [ ] textures embedded, or copied to the declared runtime path;
- [ ] Draco/Meshopt compression **measured**, not assumed;
- [ ] one mesh serves every colour variant — recolour by material, never by
      duplicating geometry;
- [ ] an LOD exists if many copies render at once;
- [ ] the procedural or simplified fallback still works when the asset fails to
      load;
- [ ] frame time and bundle-size deltas recorded.

## Pitfalls

| Symptom | Cause |
|---|---|
| Every tool times out | Blender not running, or started with `-b` |
| Screenshot is valid but black | Unpatched addon reading the front buffer |
| Connection dies mid-session | Something called `read_factory_settings()` |
| `bpy.context` errors after a few lines | Code reloaded the file mid-execution |
| Poly Haven tools vanish | A `.blend` load reset the per-scene toggles |
| Export lands in a strange place | Relative path; Blender's CWD is the launch dir |
| Everything is bevelled | `limit_method` left at its default |
| Generated model is a melty blob | Image-to-3D used for a hero asset |

## References

| File | What |
|---|---|
| `references/headless.md` | xvfb, llvmpipe, run.sh, diagnosing the black screenshot |
| `references/modelling.md` | Modifier recipes, bmesh, materials, lighting rigs |
| `references/gltf-export.md` | glTF/GLB options, axes, rigs, LOD, the export gate |
| `references/bpy-api.md` | Essential bpy operations: modeling, materials, modifiers, rendering |
| `references/recipes.md` | Complete working scenes: low-poly terrain, glass sphere, HDRI lighting, turntable |
| `references/pitfalls.md` | Connection, namespace and API-version traps, with the checks that catch them |
| `scripts/setup.sh` | Checks the host, starts Blender, verifies the port |
