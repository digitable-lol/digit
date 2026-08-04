"""Grounded question answering over the knowledge base.

The owner's requirement is that an answer be *formally defensible*: every
claim is either backed by a quoted passage from the corpus or explicitly
marked as unbacked. Two mechanisms enforce that here, and neither of them
trusts the language model to behave.

**1. Abstention is decided before generation, not by the model.**
:func:`assess` classifies a question as ``ANSWERABLE``, ``WEAK`` or
``ABSENT`` from retrieval statistics alone. If the verdict is ``ABSENT``
the generator is never invoked — "в базе знаний этого нет" is returned
from Python, so no amount of model hallucination can produce a confident
answer to "рецепт борща".

**2. The verdict rests on lexical attestation, not cosine.**
As documented in :mod:`digit_cli.kb.search`, the encoder's cosine scores
on this Russian corpus are not calibrated: an off-topic passage can outscore
the correct one. What *is* certain is FTS5's answer to "does the token
'борщ' occur anywhere in the index?" — a fact, not a threshold. So the
primary signal is the fraction of query content terms attested in the
corpus, with the dense score used only as a secondary channel so that a
legitimate paraphrase (all-synonym query, zero literal term overlap) is not
wrongly refused.

The thresholds below are calibrated against the live index rather than
guessed; see ``calibrate()`` and the report in the pull request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from digit_cli.kb import store
from digit_cli.kb.embed import OllamaClient
from digit_cli.kb.search import Hit, SearchResult, search

# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

ANSWERABLE = "answerable"
WEAK = "weak"
ABSENT = "absent"

COVERAGE_ABSENT = 0.5
"""Below this share of query terms attested, the corpus does not discuss it.

Measured against the live index rather than guessed. Term coverage for
in-corpus questions came out 0.67-1.00; for out-of-corpus questions,
0.00-0.33. The residual 0.33 is the generic-word effect — "правила игры в
нарды" attests "правила" (22 chunks) because rules are discussed all over
an engineering corpus, while "игры" and "нарды" are absent. 0.5 sits in
the middle of the observed gap [0.33, 0.67] and requires a genuinely
absent topic to attest *half* its content terms before it can escape,
which generic-word noise alone cannot do.
"""

SUPPORT_ABSENT = 0.6
"""Primary signal: best per-chunk term support among the retrieved hits.

See :attr:`digit_cli.kb.search.SearchResult.support`. Below this, no single
retrieved passage contains even a clear majority of the query's terms
together — there is nothing to quote, so there is nothing to assert.

Note the quantisation: for a two-term query support can only be 0, 0.5 or
1.0, so the boundary has to sit above 0.5 for "one of the two terms
matched" to count as insufficient. That case is exactly "рецепт борща",
where "рецепт" matches engineering prose and "борщ" matches nothing in the
same passage.
"""

SUPPORT_STRONG = 0.75
"""At or above this, a retrieved passage genuinely covers the question."""

COVERAGE_STRONG = 0.6
"""Corpus-wide attestation, kept as a secondary sanity signal only."""

DENSE_STRONG = 0.85
"""Dense score that on its own could justify answering. Deliberately high.

Measured on the finished 30,880-chunk index, cosine is badly compressed on
this English-first encoder over Russian text: a *relevant* top hit scored
0.7643 ("горутины и каналы") and 0.7903 ("бюджет ошибок SLO"), while a
completely *irrelevant* top hit for "рецепт борща" still scored 0.7410.
A 0.02-0.05 separation is not a usable decision boundary.

