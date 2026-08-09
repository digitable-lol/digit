#!/usr/bin/env python3
"""``tasks.py`` — карта Excalidraw и список задач Taskwarrior как одно и то же.

ЗАЧЕМ ЭТО НУЖНО
---------------
Схему проекта рисуют не для красоты: на ней уже сказано всё, что потом руками
переносят в трекер. Прямоугольник — работа. Стрелка — «сначала это, потом то».
Рамка — область. Цвет — «это горит». Перенос делает человек, и делает его
дважды: сначала в трекер, потом обратно, когда на схеме что-то поменялось.

Здесь перенос делает код. Модель выбирает утилиту и называет файл; разбор,
порядок, зависимости и приоритеты — детерминированные, как и весь каталог
Digit. Один и тот же файл, разобранный дважды, даёт один и тот же список задач,
включая uuid: они выводятся из ``id`` элемента Excalidraw, а не выдаются по
порядку.

ЧЕГО ЗДЕСЬ БОЯТСЯ
-----------------
**Молчаливой потери — в обе стороны.** У соседних скриптов она одна (испортить
файл владельца); здесь их две, и вторая дороже: испортить *базу задач*, в
которой работают и владелец, и другие агенты.

Отсюда четыре решения, каждое куплено измерением на taskwarrior 2.6.2:

1. **Запись идёт только через ``task import`` полной записи.** Не через
   ``modify``. Причина буквальная: у ``task <uuid> modify excalidraw:box1``,
   когда UDA не объявлена в rc, taskwarrior не ругается — он молча пишет
   ``excalid:box1`` **в описание задачи**. Описание при этом теряется. Утилита,
   которая ходит в чужую базу, не может позволить себе синтаксис, у которого
   опечатка стирает данные.

2. **Перед записью запись читается, и правки кладутся поверх прочитанного.**
   ``task import`` существующего uuid — это *замена*, а не слияние: запись,
   поданная без ``annotations``, оставляет задачу без аннотаций, и об этом
   ничего не сообщается. Поэтому здесь read-merge-import, а не import.

3. **Циклы ловятся до записи, а не во время.** Taskwarrior отказывает на
   ``depends``, замыкающем круг («Circular dependency detected»), — но отказывает
   на той задаче, до которой дошёл. Половина зависимостей уже проставлена,
   половина нет. Разбор карты проверяет ацикличность целиком и отказывается
   писать что-либо, пока круг не разорван.

4. **Ключ соответствия — ``id`` элемента Excalidraw**, он же UDA ``excalidraw``
   на задаче. Не описание: описание правят. Не номер: номер позиционный. UDA
   переживает и ``modify``, и ``done``, и не требует объявления в rc владельца,
   если писать её через ``import`` (проверено).

ЧТО ВО ЧТО ПЕРЕВОДИТСЯ
----------------------
======================  ==================================================
Фигура с подписью       задача; подпись — описание
Стрелка A → B           B зависит от A (``depends``)
Рамка (frame)           проект; имя рамки — имя проекта
Группа                  проект второго уровня; имя — свободный текст в группе
Заливка фигуры          приоритет (таблица ниже, меняется ``--priority-colors``)
``!H`` / ``!M`` / ``!L``  приоритет явной меткой; сильнее цвета
======================  ==================================================

Проект собирается как ``рамка.группа`` — ровно два уровня, глубже не бывает:
точки внутри имён заменяются на дефис. Это не украшение, а требование
модели-маршрутизатора Digit к плоским схемам (``digit-integrations.md``).

Использование
-------------
    tasks.py from-map КАРТА.excalidraw [--data-dir DIR] [--dry-run]
    tasks.py schema [--json]
    tasks.py --self-test

Коды возврата: 0 — сделано, 1 — отказ с причиной, 2 — плохие аргументы.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Договорённости
# --------------------------------------------------------------------------

#: Имя UDA, в которой на задаче лежит ``id`` её элемента на карте.
UDA = "excalidraw"

#: Пространство имён для uuid5: одинаковый элемент даёт одинаковую задачу при
#: любом числе прогонов и на любой машине.
NAMESPACE = _uuid.UUID("6f1f1f7a-5f2a-5c3e-9c1b-0d5e7a3b8c11")

#: Заливка → приоритет. Значения взяты из ``references/colors.md`` этого же
#: скилла, чтобы карта, нарисованная по палитре скилла, читалась без настройки.
#: Решение владельца: какой цвет что значит. Меняется ``--priority-colors``.
DEFAULT_PRIORITY_COLORS: Dict[str, str] = {
    "#ffc9c9": "H",   # светло-красный — «error, critical»
    "#ffd8a8": "M",   # светло-оранжевый — «warning, pending»
    "#fff3bf": "L",   # светло-жёлтый — «notes, decisions, planning»
}

#: Фигуры, которые могут быть задачей. Стрелка и линия — связи, текст —
#: подпись, рамка — область; задачей не является ни одно из них.
TASK_SHAPES = ("rectangle", "ellipse", "diamond")

#: Явная метка приоритета в подписи: сильнее цвета, потому что её написал
#: человек, а цвет мог остаться от предыдущей фигуры.
PRIORITY_MARKS = {"!h": "H", "!m": "M", "!l": "L"}


class Refused(RuntimeError):
    """Работа не сделана, и причину стоит напечатать."""


# --------------------------------------------------------------------------
# Чтение карты
# --------------------------------------------------------------------------


def load_map(path: Path) -> Dict[str, Any]:
    """Разобрать ``.excalidraw``, сохраняя порядок ключей."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"не читается {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} — не JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise Refused(f"{path} — не документ Excalidraw (нет массива elements)")
    return document


