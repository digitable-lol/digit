# Exporting for a runtime

Verified against Blender 4.5.11 LTS.

## GLB export

```python
bpy.ops.export_scene.gltf(
    filepath="/abs/path/asset.glb",     # absolute — CWD is the launch dir
    export_format='GLB',                # single binary file, textures embedded
    export_apply=True,                  # apply modifiers
    export_yup=True,                    # glTF is Y-up, Blender is Z-up
)
```

Draco compression, if you measure a win:

```python
    export_draco_mesh_compression_enable=True,
```

`export_apply=True` is almost always what you want: without it, modifiers are
dropped and the runtime receives the pre-Solidify, pre-Bevel cage.

## Axes and origins

Blender is **Z-up**; glTF is **Y-up**. `export_yup=True` converts on the way
out, so the runtime needs no re-orientation. Exporting with it off and rotating
in the runtime instead means every consumer repeats the fix.

Origin placement is a design decision, not a default:

- a prop that sits on a surface: origin at its **base**, so placing it is just a
  position;
- a whole scene or board: origin at its **centre at ground level**;
- anything spun by the runtime: origin on the **axis of rotation**.

Set it deliberately:

```python
bpy.context.scene.cursor.location = (0, 0, 0)
bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
```

Apply transforms before export, or scale arrives as a node transform that some
importers quietly ignore:

```python
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
```

Decide a unit and write it down — e.g. *1 Blender unit = 1 tile = 1 runtime
unit*. Undocumented scale is the most common reason an asset lands 100× too big.

## Facing direction

Pick a forward axis and state it. If authored assets face local `+Z` while an
older procedural path faced `-Z`, the runtime must convert one of them — and
someone has to know which. Undocumented, this shows up as characters facing the
camera instead of each other.

## Materials for runtime recolouring

One mesh, named material slots, recoloured at runtime — never one mesh per
colour variant. Name slots after their **role**, not their colour:
`Shell`, `ShellShadow`, `Visor`, `Glow`, `Metal`, `Trim`. `Shell` can become any
colour; `Cyan` cannot become coral without a lie in the name.

Keep the palette in one place and have authored materials reuse those exact
values, so a colour change is one edit rather than a hunt.

Stick to standard glTF extensions (`KHR_materials_emissive_strength`,
`KHR_materials_clearcoat`). Anything exotic is a per-engine gamble.

## Rigs and clips

- Keep the armature small. A handful of bones with rigid per-part weights beats
  an elaborate rig nobody can debug.
- Name clips for what they express — `idle`, `hover`, `win` — because runtimes
  select them **by name**. Renaming a clip is a breaking change.
- Verify the export actually contains them: count joints and clips in the GLB
  rather than trusting the exporter's summary.

## LOD

Needed when many copies render at once — a board with 50 pieces multiplies every
triangle by 50. Ship a reduced mesh alongside the full one and let the quality
preset choose. Keep material slot names identical across LODs, or recolouring
breaks at exactly the moment the LOD swaps.

## Inspecting the result

A GLB is a binary container with a JSON chunk at the front. Read it without any
3D library:

```python
import json, struct
with open("asset.glb", "rb") as f:
    magic, version, _ = struct.unpack("<III", f.read(12))
    assert magic == 0x46546C67, "not a GLB"
    length, ctype = struct.unpack("<II", f.read(8))
    gltf = json.loads(f.read(length))
acc = gltf.get("accessors", [])
tris = sum(acc[p["indices"]]["count"] // 3
           for m in gltf.get("meshes", []) for p in m["primitives"]
           if p.get("indices") is not None and p.get("mode", 4) == 4)

print("meshes:   ", [m["name"] for m in gltf.get("meshes", [])])
print("materials:", [m["name"] for m in gltf.get("materials", [])])
print("clips:    ", [a.get("name") for a in gltf.get("animations", [])])
print("joints:   ", sum(len(s["joints"]) for s in gltf.get("skins", [])))
print("triangles:", tris)
```

This is the honest check: it reads what the runtime will read, not what Blender
believed it wrote.

## The gate

Before an authored asset replaces a working procedural one:

- [ ] transforms applied, scale consistent, origin deliberate;
- [ ] mesh, material and clip names stable and documented;
- [ ] no unintended UV overlap;
- [ ] textures embedded or copied to the declared runtime path;
- [ ] compression measured, not assumed;
- [ ] loads with no network access, on every target platform;
- [ ] the fallback path still works when the asset fails to load;
- [ ] a screenshot from the real runtime proves it renders;
- [ ] frame time and bundle-size deltas recorded.

Keep the fallback. An authored asset that fails to load should downgrade the
picture, not break the product.
