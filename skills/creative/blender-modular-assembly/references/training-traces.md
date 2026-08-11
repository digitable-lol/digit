# Training Trace Contract

Capture the full compiler chain:

`idea -> FTS/Flang spec -> decomposition -> FormGraph -> part library -> Blender
assembly -> sculpt -> retopology -> bake -> material -> rig -> animation -> LOD
-> turntable -> visual QA -> correction -> GLB`

Create one durable trace per graph and append an event after every meaningful
choice or validation. An event records sequence, stage, action, optional module,
parameters, metrics, artifact paths, verdict, and defect codes. Store prompt
text only when policy permits; always store its SHA-256 digest. Never overwrite
a rejection with an acceptance—append a correction event.

The Blender MCP runtime exposes `new_trace`, `append_event`, `finalize_trace`,
and `save_trace` in `assembly.training_trace`. Finalization requires a nonempty
event list and an accepted or rejected verdict.

Use accepted source FormGraphs and deterministic invalid mutations to bootstrap
the structural ranker:

```text
terminal: python scripts/formgraph_ranker.py dataset /graphs \
  --output /dataset/formgraph-pairs.jsonl --negatives-per-positive 54
terminal: python scripts/formgraph_ranker.py train \
  --dataset /dataset/formgraph-pairs.jsonl --output /model/ranker.json
terminal: python scripts/formgraph_ranker.py sweep \
  --dataset /dataset/formgraph-pairs.jsonl --output /model/ranker.json --workers 36
terminal: python scripts/formgraph_ranker.py score \
  --model /model/ranker.json --graph /candidate/form-graph.json
```

This bootstrap model ranks structural decomposition only. It is not a visual
quality model. Add render embeddings, defect labels, pairwise user decisions,
and view-specific metrics only after those judgments exist. Split evaluation by
graph family so mutations of one asset never appear in both train and test.

For publication, package the exact JSONL dataset, prompt-hash policy, model,
feature definitions, held-out metrics, model card, license, and source commit.
Do not upload private prompts, references, tokens, absolute secret paths, or
unapproved renders.