So this threshold is set above everything observed, which makes the dense
channel a *ranking* device rather than an evidence device: it orders
candidates, but it is never allowed to authorise an answer on its own. All
authorisation flows through the lexical signals, which are facts about the
index rather than thresholds on an uncalibrated score.
"""

DENSE_RESCUE = DENSE_STRONG
"""Alias used by the ABSENT rule."""


@dataclass
class Assessment:
    verdict: str
    reason: str
    coverage: float
    dense_max: float
    unattested: List[str] = field(default_factory=list)
    support: float = 0.0

    @property
    def is_absent(self) -> bool:
        return self.verdict == ABSENT


def assess(result: SearchResult) -> Assessment:
    """Decide whether the corpus can support an answer at all."""
    coverage = result.coverage
    dense = result.dense_max
    support = result.support
    unattested = result.unattested

    if not result.hits:
        return Assessment(
            ABSENT,
            "no chunk matched the query on either the lexical or the dense channel",
            coverage, dense, unattested, support,
        )

    # ABSENT: no retrieved passage contains enough of the question's terms to
    # quote, and nothing is semantically close enough to override that.
    if support < SUPPORT_ABSENT and dense < DENSE_RESCUE:
        missing = ", ".join(unattested[:8])
        detail = (
            f"absent from the corpus entirely: {missing}. "
            if missing
            else "the terms occur in the corpus, but never together in one passage. "
        )
        return Assessment(
            ABSENT,
            f"no retrieved passage contains more than {support:.0%} of the "
            f"query's terms. {detail}"
            f"Best semantic match {dense:.3f} < {DENSE_RESCUE} — on this "
            f"encoder that is within the range irrelevant passages reach, so "
            f"it is not evidence.",
            coverage, dense, unattested, support,
        )

    # ANSWERABLE: a single retrieved passage genuinely covers the question.
    # Support is the primary signal because it is a verifiable fact about a
    # specific chunk — the same chunk that will be cited — rather than a
    # threshold on an uncalibrated similarity score.
    if support >= SUPPORT_STRONG or dense >= DENSE_STRONG:
        return Assessment(
            ANSWERABLE,
            f"top passage contains {support:.0%} of the query's terms "
            f"(corpus-wide attestation {coverage:.0%}, best cosine {dense:.3f})",
            coverage, dense, unattested, support,
        )

    return Assessment(
        WEAK,
        f"partial support: best passage covers {support:.0%} of the query's "
        f"terms (< {SUPPORT_STRONG:.0%}), best cosine {dense:.3f}",
        coverage, dense, unattested, support,
    )


# --------------------------------------------------------------------------
# Prompting
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Ты — ассистент по базе знаний Digitable. Отвечай ТОЛЬКО по приведённым "
    "фрагментам источников.\n"
    "Правила, нарушать которые нельзя:\n"
    "1. Каждое утверждение подкрепляй ссылкой на номер фрагмента в "
    "квадратных скобках, например [2].\n"
    "2. Если во фрагментах нет ответа — так и напиши: «в приведённых "
    "источниках этого нет». Не додумывай.\n"
    "3. Не используй знания вне фрагментов. Если добавляешь что-то от себя, "
    "пометь это словом НЕПОДТВЕРЖДЕНО.\n"
    "4. Отвечай на языке вопроса, по существу, без вступлений.\n"
)


def build_prompt(question: str, hits: Sequence[Hit], max_chars: int = 9000) -> str:
    """Assemble the numbered-source prompt.

    Sources are numbered from 1 and the numbering is what the model is told
    to cite, so a citation can be mechanically mapped back to a file path.
    """
    blocks: List[str] = []
    used = 0
    for i, hit in enumerate(hits, start=1):
        body = " ".join(hit.body.split())
        budget = max(600, (max_chars - used) // max(1, len(hits) - i + 1))
        if len(body) > budget:
            body = body[: budget - 1] + "…"
        block = (
            f"[{i}] файл: {hit.rel_path} | трек: {hit.track} | "
            f"статья: {hit.title}"
            + (f" | раздел: {hit.heading}" if hit.heading else "")
            + f"\n{body}"
        )
        blocks.append(block)
        used += len(block)
        if used >= max_chars:
            break
    sources = "\n\n".join(blocks)
    return (
        f"ФРАГМЕНТЫ ИСТОЧНИКОВ:\n\n{sources}\n\n"
        f"ВОПРОС: {question}\n\n"
        f"Ответ (со ссылками [n] на фрагменты):"
    )


@dataclass
class Answer:
    question: str
    verdict: str
    reason: str
    text: str
    hits: List[Hit] = field(default_factory=list)
    result: Optional[SearchResult] = None
    model: str = ""

    @property
    def grounded(self) -> bool:
        return self.verdict != ABSENT


ABSENT_MESSAGE = (
    "В базе знаний этого нет.\n\n"
    "Проверено по индексу корпуса Digitable: ни один фрагмент не содержит "
    "терминов запроса, и семантическое сходство ниже порога. Ответ не "
    "генерировался — утверждение без опоры на источник не выдаётся."
)


def ask(
    question: str,
    *,
    k: int = 6,
    client: Optional[OllamaClient] = None,
    model: Optional[str] = None,
    conn=None,
    track: Optional[str] = None,
) -> Answer:
    """Retrieve, decide, and only then (maybe) generate."""
    client = client or OllamaClient()
    if conn is not None:
        return _ask(question, k, client, model, conn, track)
    with store.connect() as own:
        return _ask(question, k, client, model, own, track)


def _ask(question, k, client, model, conn, track) -> Answer:
    result = search(question, k=k, client=client, conn=conn, track=track)
    verdict = assess(result)

    if verdict.is_absent:
        return Answer(
            question=question,
            verdict=ABSENT,
            reason=verdict.reason,
            text=ABSENT_MESSAGE,
            hits=result.hits[:3],
            result=result,
            model="(generator not invoked)",
        )

    prompt = build_prompt(question, result.hits)
    # A grounded, cited answer over ~6 passages is a few paragraphs. Capping
    # generation keeps the worst case bounded on a CPU-only host, where every
    # extra token is real wall-clock time.
    text = client.generate(
        prompt, system=SYSTEM_PROMPT, model=model, num_predict=450
    )
    if not text:
        text = (
            "Модель не вернула ответ. Ниже — найденные фрагменты; они "
            "релевантны, но синтез не выполнен."
        )

    if verdict.verdict == WEAK:
        text = (
            "⚠ Поддержка источниками слабая — "
            f"{verdict.reason}. Ответ ниже может быть неполным.\n\n" + text
        )

    return Answer(
        question=question,
        verdict=verdict.verdict,
        reason=verdict.reason,
        text=text,
        hits=result.hits,
        result=result,
        model=model or client.chat_model,
    )
