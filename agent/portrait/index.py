"""Журнал решений перестаёт перечитываться целиком на каждый поиск.

Почему это понадобилось
-----------------------
Поиск по решениям был линейным: прочитать весь ``decisions.jsonl``, разложить
каждую запись в объект, разбить каждую цитату на слова и каждое слово свести к
основе. На 650 записях это 3 мс чтения и 16 мс поиска; на 10 000 — 65 мс и
253 мс. Растёт это ровно так, как и должно расти перебором, а платит за него
ход пользователя: инструмент ``portrait`` вызывается в разговоре, и четверть
секунды на запрос — это уже пауза, которую видно.

Приём взят там же, где он уже работает: память Digit ищется индексом FTS5
(коммит fb4b45a0d, :mod:`agent.memory_recall`), и там поиск по 800 заметкам
стоит 0,11–0,19 мс. Переносится он целиком, потому что у обеих задач одна
форма — короткие русские тексты, лексический поиск, обязательная работа
офлайн. Общего кода ровно столько, сколько и было: стеммер и сборка
префиксного запроса из :mod:`digit_cli.kb.lexical`.

Что здесь НЕ взято из памяти
----------------------------
Ранжирование. У памяти выдачу упорядочивает ``bm25``; у решений — своя
формула, в которой «сделано» весит больше «сказано», а отменённое опускается
вниз. Менять её заодно с ускорением значило бы поменять ответы команды под
видом ускорения, и потом не отличить одно от другого. Поэтому FTS5 здесь
только **сужает**: он отдаёт пул кандидатов, а порядок внутри пула считает та
же функция, что и раньше. Отсюда и проверяемое требование к правке: выдача на
тех же запросах обязана совпасть с прежней.

Индекс — кэш, а не второе хранилище
-----------------------------------
Источник истины — ``decisions.jsonl``, и он остаётся журналом, который только
дописывают. Индекс хранит строки журнала как есть и пересобирается из него
целиком; потерять его нельзя ничем, кроме времени на пересборку. Свежесть
сверяется по размеру и времени изменения журнала: обычный путь (дописали
запись — дописали её же в индекс) пересборки не вызывает вообще, а сверка
ловит правку мимо этого пути.

Приватность не меняется: индекс лежит в том же ``$DIGIT_HOME/portrait/`` с
правами 0600, никуда не уезжает и удаляется вместе с портретом.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from . import store

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

#: Сколько записей отдаётся на ранжирование. Пул нужен потому, что последнее
#: слово не за индексом: «сделано» весит больше «сказано», а отменённое
#: опускается вниз — это считает :func:`agent.portrait.decisions.search`.
#: Отбирается пул в том же порядке, в каком потом ранжируют: сначала по числу
#: совпавших термов, при равенстве — по свежести. Поэтому переставить выдачу
#: внутри пула поправки могут, а вытолкнуть из него нужную запись — только
#: если записей с максимальным совпадением больше двухсот.
POOL = 200

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
    rid        TEXT NOT NULL,
    text       TEXT NOT NULL,   -- индексируемая форма: цитата + предметы
    row        TEXT NOT NULL,   -- строка журнала дословно
    ts         INTEGER NOT NULL DEFAULT 0,
    superseded TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_records_rid ON records(rid);

-- ``tokenchars`` не косметика. В индексе лежат основы, посчитанные Python-ом
-- по тому же правилу, что и при переборе, и среди них есть составные:
-- ``digit_cli/main.py``, ``прокси-сервер``. Токенизатор по умолчанию разрезал
-- бы их по ``/``, ``-``, ``.``, ``_`` — и сравнивались бы уже не те основы,
-- то есть выдача индекса разошлась бы с перебором на составных именах, ради
-- которых решения и записывают.
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    text,
    content='records',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2 tokenchars '-_./'"
);
"""


