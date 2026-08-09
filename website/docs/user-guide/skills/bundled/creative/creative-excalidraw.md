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

### Turning a Map into Tasks

A project schema is not decoration: what is on it gets carried into the tracker
by hand, twice — once into Taskwarrior, and again when the drawing changes.
`scripts/tasks.py` does that carrying, deterministically.

```bash
# Посмотреть, что получится, ничего не записывая
python skills/creative/excalidraw/scripts/tasks.py from-map ~/diagrams/план.excalidraw --dry-run

# Записать в базу задач
python skills/creative/excalidraw/scripts/tasks.py from-map ~/diagrams/план.excalidraw
```

What translates into what:

| На карте | В задаче |
|---|---|
| Фигура с подписью | задача; подпись — описание |
| Стрелка A → B | B зависит от A (`depends`) |
| Рамка (frame) | проект; имя рамки — имя проекта |
| Группа | второй уровень проекта; имя — свободный текст в группе |
| Заливка фигуры | приоритет (`#ffc9c9`→H, `#ffd8a8`→M, `#fff3bf`→L) |
| `!H` / `!M` / `!L` в подписи | приоритет явной меткой; сильнее цвета |

The colour table is a default, not a law: `--priority-colors table.json` replaces
it. The explicit mark wins over the fill because the mark was typed on *that*
shape, while a fill is usually inherited from whatever the shape was copied from.

Three things worth knowing before running it against a database someone else
uses:

- **The key is the element id**, kept in the task's `excalidraw` UDA — not the
  description (descriptions get edited) and never the positional number.
  Task uuids are derived from the element id, so the same map produces the same
  tasks on any machine.
- **Writes are read-merge-import.** `task import` of an existing uuid *replaces*
  the record: a record submitted without `annotations` leaves the task with none,
  and nothing is printed about it. Never write a partial record.
- **`task <uuid> modify excalidraw:box1` is not a shortcut for this.** With the
  UDA undeclared in the rc file, Taskwarrior does not complain — it writes
  `excalidraw:box1` into the *description*, and the description is gone. That is
  why this utility only ever imports.

Cycles are found in the map before anything is written. Taskwarrior also refuses
a circular `depends`, but it refuses at the task it happens to reach, leaving
half the dependencies applied.

`--self-test` checks the reading rules and, when `task` is on PATH, a full write
round-trip in a temporary database it creates and removes itself.

### Drawing the Backlog

The other direction draws the tasks, using **the Workbench's own library sets**
rather than shapes invented here — so the map looks like one a person drew by
dragging «Карточка задачи» out of the kanban set, because it is made of exactly
that.

```bash
python skills/creative/excalidraw/scripts/tasks.py to-map ~/diagrams/бэклог.excalidraw \
    --project digit --palette carbon
```

The library lives in the courses checkout (`static/workbench/excalidraw/`), where
it is generated and checked by `npm run test:excalidraw`; this utility walks up
from itself to find it, and `--library` points at it directly. A copy vendored
here would be a second truth that drifts from the first in silence.

- The first level of the project becomes a **frame**, the second a **group** with
  a caption — the same two things `from-map` reads back, so a drawn map parses
  into the tasks it came from. That round trip is what the self-test checks.
- Priority is written as the `!H` mark in the title, not as a fill: the fills in
  those sets carry the palette, and repainting a card pastel would break the
  look the library was taken for.
- Dependencies become arrows **bound at both ends**. An arrow bound at neither
  looks like a dependency and is not one — `from-map` says so rather than
  inventing the link.
- A card grows downward when the description does not fit, and everything below
  the title moves with it. Nothing is truncated.

`to-map` refuses to overwrite an existing map: redrawing throws away every
position the owner moved by hand.

### Keeping the Two in Step

`sync` is the run you make again and again. One rule decides everything it does:

> **Structure comes from the map, state comes from the tracker.**

