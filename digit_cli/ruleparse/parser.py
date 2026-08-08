#!/usr/bin/env python3
"""The parser: Russian query -> tool call, or an honest refusal. Never a guess.

Three configurations are measurable from here, and each is a separate column in
the report:

  R0  catalog lexicon only          - no hand-written Russian rule fires
  R1  R0 + rules.py + slots.py      - the full rule layer
  R2  R1 + lexical corpus lookup    - adds `corpus.py` for quotation answers

The contract has exactly two outcomes. Either every required argument was found
verbatim in the user's text and a tool call is emitted, or the parse fails and
the system refuses with the reason. There is no third branch that produces
prose, which is why this system's false-answer rate is bounded by routing
mistakes alone: it has no generator to hallucinate with.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import entities as E
from . import rules as R
from . import slots
from .lexicon import CatalogMatcher
from .morph import Token, normalize, tokenize

# Which argument sets are sufficient to call a tool. Any ONE satisfied group is
# enough; an empty list means the tool takes no mandatory input.
REQUIRED_GROUPS: dict[str, list[list[str]]] = {
    tool: [list(names)] for tool, names in R.REQUIRED.items()
}
REQUIRED_GROUPS.update({
    "bcrypt": [["input"], ["compareHash", "compareString"]],
    "base64-string-converter": [["textInput"], ["base64Input"]],
    "url-encoder": [["encodeInput"], ["decodeInput"]],
    "html-entities": [["escapeInput"], ["unescapeInput"]],
    "text-to-binary": [["inputText"], ["inputBinary"]],
    "text-to-unicode": [["inputText"], ["inputUnicode"]],
    "encryption": [["cypherInput", "cypherSecret"], ["decryptInput", "decryptSecret"]],
    "roman-numeral-converter": [["inputRoman"], ["inputNumeral"]],
    "percentage-calculator": [["percentageX", "percentageY"], ["numberFrom", "numberTo"]],
    "safelink-decoder": [["inputSafeLinkUrl"]],
    "color-converter": [["input"]],
    "temperature-converter": [["value", "scale"]],
    "text-diff": [["_operands"]],
    "token-generator": [["length"]],
    "lorem-ipsum-generator": [["paragraphs"], ["words"], ["sentences"]],
    "ipv4-range-expander": [["rawStartAddress", "rawEndAddress"]],
    "json-diff": [["rawLeftJson", "rawRightJson"]],
})


# Tools that run both ways. Naming one without naming a direction is not a
# request, it is half a request.
BIDIRECTIONAL = {"base64-string-converter", "url-encoder", "html-entities",
                 "text-to-binary", "text-to-unicode", "encryption",
                 "roman-numeral-converter", "base-converter"}
DIRECTION_OPS = {"encode", "decode", "encrypt", "decrypt", "convert", "parse",
                 "calc", "format", "validate"}
# Shapes that ARE a notation: percent-encoding, HTML entities, a binary string,
# a Roman numeral. Nobody writes those by hand, so seeing one fixes the
# direction to "decode" without any verb. base64 is deliberately NOT here: an
# arbitrary ASCII string is equally plausible as input or as output, which is
# why «обработай мне base64 строку dGVzdA==» has to be refused.
DIRECTION_BY_ENTITY = {"percent_enc", "entity_named", "entity_numeric",
                       "binary", "roman"}


# Wh-words. Their presence makes a query a question about the world; the
# absence of an imperative makes it NOT an instruction to run anything.
WH_WORDS = {"что", "какой", "какая", "какие", "каков", "сколько", "кто", "где",
            "когда", "чем", "почему", "зачем", "куда", "откуда", "чей", "чье",
            "чья", "как", "который", "скока"}
# Constructions that are commands without an imperative verb: «нужен ULID»,
# «надо посчитать», «хочу глянуть».
COMMAND_WORDS = {"нужный", "надо", "хотеть", "хочу", "требоваться", "дать",
                 "подскажи", "помочь", "давать", "нужно", "нужен", "нужна"}
# Argument names filled from a closed vocabulary rather than from free text.
# They are never the thing that makes a bad call, so they do not have to be
# entity-backed.
CLOSED_VOCAB_ARGS = {"scale", "algorithm", "hashFunction", "cypherAlgo",
                     "decryptAlgo", "inputBase", "outputBase", "encodeUrlSafe",
                     "_operands", "defaultCountryCode", "font"}


def is_command(toks) -> bool:
    """True when the user told the system to DO something.

    pymorphy tags the imperative mood directly, so «посчитай», «сгенери»,
    «переведи» are recognised as commands without a verb list. Queries with no
    finite verb at all («3 ulid», «md5 от password123») count as commands too:
    a bare noun phrase addressed to a tool catalog is an order.
    """
    finite = False
    for t in toks:
        tag = str(t.pos or "")
        if tag in {"VERB", "INFN"}:
            finite = True
        if t.lemma in COMMAND_WORDS or t.norm in COMMAND_WORDS:
            return True
    for t in toks:
        if t.pos == "VERB":
            from .morph import analyzer
            for p in analyzer().parse(t.norm):
                if p.tag.mood == "impr":
                    return True
    return not finite


def is_question(query: str, toks) -> bool:
    return query.rstrip().endswith("?") or any(
        t.lemma in WH_WORDS or t.norm in WH_WORDS for t in toks)


@dataclass
class Parse:
    ok: bool
    tool_id: str | None = None
    args: dict = field(default_factory=dict)
    score: float = 0.0
    margin: float = 0.0
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    runners_up: list[tuple[str, float]] = field(default_factory=list)
    kind: str = "tool"          # tool | corpus | refuse


class RuleParser:
    """Deterministic. Same query in, byte-identical parse out, every time."""

    def __init__(self, use_rules: bool = True, min_score: float = 12.0,
                 min_margin: float = 1.0, lex_weight: float = 1.0,
                 payload_policy: str = "strict"):
        self.matcher = CatalogMatcher()
        self.use_rules = use_rules
        self.min_score = min_score
        self.min_margin = min_margin
        # In R1 the catalog lexicon is demoted to a tie-breaker: it is a bag of
        # words with no notion of what is an argument and what is a verb, so at
        # full weight it out-shouts the rules on long queries.
        self.lex_weight = lex_weight if use_rules else 1.0
        self.payload_policy = payload_policy
        self.pair_table = R.derive_pair_table(self.matcher.tools) if use_rules else {}
        self.known_tools = set(self.matcher.by_id)

    # -- analysis ---------------------------------------------------------
    def analyse(self, query: str):
        toks = tokenize(query)
        ents = E.detect(query)
        low = normalize(query)
        lem = {t.lemma for t in toks} | {t.norm for t in toks}
        lem |= {w for w in re.findall(r"[a-z]{2,}[a-z0-9]*", low)}
        for t in toks:
            if "-" in t.norm:
                lem |= {p for p in t.norm.split("-") if len(p) > 1}
                lem.add(t.norm.replace("-", ""))
        lem = {w for w in lem if len(w) > 1}
        ops: set[str] = set()
        for t in toks:
            ops |= R.LEMMA_TO_OPS.get(t.lemma, set())
            ops |= R.LEMMA_TO_OPS.get(t.norm, set())
        if self.use_rules:
            ops |= R.ops_from_stems(low)
            lem |= R.lemmas_from_stems([t.norm for t in toks])
        return toks, ents, lem, ops

    @staticmethod
    def mask_payload(query: str, ents) -> str:
        chars = list(query)
        for e in ents:
            if e.kind in {"cron", "roman", "timestamp"}:
                continue                      # short shapes, no words inside
            for i in range(max(0, e.start), min(len(chars), e.end)):
                chars[i] = " "
        return "".join(chars)

    def formats_in(self, toks: list[Token], lem: set[str]) -> tuple[set[str], set[str]]:
        """(source formats, target formats) from «из X в Y» case frames.

        The head of the group is not always the format word: «в виде json»
        puts «вид» first. So the whole group is scanned for a format name,
        which is what the preposition is really pointing at.
        """
        src: set[str] = set()
        dst: set[str] = set()
        from .morph import SOURCE_PREPS, TARGET_PREPS
        for i, tok in enumerate(toks):
            if not tok.is_prep:
                continue
            role = "source" if tok.norm in SOURCE_PREPS else \
                   "target" if tok.norm in TARGET_PREPS else None
            if role is None:
                continue
            for nxt in toks[i + 1:i + 5]:
                fmt = R.FORMAT_WORDS.get(nxt.lemma) or R.FORMAT_WORDS.get(nxt.norm)
                if fmt:
                    (src if role == "source" else dst).add(fmt)
                    break
                # The group ends at the next preposition or the next verb:
                # «в проекте лежит pyproject.toml» must not read `toml` as the
                # target of «в», or the conversion direction inverts.
                if nxt.is_prep or nxt.pos in {"VERB", "INFN", "GRND", "PRTF"}:
                    break
        return src, dst

    # -- scoring ----------------------------------------------------------
    def candidates(self, query: str, toks, ents, lem, ops):
        # The pasted payload is DATA, not instruction. Scoring the catalog
        # against it routed «покажи, что изменилось: {"id":1,"status":"new"}»
        # to http-status-codes because the JSON happened to contain the key
        # `status`. Masking every detected literal before the lexical match
        # removes that whole class of error at a stroke.
        scored: dict[str, tuple[float, list[str]]] = {}
        for tool_id, score, hits in self.matcher.score(self.mask_payload(query, ents)):
            scored[tool_id] = (score * self.lex_weight, [f"lex:{h}" for h in hits[:4]])

        rule_backed: set[str] = set()
        if not self.use_rules:
            return scored, rule_backed

        ent_kinds = {e.kind for e in ents}
        low = normalize(query)
        for trig in R.TRIGGERS:
            if trig.fires(lem, ent_kinds, ops, low):
                prev, why = scored.get(trig.tool_id, (0.0, []))
                scored[trig.tool_id] = (prev + trig.weight, why + [f"rule:{trig.tool_id}"])
                rule_backed.add(trig.tool_id)

        # Conversion routing straight off the catalog's own «X в Y» titles.
        src, dst = self.formats_in(toks, lem)
        fmt_present = {R.FORMAT_WORDS[l] for l in lem if l in R.FORMAT_WORDS}
        for (a, b), tool_id in self.pair_table.items():
            hit = False
            if a in src and b in dst:
                hit = True                                   # «из json в toml»
            elif b in dst and a in fmt_present:
                hit = True                                   # «json в toml»
            elif a in fmt_present and b in fmt_present and a != b:
                hit = False
            if hit:
                prev, why = scored.get(tool_id, (0.0, []))
                scored[tool_id] = (prev + 16.0, why + [f"pair:{a}->{b}"])
                rule_backed.add(tool_id)

        # A pasted blob is evidence about the SOURCE format when a target is named.
        for kind in ent_kinds:
            fmt = {"json": "json", "yaml": "yaml", "toml": "toml", "xml": "xml",
                   "markdown": "markdown", "binary": "binary"}.get(kind)
            if not fmt:
                continue
            for (a, b), tool_id in self.pair_table.items():
                if a == fmt and b in dst | fmt_present and b != fmt:
                    prev, why = scored.get(tool_id, (0.0, []))
                    scored[tool_id] = (prev + 16.0, why + [f"blob:{fmt}->{b}"])
                    rule_backed.add(tool_id)

        # Entity guards: a tool that works on a shape needs the shape present.
        for tool_id in list(scored):
            need = R.ENTITY_GUARD.get(tool_id)
            if need and not (need & ent_kinds) and not ({"quoted", "identlike", "latin"} & need):
                scored.pop(tool_id)
                rule_backed.discard(tool_id)
        return scored, rule_backed

    # -- decision ---------------------------------------------------------
    def parse(self, query: str) -> Parse:
        toks, ents, lem, ops = self.analyse(query)
        scored, rule_backed = self.candidates(query, toks, ents, lem, ops)
        if not scored:
            return Parse(False, reason="ни один инструмент каталога не отозвался на слова запроса",
                         kind="refuse")

        ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])
        top_id, (top_score, why) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0.0
        margin = top_score - second

        if top_score < self.min_score:
            return Parse(False, score=top_score, margin=margin, kind="refuse",
                         runners_up=[(t, s) for t, (s, _) in ranked[:3]],
                         reason=f"слабое совпадение с каталогом (вес {top_score:.1f} < "
                                f"порога {self.min_score:.1f})")
        # The bag-of-words lexicon may re-rank, but it may never AUTHORISE a
        # call. Every false answer on the corpus tasks came from a query that
        # merely contained a catalog word - «регистры накопления» reaching
        # case-converter, «PostgreSQL» reaching sql-prettify. A tool is called
        # only when a written rule fired for it.
        if self.use_rules and top_id not in rule_backed:
            return Parse(False, tool_id=top_id, score=top_score, margin=margin,
                         kind="refuse", evidence=why,
                         reason="совпали только слова каталога, ни одно правило разбора "
                                f"не сработало (кандидат {top_id})")
        if margin < self.min_margin:
            return Parse(False, score=top_score, margin=margin, kind="refuse",
                         runners_up=[(t, s) for t, (s, _) in ranked[:3]],
                         reason="запрос одинаково подходит к нескольким инструментам "
                                f"({ranked[0][0]} и {ranked[1][0]}), однозначного разбора нет")

        # Bidirectional tools need a direction. «обработай мне base64 строку
        # dGVzdA==» names the tool and the payload but not whether to encode or
        # decode, and either answer would be a coin flip presented as fact.
        ent_kinds_now = {e.kind for e in ents}
        if (self.use_rules and top_id in BIDIRECTIONAL
                and not (ops & DIRECTION_OPS)
                and not (ent_kinds_now & DIRECTION_BY_ENTITY)):
            return Parse(False, tool_id=top_id, score=top_score, margin=margin,
                         kind="refuse", evidence=why,
                         reason=f"инструмент определён ({top_id}), но не указано "
                                "направление преобразования")

        args = slots.extract(top_id, query, toks, ents, lem, ops) if self.use_rules else \
            self._generic_args(top_id, query, toks, ents)

        # A question that is not also a command may only reach a tool through a
        # literal the DETECTORS recognised. «На каких трудных задачах держатся
        # RSA и ECDSA?» mentions RSA and the key generator needs no arguments,
        # so nothing else stops it from "answering" a corpus question.
        if self.use_rules and is_question(query, toks) and not is_command(toks):
            hard = [k for k, v in args.items() if k not in CLOSED_VOCAB_ARGS]
            spans = [e.value for e in ents] + list(
                slots.all_marker_literals(query, toks))
            backed = [k for k in hard if self._entity_backed(str(args[k]), spans)]
            if not backed:
                return Parse(False, tool_id=top_id, args=args, score=top_score,
                             margin=margin, kind="refuse", evidence=why,
                             reason="это вопрос, а не команда, и ни один аргумент не "
                                    "опознан как литерал известной формы")
        # Приказ, показывающий на операнд пальцем, а не называющий его.
        pointer = slots.command_pointer(toks)
        if self.use_rules and pointer is not None and not self._pointer_resolved(
                pointer, query, toks, ents, args):
            return Parse(False, tool_id=top_id, args=args, score=top_score,
                         margin=margin, kind="refuse", evidence=why,
                         reason=f"инструмент определён ({top_id}), но приказ ссылается "
                                "на значение указательным словом, а самого значения "
                                "в запросе нет")
        groups = REQUIRED_GROUPS.get(top_id, [[]])
        missing = None
        for group in groups:
            gaps = [k for k in group if k not in args or args[k] in (None, "")]
            if not gaps:
                missing = []
                break
            if missing is None or len(gaps) < len(missing):
                missing = gaps
        if missing and self.payload_policy == "lenient" and self._object_named(top_id, lem):
            # The payload is REFERRED to but not pasted («в проекте лежит
            # pyproject.toml, а скрипту нужно то же в виде json»). Lenient mode
            # routes anyway and lets the tool ask for the file.
            missing = []
        if missing:
            return Parse(False, tool_id=top_id, args=args, score=top_score, margin=margin,
                         kind="refuse", evidence=why,
                         reason=f"инструмент определён ({top_id}), но в запросе нет "
                                f"обязательных данных: {', '.join(missing)}")

        return Parse(True, tool_id=top_id, args=args, score=top_score, margin=margin,
                     evidence=why, runners_up=[(t, s) for t, (s, _) in ranked[1:3]],
                     kind="tool")

    def _pointer_resolved(self, pointer: str, query: str, toks, ents, args: dict) -> bool:
        """Есть ли в САМОМ запросе то, на что показывает приказ.

        Слой правил читает одно предложение и копирует из него подстроки.
        Разрешать ссылки ему нечем: ни предыдущего хода, ни корпуса, ни модели
        дискурса у него нет. Поэтому «переведи ЭТО ЧИСЛО в двоичную» и
        «сделай из НЕГО slug» — это запросы не к нему: число и slug-исходник
        лежат там, куда запрос только показывает. Раньше такой вызов доезжал
        до исполнения и возвращал перевод слов «Базы данных» в двоичный код с
        видом проверенного ответа.

        Ссылка считается разрешённой ровно тогда, когда указанное лежит тут же:

          «переведи ЭТОТ yaml в json: name: api»  — распознан yaml;
          «сожми ЭТУ строку»                      — маркер «строка» дал литерал,
                                                    и он попал в аргументы;
          «посчитай для НЕГО»                     — хоть один аргумент взят из
                                                    распознанной формы или из
                                                    маркера, то есть местоимению
                                                    есть на что указывать.

        Голому местоимению достаточно любой такой опоры: разбирать, к какому
        именно слову оно относится, нечем, а требовать большего значило бы
        рубить «как сокращают слово internationalization ..., посчитай для
        него», где операнд назван прямо.

        Число, взятое из запроса дословно, — опора наравне с распознанной
        формой. У числа не бывает второго прочтения: «трактовать как 400 ...
        покажи его официальное название» ссылается на 400, и 400 тут есть.
        Латинское слово так не годится — «Какой SKU оферта фиксирует» тоже
        содержит латиницу, но не значение, а вопрос о нём.
        """
        hard = [str(v) for k, v in args.items()
                if k not in CLOSED_VOCAB_ARGS and v not in (None, "")]
        spans = [e.value for e in ents if str(e.value).strip()] + \
            list(slots.all_marker_literals(query, toks))
        anchored = [v for v in hard
                    if any(v in s or s in v for s in spans)
                    or (re.fullmatch(r"\d+", v) and v in query)]
        if pointer == "":
            return bool(anchored)
        # Указательное с существительным: названа КАТЕГОРИЯ того, на что
        # показывают, и она обязана совпасть с тем, что разбор действительно
        # взял. «назови ЭТОТ ХЭШ» не совпадает ни с чем: хэша в запросе нет,
        # есть только строка, от которой его хотят.
        kinds = {e.kind for e in ents}
        slot = slots.MARKERS.get(pointer)
        if pointer in kinds or (slot and slot in kinds):
            return True
        if slot:
            # Существительное при указательном — слот-маркер, то есть родовое
            # слово («строка», «текст», «ключ»). Оно называет РОЛЬ значения, а
            # не адрес, поэтому годится любая опора: «сожми эту строку» при
            # разобранном литерале указывает на этот литерал.
            lit = slots.literal_after(query, toks, slot)
            if lit and any(lit in v or v in lit for v in hard):
                return True
            if anchored:
                return True
        return False

    @staticmethod
    def _entity_backed(value: str, spans: list[str]) -> bool:
        """Did this argument value come from a recognisable shape?

        Accepted: a detected entity, a bare number or arithmetic expression, a
        single Latin token. Rejected: a span of Russian prose, which in a
        question is almost always the subject of the question rather than an
        argument.
        """
        v = value.strip()
        if not v:
            return False
        if any(v in span or span in v for span in spans):
            return True
        if re.fullmatch(r"[\d\s+\-*/^().,%]+", v):
            return True
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+@\-]*[A-Za-z0-9]", v):
            return True
        return False

    def _object_named(self, tool_id: str, lem: set[str]) -> bool:
        """Does the query name the KIND of thing the tool consumes?

        «переведи конфиг из toml в yaml» names its object («конфиг», «toml»)
        without pasting it; «посчитай хэш» names no object at all. The first is
        an under-supplied but well-formed request, the second is not a request
        at all. Only the format words of the tool's own title count.
        """
        title_formats = {R.FORMAT_WORDS[w] for w in
                         re.findall(r"[a-zа-я0-9]+", normalize(
                             self.matcher.by_id[tool_id].title_ru))
                         if w in R.FORMAT_WORDS}
        query_formats = {R.FORMAT_WORDS[w] for w in lem if w in R.FORMAT_WORDS}
        return bool(title_formats & query_formats)

    def _generic_args(self, tool_id: str, query: str, toks, ents) -> dict:
        """R0 argument extraction: no per-tool knowledge, one generic literal.

        The catalog lists argument NAMES but not which of them is the payload,
        so R0 can only say "here is the literal the user pasted" and park it
        under the first argument name the catalog happens to list.
        """
        lit = slots.main_literal(query, toks, ents)
        if lit is None:
            return {}
        names = self.matcher.by_id[tool_id].args
        return {names[0]: lit} if names else {"input": lit}
