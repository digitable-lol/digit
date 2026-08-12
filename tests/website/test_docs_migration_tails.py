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

#: Подпись строки о связанных навыках. Вариантов шесть, потому что перевод
#: правился руками и в разное время: у 60 страниц «相关 skill», у 5 «相关
#: skills», у 3 «相关技能». Первая редакция сторожа знала только «Related
#: skills» и «相关 skills» — то есть 5 строк из 68, а про остальные 63 молчала.
#: Молчание тут неотличимо от успеха: тест был зелёным не потому, что переводы
#: сходятся, а потому, что он их не открывал. Соседнее «相关工作» (related
#: work) — не эта строка и сюда намеренно не попадает.
_RELATED_ROW = re.compile(
    r"^\|\s*(?:Related\s+skills?|相关\s*skills?|相关技能)\s*\|(.*)\|\s*$", re.M
)
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


#: Репозиторий в ссылке: «owner/repo» из https://github.com/…
_GITHUB_REPO = re.compile(r"https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")

#: Две страницы, где адрес есть только в переводе ЗАКОННО. Список намеренно
#: короткий и с причиной у каждой строки: поблажка без причины через полгода
#: неотличима от недосмотра. Устройство то же, что у COUNTEREXAMPLES сторожа
#: обещаний на портале, и проверяется тоже в обе стороны — см. тест ниже.
_ADDRESS_ONLY_IN_TRANSLATION = {
    # Абзац про закрытый апстримовый PR #12550 как про вытесненное решение.
    # Английская страница этот абзац потеряла, перевод сохранил. Упоминать
    # апстрим законно (см. FORBIDDEN выше: запрещено ОТПРАВЛЯТЬ туда читателя,
    # а не называть), поэтому это недостача английской страницы, а не ложь
    # перевода.
    ("developer-guide/browser-supervisor.md", "NousResearch/hermes-agent"),
    # Перевод старой редакции страницы: она не содержала материал, а отсылала
    # к SKILL.md в нашем же репозитории. Адрес живой, ведёт к нам; английская
    # страница с тех пор вобрала материал внутрь. Это недостача перевода —
    # чинится переводом новой редакции, а не правкой ссылки.
    ("user-guide/skills/bundled/productivity/productivity-powerpoint.md",
     "digitable-lol/digit"),
    # «Нашли баг — заведите issue» со ссылкой на НАШ трекер. Ровно то, чего
    # добивался переезд; английская страница такой фразы просто не имеет.
    ("getting-started/installation.md", "digitable-lol/digit"),
    # Ссылка на skills/autonomous-ai-agents/computer-use/SKILL.md в нашем
    # репозитории — путь проверен, существует. Английская страница ссылается
    # только на сторонний trycua/cua.
    ("user-guide/features/computer-use.md", "digitable-lol/digit"),
}


def test_перевод_не_называет_адреса_которого_нет_в_оригинале():
    """Английская страница навыка порождается генератором из SKILL.md, то есть
    она — источник истины; перевод правится руками и отстаёт молча. Поэтому
    репозиторий, названный ТОЛЬКО в переводе, — это адрес, которого источник
    уже не обещает, и читатель уходит по нему не глядя.

    Обратное (в оригинале адрес есть, в переводе нет) намеренно НЕ требуется:
    это недостача, а не ложь, — то же правило, что у строки о связанных
    навыках выше.

    Пойманное этим тестом при заведении, каждое — свой промах молчанием:
    ``digit-ai/digit`` (организации не существует, 404) вместо
    ``digitable-lol/digit``; ``nicholasgasior/gws`` под подписью «Google
    Workspace CLI» вместо ``googleworkspace/cli``; ``outlines-dev/outlines``
    после переезда в ``dottxt-ai``; ``NVIDIA/NeMo-Curator`` после переезда в
    ``NVIDIA-NeMo/Curator``; ``blackboxaicode/cli`` вместе с чужим именем
    npm-пакета; ``VoltAgent/awesome-agent-skills`` в списке tap «по
    умолчанию», где его нет у продукта (``tools/skills_hub.py`` знает это имя
    только как подпись, а не как tap).
    """
    guilty = []
    for zh_page in sorted(ZH.rglob("*.md")):
        rel = zh_page.relative_to(ZH).as_posix()
        en_page = DOCS / zh_page.relative_to(ZH)
        if not en_page.exists():
            continue
        zh_repos = set(_GITHUB_REPO.findall(zh_page.read_text(encoding="utf-8")))
        en_repos = set(_GITHUB_REPO.findall(en_page.read_text(encoding="utf-8")))
        for repo in sorted(zh_repos - en_repos):
            if (rel, repo) in _ADDRESS_ONLY_IN_TRANSLATION:
                continue
            guilty.append(f"{rel}: «{repo}» есть в переводе и нет в оригинале")
    assert not guilty, guilty


def test_поблажка_не_переживает_причину():
    """Поблажка выше держится ровно до тех пор, пока расхождение есть. Как
    только английская страница вернёт адрес себе (или перевод его потеряет),
    строка в списке станет мусором и начнёт покрывать уже настоящий промах —
    поэтому список проверяется и с этой стороны."""
    stale = []
    for rel, repo in sorted(_ADDRESS_ONLY_IN_TRANSLATION):
        zh_page, en_page = ZH / rel, DOCS / rel
        if not zh_page.exists() or not en_page.exists():
            stale.append(f"{rel}: страницы больше нет — поблажку пора убрать")
            continue
        zh_repos = set(_GITHUB_REPO.findall(zh_page.read_text(encoding="utf-8")))
        en_repos = set(_GITHUB_REPO.findall(en_page.read_text(encoding="utf-8")))
        if repo not in zh_repos - en_repos:
            stale.append(f"{rel}: «{repo}» больше не расходится — поблажку пора убрать")
    assert not stale, stale


def test_удалённый_навык_не_упоминается_ни_в_одной_локали():
    """apple-macos-computer-use удалён; ссылка на него была битой в переводе
    дольше, чем в оригинале, — ровно потому, что генератор перевод не трогает."""
    guilty = [str(page.relative_to(WEBSITE)) for page in _pages()
              if "apple-macos-computer-use" in page.read_text(encoding="utf-8")]
    assert not guilty, guilty