What exists, what waits for what, whose project it is and what matters more — is
drawn, and the map is right about it. Whether it is finished — the map cannot
say, and the tracker can.

```bash
python skills/creative/excalidraw/scripts/tasks.py sync ~/diagrams/бэклог.excalidraw --dry-run
python skills/creative/excalidraw/scripts/tasks.py sync ~/diagrams/бэклог.excalidraw
```

**Nothing is ever moved.** Not `x`, not `y`, not sizes, not `groupIds`, not
`frameId`, not arrow points. Edits go through `revise.py`, element by element, so
Excalidraw's own change tracking is advanced and nothing the utility does not
understand is dropped. A status becomes a word in the card's pill («в работе»,
«сделано», «ждёт срока», «снята») and a closed card is dimmed, not deleted —
deleting an element an arrow points at makes Excalidraw drop the arrow in
silence.

**The description is the one field both sides may write.** When it has diverged,
the utility does not choose: it prints both, leaves the field alone, and waits
for `--prefer map` or `--prefer tracker`.

**The key is the UDA, and `to-map` writes it as it draws.** Without that, a map
drawn from tasks would not recognise its own tasks on the next run — the element
id was derived from the task uuid, and the uuid derived back from the element id
is a different one — and every sync would double the backlog.

Running it twice in a row produces no second set of edits.

### Widgets from the Command Line

The Workbench's six sets each carry their own vocabulary — theme and branches for
a mindmap, participant and call for a sequence, persona and container for C4,
lane and milestone for a roadmap. Until now the only way to reach them was to
open a canvas and drag shapes with a mouse.

```bash
digit excalidraw list
digit excalidraw mindmap --text outline.txt --out карта.excalidraw
digit excalidraw c4 --text система.txt --out c4.excalidraw --palette paper
```

One input shape serves all six, because a router model must not be offered a
choice of argument form:

```
Тема                       # уровень 0 — фигура верхнего уровня набора
  Ветвь                    # отступ — уровень ниже
    Лист
Ветвь -> Лист: подпись     # связь между двумя подписями
```

Indentation width does not matter, only that it increases. On tree widgets
(mindmap, C4, flowchart) the indentation itself is drawn as a line — otherwise a
mindmap is three columns of rectangles and the reader guesses the kinship. On
lane widgets (roadmap, sequence, kanban) the top level becomes a column.

Rows are spaced by the shapes actually drawn, not by a constant: «Система в
фокусе» from C4 is taller than «Лист» from the mindmap, and one step either
overlaps in some sets or leaves holes in others. The self-test checks that no
two top-level shapes overlap, in every set.

### Why the Schemas Are Flat

Digit hands its utility catalog to a small router model one category at a time,
and the schemas in it are deliberately flat — *«без вложенности глубже двух
уровней и без альтернатив в описании входа»*, as the Workbench write-up puts it
(`content/workbench/digit-integrations.md` in the courses repo). These four
entries obey that, and the obedience is checked rather than asserted:

```bash
python skills/creative/excalidraw/scripts/tasks.py schema   # + глубина и альтернативы
digit excalidraw schema
```

Level 1 is the argument object, level 2 is one argument's own description. An
argument that is itself an object, or an array of objects, is level 3 — so every
argument here is a scalar. `oneOf` / `anyOf` / `allOf` are refused outright.

The requirement is about *input*, but it can be broken by output just as easily:
a parse that hands back a nested structure forces the next utility to accept one.
So the tests check the parse too — every field of a parsed node is a scalar, and
a project never reaches a third level even when a frame is called «этап 2.1» and
its group «разбор.первый» (dots inside a name become hyphens).

One more place the same rule shows: all six widgets take **one** input form, not
a grammar each. A router picks the utility, not the shape of its argument.

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
- Asked whether Euler circles translate into the project language, read
  `references/euler-fts.md` before answering — two of the three relations do,
  one is deliberately absent, and "nothing corresponds to it" is seven different
  cases, each already named. Do not invent an eighth.
