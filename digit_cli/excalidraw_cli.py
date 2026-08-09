"""``digit excalidraw`` — виджеты Мастерской из командной строки.

Зачем команда, а не модельный инструмент
----------------------------------------
Ступень 2 «Лестницы следа» (AGENTS.md): рисование выражается командой, поэтому
агент запускает ``digit excalidraw …`` по скиллу, а схема модельного
инструмента — за которую платит каждый вызов API — не растёт.

Зачем вообще
------------
Схемы в Мастерской уже собраны: шесть наборов, у каждого свой словарь — тема и
ветви у интеллект-карты, участник и вызов у диаграммы последовательности,
персона и контейнер у C4, дорожка и веха у дорожной карты. Достать их можно
было ровно одним способом: открыть холст и перетаскивать фигуры мышью.

Здесь тот же словарь из командной строки. Никакого второго набора фигур не
заводится: команда берёт те же ``.excalidrawlib``, что человек перетаскивает
руками, и ставит из них те же элементы. Поэтому нарисованное командой можно
открыть и продолжить руками, не заметив шва.

Вход — один и тот же для всех виджетов
--------------------------------------
Две формы строки, и больше никаких::

    Тема                       # уровень 0
      Ветвь                    # уровень 1, по отступу
        Лист                   # уровень 2
    Ветвь -> Лист: подпись     # связь между подписями

Отступ даёт уровень, уровень выбирает фигуру из набора. Стрелка соединяет две
уже названные подписи и привязывается к обеим — непривязанная стрелка выглядит
связью и связью не является.

Одна форма входа на шесть виджетов — не лень, а то же требование плоских схем,
что и у остальных утилит Digit: маршрутизатору нельзя предлагать выбор формы
аргумента, он выбирает утилиту.

Использование
-------------
    digit excalidraw mindmap --out карта.excalidraw --text - < outline.txt
    digit excalidraw sequence --out обмен.excalidraw --text обмен.txt
    digit excalidraw list
    digit excalidraw --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _skill():
    """``tasks.py`` из скилла excalidraw — там уже живёт работа с библиотеками.

    Ставить фигуру из набора значит переписать внутри неё все ссылки: подписи
    по ``containerId``, ``boundElements``, привязки стрелок, ``groupIds``.
    Второй такой реализации здесь нет намеренно — это был бы второй набор тех
    же ошибок, и расходиться они начали бы молча.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = (parent / "skills/creative/excalidraw/scripts/tasks.py")
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "excalidraw_tasks", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError(
        "не найден skills/creative/excalidraw/scripts/tasks.py — команда рисует "
        "его средствами. Запускайте её из дерева Digit."
    )


#: Виджет → набор Мастерской, фигуры по уровням, фигура связи и раскладка.
#:
#: ``levels`` — какую фигуру ставить на каком уровне отступа; последняя
#: повторяется для всех уровней глубже. Раскладок две:
#:
#: * ``tree`` — уровень идёт вправо, соседи вниз: так читается интеллект-карта
#:   и уровни C4;
#: * ``lanes`` — верхний уровень становится колонкой, его дети идут в ней вниз:
#:   так читаются дорожки, участники обмена и колонки доски.
WIDGETS: Dict[str, Dict[str, Any]] = {
    "mindmap": {
        "library": "mindmap",
        "levels": ["Центральная тема", "Ветвь первого уровня", "Подветвь", "Лист"],
        "layout": "tree",
        "about": "Интеллект-карта: тема, ветви, листья",
    },
    "sequence": {
        "library": "sequence",
        "levels": ["Участник", "Заметка"],
        "layout": "lanes",
        "about": "Диаграмма последовательности: участники и вызовы",
    },
    "c4": {
        "library": "c4",
        "levels": ["Система в фокусе", "Контейнер", "Компонент"],
        "layout": "tree",
        "about": "C4: контекст, контейнеры, компоненты",
    },
    "roadmap": {
        "library": "roadmap",
        "levels": ["Дорожка", "Полоса работы", "Веха"],
        "layout": "lanes",
        "about": "Дорожная карта: дорожки, полосы работ, вехи",
    },
    "flowchart": {
        "library": "flowchart",
        "levels": ["Начало и конец", "Действие", "Развилка"],
        "layout": "tree",
        "about": "Блок-схема: начало, действия, развилки",
    },
    "kanban": {
        "library": "kanban",
        "levels": ["Колонка с лимитом", "Карточка задачи"],
        "layout": "lanes",
        "about": "Канбан-доска: колонки и карточки",
    },
}

