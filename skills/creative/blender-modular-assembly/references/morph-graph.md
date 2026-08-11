# MorphGraph and Hybrid Contract

FormGraph describes how one form is built. MorphGraph describes which complete
forms may transition, through which operator, and which semantic modules and
sockets must survive. Do not use MorphGraph to conceal missing rig or topology
compatibility.

Each form declares an id, CanonicalRig profile, sockets, and modules. Each
directed transition declares an id, source, target, operator, status, required
sockets, and a source-to-target module correspondence. Supported operator
families are shape-key topology maps, Geometry Nodes fields, cage deformation,
socket-level module recomposition, and evidence-seal collapse.

Only `approved` transitions are routable at runtime. `draft` means that the
route is designed but has not passed intermediate-frame QA. Validate every
required socket on both forms and preserve exactly one quantum gaze and one
evidence seal throughout the transition.

A hybrid descriptor contains two or more form weights that sum to one, one
CanonicalRig profile, a set of active common sockets, and an explicit policy
for every identity-bearing module. Use weighted fields only for compatible
volumes. Use preserve/substitute policies for a mane, cloak, wings, staff,
boundary plates, gaze, and evidence seal.

For runtime use, resolve a descriptor through `assembly.morphgraph`, then send
the resolved form weights and module policies to PresenceRouter. PresenceRouter
selects state, performance budget, LOD, and animation behavior; MorphGraph owns
only geometric continuity and module compatibility.
