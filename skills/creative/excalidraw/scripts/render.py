#!/usr/bin/env python3
"""``render.py`` — показать ``.excalidraw`` там, где идёт разговор.

ЗАЧЕМ ЭТО НУЖНО
---------------
Петля «владелец рисует грубые сущности — Digit доводит их до схемы» собрана из
двух половин: ``revise.py`` правит файл поэлементно, ``canvas.py`` открывает
ровно тот же путь в браузере. Обе половины про **правку**, и ни одна не про
**показ**: пока открытого холста нет, схема существует только как JSON. Агент,
объясняющий устройство системы, вынужден пересказывать словами картинку,
которую сам же и нарисовал, а владелец — открывать браузер, чтобы взглянуть на
три прямоугольника.

Здесь показ. Тот же файл, никакой сети: документ переводится в SVG, а SVG
показывается в терминале через ``chafa``.

ЧЕГО ЗДЕСЬ БОЯТСЯ
-----------------
Того же, чего боятся соседние скрипты, — **молчаливой потери**. У показа она
своя, и опаснее правки: испорченный файл рано или поздно заметят, а картинка,
на которой чего-то нет, выглядит целой. Отсюда три решения:

1. **Ни один элемент не исчезает молча.** Тип, который здесь рисовать нечем,
   получает пунктирную рамку с именем типа и строку в отчёте. Читающий картинку
   видит, что в этом месте что-то есть, — а не ровный фон.

2. **Подпись берётся у контейнера, а не у себя.** Текст внутри фигуры несёт
   собственные ``x``/``y``/``width``, но приложение пересчитывает их при
   загрузке (об этом прямо сказано в SKILL.md). Рисовать по своим координатам
   значит показывать владельцу не то, что он увидит на холсте: подпись уезжает
   из фигуры тем дальше, чем больше её двигали.

3. **К картинке всегда идёт легенда.** Терминал — это ~80 колонок; шрифт в 16
   пикселей в них не помещается физически, и надпись превращается в серую
   полоску. Поэтому рядом печатается то, что на схеме написано, и то, что на
   ней соединено: подписи в порядке чтения и связи стрелок по их привязкам.
   Легенда — не украшение, а единственная часть вывода, которую можно прочесть
   и в трубе, и в логе, и моделью.

ЧЕГО ЗДЕСЬ НЕТ, И ЭТО НАМЕРЕННО
-------------------------------
Рисованности. Excalidraw ведёт линию через roughjs, разбивая её на дрожащие
отрезки по ``seed`` элемента; здесь линии ровные. Повторять roughjs ради
терминала незачем: на клетке 8×16 дрожание неотличимо от растрового шума.
Картинка верна по составу, расположению, порядку и цвету — и не верна по
почерку. То же с ``roundness`` у ломаных: кривая Безье заменена прямой.

ПОЧЕМУ ЭТО СКРИПТ НАВЫКА, А НЕ КОМАНДА ``digit``
------------------------------------------------
По лестнице следа (AGENTS.md, «The Footprint Ladder») — первая ступень:
возможность целиком выражается shell-командой рядом с двумя такими же, схема
инструментов модели из-за неё расти не должна.

ЗАПУСК
------
    render.py show DIAGRAM.excalidraw            # картинка в терминале + легенда
    render.py show DIAGRAM.excalidraw --size 60x30
    render.py legend DIAGRAM.excalidraw          # только легенда, без картинки
    render.py svg DIAGRAM.excalidraw --out d.svg # промежуточный SVG
    render.py png DIAGRAM.excalidraw --out d.png # растр (нужен rsvg-convert)

Коды возврата: 0 — показано, 1 — отказ с причиной, 2 — плохие аргументы.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Отступ вокруг схемы, в единицах документа.
PADDING = 16

#: Зазор между рамкой фигуры и текстом внутри неё. Столько же берёт приложение
#: (``BOUND_TEXT_PADDING``); от этого зависит, куда встанет подпись.
BOUND_TEXT_PADDING = 5

#: Межстрочный интервал по умолчанию — как в приложении для рукописного шрифта.
DEFAULT_LINE_HEIGHT = 1.25

#: Семейства шрифтов Excalidraw. Рукописного Virgil на машине нет и быть не
#: обязано, поэтому здесь родовые имена: почерк всё равно не воспроизводится.
_FONT_FAMILIES = {
    1: "sans-serif",
    2: "Helvetica, Arial, sans-serif",
    3: "monospace",
    5: "sans-serif",
    6: "monospace",
    7: "Helvetica, Arial, sans-serif",
    8: "sans-serif",
}

#: Что умеем рисовать. Всё остальное получает рамку-заглушку и строку отчёта —
#: список намеренно закрытый, чтобы новый тип элемента был замечен, а не пропущен.
_DRAWN_TYPES = frozenset(
    {"rectangle", "ellipse", "diamond", "line", "arrow", "text", "freedraw",
     "image", "frame", "magicframe"}
)

#: Типы, у которых геометрия задаётся списком точек, а не прямоугольником.
_LINEAR_TYPES = frozenset({"line", "arrow", "freedraw"})


class Refused(RuntimeError):
    """Действие не выполнено, и причину стоит напечатать."""


# --------------------------------------------------------------------------
# Файл на диске
# --------------------------------------------------------------------------


def load_document(path: Path) -> Dict[str, Any]:
    """Прочитать ``.excalidraw``. Формат проверяется так же, как в ``revise.py``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"не читается {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} — не JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise Refused(f"{path} — не документ Excalidraw (ожидался объект)")
    if not isinstance(document.get("elements"), list):
        raise Refused(f"{path} — нет массива 'elements'")
    return document


