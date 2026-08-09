"""Ни один навык не смеет звать инструмент FTS, которого нет.

История, ради которой этот файл существует. Навык ``fts`` перечислял модели
восемь инструментов — ``fts_check``, ``fts_compile``, ``fts_test``,
``fts_execute``, ``fts_generate``, ``fts_prove``, ``fts_certify``,
``fts_verify``. Ни один из них не существует ни на одном сервере проекта
(CHANGELOG, раздел Fixed; optional-mcps/fts-gate/manifest.yaml:38-42). Навык
поправили и накрыли тестом — но тест смотрел РОВНО В ОДИН ФАЙЛ и проверял
присутствие двух правильных имён, а не отсутствие восьми выдуманных. Поэтому
та же выдумка спокойно досидела в соседнем навыке ``fts-constitution``,
откуда генератор перенёс её на сайт.

Промах здесь молчаливый и дорогой: модель зовёт несуществующий инструмент,
получает отказ протокола и — в лучшем случае — выдумывает результат сама.
Никакой прогон тестов этого не показывает, потому что вызывать некому.

Поэтому сторож устроен наоборот прежнему:

* список разрешённых имён НЕ записан здесь. Он снимается с манифестов
  optional-mcps/*/manifest.yaml, то есть печатается из продукта. Появится у
  сервера третий инструмент — сторож пропустит его сам; исчезнет второй —
  сторож начнёт ловить упоминания, и это правильно;
* ищется не присутствие правильного, а ОТСУТСТВИЕ неизвестного, и не в одном
  файле, а во всех навыках и во всех сгенерированных из них страницах сайта;
* назвать выдуманное имя всё-таки можно — но только чтобы его опровергнуть.
  Отрицание распознаётся по абзацу, а не по строке: предложение «There is no
  ``fts_check``, ``fts_compile``, …» переносится через несколько строк, и
  построчная проверка объявила бы нарушением его же продолжение.

ОТДЕЛЬНО — ОПЕРАЦИЯ НЕ ЕСТЬ ИНСТРУМЕНТ. Первая версия этого сторожа поймала
``fts_extract_examples`` в навыке ``ouroboros-tracing`` и была неправа.
Ouroboros выставляет ТРИ инструмента (``ouroboros_capabilities``,
``ouroboros_describe``, ``ouroboros_invoke``), за которыми стоят девятнадцать
ОПЕРАЦИЙ, сгруппированных по темам; ``fts_extract_examples`` — операция группы
``fts``, её имя едет аргументом ``operation``, а не именем вызова. Схема ровно
та же, что у ``digit-tools-core``. Поэтому файл, документирующий маршрутизатор
(в нём есть инструмент вида ``*_invoke``), из проверки имён инструментов
исключается — и исключение это заработанное, а не дарёное: тест ниже
удостоверяется, что маршрутизатор там действительно объявлен.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFESTS = sorted((REPO / "optional-mcps").glob("*/manifest.yaml"))

# Абзац, в котором имя ОПРОВЕРГАЕТСЯ, а не предлагается к вызову.
DENIALS = (
    "there is no",
    "there are no",
    "no such tool",
    "none of those",
    "never existed",
    "does not exist",
    "do not exist",
    "не существует",
    "таких инструментов нет",
)

TOOL_NAME = re.compile(r"\bfts_[a-z][a-z0-9_]*\b")

# Точка входа маршрутизатора: её наличие означает, что имена в файле — это
# операции за одним инструментом, а не сами инструменты.
ROUTER_TOOL = re.compile(r"\b[a-z][a-z0-9_]*_invoke\b")

SCANNED = (
    ("skills", "**/SKILL.md"),
    ("optional-skills", "**/SKILL.md"),
    ("website/docs/user-guide/skills", "**/*.md"),
)


def _declared_tools() -> set[str]:
    """Имена инструментов, снятые с манифестов, а не выписанные сюда руками."""
    names: set[str] = set()
    for manifest in MANIFESTS:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            # Только пункты списка: комментарии манифеста как раз перечисляют
            # выдуманные имена, чтобы объяснить, почему их нет.
            if stripped.startswith("- ") and TOOL_NAME.fullmatch(stripped[2:].strip()):
                names.add(stripped[2:].strip())
    return names


def _paragraphs(text: str) -> list[str]:
    return re.split(r"\n\s*\n", text)


def _offenders(declared: set[str]) -> list[str]:
    found: list[str] = []
    for root, pattern in SCANNED:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            if ROUTER_TOOL.search(text):
                continue  # операции за маршрутизатором — см. шапку файла
            for para in _paragraphs(text):
                lowered = para.casefold()
                if any(marker in lowered for marker in DENIALS):
                    continue
                for name in sorted(set(TOOL_NAME.findall(para))):
                    if name not in declared:
                        found.append(f"{path.relative_to(REPO)}: {name}")
    return found


def test_the_product_declares_some_fts_tools_so_the_guard_is_not_vacuous():
    declared = _declared_tools()
    assert MANIFESTS, "манифестов MCP нет — сторожу неоткуда взять правду"
    assert declared, (
        "ни один манифест не объявляет инструмент fts_* — тогда этот сторож "
        "пропускает любое имя и только делает вид, что сторожит"
    )


def test_no_skill_or_generated_page_names_an_fts_tool_that_does_not_exist():
    declared = _declared_tools()
    offenders = _offenders(declared)

    assert not offenders, (
        "Навык называет инструмент FTS, которого нет ни на одном сервере "
        f"проекта (объявлены: {sorted(declared)}):\n  " + "\n  ".join(offenders) + "\n"
        "Модель вызовет его, получит отказ протокола и допишет результат "
        "сама. Либо назовите настоящий инструмент, либо опровергните имя "
        "вслух — абзац с отрицанием сторож пропускает."
    )


def test_a_denial_paragraph_is_still_allowed_to_name_the_ghosts():
    """Навык ``fts`` объясняет, каких имён нет, — и обязан сохранить эту возможность.

    Без этого послабления единственным способом пройти сторожа было бы
    молчание, а молчание тут хуже: имена уже разошлись по чужим страницам, и
    следующий участник впишет их снова, не найдя ни слова против.
    """
    fts_skill = REPO / "skills/software-development/fts/SKILL.md"
    body = fts_skill.read_text(encoding="utf-8")
    denial = next(p for p in _paragraphs(body) if "there is no" in p.casefold())

    ghosts = set(TOOL_NAME.findall(denial)) - _declared_tools()
    assert ghosts, "абзац-опровержение перестал называть опровергаемые имена"
    assert f"{fts_skill.relative_to(REPO)}: {sorted(ghosts)[0]}" not in _offenders(_declared_tools())


def test_the_router_exemption_is_earned_and_not_granted():
    """Навык, освобождённый как маршрутизатор, обязан маршрутизатором и быть.

    Иначе освобождение — дыра: достаточно упомянуть где-нибудь слово с
    ``_invoke``, чтобы файл перестал проверяться целиком. Здесь проверяется
    единственный сегодняшний случай: Ouroboros называет свои ТРИ инструмента и
    зовёт спорное имя операцией, а не инструментом.
    """
    skill = REPO / "skills/software-development/ouroboros-tracing/SKILL.md"
    body = skill.read_text(encoding="utf-8")

    assert ROUTER_TOOL.search(body), "освобождение снято — файл больше не документирует маршрутизатор"
    for tool in ("ouroboros_capabilities", "ouroboros_describe", "ouroboros_invoke"):
        assert tool in body, f"маршрутизатор объявлен, но инструмент {tool} не назван"
    # Имя, из-за которого понадобилось различение, обязано остаться операцией:
    # оно приходит аргументом ``operation``, и группа ``fts`` — его группа.
    assert "fts_extract_examples" in body
    assert "operation" in body
