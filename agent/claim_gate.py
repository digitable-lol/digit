"""Гейт проверки суждений: ответ агента против настоящего компилятора FTS.

Что это и чего это НЕ делает
----------------------------
Модуль — политика, а не проверка. Проверяет `digit_cli.claimcheck` настоящим
компилятором в отдельном процессе; здесь только решается, когда его звать и что
делать с каждым из трёх исходов. Ровно так же устроены соседи по цепочке гейтов
хода (`agent/verification_stop.py`, `agent/kanban_stop.py`): политика отдельно,
проверка отдельно.

ПЕРЕКЛЮЧАТЕЛЬ ВЫКЛЮЧЕН ПО УМОЛЧАНИЮ, и это не осторожность, а измеренное
решение. У большинства пользователей компилятора FTS нет вовсе
(`optional-mcps/fts-gate` ставится отдельно), а гейт, который на каждом ходе
ничего не находит, — это только цена. Включается `agent.claim_gate: true` или
`DIGIT_CLAIM_GATE=1`.

Что делает каждый исход — и почему по-разному
---------------------------------------------
  проверить нечем (RuntimeMissing)  молчит. Пишет след в лог, отдаёт ход
                                    дальше, пользователю не говорит НИЧЕГО.
                                    Компилятор — чужая установка, и ход не
                                    имеет права ломаться из-за её отсутствия.
  не_формализовано                  молчит так же. «Не понял» — не «неверно»;
                                    догадка здесь была бы хуже отказа.
  проверено_неверно                 ВОЗВРАЩАЕТ ХОД МОДЕЛИ с контрпримером в
                                    синтетическом нудже. Это единственный
                                    случай, ради которого гейт заводится, и
                                    приглушить его значит потерять смысл.
  проверено_верно                    допечатывает происхождение к телу ответа
                                    вместе с границей LIMIT_NOTE. Без границы
                                    зелёный выглядит сильнее, чем он есть:
                                    проверена выводимость следствия, а не
                                    истинность посылки.

Откуда берётся спецификация — НЕ РЕШЕНО, и здесь это видно
----------------------------------------------------------
Соглашения о месте *.fts в дереве нет (10 файлов, все шаблоны навыков), и
выбирать его за владельца модуль не станет: гейт над случайно найденным файлом
проверял бы ответ против чужого правила. Поэтому `spec_source_for_turn`
возвращает текст ровно из двух мест, и оба назвал человек:

  1. `DIGIT_CLAIM_GATE_SPEC` / `agent.claim_gate_spec` — путь, названный
     настройкой;
  2. `agent._turn_claim_spec_source` — текст спецификации, который ход уже
     держит в руках (её прочитал или написал сам агент на этом ходе).

Ни поиска по каталогам, ни угадывания по имени: нет источника — гейт молчит,
как при отсутствии компилятора. Когда владелец назовёт соглашение, добавится
третья ветка, и больше ничего.

Цена, измеренная на стенде (см. tests/agent/test_claim_gate.py)
--------------------------------------------------------------
Гейт не тратит НИ ОДНОГО токена модели, пока не сработал: проверка идёт
локальным разбором и внешним процессом, в контекст ничего не добавляется.
Плата за ход, когда гейт выключен, — один вызов `claim_gate_enabled()`.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

#: Сколько раз за ход можно вернуть модели один и тот же провал.
MAX_CLAIM_GATE_NUDGES = 2

#: Сколько предложений ответа вообще рассматривается. Ответ агента бывает
#: длинным, а проверка суждений работает над ОБЪЯВЛЕННОЙ схемой — предложений,
#: которые она может формализовать, в ответе единицы. Верхняя граница стоит,
#: чтобы стоимость гейта не зависела от длины ответа.
MAX_STATEMENTS = 8

#: Дешёвый предфильтр: предложение попадает в проверку, только если в нём есть
#: условие или порог. Без него в claimcheck уезжал бы весь текст ответа, и
#: единственным исходом стало бы «не_формализовано» — то есть цена без пользы.
#: Фильтр НЕ решает, верно ли суждение; он решает, похоже ли оно на правило.
_CLAIM_MARKERS = re.compile(
    r"\b(если|когда|не\s+менее|не\s+более|не\s+меньше|не\s+больше|больше|"
    r"меньше|превыш)|\d\s*%|процент",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


class Verdict(NamedTuple):
    """Приговор гейта одному ходу."""

    #: "нечем" | "не_формализовано" | "проверено_неверно" | "проверено_верно"
    outcome: str
    #: Текст синтетического нуджа — только для «проверено_неверно».
    nudge: str | None
    #: Что допечатать к телу ответа — только для «проверено_верно».
    provenance: str | None
    #: Сколько суждений ушло в проверку.
    statements: int


SILENT = Verdict("нечем", None, None, 0)


def claim_gate_enabled(config: dict[str, Any] | None = None) -> bool:
    """Включён ли гейт проверки суждений. ПО УМОЛЧАНИЮ — НЕТ.

    Порядок как у verify-on-stop: явная переменная окружения
    ``DIGIT_CLAIM_GATE`` сильнее всего, затем ``agent.claim_gate`` из конфига,
    затем умолчание. Отличие ровно одно и оно намеренное: умолчание здесь не
    ``"auto"``, а False. «auto» у verify-on-stop знает, на какой поверхности
    полезен; про этот гейт такого замера НЕТ, и умолчание, выбранное смело,
    выдавало бы за измеренное то, что не измерено.
    """
    env = os.environ.get("DIGIT_CLAIM_GATE")
    if env is not None:
        return env.strip().lower() not in {"", "0", "false", "no", "off"}
    if config is None:
        try:
            from digit_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception:
            config = {}
    agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
    value = agent_cfg.get("claim_gate") if isinstance(agent_cfg, dict) else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
    return False


def spec_source_for_turn(agent: Any = None,
                         config: dict[str, Any] | None = None) -> str | None:
    """Текст спецификации, против которой проверять ответ, или None.

    Возвращается ТЕКСТ, а не путь: сам слой проверки пути не видит вовсе
    (`bridge.schema_of(source)` компилирует строку), и путь читает только
    обёртка командной строки. Поэтому у гейта нет и не должно быть требования
    к тому, где файл лежит.
    """
    source = getattr(agent, "_turn_claim_spec_source", None)
    if isinstance(source, str) and source.strip():
        return source
    path = os.environ.get("DIGIT_CLAIM_GATE_SPEC")
    if not path:
        if config is None:
            try:
                from digit_cli.config import load_config_readonly

                config = load_config_readonly()
            except Exception:
                config = {}
        agent_cfg = (config or {}).get("agent") if isinstance(config, dict) else None
        path = agent_cfg.get("claim_gate_spec") if isinstance(agent_cfg, dict) else None
    if not path:
        return None
    try:
        from pathlib import Path

        text = Path(str(path)).expanduser().read_text(encoding="utf-8")
    except Exception:
        # Названный, но непрочитанный файл — это «нечем», а не «неверно».
        logger.debug("claim gate: spec %s unreadable", path, exc_info=True)
        return None
    return text or None


def candidate_statements(text: str) -> list[str]:
    """Предложения ответа, похожие на правило. Порядок сохраняется.

    Порядок важен: контрпример называют по первому провалившемуся суждению, и
    пользователь сверяет его глазами с тем, что прочитал первым.
    """
    if not text or not text.strip():
        return []
    out: list[str] = []
    for chunk in _SENTENCE_SPLIT.split(text):
        piece = chunk.strip(" \t\r\n-*•>#")
        if not piece or len(piece) < 12:
            continue
        if not _CLAIM_MARKERS.search(piece):
            continue
        out.append(piece)
        if len(out) >= MAX_STATEMENTS:
            break
    return out


def evaluate(answer_text: str, spec_source: str | None) -> Verdict:
    """Прогнать ответ через проверку суждений. Никогда не поднимает исключений.

    Любая неожиданность — это «нечем»: гейт обязан быть fail-open, как хуки
    (`agent/shell_hooks.py`), потому что цена его поломки — сломанный ход, а
    цена его молчания — отсутствие пользы, которой и так не было.
    """
    if not spec_source:
        return SILENT
    statements = candidate_statements(answer_text)
    if not statements:
        return SILENT
    try:
        from digit_cli import claimcheck

        if not claimcheck.runtime_available():
            logger.debug("claim gate: compiler unavailable, staying silent")
            return SILENT
        prepared = _schema_of(claimcheck, spec_source)
        if prepared is None:
            return SILENT
        schema, utility, category = prepared
        # Объявленные правила расчёта передаются ВМЕСТЕ с проверяемым
        # суждением: без них половина детектора слепа (см. pipeline.answer) —
        # непокрытая ветка, перекрытое правило и нарушенное свойство суть
        # утверждения о ПАРЕ «новое и уже объявленное».
        result = claimcheck.answer(
            statements, schema, category,
            base_rules=utility.get("rules"),
            base_properties=utility.get("properties"),
            base_examples=utility.get("examples"),
        )
    except Exception:
        logger.debug("claim gate: check failed, staying silent", exc_info=True)
        return SILENT

    outcome = str(result.get("outcome") or "")
    note = str(result.get("note") or "")
    if outcome == "проверено_неверно":
        return Verdict(outcome, _nudge_text(result), None, len(statements))
    if outcome == "проверено_верно":
        reading = result.get("reading") or []
        lines = ["Проверка суждений: ПРОВЕРЕНО И ВЕРНО настоящим компилятором FTS."]
        for line in reading:
            lines.append(f"  прочитано так: {line}")
        if note:
            lines.append(note)
        return Verdict(outcome, None, "\n".join(lines), len(statements))
    return Verdict("не_формализовано", None, None, len(statements))


def _schema_of(claimcheck: Any, source: str):
    """Схема, расчёт и домен из текста спецификации, или None.

    Перевод «документ компилятора -> схема» НЕ повторяется здесь: зовётся та
    же функция, что у `digit rule-check` (`claimcheck.document`). Иначе у
    команды и у гейта завелись бы две версии того, что значит «необязательное
    поле», и расхождение было бы невидимо обеим.

    Любая неудача — «нечем», а не «неверно»: неоднозначно названный расчёт и
    непустая ошибка компиляции суть отсутствие проверки, и говорить о них
    пользователю голосом отвергнутого утверждения было бы прямой ложью.
    """
    try:
        document = claimcheck.schema_of(source)
    except claimcheck.RuntimeMissing:
        logger.debug("claim gate: RuntimeMissing on schema_of")
        return None
    except Exception:
        logger.debug("claim gate: schema_of failed", exc_info=True)
        return None
    try:
        return claimcheck.schema_of_document(document)
    except Exception:
        logger.debug("claim gate: document has no single checkable utility",
                     exc_info=True)
        return None


def _nudge_text(result: dict) -> str:
    """Синтетический нудж для «проверено и неверно».

    Внутрь кладётся ровно то, что нашёл компилятор: код дефекта, нарушенное
    свойство и КОНТРПРИМЕР. Без контрпримера нудж превращается в «перепроверь
    себя», а это ровно та просьба, на которую модель отвечает уверением.
    """
    parts = [
        "СТОП: проверка суждений отвергла утверждение из твоего ответа.",
        "Проверял настоящий компилятор FTS, а не ты и не я.",
    ]
    for line in (result.get("reading") or []):
        parts.append(f"прочитано так: {line}")
    # Имена полей взяты у самого приговора, а не угаданы: `failed_check`
    # отличается от `verdict` ровно тем, ради чего нудж и существует — в его
    # `detail` стоит КОНТРПРИМЕР. Проверено запуском настоящего компилятора
    # (tests/agent/test_claim_gate.py::test_end_to_end_against_the_real_compiler),
    # и первая, угаданная по смыслу раскладка ключей тем прогоном и упала.
    failed = result.get("failed_check") or result.get("verdict") or {}
    if isinstance(failed, dict):
        if failed.get("code"):
            parts.append(f"код дефекта: {failed['code']}")
        if failed.get("stage"):
            parts.append(f"упало на: {failed['stage']}")
        if failed.get("detail"):
            parts.append(f"КОНТРПРИМЕР И ПРИЧИНА: {failed['detail']}")
    parts.append(
        "Исправь утверждение или сними его. Не пересказывай проверку как "
        "пройденную и не объясняй контрпример словами вместо правки."
    )
    if result.get("note"):
        parts.append(str(result["note"]))
    return "\n".join(parts)


def build_claim_gate_nudge(*, answer_text: str, spec_source: str | None,
                           attempts: int = 0,
                           max_attempts: int = MAX_CLAIM_GATE_NUDGES) -> str | None:
    """Нудж для цепочки гейтов хода или None. Форма — как у соседей.

    Ограничение попыток стоит здесь, а не у вызывающего: гейт, который спорит
    с моделью бесконечно, хуже отсутствующего — он не даёт ходу кончиться.
    """
    if attempts >= max_attempts:
        return None
    return evaluate(answer_text, spec_source).nudge