#: Шаг раскладки. Столбцы шире строк: подписи растут вбок, а не вниз.
STEP_X = 380
STEP_Y = 60


class Refused(RuntimeError):
    """Работа не сделана, и причину стоит напечатать."""


# --------------------------------------------------------------------------
# Разбор входа
# --------------------------------------------------------------------------


def parse_outline(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]],
                                      List[str]]:
    """Текст → узлы с уровнями, связи и то, что осталось непонятым.

    Уровень считается по отступу, а не по числу пробелов: два пробела и четыре
    одинаково означают «на один глубже», лишь бы в одном файле было единообразно.
    Иначе схема зависела бы от настроек редактора, в котором её набрали.
    """
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, str]] = []
    notes: List[str] = []
    stops: List[int] = []

    for number, raw in enumerate(str(text).splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if "->" in line:
            head, _, tail = line.strip().partition("->")
            label, _, note = tail.partition(":")
            links.append({"from": head.strip(), "to": label.strip(),
                          "text": note.strip()})
            continue

        indent = len(line) - len(line.lstrip())
        while stops and stops[-1] >= indent:
            stops.pop()
        if not stops or stops[-1] < indent:
            stops.append(indent)
        level = len(stops) - 1
        title = line.strip()
        if any(node["title"] == title for node in nodes):
            notes.append(
                f"строка {number}: подпись «{title}» уже была — связь по подписи "
                f"стала бы неоднозначной, вторая пропущена")
            continue
        nodes.append({"title": title, "level": level, "line": number})

    named = {node["title"] for node in nodes}
    kept: List[Dict[str, str]] = []
    for link in links:
        missing = [end for end in (link["from"], link["to"]) if end not in named]
        if missing:
            notes.append(
                f"связь «{link['from']}» → «{link['to']}»: нет фигуры "
                + ", ".join(f"«{name}»" for name in missing)
                + " — связь пропущена")
            continue
        kept.append(link)
    return nodes, kept, notes


# --------------------------------------------------------------------------
# Рисование
# --------------------------------------------------------------------------


def column_of(node: Dict[str, Any], layout: str, lane: int) -> int:
    """Столбец узла: уровень в дереве, номер дорожки в полосах."""
    return node["level"] if layout == "tree" else lane


def label_target(stamped: Sequence[Dict[str, Any]], primary: Dict[str, Any],
                 skill) -> Optional[Dict[str, Any]]:
    """Куда вписывать подпись узла. Порядок проб — от точного к общему.

    Одного правила не хватает, и это свойство самих наборов, а не недосмотр.
    У карточки канбана заголовок — свободный текст внутри карточки. У дорожки
    из дорожной карты подпись привязана к маленькому ярлыку слева, а самая
    большая фигура — пустое тело дорожки на 1200 точек, и внутри него текста
    нет вовсе. Пробовать только первое значило бы нарисовать дорожку с
    неизменившимся словом «Дорожка» на ней.
    """
    free = skill.title_of(stamped, primary)
    if free is not None:
        return free
    texts = [e for e in stamped if e.get("type") == "text"]
    bound_here = [t for t in texts if t.get("containerId") == primary.get("id")]
    if bound_here:
        return bound_here[0]
    bound_any = [t for t in texts if t.get("containerId")]
    if bound_any:
        return bound_any[0]
    return texts[0] if texts else None


def build(widget: str, text: str, items: Dict[str, List[Dict[str, Any]]],
          skill) -> Tuple[Dict[str, Any], List[str]]:
    """Виджет → документ Excalidraw, собранный из фигур набора."""
    spec = WIDGETS[widget]
    nodes, links, notes = parse_outline(text)
    if not nodes:
        raise Refused(
            "во входе нет ни одной подписи. Строка без отступа — верхний "
            "уровень, с отступом — уровень ниже, «А -> Б: подпись» — связь.")

    elements: List[Dict[str, Any]] = []
    primaries: Dict[str, Dict[str, Any]] = {}
    # Следующая свободная высота в каждом столбце. Считается по нарисованному,
    # а не постоянным шагом: «Система в фокусе» из C4 выше «Листа» из карты, и
    # общий шаг либо кладёт одну фигуру на другую, либо оставляет пустоту.
    cursor: Dict[int, float] = {}
    lane = -1

    for node in nodes:
        if spec["layout"] == "lanes" and node["level"] == 0:
            lane += 1
        column = column_of(node, spec["layout"], max(lane, 0))

        levels = spec["levels"]
        name = levels[min(node["level"], len(levels) - 1)]
        if name not in items:
            raise Refused(
                f"в наборе «{spec['library']}» нет фигуры «{name}»; есть: "
                + ", ".join(sorted(items)))

        # Столбец правее начинается не выше того, что уже стоит слева: иначе
        # ветвь наезжает на соседнюю там, где дерево неровное.
        top = cursor.get(column, 0.0)
        if spec["layout"] == "tree" and column > 0:
            top = max(top, cursor.get(column - 1, 0.0) - STEP_Y * 2.2)

        stamped = skill.stamp(items[name], at=(column * STEP_X, top),
                              key=f"{widget}:{node['title']}")
        primary = skill.primary_of(stamped)
        title = label_target(stamped, primary, skill)
        if title is None:
            notes.append(
                f"у фигуры «{name}» нет подписи — «{node['title']}» нарисована "
                f"без текста")
        else:
            below = skill.box_of(title)[3]
            was = float(title.get("height") or 0.0)
            holder = next((e for e in stamped
                           if e.get("id") == title.get("containerId")), primary)
            skill.set_text(title, skill.wrap(
                node["title"], skill.wrap_width(holder, title)))
            skill.fit(stamped, primary, title,
                      grew=float(title["height"]) - was, below=below)
        primaries[node["title"]] = primary
        bottom = max(skill.box_of(e)[3] for e in stamped)
        cursor[column] = bottom + STEP_Y
        elements.extend(stamped)

    arrows: List[Dict[str, Any]] = []
    drawn_pairs = {(link["from"], link["to"]) for link in links}
    if spec["layout"] == "tree":
        # Отступ уже сказал, что ветвь принадлежит теме; на дереве это
        # показывают линией. Без неё интеллект-карта — просто три столбца
        # прямоугольников, и родство приходится угадывать по высоте.
        for child, parent in _parents(nodes):
            if (parent, child) in drawn_pairs or (child, parent) in drawn_pairs:
                continue
            arrows.append(skill.arrow_between(
                primaries[parent], primaries[child],
                key=f"{widget}:родство:{parent}->{child}"))

    for link in links:
        arrow = skill.arrow_between(primaries[link["from"]], primaries[link["to"]],
                                    key=f"{widget}:{link['from']}->{link['to']}")
        arrows.append(arrow)
        if link["text"]:
            arrows.append(_arrow_label(arrow, link["text"], skill))

    document = {
        "type": "excalidraw",
        "version": 2,
        "source": "digit excalidraw",
        "elements": [*elements, *arrows],
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }
    return document, notes


def _parents(nodes: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Пары «ребёнок, родитель» по отступам, в порядке чтения."""
    stack: List[Dict[str, Any]] = []
    pairs: List[Tuple[str, str]] = []
    for node in nodes:
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        if stack:
            pairs.append((node["title"], stack[-1]["title"]))
        stack.append(node)
    return pairs


def _arrow_label(arrow: Dict[str, Any], text: str, skill) -> Dict[str, Any]:
    """Подпись связи — текст, привязанный к стрелке через ``containerId``.

    Не отдельная надпись рядом: приложение пересчитывает положение привязанной
    подписи при каждом движении стрелки, а лежащая рядом остаётся на месте и
    через две правки указывает не на ту связь.
    """
    label_id = skill.stable_id("arrow-label", arrow["id"])
    arrow.setdefault("boundElements", None)
    arrow["boundElements"] = [*(arrow.get("boundElements") or []),
                              {"type": "text", "id": label_id}]
    label = {
        "id": label_id, "type": "text",
        "x": float(arrow["x"]) + float(arrow.get("width") or 0) / 2,
        "y": float(arrow["y"]) - 12, "width": 10.0, "height": 20.0,
        "angle": 0, "strokeColor": arrow.get("strokeColor", "#1e1e1e"),
        "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 2, "strokeStyle": "solid", "roughness": 0,
        "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
        "seed": 1, "version": 1, "versionNonce": 1, "isDeleted": False,
        "boundElements": None, "updated": skill.LIBRARY_EPOCH,
        "link": None, "locked": False,
        "fontSize": 16, "fontFamily": 6, "textAlign": "center",
        "verticalAlign": "middle", "containerId": arrow["id"],
        "lineHeight": 1.25,
    }
    skill.set_text(label, text)
    return label


# --------------------------------------------------------------------------
# Схема входа
# --------------------------------------------------------------------------

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "excalidraw.widget": {
        "description": "Виджет Мастерской из командной строки",
        "type": "object",
        "properties": {
            "widget": {"type": "string",
                       "description": "mindmap, sequence, c4, roadmap, "
                                      "flowchart или kanban"},
            "text": {"type": "string",
                     "description": "строки схемы: отступ — уровень, «А -> Б» — связь"},
            "out": {"type": "string", "description": "куда записать .excalidraw"},
            "library": {"type": "string", "description": "каталог наборов Мастерской"},
            "palette": {"type": "string", "description": "carbon, paper или signal"},
        },
        "required": ["widget", "text", "out"],
    },
}


# --------------------------------------------------------------------------
# Самопроверка
# --------------------------------------------------------------------------

_SAMPLE = """
Основа
  Разбор карты
    Стрелки
    Рамки
  Рисование
Разбор карты -> Рисование: и обратно
Разбор карты -> Нет такого: связь в пустоту
"""


def self_test() -> int:
    """Разбор входа проверяется всегда, рисование — если набор рядом."""
    skill = _skill()
    checks: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    nodes, links, notes = parse_outline(_SAMPLE)
    check("уровни считаны по отступу",
          [n["level"] for n in nodes] == [0, 1, 2, 2, 1],
          str([(n["title"], n["level"]) for n in nodes]))
    check("связь между названными фигурами осталась",
          links == [{"from": "Разбор карты", "to": "Рисование",
                     "text": "и обратно"}], str(links))
    check("связь в пустоту не потерялась молча",
          any("Нет такого" in note for note in notes), "; ".join(notes))
    check("разбор повторяем", parse_outline(_SAMPLE)[0] == nodes)

    doubled = parse_outline("А\nА\n")
    check("повторная подпись отклонена", len(doubled[0]) == 1 and doubled[2],
          str(doubled))

    for name, schema in SCHEMAS.items():
        check(f"схема {name} не глубже двух уровней",
              skill.schema_depth(schema) <= 2)
        check(f"схема {name} без альтернатив",
              not skill.schema_alternatives(schema))

    drawn = 0
    for widget, spec in WIDGETS.items():
        try:
            library = skill.find_library(None, name=spec["library"])
        except skill.Refused:
            continue
        items = skill.load_library(library)
        document, _notes = build(widget, _SAMPLE, items, skill)
        drawn += 1
        check(f"{widget}: нарисован", len(document["elements"]) > 0)
        check(f"{widget}: подписи на месте",
              all(any(" ".join(str(e.get("text") or "").split()) == title
                      for e in document["elements"])
                  for title in ("Основа", "Разбор карты", "Стрелки")),
              str([e.get("text") for e in document["elements"]
                   if e.get("type") == "text"][:6]))
        check(f"{widget}: стрелка привязана обоими концами",
              all(a["startBinding"]["elementId"] and a["endBinding"]["elementId"]
                  for a in document["elements"] if a.get("type") == "arrow"))
        check(f"{widget}: фигуры не наезжают друг на друга",
              _no_overlap(document, skill), _overlap_report(document, skill))
        check(f"{widget}: рисование повторяемо",
              json.dumps(build(widget, _SAMPLE, items, skill)[0], sort_keys=True)
              == json.dumps(document, sort_keys=True))
    if not drawn:
        checks.append(("рисование", True, "пропущено: наборов Мастерской нет рядом"))

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"  {'ок ' if ok else 'ПЛОХО'} {name}"
              + (f" — {detail}" if detail and not ok else ""))
    print(f"  проверок {len(checks)}, не прошло {len(failed)}")
    return 1 if failed else 0


def _tops(document: Dict[str, Any], skill) -> List[Dict[str, Any]]:
    """Верхние фигуры: те, что не лежат внутри другой."""
    shapes = [e for e in document["elements"]
              if e.get("type") in skill.TASK_SHAPES]
    nested = skill.nested_shapes(shapes)
    return [s for s in shapes if s["id"] not in nested]


def _no_overlap(document: Dict[str, Any], skill) -> bool:
    return not _overlap_report(document, skill)


def _overlap_report(document: Dict[str, Any], skill) -> str:
    tops = _tops(document, skill)
    for i, first in enumerate(tops):
        ax0, ay0, ax1, ay1 = skill.box_of(first)
        for second in tops[i + 1:]:
            bx0, by0, bx1, by1 = skill.box_of(second)
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                return f"{first['id']} и {second['id']}"
    return ""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"не читается {source}: {exc}") from exc


def excalidraw_command(args) -> int:
    if getattr(args, "self_test", False):
        return self_test()

    command = getattr(args, "excalidraw_command", None)
    if command == "schema":
        skill = _skill()
        for name, schema in SCHEMAS.items():
            print(f"  {name}: {schema['description']}")
            for field, spec in schema["properties"].items():
                need = " (обязательно)" if field in schema.get("required", ()) else ""
                print(f"    {field}: {spec['type']}{need} — {spec['description']}")
            print(f"    глубина {skill.schema_depth(schema)}, "
                  f"альтернатив {len(skill.schema_alternatives(schema))}")
        return 0
    if command == "list":
        for name, spec in WIDGETS.items():
            print(f"  {name:10} {spec['about']}")
            print(f"             набор {spec['library']}, фигуры по уровням: "
                  + ", ".join(spec["levels"]))
        return 0
    if command not in WIDGETS:
        print("  укажите виджет: " + ", ".join(WIDGETS), file=sys.stderr)
        return 2

    try:
        skill = _skill()
        spec = WIDGETS[command]
        library = skill.find_library(args.library, name=spec["library"],
                                     palette=args.palette)
        items = skill.load_library(library)
        document, notes = build(command, _read_text(args.text), items, skill)
        Path(args.out).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  набор: {library}")
        for note in notes:
            print(f"    ! {note}")
        print(f"  элементов: {len(document['elements'])}; записан {args.out}")
        return 0
    except (Refused, RuntimeError) as exc:
        print(f"  отказ: {exc}", file=sys.stderr)
        return 1


def add_parser(subparsers) -> argparse.ArgumentParser:
    """Зарегистрировать команду ``excalidraw``."""
    parser = subparsers.add_parser(
        "excalidraw",
        help="Виджеты Мастерской: mindmap, sequence, C4, roadmap и другие",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--self-test", action="store_true",
                        help="проверить команду на встроенном примере")
    sub = parser.add_subparsers(dest="excalidraw_command")

    sub.add_parser("list", help="Какие виджеты есть и из чего собраны")
    sub.add_parser("schema", help="Схема входа и её глубина")

    for name, spec in WIDGETS.items():
        one = sub.add_parser(name, help=spec["about"])
        one.add_argument("--text", required=True,
                         help="файл со строками схемы, или - для stdin")
        one.add_argument("--out", required=True, help="куда записать .excalidraw")
        one.add_argument("--library", default=None,
                         help="каталог наборов Мастерской")
        one.add_argument("--palette", default="carbon",
                         choices=("carbon", "paper", "signal"))

    parser.set_defaults(func=excalidraw_command)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="excalidraw")
    sub = parser.add_subparsers()
    add_parser(sub)
    args = parser.parse_args(argv or ["excalidraw", "--self-test"])
    return excalidraw_command(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["excalidraw", "--self-test"]))