# --------------------------------------------------------------------------
# Разбор документа
# --------------------------------------------------------------------------


def _f(value: Any, default: float = 0.0) -> float:
    """Число из поля документа. Чужой файл — не повод падать на None."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def elements_to_draw(document: Dict[str, Any]) -> Tuple[List[Dict[str, Any]],
                                                        List[Dict[str, Any]],
                                                        List[Dict[str, Any]]]:
    """Разложить элементы на три стопки: рисуемые, удалённые, незнакомые.

    Удалённые (``isDeleted``) не рисует и само приложение — это не потеря.
    Незнакомые рисуются заглушкой и попадают в отчёт: вот это было бы потерей.
    """
    drawn: List[Dict[str, Any]] = []
    deleted: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    for element in document["elements"]:
        if not isinstance(element, dict):
            continue
        if element.get("isDeleted"):
            deleted.append(element)
            continue
        if element.get("type") in _DRAWN_TYPES:
            drawn.append(element)
        else:
            unsupported.append(element)
    return drawn, deleted, unsupported


def _points(element: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Точки ломаной в координатах документа."""
    x, y = _f(element.get("x")), _f(element.get("y"))
    raw = element.get("points")
    if not isinstance(raw, list) or len(raw) < 2:
        return [(x, y), (x + _f(element.get("width")), y + _f(element.get("height")))]
    out: List[Tuple[float, float]] = []
    for point in raw:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append((x + _f(point[0]), y + _f(point[1])))
    return out or [(x, y)]


