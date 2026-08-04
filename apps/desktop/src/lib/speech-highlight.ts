/**
 * Highlighting the sentence Digit is currently saying, inside the message it
 * already rendered.
 *
 * The problem is that the spoken script is not the rendered text. Markdown
 * syntax is gone, emoji are gone, symbols were expanded — so neither string
 * search nor character offsets survive the trip. What does survive is the
 * letters and digits, in order. Both sides are projected down to their
 * alphanumerics, the spoken sentence is located in that projection, and the
 * result is mapped back to the text nodes it came from.
 *
 * The highlight itself is painted with the CSS Custom Highlight API rather
 * than by wrapping text in elements: the message is rendered markdown, and
 * inserting spans into it would fight the renderer, break text selection, and
 * change layout mid-sentence. A `::highlight()` pseudo-element paints over
 * the existing nodes and touches nothing.
 *
 * Every step degrades to "no highlight". A browser without the API, a
 * sentence that cannot be found, a message that re-rendered underneath us —
 * all of them leave the text exactly as it was, which is the normal state
 * whenever the agent is not speaking anyway.
 */

/** Name of the registered highlight. Styled in the app stylesheet. */
export const SPEECH_HIGHLIGHT_NAME = 'digit-speech'

interface Projection {
  /** Alphanumerics of the element, lowercased, in document order. */
  text: string
  /** For each projected character, the text node it came from. */
  nodes: Text[]
  /** For each projected character, its offset inside that node. */
  offsets: number[]
}

/** Reduce an element's rendered text to its alphanumerics, remembering where
 *  each one lives in the DOM. */
export function projectElement(root: Element): Projection {
  const projection: Projection = { nodes: [], offsets: [], text: '' }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const chars: string[] = []

  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.nodeValue ?? ''

    for (let index = 0; index < value.length; index += 1) {
      const char = value[index]

      if (/[\p{L}\p{N}]/u.test(char)) {
        chars.push(char.toLowerCase())
        projection.nodes.push(node as Text)
        projection.offsets.push(index)
      }
    }
  }

  projection.text = chars.join('')

  return projection
}

/** Reduce a spoken sentence the same way, so the two can be compared. */
export function projectText(text: string): string {
  return Array.from(text)
    .filter(char => /[\p{L}\p{N}]/u.test(char))
    .join('')
    .toLowerCase()
}

/** Longest prefix (or suffix) of `needle` present in `haystack` at or after
 *  `from`, as `[position, length]`, or `[-1, 0]`.
 *
 *  Binary search is valid because the property is monotone: if a prefix of
 *  length k occurs, so does one of length k-1. */
export function longestMatch(
  needle: string,
  haystack: string,
  from: number,
  minimum: number,
  fromEnd = false
): [number, number] {
  let low = minimum
  let high = needle.length
  let bestAt = -1
  let bestLength = 0

  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    const piece = fromEnd ? needle.slice(needle.length - middle) : needle.slice(0, middle)
    const found = haystack.indexOf(piece, from)

    if (found >= 0) {
      bestAt = found
      bestLength = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }

  return [bestAt, bestLength]
}

/** Shortest anchor we are willing to trust. Below this, a match is a
 *  coincidence and a wrong highlight is worse than none. */
export const MIN_ANCHOR = 12

/** Locate a spoken sentence inside a projection, as `[start, end]` indices
 *  into the projected text, or null. */
export function locateInProjection(spoken: string, projected: string, from = 0): [number, number] | null {
  const needle = projectText(spoken)

  if (!needle) {
    return null
  }

  const exact = projected.indexOf(needle, from)

  if (exact >= 0) {
    return [exact, exact + needle.length]
  }

  if (needle.length <= MIN_ANCHOR) {
    return null
  }

  const [headAt, headLength] = longestMatch(needle, projected, from, MIN_ANCHOR)

  if (headAt < 0) {
    return null
  }

  const [tailAt, tailLength] = longestMatch(needle, projected, headAt + headLength, MIN_ANCHOR, true)

  return tailAt < 0 ? [headAt, headAt + headLength] : [headAt, tailAt + tailLength]
}

/** Build a DOM Range over a projected span. */
export function rangeForSpan(projection: Projection, span: [number, number]): null | Range {
  const [start, end] = span
  const last = end - 1

  if (start < 0 || last < start || last >= projection.nodes.length) {
    return null
  }

  const range = document.createRange()

  range.setStart(projection.nodes[start], projection.offsets[start])
  range.setEnd(projection.nodes[last], projection.offsets[last] + 1)

  return range
}

// Structural, rather than leaning on lib.dom: the Custom Highlight API is a
// recent addition whose typings move between TypeScript releases, while the
// two calls actually used here have been stable since it shipped. The runtime
// probe below is the real gate anyway — a browser without the API has to
// degrade to no highlight, not to a compile error.
interface HighlightRegistryLike {
  delete: (name: string) => void
  set: (name: string, highlight: unknown) => void
}

function highlightRegistry(): HighlightRegistryLike | null {
  const registry = (CSS as unknown as { highlights?: HighlightRegistryLike }).highlights
  const constructor = (globalThis as unknown as { Highlight?: unknown }).Highlight

  return registry && typeof constructor === 'function' ? registry : null
}

function makeHighlight(range: Range): unknown {
  const { Highlight } = globalThis as unknown as { Highlight: new (...ranges: Range[]) => unknown }

  return new Highlight(range)
}

/** Remove any speech highlight currently painted. */
export function clearSpeechHighlight(): void {
  highlightRegistry()?.delete(SPEECH_HIGHLIGHT_NAME)
}

/**
 * Paint `spoken` inside `root`. Returns true when something was highlighted.
 *
 * Called on every cue, so it does the whole projection each time rather than
 * caching: a message is a few hundred characters, the walk is microseconds,
 * and a cache would have to be invalidated by a streaming re-render — which
 * is exactly the moment it would be wrong.
 */
export function paintSpeechHighlight(root: Element | null, spoken: string): boolean {
  const registry = highlightRegistry()

  if (!registry || !root || !spoken) {
    clearSpeechHighlight()

    return false
  }

  const projection = projectElement(root)
  const span = locateInProjection(spoken, projection.text)
  const range = span && rangeForSpan(projection, span)

  if (!range) {
    clearSpeechHighlight()

    return false
  }

  registry.set(SPEECH_HIGHLIGHT_NAME, makeHighlight(range))

  return true
}