def index(document: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``id`` → элемент, только по живым элементам."""
    result: Dict[str, Dict[str, Any]] = {}
    for element in document["elements"]:
        if isinstance(element, dict) and isinstance(element.get("id"), str):
            result[element["id"]] = element
    return result


def visible(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Элементы, которые рисует само приложение: без ``isDeleted``."""
    return [
        e for e in document["elements"]
        if isinstance(e, dict) and not e.get("isDeleted")
    ]


def box_of(element: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """Прямоугольник элемента: ``x0, y0, x1, y1``."""
    x = float(element.get("x") or 0.0)
    y = float(element.get("y") or 0.0)
    return x, y, x + float(element.get("width") or 0.0), y + float(
        element.get("height") or 0.0)


def _inside(inner: Dict[str, Any], outer: Dict[str, Any]) -> bool:
    """Лежит ли центр ``inner`` внутри ``outer``."""
    ix0, iy0, ix1, iy1 = box_of(inner)
    ox0, oy0, ox1, oy1 = box_of(outer)
    cx, cy = (ix0 + ix1) / 2, (iy0 + iy1) / 2
    return ox0 <= cx <= ox1 and oy0 <= cy <= oy1


def _starts_inside(text: Dict[str, Any], shape: Dict[str, Any]) -> bool:
    """Начинается ли надпись внутри фигуры.

    Для подписи важен левый верхний угол, а не центр. Длинный заголовок
    перерастает карточку вниз — приложение растит текст по ``autoResize``, — и
    его центр оказывается уже под ней. По центру такая подпись «теряется», и
    карточка читается как фигура без подписи, хотя подпись на ней написана.
    """
    tx, ty, _tx1, _ty1 = box_of(text)
    sx0, sy0, sx1, sy1 = box_of(shape)
    return sx0 <= tx <= sx1 and sy0 <= ty <= sy1


def label_of(element: Optional[Dict[str, Any]],
             by_id: Dict[str, Dict[str, Any]],
             free_texts: Sequence[Dict[str, Any]] = ()) -> str:
    """Подпись фигуры: своя, привязанного текста или надписи внутри неё.

    Первые два случая — как в ``render.py``: у фигуры собственного текста нет,
    он лежит отдельным элементом с ``containerId``, а фигура ссылается на него
    через ``boundElements``.

    Третий случай нужен из-за того, как устроены библиотеки Мастерской:
    у «Карточки задачи» заголовок — **свободный** текст внутри карточки, а не
    привязанный. Читать только привязанные значило бы не понимать собственную
    библиотеку: карта, нарисованная её же фигурами, разбиралась бы в пустоту.
    Берётся самая верхняя надпись, чей центр лежит внутри фигуры.
    """
    if not isinstance(element, dict):
        return ""
    own = element.get("text")
    if isinstance(own, str) and own.strip():
        return " ".join(own.split())
    bound = element.get("boundElements")
    if isinstance(bound, list):
        for entry in bound:
            if not isinstance(entry, dict):
                continue
            child = by_id.get(entry.get("id"))
            if isinstance(child, dict) and isinstance(child.get("text"), str):
                if child["text"].strip():
                    return " ".join(child["text"].split())
    inside = [t for t in free_texts if _starts_inside(t, element)]
    if inside:
        inside.sort(key=lambda t: (float(t.get("y") or 0.0), float(t.get("x") or 0.0)))
        return " ".join(str(inside[0].get("text") or "").split())
    return ""


def _resolve_shape(element_id: Any,
                   by_id: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Довести привязку стрелки до фигуры.

    Стрелку можно привязать к подписи внутри фигуры; для зависимости важна
    фигура, а не её текст, поэтому подпись разворачивается в контейнер.
    """
    element = by_id.get(element_id) if isinstance(element_id, str) else None
    if not isinstance(element, dict):
        return None
    if element.get("type") == "text" and isinstance(element.get("containerId"), str):
        element = by_id.get(element["containerId"]) or element
    return element if isinstance(element, dict) else None


# --------------------------------------------------------------------------
# Разбор: проект, приоритет, описание
# --------------------------------------------------------------------------


def _flat(name: str) -> str:
    """Имя одного уровня проекта.

    Точка в Taskwarrior — разделитель уровней. Точка внутри имени рамки сделала
    бы проект глубже двух уровней, и требование плоских схем перестало бы
    выполняться на первой же рамке с названием вроде «этап 2.1».
    """
    return " ".join(str(name).replace(".", "-").split()).strip()


def project_of(element: Dict[str, Any],
               by_id: Dict[str, Dict[str, Any]],
               group_names: Dict[str, str]) -> Optional[str]:
    """Проект задачи: ``рамка.группа``, ровно два уровня.

    Рамка даёт первый уровень, группа — второй. Если есть только одно из двух,
    уровень один. Если нет ничего — проекта нет, и задача попадёт туда, куда
    её положит вызывающий (``--project``).
    """
    parts: List[str] = []

    frame = by_id.get(element.get("frameId")) if isinstance(element.get("frameId"), str) else None
    if isinstance(frame, dict):
        name = _flat(frame.get("name") or label_of(frame, by_id))
        if name:
            parts.append(name)

    groups = element.get("groupIds")
    if isinstance(groups, list) and groups:
        # groupIds идёт от внутренней группы к внешней; область — внешняя.
        for group_id in reversed(groups):
            name = group_names.get(group_id)
            if name:
                parts.append(_flat(name))
                break

    return ".".join(parts[:2]) if parts else None


def nested_shapes(shapes: Sequence[Dict[str, Any]]) -> set:
    """Фигуры, лежащие внутри других фигур, — они не самостоятельны.

    «Внутри» здесь — центр внутри чужого прямоугольника и площадь строго
    меньше. Сравнение по площади нужно, чтобы две совпадающие фигуры не
    объявили друг друга вложенными и не исчезли обе.
    """
    inner = set()
    for shape in shapes:
        area = float(shape.get("width") or 0) * float(shape.get("height") or 0)
        for other in shapes:
            if other is shape:
                continue
            bigger = float(other.get("width") or 0) * float(other.get("height") or 0)
            if bigger > area and _inside(shape, other):
                inner.add(shape["id"])
                break
    return inner


def free_texts_of(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Надписи, не привязанные ни к какой фигуре."""
    return [
        e for e in visible(document)
        if e.get("type") == "text" and not e.get("containerId")
        and str(e.get("text") or "").strip()
    ]


def group_names_of(document: Dict[str, Any]) -> Dict[str, str]:
    """Имя группы — свободная надпись **вне** фигур, лежащая в этой же группе.

    У группы в Excalidraw нет имени: это просто общий ``groupIds`` у нескольких
    элементов. Единственная подпись, которую человек может ей дать, — надпись
    рядом, включённая в ту же группу.

    «Вне фигур» — не придирка, а разделение двух ролей одного и того же вида
    элемента. Заголовок карточки из библиотеки Мастерской — тоже свободный
    текст и тоже в группе карточки; если считать его именем группы, каждая
    карточка объявит собственный проект со своим же названием.
    """
    shapes = [e for e in visible(document) if e.get("type") in TASK_SHAPES]
    names: Dict[str, str] = {}
    for element in free_texts_of(document):
        if any(_starts_inside(element, shape) for shape in shapes):
            continue
        text = " ".join(str(element.get("text") or "").split())
        groups = element.get("groupIds")
        if not isinstance(groups, list):
            continue
        for group_id in groups:
            names.setdefault(group_id, text)
    return names


def split_priority(text: str, fill: Any,
                   colors: Dict[str, str]) -> Tuple[str, Optional[str]]:
    """Разделить подпись на описание и приоритет.

    Явная метка сильнее цвета: её поставил человек именно на этой фигуре, а
    заливка могла достаться от той, с которой фигуру скопировали.
    """
    words = text.split()
    priority: Optional[str] = None
    if words and words[-1].lower() in PRIORITY_MARKS:
        priority = PRIORITY_MARKS[words[-1].lower()]
        words = words[:-1]
    if priority is None and isinstance(fill, str):
        priority = colors.get(fill.strip().lower())
    return " ".join(words), priority


def task_uuid_for(element_id: str) -> str:
    """uuid задачи, выведенный из ``id`` элемента.

    Детерминированно, чтобы повторный разбор той же карты на другой машине или
    в другой базе давал те же задачи, а не их копии.
    """
    return str(_uuid.uuid5(NAMESPACE, f"excalidraw:{element_id}"))


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------


def parse_map(document: Dict[str, Any], *,
              colors: Optional[Dict[str, str]] = None,
              default_project: Optional[str] = None) -> Dict[str, Any]:
    """Карта → план: узлы, зависимости и то, что осталось непонятым.

    Ничего не пропускается молча. Фигура без подписи, стрелка, привязанная
    только одним концом, стрелка в саму себя — всё это попадает в ``notes``, а
    не исчезает: на картинке они выглядят ровно так же, как понятые.
    """
    palette = {k.lower(): v for k, v in (colors or DEFAULT_PRIORITY_COLORS).items()}
    by_id = index(document)
    groups = group_names_of(document)
    free = free_texts_of(document)
    notes: List[str] = []

    shapes = [e for e in visible(document) if e.get("type") in TASK_SHAPES]
    nested = nested_shapes(shapes)

    nodes: Dict[str, Dict[str, Any]] = {}
    for element in shapes:
        if element["id"] in nested:
            # Фигура внутри фигуры — не вторая задача. У «Карточки задачи» из
            # библиотеки Мастерской внутри лежит плашка статуса, и она тоже
            # прямоугольник с подписью; читать её как задачу значило бы
            # заводить задачу «к работе» на каждую карточку. Тот же вывод — в
            # разборе схем категорий на «Архитекторе»: прямоугольник в
            # прямоугольнике это привычка группировать, а не обозначение.
            continue
        label = label_of(element, by_id, free)
        if not label:
            notes.append(
                f"фигура {element.get('id')} ({element.get('type')}) без подписи — "
                f"пропущена: у задачи должно быть описание"
            )
            continue
        description, priority = split_priority(
            label, element.get("backgroundColor"), palette)
        if not description:
            notes.append(
                f"фигура {element.get('id')}: в подписи только метка приоритета — "
                f"пропущена")
            continue
        nodes[element["id"]] = {
            "element": element["id"],
            "uuid": task_uuid_for(element["id"]),
            "description": description,
            "project": project_of(element, by_id, groups) or default_project,
            "priority": priority,
        }

    links: List[Dict[str, str]] = []
    for element in visible(document):
        if element.get("type") != "arrow":
            continue
        start = _resolve_shape((element.get("startBinding") or {}).get("elementId")
                               if isinstance(element.get("startBinding"), dict) else None,
                               by_id)
        end = _resolve_shape((element.get("endBinding") or {}).get("elementId")
                             if isinstance(element.get("endBinding"), dict) else None,
                             by_id)
        start_id = (start or {}).get("id")
        end_id = (end or {}).get("id")
        if start_id not in nodes or end_id not in nodes:
            # Стрелка рядом с фигурами, но не привязанная к ним, выглядит
            # связью и связью не является: сдвиньте фигуру — она останется.
            notes.append(
                f"стрелка {element.get('id')} не соединяет две задачи "
                f"(начало: {start_id or 'не привязано'}, конец: "
                f"{end_id or 'не привязано'}) — зависимости из неё нет")
            continue
        if start_id == end_id:
            notes.append(f"стрелка {element.get('id')} замкнута на себя — пропущена")
            continue
        links.append({"from": start_id, "to": end_id})

    cycle = find_cycle(nodes, links)
    return {"nodes": nodes, "links": links, "notes": notes, "cycle": cycle}


def find_cycle(nodes: Dict[str, Dict[str, Any]],
               links: Sequence[Dict[str, str]]) -> List[str]:
    """Круг в зависимостях, если он есть, — иначе пустой список.

    Проверяется до записи целиком. Taskwarrior тоже откажет, но откажет на той
    задаче, до которой дошёл, оставив половину зависимостей проставленной.
    """
    outgoing: Dict[str, List[str]] = {node: [] for node in nodes}
    for link in links:
        outgoing.setdefault(link["from"], []).append(link["to"])

    WHITE, GREY, BLACK = 0, 1, 2
    color = {node: WHITE for node in outgoing}
    stack: List[str] = []

    def walk(node: str) -> List[str]:
        color[node] = GREY
        stack.append(node)
        for neighbour in outgoing.get(node, ()):
            if color.get(neighbour) == GREY:
                return stack[stack.index(neighbour):] + [neighbour]
            if color.get(neighbour, BLACK) == WHITE:
                found = walk(neighbour)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return []

    for node in sorted(outgoing):
        if color[node] == WHITE:
            found = walk(node)
            if found:
                return found
    return []


def format_plan(plan: Dict[str, Any]) -> str:
    """План словами. Читается в трубе, в логе и моделью."""
    lines = [f"  задач на карте: {len(plan['nodes'])}, "
             f"зависимостей: {len(plan['links'])}"]
    for node in sorted(plan["nodes"].values(), key=lambda n: n["description"]):
        bits = [node["description"]]
        if node["project"]:
            bits.append(f"проект {node['project']}")
        if node["priority"]:
            bits.append(f"приоритет {node['priority']}")
        lines.append(f"    {node['element']:>14}  " + "; ".join(bits))
    for link in plan["links"]:
        lines.append(f"    {link['to']} зависит от {link['from']}")
    for note in plan["notes"]:
        lines.append(f"    ! {note}")
    if plan["cycle"]:
        lines.append("    ! круг в зависимостях: " + " → ".join(plan["cycle"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Taskwarrior
# --------------------------------------------------------------------------


def _tracker_module():
    """``digit_cli/tasks_cli.py`` — единственный способ говорить с трекером.

    Скилл ``digitable-tasks`` прямо запрещает ходить в ``task`` мимо этой
    обёртки: она отказывает на позиционных номерах, из-за которых 2026-08-04
    семь агентов подряд закрыли не ту задачу. Своей транспортной прослойки
    здесь нет намеренно — вторая реализация того же означала бы второй набор
    тех же ошибок.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "digit_cli" / "tasks_cli.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "digit_tasks_cli", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise Refused(
        "не найден digit_cli/tasks_cli.py — эта утилита говорит с Taskwarrior "
        "через него, а не через `task` напрямую. Запускайте её из дерева Digit."
    )


def open_tracker(data_dir: Optional[str] = None):
    """Трекер и путь к его базе. Путь печатается: чужую базу надо видеть."""
    module = _tracker_module()
    try:
        resolved = module.find_tracker(data_dir)
    except module.TrackerNotFound as exc:
        raise Refused(str(exc)) from exc
    return module.Tracker(resolved), resolved


def upsert(tracker, record: Dict[str, Any]) -> str:
    """Создать или обновить задачу, не потеряв того, чего не писали.

    ``task import`` существующего uuid заменяет запись целиком: поданная без
    ``annotations`` запись оставляет задачу без аннотаций, и ни одна строка
    вывода об этом не говорит. Поэтому здесь read-merge-import.
    """
    task_uuid = record["uuid"]
    existing = tracker.get(task_uuid)
    merged: Dict[str, Any] = dict(existing or {})
    merged.update(record)
    # ``id`` и ``urgency`` — вычисляемые поля вывода, обратно их не подают.
    for computed in ("id", "urgency"):
        merged.pop(computed, None)
    merged.setdefault("status", "pending")
    merged.setdefault("entry", module_now())

    code, out, err = tracker._run(["import", "-"],
                                  stdin=json.dumps([merged], ensure_ascii=False))
    after = tracker.get(task_uuid)
    if after is None:
        raise Refused(
            (err.strip() or out.strip() or f"task import отказал ({code})")
            + f" — задача {task_uuid} не появилась в {tracker.data_dir}")
    return "обновлена" if existing else "создана"


def module_now() -> str:
    """Отметка времени в формате Taskwarrior."""
    import time
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def apply_plan(tracker, plan: Dict[str, Any]) -> List[str]:
    """Записать план в трекер. Сначала задачи, потом зависимости.

    Порядок не случаен: ``depends`` ссылается на uuid, и задача, на которую
    ссылаются, должна к этому моменту существовать — иначе taskwarrior запишет
    зависимость на ничто и покажет её как выполненную.
    """
    if plan["cycle"]:
        raise Refused(
            "круг в зависимостях: " + " → ".join(plan["cycle"]) +
            ". Ничего не записано: taskwarrior отказал бы на середине списка, "
            "оставив часть зависимостей проставленной."
        )

    report: List[str] = []
    for node in plan["nodes"].values():
        record: Dict[str, Any] = {
            "uuid": node["uuid"],
            "description": node["description"],
            UDA: node["element"],
        }
        if node["project"]:
            record["project"] = node["project"]
        if node["priority"]:
            record["priority"] = node["priority"]
        report.append(f"{upsert(tracker, record)}: {node['description']}")

    depends: Dict[str, List[str]] = {}
    for link in plan["links"]:
        target = plan["nodes"][link["to"]]["uuid"]
        depends.setdefault(target, []).append(plan["nodes"][link["from"]]["uuid"])
    for task_uuid, prerequisites in depends.items():
        current = tracker.require(task_uuid)
        merged = sorted(set(current.get("depends") or []) | set(prerequisites))
        upsert(tracker, {"uuid": task_uuid, "depends": merged})
        report.append(f"зависимостей у {task_uuid}: {len(merged)}")
    return report


# --------------------------------------------------------------------------
# Рисование: библиотеки Мастерской
# --------------------------------------------------------------------------

#: Где лежат наборы Мастерской. Ищется так же, как ``digit tasks`` ищет базу:
#: по дереву вверх, а не по зашитому пути, — рядом с Digit обычно лежит
#: чекаут курсов, и в нём эти файлы уже собраны и проверены.
LIBRARY_SUBPATH = Path("static/workbench/excalidraw")

#: Набор и фигура, которыми рисуется задача. Канбан выбран не по вкусу: из
#: шести наборов Мастерской это единственный, у которого есть карточка задачи,
#: колонка и метка, то есть словарь ровно про то, что здесь рисуется.
DEFAULT_LIBRARY = "kanban"
CARD_ITEM = "Карточка задачи"

#: Отметка времени внутри собранных библиотек: одна и та же у всех элементов,
#: чтобы сборка была побайтово сравнимой. Здесь она нужна за тем же.
LIBRARY_EPOCH = 1785542400000


def find_library(explicit: Optional[str] = None, *,
                 name: str = DEFAULT_LIBRARY,
                 palette: str = "carbon",
                 start: Optional[Path] = None) -> Path:
    """Найти ``.excalidrawlib``. Порядок: ``--library`` → дерево вверх.

    Библиотеки живут в чекауте курсов, а не здесь: там они собираются
    генератором и проверяются ``npm run test:excalidraw``. Копия в Digit была
    бы второй правдой, которая расходится с первой молча.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            path = path / f"{name}-{palette}.excalidrawlib"
        if not path.is_file():
            raise Refused(f"нет файла библиотеки {path}")
        return path

    wanted = f"{name}-{palette}.excalidrawlib"
    here = (start or Path(__file__).resolve()).resolve()
    for directory in (here, *here.parents):
        if not directory.is_dir():
            continue
        candidate = directory / LIBRARY_SUBPATH / wanted
        if candidate.is_file():
            return candidate
        try:
            siblings = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            continue
        for sibling in siblings:
            candidate = sibling / LIBRARY_SUBPATH / wanted
            if candidate.is_file():
                return candidate
    raise Refused(
        f"не найден набор «{name}» ({wanted}). Он лежит в чекауте курсов, в "
        f"{LIBRARY_SUBPATH}/. Укажите его через --library, например "
        f"--library ../courses/{LIBRARY_SUBPATH}."
    )


def load_library(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """``.excalidrawlib`` → имя фигуры → её элементы."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"не читается библиотека {path}: {exc}") from exc
    if document.get("type") != "excalidrawlib":
        raise Refused(f"{path} — не библиотека Excalidraw")
    items: Dict[str, List[Dict[str, Any]]] = {}
    for item in document.get("libraryItems") or ():
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            items[item["name"]] = list(item.get("elements") or ())
    return items


_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def stable_id(*parts: str) -> str:
    """Идентификатор элемента, выведенный из его роли, а не из случая.

    Случайные id ломают повторный прогон: карта, перерисованная во второй раз,
    состояла бы из других элементов, и ручная правка внутри неё не нашлась бы.
    Форма — 21 символ из алфавита nanoid, как у самого приложения.
    """
    import hashlib
    digest = hashlib.sha256(" ".join(parts).encode("utf-8")).digest()
    return "".join(_ID_ALPHABET[b % len(_ID_ALPHABET)] for b in digest[:21])


def stamp(elements: Sequence[Dict[str, Any]], *, at: Tuple[float, float],
          key: str, frame: Optional[str] = None) -> List[Dict[str, Any]]:
    """Поставить фигуру из библиотеки в точку, переименовав всё внутри неё.

    Копия должна быть самостоятельной: у элементов новые id, у группы — новый
    ``groupIds``, а все ссылки внутри (``containerId``, ``boundElements``,
    привязки стрелок) переписаны на них. Оставить старые значило бы склеить
    две карточки в одну, потому что вторая ссылалась бы на текст первой.
    """
    origin_x = min(float(e.get("x") or 0.0) for e in elements)
    origin_y = min(float(e.get("y") or 0.0) for e in elements)
    dx, dy = at[0] - origin_x, at[1] - origin_y

    ids = {e["id"]: stable_id(key, str(e["id"])) for e in elements if e.get("id")}
    groups = {
        g: stable_id(key, "group", str(g))
        for e in elements for g in (e.get("groupIds") or ())
    }

    stamped: List[Dict[str, Any]] = []
    for element in elements:
        copy = json.loads(json.dumps(element))
        copy["id"] = ids.get(element.get("id"), stable_id(key, "?"))
        copy["x"] = float(copy.get("x") or 0.0) + dx
        copy["y"] = float(copy.get("y") or 0.0) + dy
        copy["groupIds"] = [groups[g] for g in (copy.get("groupIds") or ())]
        copy["frameId"] = frame
        copy["seed"] = int(stable_id(key, str(element.get("id")), "seed")
                           .encode("utf-8").hex()[:8], 16)
        copy["version"] = 1
        copy["versionNonce"] = copy["seed"]
        copy["updated"] = LIBRARY_EPOCH
        if isinstance(copy.get("containerId"), str):
            copy["containerId"] = ids.get(copy["containerId"])
        bound = copy.get("boundElements")
        if isinstance(bound, list):
            copy["boundElements"] = [
                {**entry, "id": ids.get(entry.get("id"), entry.get("id"))}
                for entry in bound if isinstance(entry, dict)
            ]
        for key_name in ("startBinding", "endBinding"):
            binding = copy.get(key_name)
            if isinstance(binding, dict) and binding.get("elementId") in ids:
                binding["elementId"] = ids[binding["elementId"]]
        stamped.append(copy)
    return stamped


def primary_of(stamped: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Фигура, которая и есть задача: самая большая из тех, что ею могут быть.

    У карточки из библиотеки внутри есть вторая, маленькая — плашка статуса.
    Задачей должна стать внешняя, иначе стрелка зависимости прицепится к плашке.
    """
    shapes = [e for e in stamped if e.get("type") in TASK_SHAPES]
    if not shapes:
        raise Refused("в фигуре библиотеки нет прямоугольника, эллипса или ромба")
    return max(shapes, key=lambda e: (float(e.get("width") or 0)
                                      * float(e.get("height") or 0)))


def title_of(stamped: Sequence[Dict[str, Any]],
             primary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Надпись, в которую пишется описание задачи, — верхняя внутри фигуры."""
    inside = [e for e in stamped
              if e.get("type") == "text" and not e.get("containerId")
              and _starts_inside(e, primary)]
    inside.sort(key=lambda e: (float(e.get("y") or 0.0), float(e.get("x") or 0.0)))
    return inside[0] if inside else None


def wrap(text: str, width: int) -> str:
    """Разбить описание по словам. Ничего не выбрасывается и не сокращается."""
    lines: List[str] = []
    current = ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines) or str(text)


def wrap_width(primary: Dict[str, Any], title: Dict[str, Any]) -> int:
    """Сколько знаков влезает в ширину фигуры.

    Считается по самой фигуре, а не по числу из головы: карточка канбана и
    ветвь интеллект-карты разной ширины, и один и тот же перенос выпустит текст
    за край одной из них. Отступ берётся тот, с которым заголовок нарисован в
    библиотеке, — он же и справа.
    """
    size = float(title.get("fontSize") or 16)
    pad = max(0.0, float(title.get("x") or 0.0) - float(primary.get("x") or 0.0))
    room = float(primary.get("width") or 0.0) - 2 * pad
    return max(8, int(room / (size * 0.55)))


def fit(stamped: Sequence[Dict[str, Any]], primary: Dict[str, Any],
        title: Dict[str, Any], *, grew: float, below: float) -> None:
    """Раздвинуть фигуру ровно на то, насколько вырос заголовок.

    Описание задачи длиннее подписи, ради которой рисовали карточку в
    библиотеке. Оставить как есть — заголовок вылезет за карточку и ляжет на
    соседнюю; обрезать — потерять текст. Поэтому карточка растёт, а всё, что
    было ниже заголовка **до** того, как он вырос, съезжает вниз ровно на
    столько же: расстановка внутри фигуры сохраняется, а вместе с ней и вид.

    Считается по приросту заголовка, а не по тому, насколько он вылез за
    карточку. Эти два числа разные, и второе меньше: сдвинув плашку статуса на
    него, её кладут на последние строки подписи — ровно там она и оказывалась.

    ``below`` — нижняя граница заголовка в исходной фигуре: по выросшему её
    считать нельзя, под ним уже «ничего нет».
    """
    if grew <= 0:
        return
    for element in stamped:
        if element is primary:
            element["height"] = float(element.get("height") or 0.0) + grew
        elif element is not title and float(element.get("y") or 0.0) >= below:
            element["y"] = float(element.get("y") or 0.0) + grew


def set_text(element: Dict[str, Any], text: str) -> None:
    """Вписать текст, пересчитав размер приблизительно.

    Точный размер посчитает приложение при загрузке — ``autoResize`` для того и
    стоит. Здесь нужна оценка, чтобы карта не выглядела сломанной до открытия.
    """
    lines = text.split("\n")
    size = float(element.get("fontSize") or 16)
    element["text"] = text
    element["originalText"] = text
    element["width"] = max(len(line) for line in lines) * size * 0.55
    element["height"] = len(lines) * size * 1.25
    element["autoResize"] = True


# --------------------------------------------------------------------------
# Рисование: раскладка
# --------------------------------------------------------------------------

#: Шаг раскладки. Слои идут вправо (зависимость читается слева направо),
#: задачи внутри слоя — вниз.
LAYER_X = 340
ROW_GAP = 40
FRAME_PAD = 48


def layers_of(tasks_by_uuid: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """Слой задачи — насколько глубоко она ждёт других.

    Задача без невыполненных предшественников стоит в слое 0; каждая следующая
    на один правее самого дальнего, кого она ждёт. Зависимости на задачи вне
    выборки не считаются: их на карте нет, и притворяться, что есть, нельзя.
    """
    layer: Dict[str, int] = {}

    def depth(task_uuid: str, seen: Tuple[str, ...] = ()) -> int:
        if task_uuid in layer:
            return layer[task_uuid]
        if task_uuid in seen:
            return 0
        task = tasks_by_uuid[task_uuid]
        parents = [d for d in (task.get("depends") or ()) if d in tasks_by_uuid]
        value = 0 if not parents else 1 + max(
            depth(p, seen + (task_uuid,)) for p in parents)
        layer[task_uuid] = value
        return value

    for task_uuid in sorted(tasks_by_uuid):
        depth(task_uuid)
    return layer


def draw(tasks_list: Sequence[Dict[str, Any]], items: Dict[str, List[Dict[str, Any]]],
         *, item_name: str = CARD_ITEM) -> Dict[str, Any]:
    """Задачи → документ Excalidraw, нарисованный фигурами из библиотеки.

    Проект первого уровня становится рамкой, второго — группой с надписью:
    ровно то, что :func:`parse_map` читает обратно. Приоритет пишется меткой
    ``!H``/``!M``/``!L`` в заголовке, а не заливкой, — заливка в наборах
    Мастерской несёт палитру, и перекрашивание карточки в пастель сломало бы
    вид, ради которого библиотека и берётся.
    """
    if item_name not in items:
        have = ", ".join(sorted(items)) if items else "ничего"
        raise Refused(f"в библиотеке нет фигуры «{item_name}»; есть: {have}")

    by_uuid = {t["uuid"]: t for t in tasks_list}
    layer = layers_of(by_uuid)

    # Проект → рамка.группа; порядок раскладки — по проекту, слою и описанию.
    def project_parts(task: Dict[str, Any]) -> Tuple[str, str]:
        project = str(task.get("project") or "")
        head, _, tail = project.partition(".")
        return head, tail

    order = sorted(
        tasks_list,
        key=lambda t: (project_parts(t), layer[t["uuid"]], t.get("description") or ""))

    elements: List[Dict[str, Any]] = []
    placed: Dict[str, Dict[str, Any]] = {}
    frames: Dict[str, Dict[str, Any]] = {}
    top = 0.0

    for (frame_name, group_name), members in _by_project(order, project_parts):
        # Высота карточки зависит от длины описания, поэтому следующая строка
        # в столбце начинается там, где кончилась предыдущая, а не через
        # постоянный шаг: иначе длинная задача легла бы на соседнюю.
        column_y: Dict[int, float] = {}
        group_id = stable_id("group", frame_name, group_name) if group_name else None
        block: List[Dict[str, Any]] = []

        if group_name:
            title = {
                "id": stable_id("group-title", frame_name, group_name),
                "type": "text", "x": FRAME_PAD, "y": top,
                "width": 200.0, "height": 25.0, "angle": 0,
                "strokeColor": "#9BAAB8", "backgroundColor": "transparent",
                "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
                "roughness": 0, "opacity": 100, "groupIds": [group_id],
                "frameId": None, "roundness": None,
                "seed": 1, "version": 1, "versionNonce": 1, "isDeleted": False,
                "boundElements": None, "updated": LIBRARY_EPOCH,
                "link": None, "locked": False,
                "fontSize": 20, "fontFamily": 6, "textAlign": "left",
                "verticalAlign": "top", "containerId": None,
                "lineHeight": 1.25,
            }
            set_text(title, group_name)
            block.append(title)
            top += 40

        for task in members:
            column = layer[task["uuid"]]
            at_y = column_y.get(column, top)
            stamped = stamp(items[item_name],
                            at=(FRAME_PAD + column * LAYER_X, at_y),
                            key=task["uuid"])
            if group_id:
                for element in stamped:
                    element["groupIds"] = [*element.get("groupIds", []), group_id]
            primary = primary_of(stamped)
            title = title_of(stamped, primary)
            if title is None:
                # Фигуре нечем подписаться — это не повод нарисовать пустую.
                raise Refused(
                    f"у фигуры «{item_name}» нет свободной надписи внутри: "
                    f"вписать описание задачи некуда")
            below = box_of(title)[3]
            was = float(title.get("height") or 0.0)
            set_text(title, wrap(describe_task(task), wrap_width(primary, title)))
            fit(stamped, primary, title,
                grew=float(title["height"]) - was, below=below)
            column_y[column] = box_of(primary)[3] + ROW_GAP
            placed[task["uuid"]] = primary
            block.extend(stamped)

        if frame_name:
            frames.setdefault(frame_name, {"name": frame_name, "members": []})
            frames[frame_name]["members"].extend(block)
        elements.extend(block)
        top = (max(column_y.values()) if column_y else top) + 40

    # Рамки рисуются последними по размеру своего содержимого и первыми по
    # порядку в файле: рамка — фон, и в Excalidraw она должна лежать под ним.
    frame_elements: List[Dict[str, Any]] = []
    for name, frame in frames.items():
        boxes = [box_of(e) for e in frame["members"]]
        x0 = min(b[0] for b in boxes) - FRAME_PAD
        y0 = min(b[1] for b in boxes) - FRAME_PAD
        x1 = max(b[2] for b in boxes) + FRAME_PAD
        y1 = max(b[3] for b in boxes) + FRAME_PAD
        frame_id = stable_id("frame", name)
        frame_elements.append({
            "id": frame_id, "type": "frame", "x": x0, "y": y0,
            "width": x1 - x0, "height": y1 - y0, "angle": 0,
            "strokeColor": "#bbb", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": 1, "version": 1, "versionNonce": 1,
            "isDeleted": False, "boundElements": None,
            "updated": LIBRARY_EPOCH, "link": None, "locked": False,
            "name": name,
        })
        for element in frame["members"]:
            element["frameId"] = frame_id

    arrows: List[Dict[str, Any]] = []
    for task in order:
        for parent in task.get("depends") or ():
            if parent not in placed:
                continue
            arrows.append(arrow_between(placed[parent], placed[task["uuid"]],
                                        key=f"{parent}->{task['uuid']}"))

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "digit/skills/creative/excalidraw/scripts/tasks.py",
        "elements": [*frame_elements, *elements, *arrows],
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }


def _by_project(order: Sequence[Dict[str, Any]], parts) -> List[Tuple[Tuple[str, str],
                                                                     List[Dict[str, Any]]]]:
    groups: List[Tuple[Tuple[str, str], List[Dict[str, Any]]]] = []
    for task in order:
        key = parts(task)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(task)
        else:
            groups.append((key, [task]))
    return groups


def describe_task(task: Dict[str, Any]) -> str:
    """Заголовок карточки: описание и, если он есть, приоритет меткой."""
    text = " ".join(str(task.get("description") or "").split())
    priority = task.get("priority")
    mark = {"H": "!H", "M": "!M", "L": "!L"}.get(str(priority or ""))
    return f"{text} {mark}" if mark else text


def arrow_between(source: Dict[str, Any], target: Dict[str, Any], *,
                  key: str) -> Dict[str, Any]:
    """Стрелка от предшественника к зависимой задаче, привязанная к обеим.

    Привязка — не украшение: непривязанная стрелка выглядит связью и связью не
    является, и :func:`parse_map` честно откажется читать из неё зависимость.
    """
    sx0, sy0, sx1, sy1 = box_of(source)
    tx0, ty0, tx1, ty1 = box_of(target)
    start = (sx1, (sy0 + sy1) / 2)
    end = (tx0, (ty0 + ty1) / 2)
    return {
        "id": stable_id("arrow", key), "type": "arrow",
        "x": start[0], "y": start[1],
        "width": end[0] - start[0], "height": end[1] - start[1],
        "angle": 0, "strokeColor": "#9BAAB8", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 2}, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": None, "updated": LIBRARY_EPOCH,
        "link": None, "locked": False,
        "points": [[0, 0], [end[0] - start[0], end[1] - start[1]]],
        "lastCommittedPoint": None,
        "startArrowhead": None, "endArrowhead": "arrow",
        # Форма ``{elementId, focus, gap}`` — та, что описана в SKILL.md и
        # понятна вендоренному здесь холсту. ``parse_map`` читает только
        # ``elementId``, поэтому и новая форма приложения тоже прочитается.
        "startBinding": {"elementId": source["id"], "focus": 0, "gap": 4},
        "endBinding": {"elementId": target["id"], "focus": 0, "gap": 4},
    }


# --------------------------------------------------------------------------
# Схема входа
# --------------------------------------------------------------------------

#: Схемы намеренно плоские: объект и его поля, без вложенных объектов, без
#: массивов объектов и без ``oneOf``/``anyOf``. Это требование маленькой
#: модели-маршрутизатора Digit (courses: ``content/workbench/digit-integrations.md``),
#: и здесь оно закреплено проверкой :func:`schema_depth`.
SCHEMAS: Dict[str, Dict[str, Any]] = {
    "excalidraw.from-map": {
        "description": "Карта Excalidraw → задачи Taskwarrior",
        "type": "object",
        "properties": {
            "map": {"type": "string", "description": "путь к .excalidraw"},
            "data_dir": {"type": "string",
                         "description": "каталог базы Taskwarrior"},
            "default_project": {"type": "string",
                                "description": "проект для фигур вне рамок"},
            "dry_run": {"type": "boolean",
                        "description": "только показать план, ничего не писать"},
        },
        "required": ["map"],
    },
    "excalidraw.to-map": {
        "description": "Задачи Taskwarrior → карта Excalidraw",
        "type": "object",
        "properties": {
            "map": {"type": "string", "description": "куда записать .excalidraw"},
            "data_dir": {"type": "string",
                         "description": "каталог базы Taskwarrior"},
            "project": {"type": "string", "description": "только этот проект"},
            "library": {"type": "string",
                        "description": "файл или каталог наборов Мастерской"},
            "palette": {"type": "string", "description": "carbon, paper или signal"},
            "force": {"type": "boolean",
                      "description": "перезаписать существующую карту"},
        },
        "required": ["map"],
    },
}


def schema_depth(value: Any, level: int = 1) -> int:
    """Глубина вложенности схемы.

    Уровень 1 — сам объект аргументов, уровень 2 — описание одного аргумента.
    Аргумент-объект или аргумент-массив-объектов дал бы третий уровень: именно
    его требование модели-маршрутизатора и запрещает.
    """
    if isinstance(value, dict):
        inner = value.get("properties")
        if isinstance(inner, dict) and inner:
            return max(schema_depth(v, level + 1) for v in inner.values())
        items = value.get("items")
        if isinstance(items, dict) and items.get("type") == "object":
            return schema_depth(items, level + 1)
    return level


def schema_alternatives(value: Any) -> List[str]:
    """Ключи-альтернативы в описании входа, если они есть.

    «Без альтернатив» — вторая половина того же требования: маршрутизатору
    нельзя предлагать выбор формы аргумента, он выбирает утилиту, а не схему.
    """
    found: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, sub in node.items():
                if key in ("oneOf", "anyOf", "allOf", "not"):
                    found.append(key)
                walk(sub)
        elif isinstance(node, list):
            for sub in node:
                walk(sub)

    walk(value)
    return found


# --------------------------------------------------------------------------
# Самопроверка
# --------------------------------------------------------------------------


def _sample_map() -> Dict[str, Any]:
    """Карта, на которой видно каждое правило разбора сразу.

    Форма элементов — та, что пишет приложение: подпись отдельным элементом с
    ``containerId``, стрелка с ``startBinding``/``endBinding``, рамка с
    ``name``, группа без имени и надпись, дающая ей имя.
    """
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "digit",
        "elements": [
            {"id": "frame1", "type": "frame", "x": 0, "y": 0,
             "width": 800, "height": 400, "name": "Этап 1.второй"},
            {"id": "box1", "type": "rectangle", "x": 40, "y": 60,
             "width": 200, "height": 80, "frameId": "frame1",
             "backgroundColor": "#ffc9c9", "groupIds": ["gA"],
             "boundElements": [{"id": "t1", "type": "text"}]},
            {"id": "t1", "type": "text", "x": 50, "y": 80, "width": 180,
             "height": 25, "text": "Собрать основу", "containerId": "box1"},
            {"id": "box2", "type": "rectangle", "x": 400, "y": 60,
             "width": 200, "height": 80, "frameId": "frame1",
             "backgroundColor": "#a5d8ff", "groupIds": ["gA"],
             "boundElements": [{"id": "t2", "type": "text"},
                               {"id": "arrow1", "type": "arrow"}]},
            {"id": "t2", "type": "text", "x": 410, "y": 80, "width": 180,
             "height": 25, "text": "Проверить основу !M", "containerId": "box2"},
            {"id": "gname", "type": "text", "x": 40, "y": 20, "width": 120,
             "height": 20, "text": "Разбор", "groupIds": ["gA"]},
            {"id": "arrow1", "type": "arrow", "x": 250, "y": 100,
             "width": 140, "height": 0, "points": [[0, 0], [140, 0]],
             "startBinding": {"elementId": "box1", "focus": 0, "gap": 4},
             "endBinding": {"elementId": "box2", "focus": 0, "gap": 4}},
            {"id": "loose", "type": "arrow", "x": 40, "y": 300,
             "width": 100, "height": 0, "points": [[0, 0], [100, 0]],
             "startBinding": None, "endBinding": None},
            {"id": "mute", "type": "ellipse", "x": 650, "y": 250,
             "width": 90, "height": 90},
            {"id": "gone", "type": "rectangle", "x": 0, "y": 900,
             "width": 10, "height": 10, "isDeleted": True,
             "boundElements": [{"id": "tgone", "type": "text"}]},
            {"id": "tgone", "type": "text", "x": 0, "y": 900, "width": 10,
             "height": 10, "text": "стёрта", "containerId": "gone",
             "isDeleted": True},
        ],
    }


def _sample_tasks() -> List[Dict[str, Any]]:
    """Две задачи, вторая ждёт первую, обе в проекте из двух уровней."""
    first, second = task_uuid_for("box1"), task_uuid_for("box2")
    return [
        {"uuid": first, "description": "Собрать основу", "status": "pending",
         "project": "Этап.Разбор", "priority": "H", UDA: "box1"},
        {"uuid": second, "description": "Проверить основу", "status": "pending",
         "project": "Этап.Разбор", "priority": "M", UDA: "box2",
         "depends": [first]},
    ]


#: Подмена библиотеки на случай, когда чекаута курсов рядом нет. Форма та же,
#: что у «Карточки задачи»: прямоугольник и свободная надпись внутри него, —
#: именно она проверяется, а не то, как карточка выглядит.
_STANDIN_CARD = [
    {"id": "card", "type": "rectangle", "x": 0, "y": 0, "width": 260,
     "height": 104, "groupIds": ["card"], "backgroundColor": "transparent",
     "boundElements": None},
    {"id": "card-title", "type": "text", "x": 18, "y": 16, "width": 144,
     "height": 20, "groupIds": ["card"], "text": "Что нужно сделать",
     "originalText": "Что нужно сделать", "fontSize": 16, "fontFamily": 6,
     "containerId": None, "autoResize": True},
]


def _library_for_test() -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """Настоящий набор Мастерской, если он рядом; иначе — подмена."""
    try:
        path = find_library(None)
    except Refused:
        return {CARD_ITEM: _STANDIN_CARD}, "подмена: набора Мастерской рядом нет"
    return load_library(path), str(path)


def self_test() -> int:
    """Проверить разбор и — если рядом есть taskwarrior — запись.

    Разбор проверяется всегда: он чистый. Запись — только когда база, которую
    можно испортить, заведомо своя: временный каталог, созданный здесь же.
    """
    import shutil
    import tempfile

    checks: List[Tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))

    plan = parse_map(_sample_map())
    nodes = plan["nodes"]

    check("фигуры с подписью стали задачами", set(nodes) == {"box1", "box2"},
          f"получено {sorted(nodes)}")
    check("подпись стала описанием",
          nodes["box1"]["description"] == "Собрать основу",
          nodes["box1"]["description"])
    check("метка приоритета убрана из описания",
          nodes["box2"]["description"] == "Проверить основу",
          nodes["box2"]["description"])
    check("цвет заливки дал приоритет", nodes["box1"]["priority"] == "H",
          str(nodes["box1"]["priority"]))
    check("метка сильнее цвета", nodes["box2"]["priority"] == "M",
          str(nodes["box2"]["priority"]))
    check("рамка и группа дали проект в два уровня",
          nodes["box1"]["project"] == "Этап 1-второй.Разбор",
          str(nodes["box1"]["project"]))
    check("проект не глубже двух уровней",
          all((n["project"] or "").count(".") <= 1 for n in nodes.values()))
    check("стрелка стала зависимостью",
          plan["links"] == [{"from": "box1", "to": "box2"}], str(plan["links"]))
    check("непривязанная стрелка не потерялась молча",
          any("loose" in note for note in plan["notes"]))
    check("фигура без подписи не потерялась молча",
          any("mute" in note for note in plan["notes"]))
    check("стёртая фигура не стала задачей", "gone" not in nodes)
    check("uuid выводится из id элемента",
          nodes["box1"]["uuid"] == task_uuid_for("box1"))
    check("разбор повторяем",
          parse_map(_sample_map())["nodes"] == nodes)

    cyclic = parse_map(_sample_map())
    cyclic["links"].append({"from": "box2", "to": "box1"})
    cyclic["cycle"] = find_cycle(cyclic["nodes"], cyclic["links"])
    check("круг в зависимостях найден", bool(cyclic["cycle"]),
          str(cyclic["cycle"]))

    for name, schema in SCHEMAS.items():
        check(f"схема {name} не глубже двух уровней",
              schema_depth(schema) <= 2, f"глубина {schema_depth(schema)}")
        check(f"схема {name} без альтернатив",
              not schema_alternatives(schema), str(schema_alternatives(schema)))

    # -- обратное направление: карта, нарисованная из задач, читается назад --
    items, source = _library_for_test()
    drawn = draw(_sample_tasks(), items)
    back = parse_map(drawn)
    seen = {n["description"]: n for n in back["nodes"].values()}

    check(f"карта нарисована ({source})", len(drawn["elements"]) > 0)
    check("описания вернулись", set(seen) == {"Собрать основу", "Проверить основу"},
          str(sorted(seen)))
    check("приоритет вернулся", seen.get("Собрать основу", {}).get("priority") == "H",
          str(seen.get("Собрать основу", {}).get("priority")))
    check("проект в два уровня вернулся",
          seen.get("Собрать основу", {}).get("project") == "Этап.Разбор",
          str(seen.get("Собрать основу", {}).get("project")))
    check("зависимость вернулась", len(back["links"]) == 1, str(back["links"]))
    check("на обратном пути ничего не осталось непонятым",
          not back["notes"], "; ".join(back["notes"]))
    check("рисование повторяемо",
          json.dumps(draw(_sample_tasks(), items), sort_keys=True) ==
          json.dumps(drawn, sort_keys=True))

    if shutil.which("task"):
        sandbox = Path(tempfile.mkdtemp(prefix="excalidraw-tasks-"))
        try:
            data = sandbox / "data"
            data.mkdir()
            (sandbox / "taskrc").write_text(
                f"data.location={data}\n", encoding="utf-8")
            module = _tracker_module()
            tracker = module.Tracker(data)

            apply_plan(tracker, plan)
            written = {t["description"]: t for t in tracker.export([])}
            check("задача записана в базу", "Собрать основу" in written,
                  str(sorted(written)))
            check("UDA несёт id элемента",
                  written.get("Собрать основу", {}).get(UDA) == "box1",
                  str(written.get("Собрать основу", {}).get(UDA)))
            check("зависимость записана",
                  task_uuid_for("box1") in
                  (written.get("Проверить основу", {}).get("depends") or []),
                  str(written.get("Проверить основу", {}).get("depends")))

            # Повторный прогон не создаёт вторых копий и не стирает аннотацию.
            tracker.annotate(task_uuid_for("box1"), "проверено вручную")
            apply_plan(tracker, parse_map(_sample_map()))
            again = tracker.export([])
            check("повторный прогон не размножил задачи", len(again) == 2,
                  f"задач {len(again)}")
            kept = next(t for t in again if t["uuid"] == task_uuid_for("box1"))
            check("повторный прогон не стёр аннотацию",
                  len(kept.get("annotations") or []) == 1,
                  str(kept.get("annotations")))
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)
    else:
        checks.append(("запись в базу", True, "пропущена: нет `task` в PATH"))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        mark = "ок " if ok else "ПЛОХО"
        print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))
    print(f"  проверок {len(checks)}, не прошло {len(failed)}")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_colors(path: Optional[str]) -> Optional[Dict[str, str]]:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"не читается таблица цветов {path}: {exc}") from exc
    if not isinstance(data, dict) or any(
            v not in ("H", "M", "L") for v in data.values()):
        raise Refused(
            "таблица цветов — это объект «цвет заливки» → «H», «M» или «L»")
    return data


def read_tasks(tracker, *, project: Optional[str] = None,
               status: str = "pending") -> List[Dict[str, Any]]:
    filters = [f"status:{status}"]
    if project:
        filters.append(f"project:{project}")
    return tracker.export(filters)


def cmd_to_map(args) -> int:
    target = Path(args.map)
    if target.exists() and not args.force:
        raise Refused(
            f"{target} уже есть. Перерисовать её заново значит потерять всё, что "
            f"в ней двигали руками: для повторного прогона есть `sync`, он "
            f"обновляет карту, не трогая раскладку. --force, если правда надо."
        )
    tracker, resolved = open_tracker(args.data_dir)
    library = find_library(args.library, palette=args.palette)
    items = load_library(library)
    found = read_tasks(tracker, project=args.project, status=args.status)
    if not found:
        raise Refused(
            f"в {resolved} нет задач по фильтру "
            f"(status:{args.status}" +
            (f", project:{args.project}" if args.project else "") + ")")
    document = draw(found, items)
    Path(target).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"  база задач: {resolved}")
    print(f"  библиотека: {library}")
    print(f"  задач: {len(found)}, элементов: {len(document['elements'])}")
    print(f"  записана {target}")
    return 0


def cmd_from_map(args) -> int:
    document = load_map(args.map)
    plan = parse_map(document,
                     colors=_load_colors(args.priority_colors),
                     default_project=args.default_project)
    print(format_plan(plan))
    if args.dry_run:
        print("  (--dry-run: ничего не записано)")
        return 1 if plan["cycle"] else 0
    tracker, resolved = open_tracker(args.data_dir)
    print(f"  база задач: {resolved}")
    for line in apply_plan(tracker, plan):
        print(f"    {line}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--self-test", action="store_true",
                        help="проверить утилиту на встроенной карте")
    sub = parser.add_subparsers(dest="command")

    p_from = sub.add_parser("from-map",
                            help="Карта Excalidraw → задачи Taskwarrior")
    p_from.add_argument("map", type=Path)
    p_from.add_argument("--data-dir", default=None,
                        help="каталог базы Taskwarrior (иначе — как у `digit tasks`)")
    p_from.add_argument("--default-project", default=None,
                        help="проект для фигур вне рамок и групп")
    p_from.add_argument("--priority-colors", default=None,
                        help="JSON: заливка → H/M/L, вместо таблицы по умолчанию")
    p_from.add_argument("--dry-run", action="store_true",
                        help="показать план и ничего не писать")

    p_to = sub.add_parser("to-map",
                          help="Задачи Taskwarrior → карта Excalidraw")
    p_to.add_argument("map", type=Path)
    p_to.add_argument("--data-dir", default=None)
    p_to.add_argument("--project", default=None, help="только этот проект")
    p_to.add_argument("--status", default="pending")
    p_to.add_argument("--library", default=None,
                      help="файл .excalidrawlib или каталог наборов Мастерской")
    p_to.add_argument("--palette", default="carbon",
                      choices=("carbon", "paper", "signal"))
    p_to.add_argument("--force", action="store_true",
                      help="перезаписать карту (ручная раскладка пропадёт)")

    p_schema = sub.add_parser("schema", help="Схемы входа утилит")
    p_schema.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "from-map":
            return cmd_from_map(args)
        if args.command == "to-map":
            return cmd_to_map(args)
        if args.command == "schema":
            if args.json:
                print(json.dumps(SCHEMAS, ensure_ascii=False, indent=2))
            else:
                for name, schema in SCHEMAS.items():
                    print(f"  {name}: {schema['description']}")
                    for field, spec in schema["properties"].items():
                        need = " (обязательно)" if field in schema.get("required", ()) else ""
                        print(f"    {field}: {spec['type']}{need} — "
                              f"{spec['description']}")
                    print(f"    глубина {schema_depth(schema)}, "
                          f"альтернатив {len(schema_alternatives(schema))}")
            return 0
    except Refused as exc:
        print(f"  отказ: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"  ошибка: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
