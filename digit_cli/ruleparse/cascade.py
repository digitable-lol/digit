"""Решение каскада: разобрать запрос правилами — или молча уступить модели.

Единственная точка, где слой правил соприкасается с Digit. Всё остальное в
пакете — измеренный артефакт, который нельзя трогать, не переизмерив.

Контракт
--------
`route(query)` возвращает `RuleDecision`. Ровно два исхода:

  * `routed=True`  — запрос разобран: назван инструмент и все обязательные
    аргументы найдены в тексте пользователя дословно. Ответ можно выдавать
    без модели.
  * `routed=False` — не разобран. Это НЕ отказ пользователю. Это уступка:
    вызывающая сторона обязана продолжить обычным путём (модель), а `reason`
    и `declined` существуют для наблюдаемости, а не для показа как отказ.

Почему такая асимметрия. В самостоятельном измерении слой правил отказывает
пользователю, и его излишний отказ — 58,8 % против 13,0 % у модели: он
блистает на маршрутизации (96,0 % против 85,0 %, аргументы 100,0 % против
90,1 %) и даёт РОВНО НОЛЬ на генерации спецификаций, композиции шагов и
ответах цитатой. Каскад берёт первое и не платит вторым: правило, которое
не разобрало запрос, обязано промолчать, а не отказать.

Что здесь НЕ делается
---------------------
Каскад не исполняет утилиту сам и не переводит маршрут в пространство имён
исполняемого бинаря tools-core. Имена аргументов у публичного каталога
it-tools и у tools-core расходятся (у 52 из 76 инструментов, где аналог
вообще есть), то есть перевод — это ВТОРОЙ слой, который здесь никем не
измерен. Ошибка в нём превратила бы «правило ответило» в «правило соврало» —
ровно тот отказ, границу которого сторожит цифра 3,8 %. Поэтому исполнение
вынесено за шов `register_executor`: если кто-то умеет исполнять публичный
контракт it-tools, каскад отдаст ему разобранный вызов; если такого нет,
ответом остаётся сам разобранный вызов — то самое утверждение, которое
харнесс засчитывает как проверенный ответ режима A.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Слой измерен на русских запросах: во всех 630 задачах основного и отложенного
# наборов есть кириллица. Латиница сюда не приходила ни разу, значит поведение
# на ней не измерено — и запускать на ней разбор было бы утверждением без
# основания. Гейт дешёвый и сужает, а не расширяет: он умеет только промолчать.
_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")

# Предел длины запроса. В измеренном наборе самый длинный — 282 символа. Всё,
# что заметно длиннее, — это вставленный файл или лог, а не обращение к
# утилите; разбирать такое незачем, а стоимость токенизации растёт линейно.
# Порог с большим запасом, чтобы не отсечь ничего из измеренного.
MAX_QUERY_CHARS = 1000

_parser = None
_parser_lock = threading.Lock()
_executor: Optional[Callable[[str, dict], Any]] = None


@dataclass(frozen=True)
class RuleDecision:
    """Что решил слой правил. Неизменяемо: это протокол, а не рабочее место."""

    routed: bool
    tool_id: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    margin: float = 0.0
    evidence: tuple[str, ...] = ()
    #: человекочитаемое обоснование: чем подтверждён маршрут либо почему уступили
    reason: str = ""
    #: машинный код уступки, пустой при routed=True. Значения:
    #: "empty" | "not-russian" | "too-long" | "no-dictionary" | "no-parse" | "error"
    declined: str = ""
    latency_ms: float = 0.0

    def as_telemetry(self) -> dict[str, Any]:
        """Плоская запись для журналов и футера хода. Без свободного текста."""
        return {
            "routed": self.routed,
            "tool_id": self.tool_id,
            "arg_names": sorted(self.args),
            "score": round(self.score, 2),
            "margin": round(self.margin, 2),
            "declined": self.declined,
            "latency_ms": round(self.latency_ms, 2),
        }


def dependency_available() -> bool:
    """Установлен ли pymorphy3. Не поднимает словарь."""
    from .morph import available

    return available()


def catalog_size() -> int:
    """Сколько утилит в каталоге, по которому маршрутизирует слой."""
    from .lexicon import CATALOG_PATH

    with open(CATALOG_PATH, encoding="utf-8") as fh:
        return len(json.load(fh)["tools"])


# Планка уверенности: ниже неё разбор не берётся. Умолчания — ровно
# конфигурация R1, та, на которой измерены 3,8 % ложных ответов и 96,0 %
# выбора инструмента. Менять их значит менять то, к чему относятся цифры.
#
# ПОЧЕМУ планка вообще вынесена наружу. Как самостоятельная система слой
# правил обязан брать всё, что может: его отказ — это отказ пользователю. Как
# первый каскад он свободен брать меньше, потому что уступка бесплатна. Где
# именно проходит выгодная граница — зависит от того, КТО стоит вторым, и это
# измерено, а не предположено:
#
#   вторым сильный шлюз (400 задач):  12/1 -> ложные 4,0 %, инструмент 96,0 %
#                                     30/4 -> ложные 2,2 %, инструмент 95,0 %
#   вторым роутер 1.7B (250 задач):   12/1 -> ложные 8,8 %, инструмент 96,0 %
#                                     30/4 -> ложные 8,4 %, инструмент 91,0 %
#
# То есть строгая планка окупается только позади сильной второй ступени. У
# Digit вторая ступень — полный агент, поэтому оператору с сильной моделью
# планку поднимать выгодно; умолчание оставлено измеренным, а не смелым.
MIN_SCORE = float(os.environ.get("DIGIT_RULE_CASCADE_MIN_SCORE", "12.0"))
MIN_MARGIN = float(os.environ.get("DIGIT_RULE_CASCADE_MIN_MARGIN", "1.0"))


def _get_parser():
    """Один разборщик на процесс.

    Замок нужен потому, что первый вызов читает словарь ~0.2 с, а ходы Digit
    идут из разных потоков: без него два первых запроса построили бы два
    анализатора и заплатили бы за словарь дважды.
    """
    global _parser
    if _parser is None:
        with _parser_lock:
            if _parser is None:
                from .parser import RuleParser

                _parser = RuleParser(use_rules=True,
                                     min_score=MIN_SCORE,
                                     min_margin=MIN_MARGIN)
    return _parser


def route(query: str) -> RuleDecision:
    """Разобрать запрос правилами. Никогда не бросает исключений.

    Каскад, который падает, — это каскад, который отказал пользователю. Любая
    поломка внутри разбора обязана выглядеть как уступка модели, поэтому здесь
    голый `except Exception`: это не проглатывание ошибки (она уходит в
    журнал), а превращение её в «пропускаю ход».
    """
    t0 = time.perf_counter()

    def _decline(code: str, reason: str) -> RuleDecision:
        return RuleDecision(
            routed=False,
            declined=code,
            reason=reason,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    text = (query or "").strip()
    if not text:
        return _decline("empty", "пустой запрос")
    if len(text) > MAX_QUERY_CHARS:
        return _decline("too-long",
                        f"запрос длиннее {MAX_QUERY_CHARS} символов — это вставленный "
                        "текст, а не обращение к утилите")
    if not _CYRILLIC.search(text):
        return _decline("not-russian", "в запросе нет кириллицы; слой измерен на русском")
    if not dependency_available():
        return _decline("no-dictionary",
                        "нет pymorphy3 — морфология недоступна (digit install ruleparse)")

    try:
        parsed = _get_parser().parse(text)
    except Exception:
        logger.debug("слой правил не смог разобрать запрос; уступаю модели", exc_info=True)
        return _decline("error", "разбор упал")

    latency_ms = (time.perf_counter() - t0) * 1000.0
    if not parsed.ok:
        return RuleDecision(
            routed=False,
            tool_id=parsed.tool_id,
            score=parsed.score,
            margin=parsed.margin,
            declined="no-parse",
            reason=parsed.reason,
            latency_ms=latency_ms,
        )
    return RuleDecision(
        routed=True,
        tool_id=parsed.tool_id,
        args=dict(parsed.args),
        score=parsed.score,
        margin=parsed.margin,
        evidence=tuple(parsed.evidence),
        reason="; ".join(parsed.evidence) if parsed.evidence else "",
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Шов исполнения
# ---------------------------------------------------------------------------
def register_executor(fn: Optional[Callable[[str, dict], Any]]) -> None:
    """Назначить исполнителя публичного контракта it-tools.

    `fn(tool_id, args)` возвращает результат утилиты (что угодно сериализуемое)
    либо бросает исключение. Исключение означает «исполнить не вышло» и ведёт к
    уступке модели, а не к отказу пользователю: вернуть половину ответа хуже,
    чем не отвечать вовсе.

    Встроенного исполнителя Digit не поставляет — см. заголовок модуля.
    """
    global _executor
    _executor = fn


def has_executor() -> bool:
    return _executor is not None


def execute(decision: RuleDecision) -> Any:
    """Исполнить разобранный вызов. `None`, если исполнителя нет или он не смог."""
    if _executor is None or not decision.routed or not decision.tool_id:
        return None
    try:
        return _executor(decision.tool_id, dict(decision.args))
    except Exception:
        logger.debug("исполнитель правила упал на %s; уступаю модели",
                     decision.tool_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Показ
# ---------------------------------------------------------------------------
def render_answer(decision: RuleDecision, result: Any = None) -> str:
    """Текст ответа хода, когда ответило правило.

    Аргументы печатаются целиком и дословно — это и есть доказательство: их
    значения взяты из запроса пользователя посимвольно, а не восстановлены по
    смыслу. Читатель обязан иметь возможность сверить их глазами.
    """
    if not decision.routed or not decision.tool_id:
        return ""
    lines = [f"Инструмент: `{decision.tool_id}`"]
    if decision.args:
        lines.append("Аргументы:")
        for name in sorted(decision.args):
            value = decision.args[name]
            shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            lines.append(f"  - `{name}` = {shown}")
    else:
        lines.append("Аргументы не требуются.")
    if result is not None:
        lines.append("")
        lines.append("Результат:")
        lines.append(result if isinstance(result, str)
                     else json.dumps(result, ensure_ascii=False, indent=2))
    return "\n".join(lines)
