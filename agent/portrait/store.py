"""Где лежит портрет, кто его читает и что с ним при передаче сессии.

Портрет — самые чувствительные данные в системе: не «что человек делал», а
«как он устроен в речи». Поэтому он не подселяется к памяти, а живёт
отдельно, и отдельность здесь несёт нагрузку:

* ``$DIGIT_HOME/portrait/`` — своя директория с правами ``0700``, файлы
  ``0600``. ``memories/`` лежит рядом и попадает в ``digit backup``,
  ``agent_import`` и профили; портрет там не должен оказаться случайно.
* **Ничего не уезжает в системный промпт автоматически.** Коммит fb4b45a0d
  снял с памяти именно эту цену; заводить её заново ради портрета — значит
  платить за него в каждом запросе и вынести его в каждый лог провайдера.
  Читается портрет явно: командой ``digit portrait`` или инструментом.
* **Передача сессии другому человеку.** Сессия — это переписка, портрет —
  свойство владельца. Они не связаны: сессия ссылается на портрет только
  через ``session_id`` внутри записей, обратной ссылки нет. Поэтому
  передача сессии не передаёт портрет: другой человек работает под своим
  ``$DIGIT_HOME``, где ``portrait/`` либо пуст, либо его собственный. Если
  же передаётся сама машина — есть ``digit portrait forget``, и он умеет
  вычищать по ``session_id``, а не только целиком.
* Накопление **выключено по умолчанию**. Профиль речи не должен начать
  собираться оттого, что человек обновил пакет.

Формат — два файла, по одному на слой, который вообще хранится:

``style.json``      достаточные статистики слоя STYLE (счётчики, гистограммы)
``decisions.jsonl`` журнал слоя RECORD, только дописывание

Третьего файла нет и не будет. Догадка (``CONJECTURE``) не хранится нигде:
записанная догадка через неделю неотличима от записи решения, а именно этого
различия ради всё и построено. Она собирается на запрос и умирает с ним.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

DIR_MODE = 0o700
FILE_MODE = 0o600

STYLE_FILE = "style.json"
DECISIONS_FILE = "decisions.jsonl"


def portrait_dir(*, create: bool = False) -> Path:
    """Директория портрета в текущем профиле.

    Путь берётся динамически, а не константой на уровне модуля: смена
    профиля меняет ``DIGIT_HOME``, и закешированный путь молча писал бы
    портрет одного профиля в другой.
    """
    from digit_constants import get_digit_home

    path = get_digit_home() / "portrait"
    if create:
        path.mkdir(parents=True, exist_ok=True)
        _chmod(path, DIR_MODE)
    return path


def _chmod(path: Path, mode: int) -> None:
    """Выставить права, не роняя всё на файловой системе без прав.

    На Windows и на смонтированных FAT/exFAT ``chmod`` — no-op или ошибка.
    Портрет там останется с правами по умолчанию; это хуже, но это не повод
    отказать в работе, а повод сказать это вслух в ``digit portrait``.
    """
    try:
        os.chmod(path, mode)
    except OSError as exc:
        logger.debug("portrait: chmod %s failed: %s", path, exc)


def permissions_ok() -> bool:
    """Правда ли директория портрета закрыта от чужих глаз."""
    path = portrait_dir()
    if not path.exists():
        return True
    try:
        return (path.stat().st_mode & 0o077) == 0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Чтение / запись
# ---------------------------------------------------------------------------

def read_json(name: str) -> Optional[Dict[str, Any]]:
    """Прочитать JSON-файл портрета. ``None`` — нет файла или он битый.

    Битый файл возвращает ``None``, а не поднимает исключение: портрет —
    производное наблюдение, и падать в середине хода пользователя из-за него
    нельзя. Но и молча перезаписывать битый файл нельзя тоже, поэтому
    :func:`write_json` сначала уводит его в ``.bad``.
    """
    path = portrait_dir() / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("portrait: %s нечитаем (%s)", path.name, exc)
        return None


def write_json(name: str, payload: Dict[str, Any]) -> None:
    """Атомарно записать JSON-файл портрета.

    Через временный файл в той же директории + ``rename``: обрыв на записи
    не должен оставить наполовину записанный профиль, который потом молча
    прочитается как ``None`` и начнёт накапливаться с нуля.
    """
    directory = portrait_dir(create=True)
    path = directory / name
    if path.exists() and read_json(name) is None:
        _quarantine(path)
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _quarantine(path: Path) -> None:
    """Отложить нечитаемый файл вместо того, чтобы затереть его."""
    bad = path.with_suffix(path.suffix + ".bad")
    try:
        os.replace(path, bad)
        logger.warning("portrait: %s отложен как %s", path.name, bad.name)
    except OSError:
        pass


def append_line(name: str, payload: Dict[str, Any]) -> None:
    """Дописать строку в журнал портрета.

    Журнал только дописывается. Отмена прежней записи — это ещё одна
    строка (``{"t":"s"}``), а не правка старой: журнал происхождения,
    который переписывают, перестаёт быть свидетельством.
    """
    directory = portrait_dir(create=True)
    path = directory / name
    existed = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    if not existed:
        _chmod(path, FILE_MODE)


def iter_lines(name: str) -> Iterator[Dict[str, Any]]:
    """Прочитать журнал построчно, пропуская испорченные строки."""
    path = portrait_dir() / name
    try:
        handle = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("portrait: %s нечитаем (%s)", path.name, exc)
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def rewrite_lines(name: str, rows: list) -> int:
    """Переписать журнал целиком. Только для ``forget``.

    Единственная операция, которая нарушает «только дописывание», и она
    существует ровно затем, зачем нужна: убрать из портрета то, чего в нём
    быть не должно. Возвращает число оставшихся строк.
    """
    directory = portrait_dir(create=True)
    path = directory / name
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(rows)


def wipe() -> int:
    """Удалить портрет целиком. Возвращает число удалённых файлов."""
    directory = portrait_dir()
    if not directory.exists():
        return 0
    removed = 0
    for name in (STYLE_FILE, DECISIONS_FILE):
        try:
            (directory / name).unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("portrait: не удалось удалить %s: %s", name, exc)
    return removed
