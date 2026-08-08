"""«Забудь этот разговор» — целиком, а не наполовину.

Почему это отдельный модуль
---------------------------
Забывание — единственная операция, которая обязана пройти по ВСЕМУ портрету:
по журналу решений, по агрегату стиля, по производному индексу. Пока она
жила в одной ветке CLI, она честно чистила журнал и молча оставляла стиль:
словарь, длины, зачины, формулировки речевых актов того разговора оставались
в портрете навсегда. Формально команда отрабатывала, фактически обещание было
ложным — а портрет это самые чувствительные данные в системе, и обещание про
них должно быть точным.

Как это стало возможным
-----------------------
Стиль — сумма достаточных статистик, и сумму можно разобрать обратно, если
слагаемые сохранены. Поэтому рядом с агрегатом теперь лежит посессионный срез
(``portrait/sessions/``): те же счётчики, но по одной сессии. Забывание
вычитает срез из агрегата и удаляет сам срез.

Чего это НЕ делает и почему так сказано вслух
---------------------------------------------
Сессия, наблюдавшаяся до появления срезов, вычитается только вместе со всем
портретом. Соврать здесь нельзя: «убрано» про то, что осталось, — это ровно
та ложь, ради которой всё и переделывалось. Команда считает такие сообщения и
называет их число.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import index as index_mod
from . import store
from . import style as style_mod

logger = logging.getLogger(__name__)


def unattributed_messages() -> int:
    """Сколько наблюдённых сообщений не привязано ни к одной сессии.

    Считается разностью, а не счётчиком: так в неё честно попадает и то, что
    накопилось до появления посессионных срезов, — а именно оно и не
    вычитается.
    """
    stats = store.read_json(store.STYLE_FILE)
    total = style_mod.observed_messages(stats)
    known = sum(
        int((slice_.get("owner") or {}).get("messages", 0))
        for slice_ in store.iter_session_slices()
    )
    return max(0, total - known)


def matching_sessions(prefix: str) -> List[str]:
    """Идентификаторы сессий, начинающиеся с ``prefix``."""
    return sorted(
        str(slice_["session"])
        for slice_ in store.iter_session_slices()
        if str(slice_["session"]).startswith(prefix)
    )


def session(prefix: str) -> Dict[str, Any]:
    """Убрать из портрета всё, что принесли сессии с этим префиксом.

    Возвращает отчёт: он же и есть ответ на вопрос «что именно убрано», и
    команда печатает его дословно, не пересказывая.
    """
    if not prefix:
        raise ValueError("нужен идентификатор сессии")

    before = style_mod.observed_messages(store.read_json(store.STYLE_FILE))

    # 1. Журнал решений. Перезапись — единственное место, где нарушается
    #    «только дописывание», и существует оно ровно затем, зачем нужно.
    rows = list(store.iter_lines(store.DECISIONS_FILE))
    kept_rows = [
        row for row in rows
        if not (row.get("t") == "d"
                and str(row.get("session", "")).startswith(prefix))
    ]
    dropped_ids = {
        str(row.get("id", "")) for row in rows
        if row.get("t") == "d" and str(row.get("session", "")).startswith(prefix)
    }
    # Отмены, ссылающиеся на удалённые записи, — ссылки в пустоту: они
    # переживают свою запись и при чтении журнала молча ни к чему не
    # приводят. Убираются вместе с ней.
    kept_rows = [
        row for row in kept_rows
        if not (row.get("t") == "s" and str(row.get("id", "")) in dropped_ids)
    ]
    dropped_decisions = len(rows) - len(kept_rows)
    if dropped_decisions:
        store.rewrite_lines(store.DECISIONS_FILE, kept_rows)

    # 2. Агрегат стиля. Вычитается ровно то, что принесла сессия.
    sessions = matching_sessions(prefix)
    stats = store.read_json(store.STYLE_FILE)
    for session_id in sessions:
        slice_ = store.read_session(session_id)
        if slice_ is None:
            continue
        if stats is not None:
            style_mod.subtract(stats, slice_)
        store.drop_session(session_id)
    if stats is not None and sessions:
        _renew_seen(stats)
        store.write_json(store.STYLE_FILE, stats)

    # 3. Производный индекс. Он мирится с журналом сам — сверка по отпечатку
    #    увидит перезапись, — но ждать первого поиска значит держать на диске
    #    забытые цитаты дольше, чем нужно. Индекс сносится сразу.
    index = index_mod.open_index()
    if index is not None:
        index.drop()
    index_mod.forget()

    after = style_mod.observed_messages(store.read_json(store.STYLE_FILE))
    return {
        "сессий": len(sessions),
        "идентификаторы": sessions,
        "записей решений убрано": dropped_decisions,
        "сообщений вычтено из стиля": before - after,
        "сообщений осталось в стиле": after,
        "не привязано к сессиям": unattributed_messages(),
    }


def _renew_seen(stats: Dict[str, Any]) -> None:
    """Пересчитать границы наблюдения по оставшимся срезам.

    Оставить прежние значило бы сказать «последний раз наблюдалось тогда-то»
    про разговор, который только что забыт.
    """
    firsts = []
    lasts = []
    for slice_ in store.iter_session_slices():
        if slice_.get("first_seen"):
            firsts.append(int(slice_["first_seen"]))
        if slice_.get("last_seen"):
            lasts.append(int(slice_["last_seen"]))
    if firsts:
        stats["first_seen"] = min(firsts)
        stats["last_seen"] = max(lasts) if lasts else stats.get("last_seen")
    elif style_mod.observed_messages(stats) == 0:
        stats["first_seen"] = None
        stats["last_seen"] = None


def everything() -> int:
    """Удалить портрет целиком. Возвращает число удалённых файлов."""
    index_mod.forget()
    return store.wipe()
