---
name: blender-modular-assembly
description: Build complex Blender assets from validated modules.
version: 1.0.0
author: Marat Zimnurov and Digit
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [blender, mcp, 3d, modelling, decomposition, assembly, rigging, qa]
    category: creative
    related_skills: [blender-mcp, fts, fts-specify]
---

# Blender Modular Assembly Skill

Decompose a complex visual idea into a typed graph of small Blender modules,
build those modules incrementally, and compose them only after local QA passes.
Do not use this workflow for one-off primitive props where a single short Blender
operation is already sufficient.

## When to Use

- Build a hero character, mascot, creature, vehicle, building, machine, costume,
  or other asset with several interacting parts.
- Reproduce concept art while preserving editable parts, rigging, animation, or
  controlled morphing.
- Repair a monolithic or visually plausible model whose topology, hierarchy, or
  component identity is unusable.
- Require evidence that every part was built, inspected, and attached before the
  complete asset was exported.

## Prerequisites

- Install and start the `blender` MCP described by the `blender-mcp` skill.
- Require the MCP tools `get_scene_info`, `get_object_info`,
  `get_viewport_screenshot`, and `execute_blender_code`.
- On a remote renderer, run both Blender and its socket-side bridge on that host.
  Prefer `Digit -> ssh gpu -> blender-mcp -> 127.0.0.1:9876 -> Blender`; never
  expose the unauthenticated Blender socket publicly.
- Keep telemetry disabled. Treat `execute_blender_code` as arbitrary code
  execution with the Blender host user's filesystem permissions.

## How to Run

Create the graph before creating detailed geometry:

```text
terminal: python <skill-dir>/scripts/form_graph.py new \
  --id archivist-a --output /absolute/work/form-graph.json
```

Edit the graph with `patch`, validate it, then ask for the next dependency-safe
modules:

```text
terminal: python <skill-dir>/scripts/form_graph.py validate /absolute/work/form-graph.json
terminal: python <skill-dir>/scripts/form_graph.py ready /absolute/work/form-graph.json
```

Inside `execute_blender_code`, import the assembly runtime shipped by
`digitable-lol/blender-mcp`:

```python
from assembly.blender_runtime import begin_module, complete_module

ctx = begin_module("/absolute/work/form-graph.json", "hood.rim")
# Create only hood.rim objects here.
complete_module(ctx, [panel_left, panel_top, panel_right])
```

Read `references/form-graph.md` before authoring or changing the graph. Read
`references/qa-contract.md` before the first render checkpoint.

## Quick Reference

| Operation | Command or MCP action |
| --- | --- |
| Create graph | `scripts/form_graph.py new` |
| Validate dependencies and invariants | `scripts/form_graph.py validate` |
| List buildable leaf modules | `scripts/form_graph.py ready` |
| Record an external state transition | `scripts/form_graph.py set-status` |
| Record a render gate | `scripts/form_graph.py checkpoint` |
| Inspect progress | `scripts/form_graph.py summary` |
| Begin/complete inside Blender | `assembly.blender_runtime` through `execute_blender_code` |
| Inspect the actual scene | `get_scene_info`, `get_object_info` |
| Judge appearance | `get_viewport_screenshot` plus saved renders |

## Procedure

### 1. Freeze the intent contract

Save the source prompt and every approved reference. State the target use,
coordinate system, real scale, required views, polygon budgets, deformation
needs, material families, and identity invariants. Keep inferred details marked
as inferred. A reference image is an appearance constraint, not topology.

### 2. Decompose recursively

Create semantic assemblies first, then split them until every leaf has:

- one generator or modelling technique;
- one attachment interface and owning bone or parent;
- one material responsibility;
- one local acceptance test;
- a bounded rebuild cost.

Split a candidate again when its description contains independent shapes joined
by “and”, requires unrelated modifiers, attaches to more than one moving part,
or cannot be rejected without discarding already-correct geometry. Preserve
repeated parts as parameterized instances, but keep their object identities.

### 3. Build the support structure first

For characters, construct the anatomical armature, massing volumes, attachment
sockets, and naming hierarchy before surface detail. For hard-surface assets,
construct reference axes, frames, hinges, and mounting points first. Render the
blockout in front, side, three-quarter, and back views. Do not detail a silhouette
that has not passed.

### 4. Build one ready module at a time

Call `ready`, choose one leaf, and call `begin_module`. Make each Blender call
idempotent and scoped to that module's collection. Use primitives, curves,
`bmesh`, Geometry Nodes, and non-destructive modifiers before destructive mesh
editing. Apply transforms before armature deformation or export.

Never clear or rebuild unrelated passing modules. If a module fails, mark it
`rejected`, retain the evidence, and rebuild only that module.

### 5. Validate locally before composition

Check non-empty geometry, finite transforms, intended dimensions, origin,
materials, attachment metadata, manifold requirements, modifier order, and
bone ownership. Render a close view when the module carries identity, such as a
face, hand, wheel, hinge, or emblem.

Mark the module `built`, then `validated`. Only validated modules can satisfy a
render checkpoint.

### 6. Compose by interfaces

Attach modules through declared sockets, bones, pivots, or surfaces. Do not
repair a bad fit by hiding intersections inside another mesh. Check contact,
clearance, symmetry policy, deformation range, and silhouette after each major
assembly stage.

### 7. Run visual checkpoints

At minimum produce assembled front, side, three-quarter, back, detail,
wireframe/rig, and exploded views. Compare at consistent camera, lighting,
scale, and pose. Record concrete module-addressed defects; “looks wrong” is not
an actionable verdict.

Do not mark an approval checkpoint as passed without inspecting the returned
image. A successful screenshot response can still contain a black or stale
frame.

### 8. Rig, animate, optimize, and export

Add animation only after the neutral assembly passes. Test extreme poses and
morph transitions for detachment and interpenetration. Generate LODs from the
approved master, preserve names and material slots, export an animated GLB, and
retain the editable `.blend`, graph, reports, and render evidence.

## Pitfalls

- **Monolithic prompt-to-mesh generation:** useful for rough props, not hero
  identity, rigging, or controlled topology.
- **Detail before silhouette:** produces expensive rework and visually dense
  but structurally wrong assets.
- **One object per concept:** “cloak”, “head”, or “staff” are assemblies, not
  useful leaf modules.
- **Object/bone scale inheritance:** apply authored transforms before rigid
  skinning and verify evaluated dimensions.
- **Unbounded MCP scripts:** divide code by module and checkpoint; preserve the
  server connection and return compact JSON evidence.
- **Self-approval:** programmatic checks can approve structure, never taste or
  identity. Keep a user or independent visual gate for hero assets.

## Verification

- [ ] `form_graph.py validate` reports `ok: true`.
- [ ] The dependency graph is acyclic and every attachment target exists.
- [ ] No leaf combines unrelated generators, attachments, or QA duties.
- [ ] Every built object carries `assembly.module_id` metadata.
- [ ] Every required module is `validated`; rejected modules retain notes.
- [ ] Required render checkpoints include assembled, detail, rig, and exploded
      evidence and have been visually inspected.
- [ ] The armature and extreme-pose tests preserve attachments and volume.
- [ ] `.blend`, animated `.glb`, FormGraph, statistics, and QA renders exist.
- [ ] The approved asset, not an earlier draft, is the one selected for runtime.
