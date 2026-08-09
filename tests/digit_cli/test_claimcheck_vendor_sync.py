"""Сторож дословности перенесённого claimcheck.

NOTICE раздела 1 обещает: claimparse.py и ftsemit.py перенесены байт в байт,
и «проверяется это не обещанием, а сравнением sha256 с оригиналом». Обещание
было, сравнения не было — файлы лежали в дереве ровно как обычный код,
который любой участник поправит «на месте», не заметив, чем платит.

Платит он числами. Раздел 4 NOTICE и шапка ``digit_cli/claimcheck/__init__``
называют 1 161 замер, ноль тихих ошибок и 1 698 пойманных испорченных
утверждений — и все они сняты харнессом digit-ml, прогнанным против ИМЕННО
этого разбора и ИМЕННО этой печати. Правка одной строки в claimparse.py не
роняет ни одного теста и не меняет ни одной буквы NOTICE: цифры остаются
напечатанными, а относиться начинают к коду, которого больше нет. Это не
ошибка вычисления, это молчаливая ложь в поставке, и поймать её нечем, кроме
дайджеста.

Поэтому здесь два разных сторожа, и они ловят разное:

* запись VENDORED.sha256 против дерева — работает везде и всегда, ловит
  правку копии;
* запись против оригинала — работает только на машине, где площадка
  экспериментов лежит рядом, и ловит случай, когда запись обновили заодно
  с правкой, то есть заглушили первого сторожа.

Второй пропускается, а не падает, когда оригинала нет: он про синхронизацию
двух репозиториев, и в CI, где второго репозитория не существует, его
падение означало бы только «здесь нет /home/a», а не расхождение.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "digit_cli" / "claimcheck"
RECORD = PKG / "VENDORED.sha256"
NOTICE = PKG / "NOTICE"

# Площадка экспериментов, названная в NOTICE и в шапке пакета. Переменная
# окружения — чтобы сторож работал и у того, чей чекаут лежит не там.
ORIGIN = Path(os.environ.get("DIGIT_CLAIMCHECK_ORIGIN", "/home/a/projects/digit-ml/claimcheck"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record() -> dict[str, str]:
    """Разбор формата ``sha256sum`` — того же, в котором запись ведёт оригинал."""
    out: dict[str, str] = {}
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        out[name.strip()] = digest.strip()
    return out


def test_the_record_exists_and_is_not_empty():
    assert RECORD.is_file(), f"{RECORD} — запись дайджестов, без неё сторожить нечем"
    assert _record(), "запись есть, но пуста — это сторож, который всегда зелёный"


def test_vendored_files_still_match_the_recorded_digest():
    record = _record()
    drifted = []
    for name, expected in sorted(record.items()):
        path = PKG / name
        assert path.is_file(), f"{name} записан в VENDORED.sha256, но в пакете его нет"
        actual = _digest(path)
        if actual != expected:
            drifted.append(f"{name}: записано {expected[:12]}…, в дереве {actual[:12]}…")

    assert not drifted, (
        "Дословно перенесённый файл изменился: "
        + "; ".join(drifted)
        + ". Числа NOTICE раздела 4 сняты на прежнем коде — либо верните файл, "
        "либо перемеряйте харнессом digit-ml и обновите И запись, И числа."
    )


def test_the_record_covers_exactly_the_files_the_notice_calls_verbatim():
    """Запись и проза обязаны говорить об одном наборе файлов.

    Иначе NOTICE называет дословным один список, а сторожится другой — и
    добавленный третий файл окажется без охраны при том, что документ уверяет
    в обратном.
    """
    line = next(
        (ln for ln in NOTICE.read_text(encoding="utf-8").splitlines() if "ДОСЛОВНО" in ln),
        None,
    )
    assert line, "NOTICE больше не называет ни одного файла дословным — сторож остался без предмета"

    named = set(re.findall(r"[\w.-]+\.(?:py|mjs)", line))
    assert named == set(_record()), (
        f"NOTICE называет дословными {sorted(named)}, а запись сторожит {sorted(_record())}"
    )


@pytest.mark.skipif(not ORIGIN.is_dir(), reason=f"площадка экспериментов недоступна: {ORIGIN}")
def test_the_record_still_matches_the_original_snapshot():
    """Тот самый «сравнение sha256 с оригиналом», который обещал NOTICE."""
    diverged = []
    for name, expected in sorted(_record().items()):
        src = ORIGIN / name
        if not src.is_file():
            diverged.append(f"{name}: в оригинале файла нет")
            continue
        actual = _digest(src)
        if actual != expected:
            diverged.append(f"{name}: оригинал {actual[:12]}…, запись {expected[:12]}…")

    assert not diverged, (
        "Копия и оригинал разошлись: "
        + "; ".join(diverged)
        + ". Синхронизация здесь ручная и осознанная (NOTICE, раздел 5) — "
        "перенесите изменение целиком и перемеряйте, а не подгоняйте запись."
    )
