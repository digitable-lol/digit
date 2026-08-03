# Modelling through `execute_blender_code`

Everything here runs as the `code` argument of `execute_blender_code`.

## Clearing a scene

```python
import bpy

def clear():
    # Not read_homefile(): reloading the file mid-script invalidates the context
    # this code is still running in, and every later bpy.context lookup fails.
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.lights):
        for d in list(coll):
            if d.users == 0:
                coll.remove(d)
```

Iterate over `list(...)` — removing while iterating the live collection skips
items.

## Modifiers over manual geometry

Modifiers are non-destructive, stay adjustable, and keep topology clean.

```python
sol = obj.modifiers.new("Solidify", 'SOLIDIFY')
sol.thickness = 0.004
sol.offset = -1        # -1 inward, 0 centred, +1 outward
                       # inward keeps the outer silhouette exactly where it was

bev = obj.modifiers.new("Bevel", 'BEVEL')
bev.width = 0.0012
bev.segments = 3
bev.limit_method = 'ANGLE'              # default bevels EVERY edge
bev.angle_limit = math.radians(35)

sub = obj.modifiers.new("Subsurf", 'SUBSURF')
sub.levels = 2                          # viewport
sub.render_levels = 3                   # render
```

Order matters: Solidify → Bevel → Subsurf. Bevelling after subdivision wastes
geometry on edges that are already round.

## Opening a face with bmesh

Deleting the top face so Solidify walls a shape instead of filling it:

```python
import bmesh

me = obj.data
bm = bmesh.new()
bm.from_mesh(me)
bm.faces.ensure_lookup_table()          # required before indexing
top = max(bm.faces, key=lambda f: f.calc_center_median().z)
bmesh.ops.delete(bm, geom=[top], context='FACES')
bm.to_mesh(me)
bm.free()                               # bmesh is not garbage-collected
```

Always `ensure_lookup_table()` after building a bmesh, and always `free()`.

## Curves as geometry

A Bezier curve with bevel depth is the cheapest way to get a clean tube — a mug
handle, a cable, a pipe.

```python
cu = bpy.data.curves.new("HandleCurve", 'CURVE')
cu.dimensions = '3D'
cu.bevel_depth = 0.0055        # tube radius
cu.bevel_resolution = 6        # roundness of the cross-section
cu.resolution_u = 16           # smoothness along the path
sp = cu.splines.new('BEZIER')
sp.bezier_points.add(3)        # add() is on top of the 1 that already exists
```

`bezier_points.add(n)` adds `n` **more** points; a new spline starts with one.

## Smooth shading

```python
for p in obj.data.polygons:
    p.use_smooth = True
# Blender 4.1+ replaced auto-smooth with a modifier:
bpy.ops.object.shade_auto_smooth(angle=math.radians(30))
```

`mesh.use_auto_smooth` was removed in 4.1 — code written for older Blender fails
here.

## Materials

```python
mat = bpy.data.materials.new("Ceramic")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.85, 0.85, 0.82, 1.0)  # RGBA, linear
bsdf.inputs["Roughness"].default_value = 0.35
bsdf.inputs["Metallic"].default_value = 0.0
obj.data.materials.append(mat)
```

Colours are **linear float RGBA**, not sRGB hex. Convert:
`linear = ((srgb + 0.055) / 1.055) ** 2.4` for values above 0.04045.

Socket names changed across versions — `"Emission"` became `"Emission Color"` in
4.x. Index by name and let a `KeyError` tell you, rather than guessing an index.

Name material slots deliberately if a runtime will recolour them: one mesh with
named slots beats one mesh per colour variant.

## Lighting to judge a model

```python
sun = bpy.data.lights.new("Sun", 'SUN')
sun.energy = 3.0
bpy.context.collection.objects.link(bpy.data.objects.new("Sun", sun))

bpy.ops.object.camera_add(location=(6, -6, 4))
bpy.context.scene.camera = bpy.context.active_object
```

Point the camera with a Track To constraint rather than computing Euler angles:

```python
c = cam.constraints.new('TRACK_TO')
c.target = obj
c.track_axis, c.up_axis = 'TRACK_NEGATIVE_Z', 'UP_Y'
```

An HDRI from Poly Haven (`download_polyhaven_asset(asset_type="hdris")`) gives
better material read than any point light, and needs no API key.

## Rendering

```python
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 16              # 16 is plenty for a shape check
scene.render.resolution_x = 480
scene.render.resolution_y = 360
scene.render.filepath = "/abs/path/out.png"
bpy.ops.render.render(write_still=True)
```

Absolute paths only — Blender's CWD is wherever it was launched.
