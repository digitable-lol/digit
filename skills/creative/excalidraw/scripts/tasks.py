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


def label_of(element: Optional[Dict[str, Any]],
             by_id: Dict[str, Dict[str, Any]]) -> str:
    """Подпись фигуры: своя, либо привязанного к ней текста.

    Та же правка, что в ``render.py``: у фигуры собственного текста нет, он
    лежит отдельным элементом с ``containerId``, а фигура ссылается на него
    через ``boundElements``.
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


def group_names_of(document: Dict[str, Any]) -> Dict[str, str]:
    """Имя группы — свободный текст, лежащий в этой же группе.

    У группы в Excalidraw нет имени: это просто общий ``groupIds`` у нескольких
    элементов. Единственная подпись, которую человек может ей дать, — надпись
    рядом, включённая в ту же группу. Текст, привязанный к фигуре
    (``containerId``), именем группы не считается: это подпись задачи.
    """
    names: Dict[str, str] = {}
    for element in visible(document):
        if element.get("type") != "text" or element.get("containerId"):
            continue
        text = " ".join(str(element.get("text") or "").split())
        if not text:
            continue
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
    notes: List[str] = []

    nodes: Dict[str, Dict[str, Any]] = {}
    for element in visible(document):
        if element.get("type") not in TASK_SHAPES:
            continue
        label = label_of(element, by_id)
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