def element_box(element: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """Габариты элемента без учёта поворота."""
    if element.get("type") in _LINEAR_TYPES:
        pts = _points(element)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    x, y = _f(element.get("x")), _f(element.get("y"))
    return x, y, x + _f(element.get("width")), y + _f(element.get("height"))


def bounds(elements: Sequence[Dict[str, Any]],
           by_id: Optional[Dict[str, Dict[str, Any]]] = None
           ) -> Tuple[float, float, float, float]:
    """Габариты схемы. Поворот учитывается описанной окружностью — грубо в
    большую сторону, потому что срезанный угол хуже лишнего поля.

    Подпись, привязанная к фигуре, в габариты не входит: её собственные
    координаты в файле — те, что оставило приложение до пересчёта, и они
    бывают сколь угодно далеко. Раздувшийся холст незаметен по числам и очень
    заметен глазом: схема съезжает в угол пустого поля.
    """
    boxes: List[Tuple[float, float, float, float]] = []
    for element in elements:
        if element.get("type") == "text" and by_id is not None:
            container = by_id.get(element.get("containerId"))
            if isinstance(container, dict):
                continue
        x0, y0, x1, y1 = element_box(element)
        angle = _f(element.get("angle"))
        if angle:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            radius = math.hypot(x1 - x0, y1 - y0) / 2
            x0, y0, x1, y1 = cx - radius, cy - radius, cx + radius, cy + radius
        stroke = _f(element.get("strokeWidth"), 2.0) / 2
        boxes.append((x0 - stroke, y0 - stroke, x1 + stroke, y1 + stroke))
    if not boxes:
        return 0.0, 0.0, 0.0, 0.0
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _by_id(document: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        e["id"]: e for e in document["elements"]
        if isinstance(e, dict) and isinstance(e.get("id"), str)
    }


# --------------------------------------------------------------------------
# Текст: где он на самом деле окажется
# --------------------------------------------------------------------------


def _lines(element: Dict[str, Any]) -> List[str]:
    """Строки подписи.

    Берётся ``text``, а не ``originalText``: приложение хранит в ``text`` уже
    разложенный по ширине контейнера вариант, и именно его владелец видит.
    """
    text = element.get("text")
    if not isinstance(text, str):
        text = element.get("originalText")
    if not isinstance(text, str):
        return []
    return text.split("\n")


def text_layout(element: Dict[str, Any],
                container: Optional[Dict[str, Any]]) -> Tuple[float, float, str, float]:
    """Куда встанет подпись: (x, y верхней строки, выравнивание, поворот).

    Без контейнера — по собственным координатам. С контейнером — по контейнеру:
    приложение пересчитывает ``x``/``y`` подписи при загрузке, и записанные в
    файле числа могут быть сколь угодно устаревшими.
    """
    font_size = _f(element.get("fontSize"), 20.0)
    line_height = _f(element.get("lineHeight"), DEFAULT_LINE_HEIGHT) or DEFAULT_LINE_HEIGHT
    block = max(1, len(_lines(element))) * font_size * line_height
    align = element.get("textAlign") if isinstance(element.get("textAlign"), str) else None

    if container is None:
        return (_f(element.get("x")), _f(element.get("y")),
                align or "left", _f(element.get("angle")))

    if container.get("type") in _LINEAR_TYPES:
        # Подпись стрелки живёт на её середине, а не там, где её оставили.
        pts = _points(container)
        middle = pts[len(pts) // 2] if len(pts) % 2 else _midpoint(pts)
        return (middle[0], middle[1] - block / 2, align or "center",
                _f(container.get("angle")))

    cx0, cy0 = _f(container.get("x")), _f(container.get("y"))
    width, height = _f(container.get("width")), _f(container.get("height"))
    align = align or "center"
    if align == "left":
        x = cx0 + BOUND_TEXT_PADDING
    elif align == "right":
        x = cx0 + width - BOUND_TEXT_PADDING
    else:
        x = cx0 + width / 2

    vertical = container.get("verticalAlign") or element.get("verticalAlign") or "middle"
    if vertical == "top":
        y = cy0 + BOUND_TEXT_PADDING
    elif vertical == "bottom":
        y = cy0 + height - BOUND_TEXT_PADDING - block
    else:
        y = cy0 + (height - block) / 2
    return x, y, align, _f(container.get("angle"))


def _midpoint(pts: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    first, last = pts[0], pts[-1]
    return ((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _color(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


def _dash(element: Dict[str, Any]) -> str:
    width = _f(element.get("strokeWidth"), 2.0) or 2.0
    style = element.get("strokeStyle")
    if style == "dashed":
        return f' stroke-dasharray="{_n(width * 4)} {_n(width * 4)}"'
    if style == "dotted":
        return f' stroke-linecap="round" stroke-dasharray="0 {_n(width * 3)}"'
    return ""


def _n(value: float) -> str:
    """Число для SVG. Округление до сотых — чтобы вывод был побайтово
    воспроизводим и годился на сравнение дифом."""
    rounded = round(value + 0.0, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def _transform(element: Dict[str, Any]) -> str:
    angle = _f(element.get("angle"))
    if not angle:
        return ""
    x0, y0, x1, y1 = element_box(element)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return f' transform="rotate({_n(math.degrees(angle))} {_n(cx)} {_n(cy)})"'


class _Fills:
    """Штриховки. Excalidraw заливает фигуру косыми штрихами, а не сплошным
    цветом, и на расстоянии это различимо: штрихованная фигура светлее. Каждый
    цвет получает свой ``<pattern>``; имена шаблонов выдаются по порядку
    обращения, поэтому вывод остаётся воспроизводимым."""

    def __init__(self) -> None:
        self.defs: List[str] = []
        self._known: Dict[Tuple[str, str], str] = {}

    def paint(self, color: str, style: Any) -> str:
        if color == "transparent":
            return "none"
        if style in (None, "solid"):
            return color
        key = (color, str(style))
        if key not in self._known:
            name = f"fill{len(self._known)}"
            self._known[key] = name
            self.defs.append(self._pattern(name, color, str(style)))
        return f"url(#{self._known[key]})"

    @staticmethod
    def _pattern(name: str, color: str, style: str) -> str:
        strokes = ['<path d="M0,8 L8,0" stroke="%s" stroke-width="1.6"/>' % color]
        if style == "cross-hatch":
            strokes.append('<path d="M0,0 L8,8" stroke="%s" stroke-width="1.6"/>' % color)
        return (f'<pattern id="{name}" width="8" height="8" '
                f'patternUnits="userSpaceOnUse">{"".join(strokes)}</pattern>')


def _arrowhead(kind: Any, tip: Tuple[float, float], prev: Tuple[float, float],
               color: str, width: float) -> str:
    """Наконечник на конце отрезка. Неизвестный вид рисуется как ``arrow``:
    видимый наконечник не той формы честнее исчезнувшего."""
    if not kind:
        return ""
    angle = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
    size = 12 + width * 2
    if kind in ("dot", "circle", "circle_outline"):
        return (f'<circle cx="{_n(tip[0])}" cy="{_n(tip[1])}" r="{_n(size / 3)}" '
                f'fill="{color}"/>')
    if kind == "bar":
        left = (tip[0] + math.cos(angle + math.pi / 2) * size / 2,
                tip[1] + math.sin(angle + math.pi / 2) * size / 2)
        right = (tip[0] - math.cos(angle + math.pi / 2) * size / 2,
                 tip[1] - math.sin(angle + math.pi / 2) * size / 2)
        return (f'<path d="M{_n(left[0])},{_n(left[1])} L{_n(right[0])},{_n(right[1])}" '
                f'stroke="{color}" stroke-width="{_n(width)}" fill="none"/>')
    wing = math.radians(28)
    left = (tip[0] - math.cos(angle - wing) * size, tip[1] - math.sin(angle - wing) * size)
    right = (tip[0] - math.cos(angle + wing) * size, tip[1] - math.sin(angle + wing) * size)
    if kind in ("triangle", "triangle_outline"):
        return (f'<path d="M{_n(tip[0])},{_n(tip[1])} L{_n(left[0])},{_n(left[1])} '
                f'L{_n(right[0])},{_n(right[1])} Z" fill="{color}" stroke="{color}" '
                f'stroke-width="{_n(width)}"/>')
    return (f'<path d="M{_n(left[0])},{_n(left[1])} L{_n(tip[0])},{_n(tip[1])} '
            f'L{_n(right[0])},{_n(right[1])}" fill="none" stroke="{color}" '
            f'stroke-width="{_n(width)}" stroke-linecap="round"/>')


def _shape(element: Dict[str, Any], fills: _Fills) -> str:
    kind = element.get("type")
    x, y = _f(element.get("x")), _f(element.get("y"))
    width, height = _f(element.get("width")), _f(element.get("height"))
    stroke = _color(element.get("strokeColor"), "#1e1e1e")
    stroke_width = _f(element.get("strokeWidth"), 2.0)
    fill = fills.paint(_color(element.get("backgroundColor"), "transparent"),
                       element.get("fillStyle"))
    common = (f' fill="{fill}" stroke="{stroke}" stroke-width="{_n(stroke_width)}"'
              + _dash(element) + _transform(element))

    if kind == "ellipse":
        return (f'<ellipse cx="{_n(x + width / 2)}" cy="{_n(y + height / 2)}" '
                f'rx="{_n(abs(width) / 2)}" ry="{_n(abs(height) / 2)}"{common}/>')
    if kind == "diamond":
        pts = [(x + width / 2, y), (x + width, y + height / 2),
               (x + width / 2, y + height), (x, y + height / 2)]
        path = " ".join(f"{_n(px)},{_n(py)}" for px, py in pts)
        return f'<polygon points="{path}"{common}/>'
    radius = ""
    if isinstance(element.get("roundness"), dict) and width and height:
        radius = f' rx="{_n(min(32.0, min(abs(width), abs(height)) * 0.25))}"'
    return (f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(abs(width))}" '
            f'height="{_n(abs(height))}"{radius}{common}/>')


def _linear(element: Dict[str, Any], fills: _Fills) -> str:
    pts = _points(element)
    stroke = _color(element.get("strokeColor"), "#1e1e1e")
    stroke_width = _f(element.get("strokeWidth"), 2.0)
    path = " ".join(f"{'M' if i == 0 else 'L'}{_n(px)},{_n(py)}"
                    for i, (px, py) in enumerate(pts))
    closed = element.get("type") == "freedraw" or (
        element.get("type") == "line" and len(pts) > 2 and pts[0] == pts[-1])
    fill = fills.paint(_color(element.get("backgroundColor"), "transparent"),
                       element.get("fillStyle")) if closed else "none"
    out = [f'<path d="{path}" fill="{fill}" stroke="{stroke}" '
           f'stroke-width="{_n(stroke_width)}" stroke-linejoin="round" '
           f'stroke-linecap="round"{_dash(element)}{_transform(element)}/>']
    if element.get("type") == "arrow":
        out.append(_arrowhead(element.get("startArrowhead"), pts[0], pts[1],
                              stroke, stroke_width))
        out.append(_arrowhead(element.get("endArrowhead", "arrow"), pts[-1], pts[-2],
                              stroke, stroke_width))
    return "".join(part for part in out if part)


def _text(element: Dict[str, Any], container: Optional[Dict[str, Any]],
          backdrop: Optional[str] = None) -> str:
    lines = _lines(element)
    if not lines:
        return ""
    font_size = _f(element.get("fontSize"), 20.0)
    line_height = _f(element.get("lineHeight"), DEFAULT_LINE_HEIGHT) or DEFAULT_LINE_HEIGHT
    x, top, align, angle = text_layout(element, container)
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(align, "start")
    family = _FONT_FAMILIES.get(element.get("fontFamily"), "sans-serif")
    color = _color(element.get("strokeColor"), "#1e1e1e")
    spans = []
    if backdrop:
        # Подпись стрелки: приложение разрывает под ней линию. Разрыва здесь
        # нет, поэтому под текст кладётся фон — иначе линия перечёркивает
        # надпись, и в терминале от неё остаётся серая полоса.
        span = max(len(line) for line in lines) * font_size * 0.55
        left = {"middle": x - span / 2, "end": x - span}.get(anchor, x)
        spans.append(f'<rect x="{_n(left - 3)}" y="{_n(top)}" width="{_n(span + 6)}" '
                     f'height="{_n(font_size * line_height * len(lines))}" '
                     f'fill="{backdrop}"/>')
    for index, line in enumerate(lines):
        # Базовая линия: середина строки плюс треть кегля — так строка стоит в
        # своём межстрочном боксе там же, где её ставит приложение.
        baseline = top + font_size * line_height * (index + 0.5) + font_size * 0.34
        spans.append(f'<text x="{_n(x)}" y="{_n(baseline)}" font-size="{_n(font_size)}" '
                     f'font-family="{family}" fill="{color}" '
                     f'text-anchor="{anchor}">{_escape(line)}</text>')
    body = "".join(spans)
    if angle:
        cx, cy = x, top + font_size * line_height * len(lines) / 2
        return (f'<g transform="rotate({_n(math.degrees(angle))} {_n(cx)} {_n(cy)})">'
                f'{body}</g>')
    return body


def _image(element: Dict[str, Any], document: Dict[str, Any]) -> str:
    """Картинка. Данные лежат не в элементе, а в ``files`` документа; если их
    там нет — рамка с именем, а не пустое место."""
    files = document.get("files")
    entry = files.get(element.get("fileId")) if isinstance(files, dict) else None
    url = entry.get("dataURL") if isinstance(entry, dict) else None
    x, y = _f(element.get("x")), _f(element.get("y"))
    width, height = _f(element.get("width")), _f(element.get("height"))
    if isinstance(url, str) and url:
        return (f'<image x="{_n(x)}" y="{_n(y)}" width="{_n(width)}" '
                f'height="{_n(height)}" href="{_escape(url)}" '
                f'preserveAspectRatio="none"{_transform(element)}/>')
    return _placeholder(element, "image: нет данных в files")


def _placeholder(element: Dict[str, Any], caption: str) -> str:
    """Рамка на месте того, что нарисовать нечем. Смысл — не украсить, а не
    дать читателю принять неполную картинку за полную."""
    x0, y0, x1, y1 = element_box(element)
    if x1 - x0 < 1 or y1 - y0 < 1:
        x1, y1 = x0 + 120, y0 + 60
    return (f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(x1 - x0)}" '
            f'height="{_n(y1 - y0)}" fill="none" stroke="#868e96" stroke-width="1.5" '
            f'stroke-dasharray="6 4"/>'
            f'<text x="{_n((x0 + x1) / 2)}" y="{_n((y0 + y1) / 2)}" font-size="14" '
            f'font-family="sans-serif" fill="#868e96" text-anchor="middle">'
            f'{_escape(caption)}</text>')


def to_svg(document: Dict[str, Any], *, padding: float = PADDING) -> str:
    """Собрать SVG. Порядок элементов сохраняется — в Excalidraw он же z-порядок."""
    drawn, _deleted, unsupported = elements_to_draw(document)
    by_id = _by_id(document)
    fills = _Fills()
    appstate = document.get("appState")
    background = _color(
        appstate.get("viewBackgroundColor") if isinstance(appstate, dict) else None,
        "#ffffff")

    body: List[str] = []
    for element in drawn + unsupported:
        kind = element.get("type")
        opacity = _f(element.get("opacity"), 100.0)
        if kind == "text":
            container_id = element.get("containerId")
            container = by_id.get(container_id) if isinstance(container_id, str) else None
            on_line = isinstance(container, dict) and container.get("type") in _LINEAR_TYPES
            fragment = _text(element, container, background if on_line else None)
        elif kind in _LINEAR_TYPES:
            fragment = _linear(element, fills)
        elif kind == "image":
            fragment = _image(element, document)
        elif kind in ("frame", "magicframe"):
            fragment = _placeholder(element, str(element.get("name") or "frame"))
        elif kind in _DRAWN_TYPES:
            fragment = _shape(element, fills)
        else:
            fragment = _placeholder(element, f"{kind}: рисовать нечем")
        if not fragment:
            continue
        if opacity < 100:
            fragment = f'<g opacity="{_n(opacity / 100)}">{fragment}</g>'
        body.append(fragment)

    x0, y0, x1, y1 = bounds(drawn + unsupported, by_id)
    x0, y0 = x0 - padding, y0 - padding
    width = max(1.0, x1 - x0 + padding)
    height = max(1.0, y1 - y0 + padding)

    defs = f"<defs>{''.join(fills.defs)}</defs>" if fills.defs else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}" '
        f'height="{_n(height)}" viewBox="{_n(x0)} {_n(y0)} {_n(width)} {_n(height)}">'
        f'{defs}'
        f'<rect x="{_n(x0)}" y="{_n(y0)}" width="{_n(width)}" height="{_n(height)}" '
        f'fill="{background}"/>'
        f'{"".join(body)}'
        f'</svg>\n'
    )


# --------------------------------------------------------------------------
# Легенда: то, что можно прочесть без картинки
# --------------------------------------------------------------------------


def _label_of(element: Optional[Dict[str, Any]],
              by_id: Dict[str, Dict[str, Any]]) -> str:
    """Подпись фигуры: своя, либо привязанного к ней текста."""
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
                return " ".join(child["text"].split())
    return ""


def legend(document: Dict[str, Any]) -> Dict[str, Any]:
    """Схема словами: подписи в порядке чтения и связи по привязкам стрелок.

    Это единственная часть вывода, переживающая трубу, лог и модель: растр в
    терминале для них — либо мусор, либо ничего.
    """
    drawn, deleted, unsupported = elements_to_draw(document)
    by_id = _by_id(document)

    links: List[Dict[str, Any]] = []
    loose = 0
    for element in drawn:
        if element.get("type") != "arrow":
            continue
        ends: List[str] = []
        bound = 0
        for key in ("startBinding", "endBinding"):
            binding = element.get(key)
            target = by_id.get(binding.get("elementId")) if isinstance(binding, dict) else None
            if isinstance(target, dict):
                bound += 1
            ends.append(_label_of(target, by_id) or (target or {}).get("id") or "?")
        if not bound:
            # Стрелка, нарисованная рядом с фигурами, но не привязанная к ним,
            # выглядит связью и связью не является: сдвиньте фигуру — стрелка
            # останется на месте. Молча пропускать такое нельзя.
            loose += 1
            continue
        links.append({"id": element.get("id"), "from": ends[0], "to": ends[1],
                      "text": _label_of(element, by_id)})

    shown = {link["id"] for link in links}
    labels: List[Dict[str, Any]] = []
    for element in drawn:
        if element.get("type") == "text" and isinstance(element.get("containerId"), str):
            continue  # покажется как подпись своей фигуры
        if element.get("id") in shown:
            continue  # подпись стрелки уже прочитана в разделе связей
        text = _label_of(element, by_id)
        if not text:
            continue
        x0, y0, _x1, _y1 = element_box(element)
        labels.append({"id": element.get("id"), "type": element.get("type"),
                       "text": text, "x": x0, "y": y0})
    labels.sort(key=lambda item: (round(item["y"] / 24), item["x"]))

    return {
        "elements": len(drawn),
        "labels": labels,
        "links": links,
        "unbound_arrows": loose,
        "deleted": len(deleted),
        "unsupported": sorted({str(e.get("type")) for e in unsupported}),
    }


def format_legend(data: Dict[str, Any]) -> str:
    lines = [f"  схема: {data['elements']} элемент(ов)"]
    if data["labels"]:
        lines.append("  надписи (сверху вниз):")
        for item in data["labels"]:
            lines.append(f"    {item['type']:<10} {item['text']}")
    if data["links"]:
        lines.append("  связи:")
        for link in data["links"]:
            note = f"  — {link['text']}" if link["text"] else ""
            lines.append(f"    {link['from']} → {link['to']}{note}")
    if data["unbound_arrows"]:
        lines.append(f"  стрелок без привязки: {data['unbound_arrows']}"
                     " — они выглядят связью, но за фигурой не поедут")
    if data["deleted"]:
        lines.append(f"  скрыто удалённых элементов: {data['deleted']}"
                     " (их не показывает и приложение)")
    if data["unsupported"]:
        lines.append("  НАРИСОВАНО ЗАГЛУШКОЙ, тип не поддержан: "
                     + ", ".join(data["unsupported"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Показ
# --------------------------------------------------------------------------


def terminal_size(explicit: Optional[str]) -> Tuple[int, int]:
    """Размер картинки в клетках. Явное значение сильнее вычисленного."""
    if explicit:
        try:
            cols, rows = explicit.lower().split("x", 1)
            return max(8, int(cols)), max(4, int(rows))
        except ValueError as exc:
            raise Refused(f"--size ждёт ШИРИНАxВЫСОТА, получено {explicit!r}") from exc
    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(8, min(size.columns, 120)), max(4, size.lines - 3)


def chafa_argv(path: Path, cols: int, rows: int) -> List[str]:
    """Команда показа. Вынесена отдельно, чтобы её можно было проверить, не
    заводя терминал."""
    return ["chafa", "--size", f"{cols}x{rows}", "--animate", "off", str(path)]


def show(document: Dict[str, Any], *, size: Optional[str] = None,
         stdout: Any = None) -> None:
    """Нарисовать схему в терминале. Нет chafa — отказ с именем пакета, а не
    пустой экран."""
    if shutil.which("chafa") is None:
        raise Refused(
            "не найден chafa — без него схему в терминале показать нечем. "
            "Установите его (apt install chafa / brew install chafa) либо "
            "получите файл: render.py svg ... --out d.svg"
        )
    stream = stdout or sys.stdout
    cols, rows = terminal_size(size)
    handle, name = tempfile.mkstemp(suffix=".svg", prefix="excalidraw-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(to_svg(document))
        result = subprocess.run(chafa_argv(Path(name), cols, rows),
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise Refused(f"chafa не смог показать SVG: {result.stderr.strip()}")
        stream.write(result.stdout)
    finally:
        os.unlink(name)


def to_png(document: Dict[str, Any], out: Path, *, scale: float = 1.0) -> None:
    """Растр для тех, кто не терминал: страница курса, загрузка, зрение модели."""
    if shutil.which("rsvg-convert") is None:
        raise Refused(
            "не найден rsvg-convert — растр собрать нечем. "
            "Установите librsvg2-bin либо возьмите SVG: render.py svg ... --out d.svg"
        )
    handle, name = tempfile.mkstemp(suffix=".svg", prefix="excalidraw-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(to_svg(document))
        result = subprocess.run(
            ["rsvg-convert", "--zoom", str(scale), "-o", str(out), name],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise Refused(f"rsvg-convert не смог собрать растр: {result.stderr.strip()}")
    finally:
        os.unlink(name)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="Показать схему в терминале")
    p_show.add_argument("path", type=Path)
    p_show.add_argument("--size", default=None, help="ШИРИНАxВЫСОТА в клетках")
    p_show.add_argument("--no-legend", action="store_true",
                        help="только картинка, без разбора словами")

    p_legend = sub.add_parser("legend", help="Только разбор словами, без картинки")
    p_legend.add_argument("path", type=Path)
    p_legend.add_argument("--json", action="store_true")

    p_svg = sub.add_parser("svg", help="Собрать SVG")
    p_svg.add_argument("path", type=Path)
    p_svg.add_argument("--out", type=Path, default=None, help="по умолчанию — stdout")

    p_png = sub.add_parser("png", help="Собрать растр (нужен rsvg-convert)")
    p_png.add_argument("path", type=Path)
    p_png.add_argument("--out", type=Path, required=True)
    p_png.add_argument("--scale", type=float, default=1.0)

    args = parser.parse_args(argv)

    try:
        document = load_document(args.path)
        if args.command == "svg":
            svg = to_svg(document)
            if args.out:
                args.out.write_text(svg, encoding="utf-8")
                print(f"  записан {args.out}")
            else:
                sys.stdout.write(svg)
            return 0
        if args.command == "png":
            to_png(document, args.out, scale=args.scale)
            print(f"  записан {args.out}")
            return 0
        if args.command == "legend":
            data = legend(document)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(format_legend(data))
            return 0

        show(document, size=args.size)
        if not args.no_legend:
            print(format_legend(legend(document)))
        return 0
    except Refused as exc:
        print(f"  отказ: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"  ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
