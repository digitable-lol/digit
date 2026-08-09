"""Плоские схемы: требование модели-маршрутизатора Digit, закреплённое здесь.

Каталог утилит Digit подаётся модели по категориям, и схемы входа в нём
намеренно плоские — «без вложенности глубже двух уровней и без альтернатив в
описании входа». Источник требования — раздел о каталоге утилит в
``content/workbench/digit-integrations.md`` репозитория курсов.

Утилиты работы с картой Excalidraw добавляют в каталог четыре записи, и
проверить надо не только их схемы. Требование про **вход**, но нарушить его
можно и выходом: разбор карты, отдающий вложенную структуру, вынуждает
следующую утилиту принимать её на вход. Поэтому здесь проверяется и то, и
другое — схемы и форма разбора.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "skills/creative/excalidraw/scripts/tasks.py"

#: Предложение, которым требование сформулировано. Совпадать должно дословно:
#: если формулировку смягчат, эти проверки надо пересмотреть, а не молча
#: продолжать проверять то, чего больше не требуют.
REQUIREMENT = "без вложенности глубже двух уровней"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tasks = _load(SCRIPT, "excalidraw_tasks")

from digit_cli import excalidraw_cli as widgets  # noqa: E402

ALL_SCHEMAS = {**tasks.SCHEMAS, **widgets.SCHEMAS}


def _sources():
    """Файл с формулировкой требования — в соседнем чекауте курсов."""
    for parent in REPO.parents:
        if not parent.is_dir():
            continue
        try:
            bases = [parent, *(p for p in parent.iterdir() if p.is_dir())]
        except OSError:
            continue
        for base in bases:
            candidate = base / "content/workbench/digit-integrations.md"
            if candidate.is_file():
                return candidate
    return None


def test_the_requirement_still_says_what_these_tests_check():
    """Проверка, сверенная с источником, а не с памятью того, кто её писал."""
    source = _sources()
    if source is None:
        pytest.skip("чекаут курсов не найден рядом")
    assert REQUIREMENT in source.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(ALL_SCHEMAS))
def test_schema_is_not_deeper_than_two_levels(name):
    assert tasks.schema_depth(ALL_SCHEMAS[name]) <= 2


@pytest.mark.parametrize("name", sorted(ALL_SCHEMAS))
def test_schema_offers_no_alternatives(name):
    assert tasks.schema_alternatives(ALL_SCHEMAS[name]) == []


@pytest.mark.parametrize("name", sorted(ALL_SCHEMAS))
def test_every_argument_is_a_scalar(name):
    """Аргумент-объект или аргумент-массив-объектов и есть третий уровень."""
    for field, spec in ALL_SCHEMAS[name]["properties"].items():
        assert spec["type"] in ("string", "boolean", "number", "integer"), field
        assert "properties" not in spec, field
        assert "items" not in spec, field


@pytest.mark.parametrize("name", sorted(ALL_SCHEMAS))
def test_every_argument_is_described(name):
    """Маршрутизатор выбирает утилиту по описанию, а не по имени поля."""
    for field, spec in ALL_SCHEMAS[name]["properties"].items():
        assert spec.get("description", "").strip(), field


def test_the_depth_check_can_fail():
    """Проверка, которая не умеет провалиться, не доказывает ничего."""
    assert tasks.schema_depth({
        "type": "object",
        "properties": {"где": {"type": "object",
                               "properties": {"x": {"type": "number"}}}},
    }) == 3
    # Массив объектов уходит ещё на уровень глубже: сам массив, объект в нём и
    # его поле. Важно не точное число, а что оно больше двух.
    assert tasks.schema_depth({
        "type": "object",
        "properties": {"список": {"type": "array",
                                  "items": {"type": "object",
                                            "properties": {"x": {"type": "number"}}}}},
    }) > 2
    assert tasks.schema_alternatives(
        {"properties": {"x": {"oneOf": [{"type": "string"}]}}}) == ["oneOf"]


# -- разбор карты укладывается в те же рамки -------------------------------


def test_the_parse_of_a_map_is_flat():
    """Требование про вход, но нарушить его можно и выходом: вложенный разбор
    вынуждает следующую утилиту принимать вложенное."""
    plan = tasks.parse_map(tasks._sample_map())
    for node in plan["nodes"].values():
        for field, value in node.items():
            assert not isinstance(value, (dict, list)), f"{field}: {value!r}"
    for link in plan["links"]:
        assert set(link) == {"from", "to"}
        assert all(isinstance(v, str) for v in link.values())


def test_a_project_never_gets_a_third_level():
    """Рамка и группа дают два уровня. Точка внутри имени дала бы третий."""
    document = tasks._sample_map()
    for element in document["elements"]:
        if element["id"] == "frame1":
            element["name"] = "этап 2.1.черновой"
        if element["id"] == "gname":
            element["text"] = "разбор.первый"

    plan = tasks.parse_map(document)
    for node in plan["nodes"].values():
        assert (node["project"] or "").count(".") <= 1, node["project"]


def test_the_widget_input_has_one_form_not_a_choice_of_forms():
    """«Без альтернатив» — это и про текстовый вход тоже: одна форма строки на
    все шесть виджетов, а не своя грамматика у каждого."""
    assert len(widgets.SCHEMAS) == 1
    shapes = {tuple(sorted(spec.keys())) for spec in widgets.WIDGETS.values()}
    assert len(shapes) == 1, "у виджетов разошлось описание — вход перестал быть общим"
