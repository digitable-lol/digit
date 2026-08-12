"""Своя документация не должна тихо съехать обратно на апстримовую.

Переезд сделан: docs.digitable.life поднят, CNAME лежит в website/static,
deploy-docs отпущен форку, и около 190 ссылок в справке, подсказках, дашборде
и на страницах навыков переписаны шестью коммитами 2026-08-07. Осталось
свойство, которого переезд сам по себе не даёт: НИЧТО не мешало вписать
апстримовый адрес обратно.

Мешать надо именно этому классу, а не упоминаниям апстрима вообще. Апстрим
называется в проекте законно и часто: HTTP-Referer провайдерам остаётся
апстримовым намеренно (tests/run_agent/test_provider_attribution_headers.py
держит это четырьмя утверждениями), setup.hermes-agent.nousresearch.com —
живая служба, портал и inference-хосты — тоже. Сторож здесь узкий: адрес
ЧУЖОЙ ДОКУМЕНТАЦИИ, то есть hermes-agent.nousresearch.com/docs. Читателя,
которого туда отправили, ждут 395 страниц про другого агента.

Три сегодняшних вхождения разрешены не списком, а причиной, и причина
проверяется:

* два — запасной адрес в загрузке skills-index.json (website/scripts и
  workflow). Запасной он лишь до тех пор, пока НАШ адрес в том же файле стоит
  раньше; переставь их местами — и запасной станет основным, а сторож
  покраснеет;
* одно — строка CHANGELOG, описывающая сам переезд. Это история, а не ссылка.

Всякое четвёртое вхождение — новое, и тест назовёт файл.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

UPSTREAM_DOCS = "hermes-agent.nousresearch.com/docs"
OUR_DOCS = "docs.digitable.life"

# Файлы, где запасной адрес допустим — но только как ЗАПАСНОЙ (см. ниже).
BOOTSTRAP_FALLBACKS = (
    Path("website/scripts/prebuild.mjs"),
    Path(".github/workflows/deploy-site.yml"),
)

# Файл, где адрес назван как факт истории, а не как ссылка для читателя.
HISTORY = Path("CHANGELOG.md")

# И сам сторож: чтобы искать строку, он обязан её содержать. Путь вычисляется,
# а не вписан, — переименование файла не должно тихо выключать проверку.
# Пойман этот случай не чтением: в одиночку файл был ещё не в индексе, git
# ls-files его не отдавал, и тест зеленел; покраснел он на первом же общем
# прогоне после коммита.
SELF = Path(__file__).resolve().relative_to(REPO)


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


def _files_naming_upstream_docs() -> dict[Path, list[str]]:
    hits: dict[Path, list[str]] = {}
    for rel in _tracked_files():
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # двоичное или нечитаемое — ссылкой быть не может
        lines = [ln.strip() for ln in text.splitlines() if UPSTREAM_DOCS in ln]
        if lines:
            hits[rel] = lines
    return hits


def test_no_new_place_sends_the_reader_to_the_upstream_docs_site():
    allowed = {*BOOTSTRAP_FALLBACKS, HISTORY, SELF}
    found = _files_naming_upstream_docs()
    strangers = sorted(str(p) for p in found if p not in allowed)

    assert not strangers, (
        "Адрес чужой документации вернулся в: "
        + ", ".join(strangers)
        + f". Там 395 страниц про другого агента. Своя документация — {OUR_DOCS}."
    )


# Оба разрешённых файла тянут ОДИН и тот же ресурс двумя адресами подряд.
# Сравнивать надо положение именно этих двух строк, а не первое упоминание
# хоста в файле: выше по тексту наш адрес стоит ещё и в комментарии, и по нему
# порядок выглядел бы правильным, даже если список перевёрнут. Первая версия
# теста ловилась ровно на этом — нарочная перестановка её не покраснила.
INDEX_PATH = "/api/skills-index.json"


def test_the_bootstrap_fallback_is_still_a_fallback_and_not_the_primary():
    """Разрешение держится на порядке, поэтому порядок и проверяется."""
    ours = f"https://{OUR_DOCS}{INDEX_PATH}"
    theirs = f"https://{UPSTREAM_DOCS}{INDEX_PATH}"

    for rel in BOOTSTRAP_FALLBACKS:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert theirs in text, f"{rel}: запасного адреса нет — обновите список разрешённых"
        assert ours in text, f"{rel}: своего адреса нет вовсе, значит апстримовый и есть основной"
        assert text.index(ours) < text.index(theirs), (
            f"{rel}: апстримовый адрес индекса идёт РАНЬШЕ своего — запасной стал основным"
        )


def test_the_history_mention_stays_prose_and_not_a_live_link():
    text = (REPO / HISTORY).read_text(encoding="utf-8")
    naming = [ln.strip() for ln in text.splitlines() if UPSTREAM_DOCS in ln]

    assert naming, "CHANGELOG перестал упоминать переезд — уберите файл из разрешённых"
    for line in naming:
        assert f"https://{UPSTREAM_DOCS}" not in line, (
            f"строка CHANGELOG стала настоящей ссылкой, а не рассказом о переезде: {line}"
        )


def test_the_guard_names_the_forbidden_host_only_as_a_constant():
    """Освобождение сторожа для самого себя не должно стать лазейкой.

    Файл обязан содержать искомую строку, но не смеет быть ещё одной ссылкой:
    здесь она живёт в UPSTREAM_DOCS без схемы.
    """
    text = (REPO / SELF).read_text(encoding="utf-8")

    assert f"https://{UPSTREAM_DOCS}" not in text.replace(f'f"https://{{UPSTREAM_DOCS}}"', ""), (
        "сторож сам стал ссылкой на чужую документацию"
    )


def test_our_own_docs_host_is_actually_used_somewhere():
    """Иначе сторож зелен на дереве, где документации нет вовсе."""
    users = [rel for rel in _tracked_files() if rel.suffix in {".py", ".ts", ".tsx", ".mjs", ".yml"}]
    hits = 0
    for rel in users:
        try:
            if OUR_DOCS in (REPO / rel).read_text(encoding="utf-8"):
                hits += 1
        except (UnicodeDecodeError, OSError):
            continue

    assert hits > 1, f"{OUR_DOCS} почти нигде не используется — переезд не состоялся"