#: Что от записи попадает в индекс. Задаётся снаружи — :mod:`decisions`
#: передаёт сюда ту же функцию, которой считает основы при переборе. Держать
#: здесь второе определение значило бы завести два правила сравнения, которые
#: обязаны совпадать, но ничем к этому не принуждены.
TextOf = Callable[[Dict[str, Any]], str]


class DecisionIndex:
    """FTS5 над журналом решений. Журнал — источник истины, это — кэш."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    # -- соединение ---------------------------------------------------------

    def connect(self, *, _retry: bool = True) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        store.portrait_dir(create=True)
        try:
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA_SQL)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            # Файл производный: единственный разумный ответ на битый — снести
            # и пересобрать. Жить с постоянно падающим индексом значило бы
            # потерять поиск навсегда из-за одного оборванного копирования.
            if not _retry:
                raise
            logger.warning("portrait: индекс решений непригоден (%s); пересобираю", exc)
            self.drop()
            return self.connect(_retry=False)
        if version is None:
            conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                         (SCHEMA_VERSION,))
            conn.commit()
        elif version["value"] != SCHEMA_VERSION:
            conn.close()
            self.drop()
            return self.connect(_retry=_retry)
        self._chmod()
        self._conn = conn
        return conn

    def _chmod(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(str(self.path) + suffix, store.FILE_MODE)
            except OSError:
                pass

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def drop(self) -> None:
        """Снести индекс вместе с журналом WAL."""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except (FileNotFoundError, OSError):
                pass

    # -- свежесть -----------------------------------------------------------

    def _journal_stamp(self) -> str:
        """Отпечаток журнала: размер и время изменения.

        Не sha256 содержимого: на 10 000 записей это несколько миллисекунд на
        каждой сверке, а сверка случается перед каждым поиском. Обычный путь
        записи держит индекс в согласии сам (дописали в журнал — дописали в
        индекс), и отпечатку остаётся ловить правку мимо этого пути, для чего
        размера и mtime достаточно.
        """
        try:
            info = (store.portrait_dir() / store.DECISIONS_FILE).stat()
        except OSError:
            return "нет журнала"
        return f"{info.st_size}:{info.st_mtime_ns}"

    def _stamp(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('journal', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._journal_stamp(),),
        )

    def ensure_fresh(
        self,
        rows: Callable[[], Iterable[Dict[str, Any]]],
        text_of: TextOf,
    ) -> bool:
        """Сверить индекс с журналом, пересобрав при расхождении. True — пересобрали."""
        conn = self.connect()
        stored = conn.execute("SELECT value FROM meta WHERE key = 'journal'").fetchone()
        if stored is not None and stored["value"] == self._journal_stamp():
            return False
        self.rebuild(rows(), text_of)
        return True

    # -- запись -------------------------------------------------------------

    def rebuild(self, rows: Iterable[Dict[str, Any]], text_of: TextOf) -> int:
        """Пересобрать индекс из строк журнала. Возвращает число записей."""
        conn = self.connect()
        count = 0
        with conn:
            conn.execute("DELETE FROM records")
            supersedes: List[tuple] = []
            for row in rows:
                kind = row.get("t")
                if kind == "d":
                    conn.execute(
                        "INSERT INTO records(rid, text, row, ts) VALUES (?, ?, ?, ?)",
                        (str(row.get("id", "")), text_of(row),
                         json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                         int(row.get("ts", 0) or 0)),
                    )
                    count += 1
                elif kind == "s":
                    supersedes.append((str(row.get("by", "")), str(row.get("id", ""))))
            # external-content FTS не чистится каскадом за DELETE: 'rebuild' —
            # единственный способ не оставить призраков удалённых записей.
            conn.execute("INSERT INTO records_fts(records_fts) VALUES ('rebuild')")
            if supersedes:
                conn.executemany(
                    "UPDATE records SET superseded = ? WHERE rid = ?", supersedes)
            self._stamp(conn)
        self._chmod()
        return count

    def add(self, row: Dict[str, Any], text_of: TextOf) -> None:
        """Дописать в индекс запись, только что дописанную в журнал."""
        conn = self.connect()
        text = text_of(row)
        with conn:
            cur = conn.execute(
                "INSERT INTO records(rid, text, row, ts) VALUES (?, ?, ?, ?)",
                (str(row.get("id", "")), text,
                 json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                 int(row.get("ts", 0) or 0)),
            )
            conn.execute(
                "INSERT INTO records_fts(rowid, text) VALUES (?, ?)",
                (int(cur.lastrowid), text),
            )

    def mark_superseded(self, record_id: str, by: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute("UPDATE records SET superseded = ? WHERE rid = ?",
                         (by, record_id))

    def stamp(self) -> None:
        """Зафиксировать, что индекс соответствует текущему журналу."""
        conn = self.connect()
        with conn:
            self._stamp(conn)

    # -- чтение -------------------------------------------------------------

    def candidates(self, exprs: Sequence[str], *, pool: int = POOL) -> List[Dict[str, Any]]:
        """Записи, у которых совпал хоть один терм, — по убыванию совпадения.

        Выражение приходит по одному на ТЕРМ запроса, а не одно на запрос
        целиком. Иначе не сосчитать совпадение: FTS5 говорит «эта запись
        подходит», но не говорит, сколькими термами, а именно это число и
        есть основа прежнего порядка. Один запрос на терм, ``UNION ALL`` и
        ``COUNT`` дают его точно — каждая запись попадает в объединение по
        разу за терм, сколько бы раз терм в ней ни встретился.
        """
        if not exprs:
            return []
        conn = self.connect()
        union = " UNION ALL ".join(
            ["SELECT rowid FROM records_fts WHERE records_fts MATCH ?"] * len(exprs)
        )
        try:
            rows = conn.execute(
                f"WITH hits(rowid) AS ({union}) "
                "SELECT r.row AS row, r.superseded AS superseded, "
                "COUNT(*) AS overlap "
                "FROM hits h JOIN records r ON r.rowid = h.rowid "
                "GROUP BY h.rowid ORDER BY overlap DESC, r.ts DESC LIMIT ?",
                (*exprs, pool),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # Кривое выражение не должно ронять ход: без поиска по решениям
            # агент работает хуже, без ответа — вообще никак.
            logger.debug("portrait: запрос к индексу не удался (%s): %s", exprs, exc)
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["row"])
            except ValueError:
                continue
            if row["superseded"]:
                payload = dict(payload, superseded_by=row["superseded"])
            out.append(payload)
        return out

    def count(self) -> int:
        return int(self.connect().execute(
            "SELECT COUNT(*) AS n FROM records").fetchone()["n"])


_INDEX: Optional[DecisionIndex] = None
_INDEX_PATH: Optional[Path] = None


def open_index() -> Optional[DecisionIndex]:
    """Индекс текущего профиля, или ``None``, если FTS5 недоступен.

    Путь пересчитывается на каждый вызов: смена профиля меняет ``DIGIT_HOME``,
    и закешированное соединение искало бы решения одного профиля в другом.
    """
    global _INDEX, _INDEX_PATH
    path = store.portrait_dir() / store.INDEX_FILE
    if _INDEX is not None and _INDEX_PATH == path:
        return _INDEX
    if _INDEX is not None:
        _INDEX.close()
        _INDEX = None
    candidate = DecisionIndex(path)
    try:
        candidate.connect()
    except sqlite3.DatabaseError as exc:
        # Сборка SQLite без FTS5 существует. Поиск обязан работать и на ней —
        # просто перебором, как раньше.
        logger.warning("portrait: индекс решений недоступен (%s); поиск перебором", exc)
        return None
    _INDEX, _INDEX_PATH = candidate, path
    return _INDEX


def forget() -> None:
    """Забыть открытый индекс — например, после ``forget`` или смены профиля."""
    global _INDEX, _INDEX_PATH
    if _INDEX is not None:
        _INDEX.close()
    _INDEX, _INDEX_PATH = None, None
