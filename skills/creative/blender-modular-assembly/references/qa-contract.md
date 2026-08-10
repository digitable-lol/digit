# Modular 3D QA contract

## Structural gate

- Graph validation passes and has no cycles or missing references.
- Every Blender object generated for a module has `assembly.module_id`.
- Object names are stable and unique.
- Transforms are finite; scale is applied before armature deformation/export.
- Module collections contain only their declared objects.
- Attachment targets, bones, sockets, pivots, and constraints exist.
- Geometry and evaluated dimensions are non-empty and within the declared scale.

## Geometry gate

- Check non-manifold geometry only where a watertight surface is required.
- Check normals, zero-area faces, loose geometry, modifier errors, and accidental
  duplicate surfaces.
- Check intersections at attachments and across the full intended pose range.
- Preserve separate topology where morphing, material replacement, damage,
  visibility, or reuse requires it.
- Record vertices, triangles, material slots, bones, modifiers, and object counts.

## Visual gate

Render all views with a fixed camera family and neutral studio lighting:

1. front;
2. side;
3. three-quarter;
4. back;
5. identity/detail crop;
6. rig or wireframe;
7. exploded assembly.

For every failure, name the module and visible defect. Prefer
`hood.rim: aperture too rectangular` over `face looks robotic`. A structural
pass never overrides an identity failure.

## Animation gate

- Neutral pose matches the approved assembly.
- Extreme limb, head, tail, cloth, and prop poses preserve attachments.
- Loops have stable root motion and no unintended scale changes.
- Morph transitions preserve volume or intentionally document fragmentation.
- Runtime clip names and durations are stable.

## Export gate

- Save the editable `.blend` before export.
- Export one animated GLB with stable names and embedded or declared textures.
- Preserve material slots needed for runtime recoloring.
- Verify the GLB in a renderer independent of the authoring scene.
- Measure file size, load time, triangles, draw calls, and frame time.
- Keep the FormGraph, checkpoint report, statistics, and renders beside the
  approved asset.
