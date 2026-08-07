"""Слой 3 — ДОГАДКА: что владелец, возможно, ответил бы. Только владельцу.

Почему этот слой отделён от остальных
-------------------------------------
Digit держится на том, что содержание не сочиняется моделью: у ответа есть
источник, и его можно проверить. Двойник по определению делает обратное — он
порождает текст от чужого имени там, где этот человек ничего не говорил.
Совместить это с остальной системой нельзя; можно только огородить.

Ограда состоит из трёх вещей, и все три — в коде:

1. **Догадка не хранится.** В :mod:`agent.portrait.store` нет для неё файла и
   нет функции записи. Записанная догадка через неделю неотличима от записи
   решения — а различать их и есть вся задача.
2. **Догадка не уходит наружу.** :func:`agent.portrait.provenance.outward_safe`
   на неё не «ставит флажок», а бросает исключение. Есть ровно один
   получатель — сам владелец.
3. **Догадку сочиняет не этот модуль.** Здесь собирается *бриф*: ограничения
   стиля (слой 1) и относящиеся к вопросу решения (слой 2, с цитатами).
   Текст пишет модель — снаружи, видя бриф. Так у догадки всегда видно, из
   чего она выведена, и видно, где кончается выведенное.

Что в брифе
-----------
``style``   — как формулировать: длина, лексика, формы отказа и согласия.
``records`` — что владелец уже решал по этому поводу, дословно, со ссылками.
``gaps``    — чего в портрете нет. Это не вежливость: пустой раздел решений
              означает, что весь ответ будет сочинён целиком, и владелец
              должен видеть это до того, как прочтёт красивый абзац.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .decisions import DecisionRecord
from .provenance import Origin, outward_safe, render
from .style import StyleProfile

BANNER = (
    "⚠ ЭТО НЕ СЛОВА ВЛАДЕЛЬЦА. Ниже — реконструкция: модель пишет за него, "
    "опираясь на измеренный стиль и на прежние решения. Показывать это кому-то, "
    "кроме владельца, нельзя — ни целиком, ни цитатой."
)


@dataclass
class Conjecture:
    """Слой 3: догадка о том, что владелец ответил бы. Не хранится нигде."""

    origin = Origin.CONJECTURE

    question: str
    text: Optional[str] = None

    def as_text(self) -> str:
        if not self.text:
            return (
                f"Догадка по вопросу «{self.question}» не составлена: этот "
                "модуль текст не сочиняет, он готовит бриф. Заполнить должна "
                "модель, видя бриф целиком."
            )
        return f"{BANNER}\n\n{self.text}"


@dataclass
class Draft:
    """Ответ «в манере владельца», разобранный на три слоя.

    Класс существует ради одного: чтобы «в манере владельца» нельзя было
    отдать одним куском текста. Слои лежат отдельными полями, и у каждого
    поля свой получатель — :meth:`for_owner` и :meth:`for_outside`.
    """

    question: str
    style: StyleProfile
    records: List[DecisionRecord] = field(default_factory=list)
    conjecture: Optional[Conjecture] = None
    gaps: List[str] = field(default_factory=list)

    def for_owner(self) -> str:
        """Всё, что есть, с метками слоёв. Только владельцу."""
        blocks = [render(self.style)]
        if self.records:
            body = "\n\n".join(record.as_text() for record in self.records)
            blocks.append(f"[{_record_label()}]\n{body}")
        else:
            blocks.append(
                f"[{_record_label()}]\nНичего: в портрете нет решений по этому "
                "вопросу. Всё, что будет сказано ниже, — сочинено."
            )
        if self.conjecture is not None:
            blocks.append(render(self.conjecture))
        if self.gaps:
            blocks.append("[ПРОБЕЛЫ]\n" + "\n".join(f"— {g}" for g in self.gaps))
        return "\n\n".join(blocks)

    def for_outside(self) -> str:
        """То, что можно показать не владельцу: только решения со ссылками.

        Проходит через :func:`outward_safe`, а не через ``if``: фильтр,
        написанный здесь руками, однажды разойдётся с определением слоёв.
        Если в черновике каким-то образом окажется догадка — вызов упадёт,
        и это правильное поведение.
        """
        allowed = outward_safe(self.records)
        if not allowed:
            return (
                "По этому вопросу в портрете нет ни одного зафиксированного "
                "решения владельца. Сказать от его имени нечего."
            )
        lines = ["Владелец решал по этому поводу следующее:"]
        for record in allowed:
            lines.append("")
            lines.append(record.as_text())
        return "\n".join(lines)


def _record_label() -> str:
    from .provenance import label

    return label(Origin.RECORD)


def build(
    question: str,
    style: StyleProfile,
    records: List[DecisionRecord],
) -> Draft:
    """Собрать черновик ответа в манере владельца.

    Ничего не сочиняет: слот догадки остаётся пустым, а его заполнение —
    отдельный, видимый шаг снаружи.
    """
    gaps: List[str] = []
    if style.messages == 0:
        gaps.append("стиль не измерен — воспроизводить нечего")
    elif not style.established:
        gaps.append(
            f"стиль измерен на {style.messages} сообщениях — этого мало для "
            "устойчивых средних"
        )
    if not records:
        gaps.append(
            "решений по этому вопросу нет — поиск лексический, попробуйте "
            "другие слова, прежде чем считать, что решений не было"
        )
    else:
        superseded = [r for r in records if r.superseded_by]
        if superseded:
            gaps.append(
                f"{len(superseded)} из найденных решений отменены более "
                "поздними — смотрите даты"
            )
        if all(r.evidence == "сказано" for r in records):
            gaps.append(
                "все найденные решения только произнесены; действий за ними "
                "в тех ходах не было"
            )
    return Draft(
        question=question,
        style=style,
        records=records,
        conjecture=Conjecture(question=question),
        gaps=gaps,
    )


def brief_for_model(draft: Draft) -> str:
    """Инструкция модели, которая будет заполнять слот догадки.

    Отдаётся модели вместе с вопросом. Здесь же — единственное место, где
    сказано, чего в догадке быть не должно: выдуманных фактов. Стиль
    воспроизводить можно, содержание — только из слоя решений.
    """
    parts = [
        "Задача: написать, что владелец МОГ БЫ ответить. Это догадка, и она "
        "будет показана только ему самому.",
        "",
        "Воспроизводи ФОРМУ по измеренному стилю: длину фраз, лексику, "
        "формулировки отказа и согласия.",
        "СОДЕРЖАНИЕ бери только из раздела решений. Ни одного факта, которого "
        "там нет: догадка о манере — допустима, догадка о фактах — это ложь "
        "от его имени.",
        "Если решений по вопросу нет — так и напиши, вместо ответа.",
        "",
        render(draft.style),
        "",
    ]
    if draft.records:
        parts.append("Прежние решения владельца по этому вопросу:")
        for record in draft.records:
            parts.append(record.as_text())
    else:
        parts.append("Прежних решений по этому вопросу в портрете нет.")
    return "\n".join(parts)
