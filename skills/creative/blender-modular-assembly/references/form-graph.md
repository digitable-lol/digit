# FormGraph reference

## Contents

1. Purpose
2. Top-level shape
3. Module contract
4. Attachment contract
5. Checkpoints
6. Decomposition rules
7. Example

## Purpose

FormGraph is the durable plan between an idea and a Blender scene. It records
semantic decomposition, dependency order, attachment intent, acceptance rules,
build state, and evidence. It does not encode raw vertices and does not replace
the `.blend` file.

Use schema version `1` and keep identifiers stable across rebuilds. A renderer
may create many object instances for one leaf module, but every object must keep
the leaf's `assembly.module_id` tag.

## Top-level shape

```json
{
  "schemaVersion": 1,
  "id": "archivist-a",
  "units": "m",
  "coordinateSystem": "Z_UP",
  "rootModule": "character",
  "modules": [],
  "checkpoints": [],
  "metadata": {}
}
```

Required invariants:

- `schemaVersion` equals `1`;
- `id` and every nested id use lowercase letters, digits, `.`, `_`, or `-`;
- `units` is `m`, `cm`, or `mm`;
- `coordinateSystem` is `Z_UP` or `Y_UP`;
- `rootModule` names exactly one module whose `parent` is `null`;
- module ids and checkpoint ids are unique;
- parent, dependency, attachment, and checkpoint references exist;
- the combined parent/dependency graph is acyclic.

## Module contract

```json
{
  "id": "hood.rim",
  "kind": "mesh",
  "parent": "hood",
  "stage": 3,
  "generator": "faceted-panels",
  "dependsOn": ["rig.head", "hood.blockout"],
  "attachments": [
    {"to": "rig.head", "interface": "bone:head", "mode": "rigid-skin"}
  ],
  "parameters": {"segments": 8},
  "acceptance": {
    "required": ["nonempty", "finite-transform", "applied-scale"],
    "visual": ["face aperture remains animal-like", "no neck gap"]
  },
  "status": "planned",
  "notes": [],
  "artifacts": []
}
```

Allowed statuses are `planned`, `building`, `built`, `validated`, and
`rejected`. Use this normal path:

```text
planned -> building -> built -> validated
                    \-> rejected -> building
validated -----------------------> rejected
```

Set `rejected` when the source module is visually or structurally wrong. Do not
delete its notes or evidence. A module is ready only when it is `planned` or
`rejected` and every entry in `dependsOn` is `built` or `validated`.

`kind` is descriptive rather than exhaustive. Prefer `assembly`, `rig`, `mesh`,
`curve`, `material`, `light`, `camera`, or `export`. Keep `generator` specific
enough to select a reusable Blender construction routine.

## Attachment contract

Each attachment has:

- `to`: an existing module id;
- `interface`: a stable semantic socket such as `bone:head`, `pivot:hinge-a`,
  `surface:shoulder-left`, or `socket:staff-grip`;
- `mode`: `parent`, `rigid-skin`, `deform`, `constraint`, `socket`, or `surface`.

An attachment declares ownership and motion, not merely spatial proximity. If a
leaf follows two independently moving targets, split it or introduce an explicit
deformation module.

## Checkpoints

```json
{
  "id": "identity-turnaround",
  "after": ["hood", "cloak", "hands", "tail", "staff"],
  "requiredViews": ["front", "side", "three-quarter", "back", "detail"],
  "validators": ["manifest", "geometry", "visual"],
  "status": "pending",
  "renders": {},
  "notes": []
}
```

Allowed checkpoint statuses are `pending`, `pass`, and `fail`. A checkpoint can
pass only when every module in `after` is `validated`, every required view has a
render path, and the images were actually inspected.

## Decomposition rules

Stop decomposing when a leaf has one generator, one moving owner, one material
responsibility, and one local verdict. Continue decomposing when:

- the description joins independent shapes with “and”;
- different regions need unrelated modifiers or topology;
- one reject decision would discard already-correct work;
- attachment ownership changes inside the object;
- repeated parts need different motion, damage, visibility, or morph weights;
- the part cannot be shown clearly in an exploded view.

Keep repeated leaves as separate objects with shared data when possible. For a
feathered cloak, `cloak.layer.07` may be a semantic leaf that produces many
named feather objects, while `cloak` remains an assembly. Split individual
feathers into graph nodes only when they need unique behavior or review.

## Example

The template at `../templates/form-graph.json` demonstrates an anthropomorphic
mascot with a rig, blockout, hood, cloak, hands, tail, prop, and three review
gates. Copy it and replace its identity-specific constraints rather than
starting from an empty graph.
