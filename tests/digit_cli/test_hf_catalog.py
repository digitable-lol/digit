"""Каталог открытых моделей: `digit model --catalog`.

Тесты закрывают три места, где ошибка тихая и дорогая.

Первое — состав записи. Каталог существует ради ответа на вопрос «зачем она,
чем подтверждено и чего она не умеет». Запись без границ читается как реклама,
и вместе с ней перестают верить числам соседних записей, поэтому пустой или
отсутствующий `limits` — это провал теста, а не пропуск поля.

Второе — что каталог совпадает с тем, что Digit реально скачивает. В
`local_model.WEIGHTS` лежат репозитории, которые команда `digit local start`
тянет с Hugging Face; если каталог о них молчит, человек читает список, в
котором нет как раз того, что у него на диске.

Третье — что печать не требует терминала. Это чтение справки, а не выбор
модели: она обязана работать в конвейере и в CI.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import pytest

from digit_cli import hf_catalog
from digit_cli import local_model as lm
from digit_cli.subcommands.model import build_model_parser


@pytest.fixture(scope="module")
def catalog() -> dict:
    return hf_catalog.load_catalog()


def test_catalog_ships_inside_the_package(catalog):
    """Файл лежит рядом с модулем, а не ищется в рабочем каталоге."""
    assert hf_catalog.CATALOG_PATH.is_file()
    assert hf_catalog.CATALOG_PATH.parent == Path(hf_catalog.__file__).parent
    assert catalog["org"] == "digitable-lol"


def test_every_entry_names_its_purpose_evidence_and_limits(catalog):
    """Состав записи обязателен целиком: «зачем», числа и граница."""
    for entry in catalog["entry"]:
        where = entry.get("id", "<без id>")
        for field in ("id", "kind", "role", "repo", "url", "title", "tagline",
                      "purpose", "base", "size", "license", "trained_on"):
            assert entry.get(field), f"{where}: пустое поле {field}"
        assert entry["kind"] in {"model", "dataset"}, where
        assert entry["url"].startswith("https://huggingface.co/"), where
        assert entry["repo"] in entry["url"], where

        limits = entry.get("limits") or []
        assert limits, (
            f"{where}: не названо ни одной границы. Запись, у которой перечислены "
            "только сильные стороны, — это реклама, а не карточка"
        )

        evidence = entry.get("evidence") or []
        assert evidence, f"{where}: нет ни одной улики"
        for item in evidence:
            assert item.get("value"), where
            assert item.get("claim"), where
            assert item.get("source"), (
                f"{where}: у числа {item.get('value')!r} не назван источник. "
                "Число без места, где оно посчитано, ничем не отличается от выдумки"
            )


def test_every_locally_served_model_is_in_the_catalogue(catalog):
    """Что Digit качает — то и объяснено.

    `digit local start --model router|specgen` тянет веса из репозиториев
    Hugging Face. Каталог, умалчивающий о них, оставляет человека без
    объяснения ровно того файла, который у него уже на диске.
    """
    listed = {entry["repo"] for entry in catalog["entry"]}
    for key, spec in lm.WEIGHTS.items():
        if not spec.repo.startswith("digitable-lol/"):
            continue  # чужие веса объясняет их владелец, а не мы
        assert spec.repo in listed, f"{key}: {spec.repo} не описан в каталоге"


def test_printed_view_keeps_the_limits(catalog):
    """Границы нельзя потерять при печати — ни флагом, ни форматированием."""
    text = hf_catalog.format_catalog(catalog, use_color=False)
    assert "Чего не умеет" in text
    assert text.count("Чего не умеет") == len(catalog["entry"])
    assert "Чем подтверждено" in text
    # Числа доезжают до вывода, а не остаются в файле.
    assert "150/150 против 79/150" in text
    assert "91,6 %" in text
    # Ни одна строка не шире обёртки: вывод читают в узком терминале и в логе.
    assert max(len(line) for line in text.splitlines()) <= hf_catalog.WRAP + 2


def test_catalog_flag_does_not_need_a_terminal():
    """Флаг разобран парсером и обрабатывается до проверки на TTY."""
    parser = argparse.ArgumentParser()
    build_model_parser(parser.add_subparsers(dest="command"), cmd_model=lambda args: 0)
    args = parser.parse_args(["model", "--catalog"])
    assert args.catalog is True
    assert parser.parse_args(["model"]).catalog is False


def test_portal_copy_is_byte_identical_when_present():
    """Источник правды один.

    Портал держит копию этого файла в `data/hf-catalog.toml`. Разошедшиеся
    списки обнаруживает не сборка, а читатель, сверивший страницу с выводом
    команды, — поэтому копия сверяется побайтово с обеих сторон. Здесь проверка
    мягкая: соседний клон портала есть не у всех, и его отсутствие не повод
    ронять тесты Digit.
    """
    for candidate in (
        Path(__file__).resolve().parents[2].parent / "courses" / "data" / "hf-catalog.toml",
        Path(__file__).resolve().parents[3] / "courses" / "data" / "hf-catalog.toml",
    ):
        if candidate.is_file():
            assert candidate.read_bytes() == hf_catalog.CATALOG_PATH.read_bytes(), (
                f"копия портала разошлась с оригиналом: {candidate}. "
                "Обновите её командой `npm run hf:catalog` в репозитории courses"
            )
            return
    pytest.skip("соседнего клона портала нет — сверять нечего")


def test_catalog_file_parses_with_limits_before_evidence():
    """limits обязан стоять ДО блоков [[entry.evidence]].

    TOML относит любой ключ после заголовка подтаблицы к этой подтаблице.
    limits, съехавший вниз, станет полем последней улики и молча исчезнет со
    страницы и из вывода — при полностью валидном файле.
    """
    with open(hf_catalog.CATALOG_PATH, "rb") as fh:
        raw = tomllib.load(fh)
    for entry in raw["entry"]:
        for item in entry.get("evidence", []):
            assert "limits" not in item, (
                f"{entry['id']}: limits съехал внутрь [[entry.evidence]] — "
                "переставьте его выше первого блока улик"
            )
