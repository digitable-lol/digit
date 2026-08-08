"""Хвосты переезда документации: страница не должна называть чужие пути своими.

Форк унаследовал 395 страниц, и опасны в них не упоминания апстрима как
такового — ссылка на чужой issue или предупреждение про чужой образ на месте, —
а те места, где страница ВЕЛИТ читателю пойти по апстримовому пути так, будто
это наш: каталог установки, репозиторий для issue, имя соседнего навыка. Такой
промах молчалив с обеих сторон: команда выполняется, ссылка открывается, и
только результат оказывается не тот.

Вторая половина — расхождение локалей. Генератор страниц навыков
(``website/scripts/generate-skill-docs.py``) пишет только в ``website/docs`` и
о ``website/i18n`` не знает вовсе, поэтому китайские страницы правятся руками и
отстают молча. Здесь закреплено не «перевод полон» (он не полон, и это
отдельная работа), а то, что там, где перевод ГОВОРИТ о связанном навыке, он
говорит то же, что английская страница: сослаться на другой навык хуже, чем не
сослаться совсем.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBSITE = Path(__file__).resolve().parent.parent.parent / "website"
DOCS = WEBSITE / "docs"
ZH = WEBSITE / "i18n/zh-Hans/docusaurus-plugin-content-docs/current"

#: Строки, каждая из которых — указание читателю пойти не туда. Не путать с
#: упоминанием апстрима: оно законно и здесь не проверяется.
FORBIDDEN = (
    "~/.digit/hermes-agent",
    "--repo NousResearch/hermes-agent",
)

#: Ссылка, у которой подпись называет один репозиторий, а ведёт она в другой.
_LABELLED_LINK = re.compile(r"\[github\.com/([^\]]+)\]\(https://github\.com/([^)]+)\)")


def _owner_repo(path: str) -> str:
    """«owner/repo» из пути ссылки: подпись и цель часто расходятся хвостом
    (/issues, /releases), и это не то расхождение, которое здесь ищут."""
    return "/".join(path.strip("/").split("/")[:2])

_RELATED_ROW = re.compile(r"^\|\s*(?:Related skills|相关 skills)\s*\|(.*)\|\s*$", re.M)
_LINK_TARGET = re.compile(r"\((/[^)]+)\)")


def _pages() -> list[Path]:
    return sorted(DOCS.rglob("*.md")) + sorted(ZH.rglob("*.md"))


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_страница_не_ведёт_читателя_по_апстримовому_пути(forbidden):
    guilty = [str(page.relative_to(WEBSITE)) for page in _pages()
              if forbidden in page.read_text(encoding="utf-8")]
    assert not guilty, (
        f"{forbidden!r} — путь апстрима, выданный за наш, в: {guilty}. "
        "Упоминать апстрим можно, отправлять туда читателя нельзя."
    )


def test_подпись_ссылки_называет_тот_же_репозиторий_куда_ведёт():
    """Подпись «github.com/NousResearch/hermes-agent» над ссылкой на наш
    репозиторий — переезд, доведённый до половины: ведёт верно, называет чужое."""
    guilty = []
    for page in _pages():
        for label, target in _LABELLED_LINK.findall(page.read_text(encoding="utf-8")):
            if _owner_repo(label) != _owner_repo(target):
                guilty.append(f"{page.relative_to(WEBSITE)}: «{label}» → {target}")
    assert not guilty, guilty


def test_перевод_не_отправляет_к_другому_навыку():
    """Пустой строки «связанные навыки» в переводе тест не требует: переводы
    старше самой строки, и это недостача, а не ложь. Требуется другое — если
    строка есть, она ведёт туда же, куда английская."""
    guilty = []
    for zh_page in sorted(ZH.rglob("*.md")):
        en_page = DOCS / zh_page.relative_to(ZH)
        if not en_page.exists():
            continue
        zh_rows = _RELATED_ROW.findall(zh_page.read_text(encoding="utf-8"))
        if not zh_rows:
            continue
        en_rows = _RELATED_ROW.findall(en_page.read_text(encoding="utf-8"))
        zh_targets = [_LINK_TARGET.findall(row) for row in zh_rows]
        en_targets = [_LINK_TARGET.findall(row) for row in en_rows]
        if zh_targets != en_targets:
            guilty.append(f"{zh_page.relative_to(WEBSITE)}: {zh_targets} != {en_targets}")
    assert not guilty, guilty


def test_удалённый_навык_не_упоминается_ни_в_одной_локали():
    """apple-macos-computer-use удалён; ссылка на него была битой в переводе
    дольше, чем в оригинале, — ровно потому, что генератор перевод не трогает."""
    guilty = [str(page.relative_to(WEBSITE)) for page in _pages()
              if "apple-macos-computer-use" in page.read_text(encoding="utf-8")]
    assert not guilty, guilty
