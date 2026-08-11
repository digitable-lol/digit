# Authoring v2 Contract

Use this contract for a hero Digitmorf asset. A lower target is allowed only
when the intent contract explicitly calls for a lightweight prototype.

## Sculpt and retopology

- Source sculpt: 1,000,000–8,000,000 triangles; target about 3,500,000.
- Named layers: primary form, secondary anatomy, surface breakup, identity detail.
- LOD0: 140,000–300,000 triangles with authored deformation loops.
- LOD1: 60,000–120,000 triangles.
- LOD2: 12,000–35,000 triangles.
- Require manifold LOD0 and loops at shoulder, elbow, wrist, hip, knee, ankle,
  jaw, ear root, tail root, and mantle root.
- Never present an automatically decimated mesh as final LOD0 retopology.

## Bake and material

Bake at 4096 square and 16-bit where the format supports it. Use an explicit
cage and MikkTSpace tangent normals. Produce normal, ambient occlusion,
curvature, thickness, and position maps. Record ray distance, cage artifact,
color space, checksum, and render evidence in the trace.

The canonical PBR channels are base color, roughness, metallic, normal, ambient
occlusion, emissive, transmission, and thickness. Digitmorf material families
are smoky glass tissue, silver boundary, cyan current, amber evidence, and the
quantum gaze. Cloak, gaze, tail, and tool must remain separately addressable.

## CanonicalRig v2

Expand the semantic template to 60–100 bones; the reference Digitmorf profile
uses 87. Required semantics include root/motion/center, pelvis, six spine,
three neck, head/jaw/face void, gaze particles, evidence seal, six limb slots,
four mantle chains, an eight-segment tail, and a five-segment tool chain.

Preserve these sockets across forms and LODs: head, face void, quantum gaze,
evidence seal, four mantle roots, tail, left/right wing, tool grip, staff, and
D-current start/end.

Required systems are retargeting, limb IK, gaze look-at, mantle/tail secondary
motion, and a state machine with locomotion, tool-use, explain, success,
failure, guard, wait, morph-in, and morph-out states. Ship at least 12 named
clips and test extreme poses against volume loss, penetrations, and detachments.

## Acceptance evidence

Retain source sculpt, retopology mesh, cage, bake maps, texture sources,
weighted rig, actions, three LODs, animated GLB, clean-import report,
turntable, rig/wireframe/exploded renders, FormGraph, MorphGraph, and training
trace. The proof is not production-ready until the visual approval gate passes.
