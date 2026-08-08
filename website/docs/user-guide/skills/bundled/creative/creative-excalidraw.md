---
title: "Excalidraw — Hand-drawn Excalidraw JSON diagrams (arch, flow, seq)"
sidebar_label: "Excalidraw"
description: "Hand-drawn Excalidraw JSON diagrams (arch, flow, seq)"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Excalidraw

Hand-drawn Excalidraw JSON diagrams (arch, flow, seq).

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/creative/excalidraw` |
| Version | `1.1.0` |
| Author | Digit |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Excalidraw`, `Diagrams`, `Flowcharts`, `Architecture`, `Visualization`, `JSON` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Excalidraw Diagram Skill

Create diagrams by writing standard Excalidraw element JSON and saving as `.excalidraw` files. These files can be drag-and-dropped onto [excalidraw.com](https://excalidraw.com) for viewing and editing. No accounts, no API keys, no rendering libraries -- just JSON.

## When to use

Generate `.excalidraw` files for architecture diagrams, flowcharts, sequence diagrams, concept maps, and more. Files can be opened at excalidraw.com, opened locally on a canvas the owner shares with you (`scripts/canvas.py`), shown in the terminal while you explain them (`scripts/render.py`), or uploaded for shareable links.

## Workflow

1. **Load this skill** (you already did)
2. **Write the elements JSON** -- an array of Excalidraw element objects
3. **Save the file** using `write_file` to create a `.excalidraw` file
4. **Optionally upload** for a shareable link using `scripts/upload.py` via `terminal`

For a diagram the owner is also editing, this one-way workflow is the wrong one:
read [Revising a Diagram](#revising-a-diagram-someone-else-is-editing) and
[Letting the Owner Open the Same File](#letting-the-owner-open-the-same-file).

### Saving a Diagram

Wrap your elements array in the standard `.excalidraw` envelope and save with `write_file`:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "digit",
  "elements": [ ...your elements array here... ],
  "appState": {
    "viewBackgroundColor": "#ffffff"
  }
}
```

Save to any path, e.g. `~/diagrams/my_diagram.excalidraw`.

### Revising a Diagram Someone Else Is Editing

`write_file` is for a diagram you are creating. For one the owner has drawn in —
the "owner sketches rough shapes, agent tidies them" loop — use
`scripts/revise.py`, and do **not** rewrite the file wholesale.

The reason is that most of what is in the file is not yours. Each element carries
`seed`, `versionNonce`, `groupIds`, `boundElements`, `frameId`, `customData` and
whatever the current app version added; arrows attach to shapes through
`startBinding`/`endBinding`, text sits inside a shape via `containerId`; and
Excalidraw reconciles edits using `version`/`versionNonce`/`updated`. Re-emitting
"the same" element from the subset you understand drops the rest, breaks the
arrows, and can lose to a stale copy in the owner's open tab. All three failures
are silent.

```bash
# See what is there, without pulling the whole JSON into the conversation
python skills/creative/excalidraw/scripts/revise.py inspect ~/diagrams/d.excalidraw

# Change things by element id; everything else is left exactly as it was
python skills/creative/excalidraw/scripts/revise.py apply ~/diagrams/d.excalidraw --edits - <<'EOF'
[{"id": "box1", "set": {"backgroundColor": "#a5d8ff", "x": 140}},
 {"id": "label1", "set": {"text": "Payment gateway"}},
 {"add": {"type": "rectangle", "id": "box9", "x": 400, "y": 100,
          "width": 180, "height": 80}}]
EOF
```

`inspect` prints, per element, how many fields it does **not** interpret. Treat
that count as the measure of what a from-scratch rewrite would throw away.

Deleting an element that something else refers to is refused, because Excalidraw
drops the orphaned arrow or label without reporting it. Either fix the referring
elements first, or pass `--force` to delete and have the references cleaned.
`--output` writes elsewhere and leaves the original alone.

### Letting the Owner Open the Same File

`revise.py` is only half of the loop. The other half is the owner opening the
file — and "drag it onto excalidraw.com" is not that half, because the site
opens a *copy*: its save goes to the downloads folder, not back to the path you
are editing. Two people working on a file and its copy are exchanging versions,
not editing together.

`scripts/canvas.py` serves a canvas on 127.0.0.1 that reads and writes **that
exact path**. Excalidraw itself is vendored under `canvas/vendor` (MIT), so no
network is involved.

```bash
python skills/creative/excalidraw/scripts/canvas.py ~/diagrams/d.excalidraw
# prints: Холст: http://127.0.0.1:PORT/?t=TOKEN   (and opens the browser)
python skills/creative/excalidraw/scripts/canvas.py ~/diagrams/d.excalidraw --no-browser
```

Give the owner the printed URL. The loop then looks like this:

- The owner draws; the file is written a moment later, and your next `inspect`
  sees it.
- You run `revise.py`; the open tab picks the change up within ~2 s without a
  reload — the owner watches the tidy-up land.
- If both sides changed the file, the save is refused and the owner is asked
  which version to keep. Nothing is overwritten silently in either direction.
- Merely opening a file does not rewrite it.

Two things to know before promising anything: the token in the URL is required
(the API refuses without it), and one server serves one file — start another
for another diagram.

### Showing the Diagram Where the Conversation Is

`revise.py` and `canvas.py` are both about *editing*. Neither shows you what you
drew, and a diagram you cannot see is a diagram you cannot check. `scripts/render.py`
turns the same file into a picture — no network, no browser.

```bash
# Картинка в терминале (нужен chafa) плюс разбор словами
python skills/creative/excalidraw/scripts/render.py show ~/diagrams/d.excalidraw
python skills/creative/excalidraw/scripts/render.py show ~/diagrams/d.excalidraw --size 60x30

# Только разбор словами — дёшево и читается в трубе, в логе и моделью
python skills/creative/excalidraw/scripts/render.py legend ~/diagrams/d.excalidraw --json

# Файлы: SVG всегда, растр — если есть rsvg-convert
python skills/creative/excalidraw/scripts/render.py svg ~/diagrams/d.excalidraw --out d.svg
python skills/creative/excalidraw/scripts/render.py png ~/diagrams/d.excalidraw --out d.png --scale 2
```

**Read the legend, not the pixels.** A terminal is ~80 columns wide, so a
`fontSize: 16` label is physically a couple of pixels tall — it renders as a grey
smudge. `show` therefore always prints, beside the picture, what the diagram
*says* and what it *connects*: labels in reading order, and arrows as
`«Клиент» → «Шлюз»` resolved through their bindings. That part survives a pipe, a
log, and your own context window; the raster does not.

Three things the legend reports that the picture cannot:

- **Unbound arrows.** An arrow drawn next to two shapes but bound to neither
  looks like a connection and is not one — move the shape and it stays behind.
- **Element types with nothing to draw them with** (`embeddable`, and whatever a
  future app version adds) get a dashed placeholder carrying the type name, plus
  a line in the report. A picture missing something looks exactly like a complete
  picture, which is why nothing is ever dropped in silence.
- **Deleted elements** (`isDeleted`) are hidden, as the app hides them, but counted.

What the picture is *not*: hand-drawn. Excalidraw draws through roughjs, jittering
every line from the element's `seed`; here lines are straight, and curved
(`roundness`) polylines are drawn as straight segments. On an 8×16 terminal cell
that jitter is indistinguishable from raster noise, so it is not reproduced. The
picture is faithful in *what*, *where*, *in front of what*, and *in what colour* —
and not in handwriting.

Bound labels are placed from their **container**, not from their own `x`/`y`,
because the app recomputes those on load (as noted above) and the numbers in the
file can be arbitrarily stale.

### Uploading for a Shareable Link

Run the upload script (located in this skill's `scripts/` directory) via terminal:

```bash
python skills/creative/excalidraw/scripts/upload.py ~/diagrams/my_diagram.excalidraw
```

This uploads to excalidraw.com (no account needed) and prints a shareable URL. Requires the `cryptography` pip package (`pip install cryptography`).

---

## Element Format Reference

### Required Fields (all elements)
`type`, `id` (unique string), `x`, `y`, `width`, `height`

### Defaults (skip these -- they're applied automatically)
- `strokeColor`: `"#1e1e1e"`
- `backgroundColor`: `"transparent"`
- `fillStyle`: `"solid"`
- `strokeWidth`: `2`
- `roughness`: `1` (hand-drawn look)
- `opacity`: `100`

Canvas background is white.

### Element Types

**Rectangle**:
```json
{ "type": "rectangle", "id": "r1", "x": 100, "y": 100, "width": 200, "height": 100 }
```
- `roundness: { "type": 3 }` for rounded corners
- `backgroundColor: "#a5d8ff"`, `fillStyle: "solid"` for filled

**Ellipse**:
```json
{ "type": "ellipse", "id": "e1", "x": 100, "y": 100, "width": 150, "height": 150 }
```

**Diamond**:
```json
{ "type": "diamond", "id": "d1", "x": 100, "y": 100, "width": 150, "height": 150 }
```

**Labeled shape (container binding)** -- create a text element bound to the shape:

> **WARNING:** Do NOT use `"label": { "text": "..." }` on shapes. This is NOT a valid
> Excalidraw property and will be silently ignored, producing blank shapes. You MUST
> use the container binding approach below.

The shape needs `boundElements` listing the text, and the text needs `containerId` pointing back:
```json
{ "type": "rectangle", "id": "r1", "x": 100, "y": 100, "width": 200, "height": 80,
  "roundness": { "type": 3 }, "backgroundColor": "#a5d8ff", "fillStyle": "solid",
  "boundElements": [{ "id": "t_r1", "type": "text" }] },
{ "type": "text", "id": "t_r1", "x": 105, "y": 110, "width": 190, "height": 25,
  "text": "Hello", "fontSize": 20, "fontFamily": 1, "strokeColor": "#1e1e1e",
  "textAlign": "center", "verticalAlign": "middle",
  "containerId": "r1", "originalText": "Hello", "autoResize": true }
```
- Works on rectangle, ellipse, diamond
- Text is auto-centered by Excalidraw when `containerId` is set
- The text `x`/`y`/`width`/`height` are approximate -- Excalidraw recalculates them on load
- `originalText` should match `text`
- Always include `fontFamily: 1` (Virgil/hand-drawn font)

**Labeled arrow** -- same container binding approach:
```json
{ "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 200, "height": 0,
  "points": [[0,0],[200,0]], "endArrowhead": "arrow",
  "boundElements": [{ "id": "t_a1", "type": "text" }] },
{ "type": "text", "id": "t_a1", "x": 370, "y": 130, "width": 60, "height": 20,
  "text": "connects", "fontSize": 16, "fontFamily": 1, "strokeColor": "#1e1e1e",
  "textAlign": "center", "verticalAlign": "middle",
  "containerId": "a1", "originalText": "connects", "autoResize": true }
```

**Standalone text** (titles and annotations only -- no container):
```json
{ "type": "text", "id": "t1", "x": 150, "y": 138, "text": "Hello", "fontSize": 20,
  "fontFamily": 1, "strokeColor": "#1e1e1e", "originalText": "Hello", "autoResize": true }
```
- `x` is the LEFT edge. To center at position `cx`: `x = cx - (text.length * fontSize * 0.5) / 2`
- Do NOT rely on `textAlign` or `width` for positioning

**Arrow**:
```json
{ "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 200, "height": 0,
  "points": [[0,0],[200,0]], "endArrowhead": "arrow" }
```
- `points`: `[dx, dy]` offsets from element `x`, `y`
- `endArrowhead`: `null` | `"arrow"` | `"bar"` | `"dot"` | `"triangle"`
- `strokeStyle`: `"solid"` (default) | `"dashed"` | `"dotted"`

### Arrow Bindings (connect arrows to shapes)

```json
{
  "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 150, "height": 0,
  "points": [[0,0],[150,0]], "endArrowhead": "arrow",
  "startBinding": { "elementId": "r1", "fixedPoint": [1, 0.5] },
  "endBinding": { "elementId": "r2", "fixedPoint": [0, 0.5] }
}
```

`fixedPoint` coordinates: `top=[0.5,0]`, `bottom=[0.5,1]`, `left=[0,0.5]`, `right=[1,0.5]`

### Drawing Order (z-order)
- Array order = z-order (first = back, last = front)
- Emit progressively: background zones → shape → its bound text → its arrows → next shape
- BAD: all rectangles, then all texts, then all arrows
- GOOD: bg_zone → shape1 → text_for_shape1 → arrow1 → arrow_label_text → shape2 → text_for_shape2 → ...
- Always place the bound text element immediately after its container shape

### Sizing Guidelines

**Font sizes:**
- Minimum `fontSize`: **16** for body text, labels, descriptions
- Minimum `fontSize`: **20** for titles and headings
- Minimum `fontSize`: **14** for secondary annotations only (sparingly)
- NEVER use `fontSize` below 14

**Element sizes:**
- Minimum shape size: 120x60 for labeled rectangles/ellipses
- Leave 20-30px gaps between elements minimum
- Prefer fewer, larger elements over many tiny ones

### Color Palette

See `references/colors.md` for full color tables. Quick reference:

| Use | Fill Color | Hex |
|-----|-----------|-----|
| Primary / Input | Light Blue | `#a5d8ff` |
| Success / Output | Light Green | `#b2f2bb` |
| Warning / External | Light Orange | `#ffd8a8` |
| Processing / Special | Light Purple | `#d0bfff` |
| Error / Critical | Light Red | `#ffc9c9` |
| Notes / Decisions | Light Yellow | `#fff3bf` |
| Storage / Data | Light Teal | `#c3fae8` |

### Tips
- Use the color palette consistently across the diagram
- **Text contrast is CRITICAL** -- never use light gray on white backgrounds. Minimum text color on white: `#757575`
- Do NOT use emoji in text -- they don't render in Excalidraw's font
- For dark mode diagrams, see `references/dark-mode.md`
- For larger examples, see `references/examples.md`
