"""Заметки ищутся, а не едут целиком в системный промпт.

Почему это вообще понадобилось
------------------------------
``MEMORY.md`` и ``USER.md`` целиком уезжали в системный промпт **каждого**
запроса. Отсюда и потолок в 2200 / 1375 символов: это не свойство файла, это
цена места в промпте. Пока память была блокнотом, цена была честной. С тех
пор как :mod:`agent.memory_links` научился читать ``[[вики-ссылки]]`` и
``#теги``, заметки стали сетью — а сети некуда расти, если весь её объём
оплачивается на каждом ходу.

Здесь потолок снимается ровно одним способом: **в промпт попадает не всё, а
относящееся к запросу**. Хранилище остаётся прежним (те же два файла, те же
``§``-разделители) — меняется только то, кто и когда их читает.

Почему FTS5, а не эмбеддинги
----------------------------
У Digit уже есть готовая машинерия поиска — ``digit kb`` (SQLite + FTS5 +
плотные векторы). Но её плотный канал ходит на удалённый эмбеддер, а память
ищется на **каждом** ходу и обязана работать офлайн: агент без сети должен
помнить, а не разводить руками. Поэтому берётся только лексическая половина
``kb`` — :mod:`digit_cli.kb.lexical` (стеммер + сборка префиксного запроса) —
и ничего больше. Это тот же приём, что в
``/home/a/projects/digit-ml/ruleparse/corpus.py``: BM25 по морфологически
свёрнутому тексту, никакой модели в процессе.

Морфология тут не украшение. FTS5 с ``unicode61`` индексирует кириллицу, но
не знает словоформ: запрос «прокси» не встретит заметку про «прокси-сервером»
без префиксного стемминга. Русская память без этого просто не ищется.

Что даёт граф связей
--------------------
После BM25 к найденному добавляется один шаг по ``[[вики-ссылкам]]``. Это и
есть разница между списком заметок и сетью: заметка «Прокси в CI» может не
содержать слова из запроса, но быть прямо связанной с той, что содержит.
Ссылка — это утверждение автора «читай вместе»; игнорировать его при выдаче
значило бы обесценить то, ради чего связи вообще проставляются.

Границы честности
-----------------
Это лексический поиск. Он находит заметку по словам, а не по смыслу — и там,
где слова разошлись, за него доигрывает словарь синонимов
(:mod:`agent.memory_recall_synonyms`): «машина» находит «Автомобиль». Словарь
включается ТОЛЬКО после того, как поиск по словам не нашёл ничего, и
засчитывает синоним только в заголовке заметки — почему именно так и чего
стоили другие варианты, замерено и записано в том модуле.

Граница осталась, но переехала: словарь знает то, что в нём написано, и
ничего сверх. Взамен поиск детерминирован, объясним (видно, какие термы
совпали), не требует сети и стоит доли миллисекунды. Для памяти это
правильный размен: промахнувшийся поиск агент чинит сам —
``memory(action="search", …)`` доступен ему как инструмент.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# Заметка, помеченная этим тегом, всегда лежит в промпте и не участвует в
# отборе: есть факты («отвечай по-русски», «прод трогать нельзя»), которые
# обязаны действовать в ходе, который про них не спрашивает. Поиск такое
# принципиально не гарантирует, поэтому для них оставлена ручка.
PINNED_TAG = "pinned"

# 2: у FTS появилась отдельная колонка ``title`` — по ней ищет запасной заход
# по синонимам. Индекс производный, поэтому смена версии стоит одной пересборки.
SCHEMA_VERSION = "2"

# ``notes.text`` — индексируемая форма, она нужна только FTS5; тащить её в
# каждую выдачу значит удваивать объём ответа ради колонки, которую никто не
# читает.
_NOTE_COLS = (
    "n.id, n.source, n.ordinal, n.title, n.body, n.tags, n.sector, n.pinned"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source  TEXT NOT NULL,              -- 'memory' | 'user'
    ordinal INTEGER NOT NULL,           -- позиция записи в своём файле
    title   TEXT NOT NULL,              -- первая строка, она же цель вики-ссылки
    body    TEXT NOT NULL,              -- запись целиком, дословно
    text    TEXT NOT NULL,              -- индексируемая форма (заголовок весит больше)
    tags    TEXT NOT NULL DEFAULT '',
    sector  TEXT NOT NULL DEFAULT '',
    pinned  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source);
CREATE INDEX IF NOT EXISTS idx_notes_pinned ON notes(pinned) WHERE pinned = 1;

-- Рёбра графа, уже разрешённые в id: считать их на каждом запросе значило бы
-- перечитывать обе стороны связи ради одного шага обхода.
CREATE TABLE IF NOT EXISTS links (
    src INTEGER NOT NULL,
    dst INTEGER NOT NULL,
    PRIMARY KEY (src, dst)
);

CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst);

-- external content: индексируемый текст уже лежит в ``notes.text``, и вторая
-- его копия внутри FTS означала бы два места, которые обязаны совпадать, но
-- ничем к этому не принуждены. ``rebuild`` перечитывает содержимое из notes,
-- поэтому расхождение невозможно по построению.
--
-- ``title`` — отдельной колонкой, хотя заголовок и так входит в ``text``.
-- Она нужна не для веса (вес даёт удвоение внутри ``text``), а для того, чтобы
-- запасной заход по синонимам мог спросить «эта заметка НАЗЫВАЕТСЯ так?» —
-- ``title:"план"*``. Через общий индекс такой вопрос не задать: bm25 ставит
-- заметку, у которой синоним в заголовке, ниже десятка тех, у кого в теле
-- совпало два слова из трёх (проверено: 41-е место из 41).
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title,
    text,
    content='notes',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def note_title(entry: str) -> str:
    """Первая непустая строка записи — то, чем на неё ссылаются.

    ``[[вики-ссылка]]`` резолвится в :mod:`agent.memory_links` именно по
    первой строке, поэтому заголовок здесь обязан считаться так же — иначе
    ссылка, которая видна в графе, не сработает в поиске.
    """
    for line in (entry or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


class RecallIndex:
    """FTS5-индекс над записями памяти. Файлы — источник истины, индекс — кэш.

    Индекс намеренно **производный**: он целиком перестраивается из
    ``MEMORY.md`` / ``USER.md`` и ничего не хранит сверх них. Потерять его
    не страшно (пересоберётся), рассинхронизировать — тоже (сверка по
    sha256 файла на каждой синхронизации). Единственное, чего нельзя, —
    сделать его вторым местом, где живут заметки: тогда «что считать
    памятью» становится вопросом, а у него должен быть один ответ.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    # -- соединение ----------------------------------------------------------

    def connect(self, *, _retry: bool = True) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL: индекс пишется из хода агента и читается им же, а рядом
            # может стоять вторая сессия. Блокировка читателя писателем стоила
            # бы хода.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA_SQL)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            # Битый или чужой файл. Он производный — единственный разумный
            # ответ выбросить и пересобрать; жить с постоянно падающим
            # индексом значило бы навсегда потерять поиск из-за одного
            # оборванного копирования (профиль экспортируется вместе с
            # каталогом memories, и БД туда попадает «на живую»).
            if not _retry:
                raise
            logger.warning("Recall index unusable (%s); rebuilding from scratch", exc)
            self._drop_files()
            return self.connect(_retry=False)
        if version is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            conn.commit()
        elif version["value"] != SCHEMA_VERSION:
            # Схема сменилась — старое содержимое всё равно производное,
            # поэтому дешевле выбросить, чем мигрировать.
            conn.close()
            self._drop_files()
            return self.connect(_retry=_retry)
        self._conn = conn
        return conn

    def _drop_files(self) -> None:
        """Снести файл индекса вместе с журналом WAL.

        Оставить -wal/-shm от предыдущей базы — значит открыть новую поверх
        чужого журнала, и SQLite вправе на этом упасть.
        """
        self._conn = None
        for suffix in ("", "-wal", "-shm"):
            try:
                self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- индексация ----------------------------------------------------------

    def sync(
        self,
        memory_entries: Sequence[str],
        user_entries: Sequence[str],
        *,
        sanitize: Optional[Any] = None,
        force: bool = False,
    ) -> bool:
        """Привести индекс в соответствие с записями. True — если пересобрали.

        Сверка идёт по sha256 всего набора записей, а не по mtime файла:
        память пишется атомарной подменой файла, и mtime у неё меняется даже
        когда содержимое то же (перезапись тем же текстом при консолидации).
        Лишняя пересборка стоит миллисекунды, но лишний sha256 — микросекунды,
        и он не врёт.

        ``sanitize(entries, filename) -> entries`` — тот же сканер угроз, что
        стоит на пути записей в системный промпт. Он ОБЯЗАН применяться и
        здесь: выдача поиска уезжает в промпт так же, как инлайн, и без него
        отравленная запись въезжала бы туда мимо защиты, ради которой сканер
        и поставлен. Вызывается ТОЛЬКО при реальной пересборке — на корпусе в
        800 заметок скан стоит ~40 мс, и платить их на каждой сверке (а она
        случается после каждой записи в память) было бы разорительно.
        """
        from agent import memory_links

        conn = self.connect()
        # Дайджест считается по СЫРЫМ записям: источник истины — файл, и
        # «изменилось ли что-нибудь» — вопрос о нём, а не о результате скана.
        payload = "\n\x00\n".join(
            ["memory", *memory_entries, "\x01", "user", *user_entries]
        )
        digest = _digest(payload)
        if not force:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'entries_digest'"
            ).fetchone()
            if row is not None and row["value"] == digest:
                return False

        if sanitize is not None:
            memory_entries = sanitize(list(memory_entries), "MEMORY.md")
            user_entries = sanitize(list(user_entries), "USER.md")

        cards: List[Dict[str, Any]] = []
        for source, entries in (("memory", memory_entries), ("user", user_entries)):
            for ordinal, entry in enumerate(entries):
                title = note_title(entry)
                cards.append({
                    "source": source,
                    "ordinal": ordinal,
                    "title": title,
                    # ``key`` — то, по чему memory_links резолвит ссылки; она
                    # предпочитает его усечённому title.
                    "key": title,
                    "body": entry,
                })

        annotated = memory_links.annotate(cards)
        sectors = annotated["sectors"]
        out_links = annotated["links"]

        with conn:
            conn.execute("DELETE FROM notes")
            conn.execute("DELETE FROM links")
            ids: List[int] = []
            for i, card in enumerate(cards):
                tags = [str(t) for t in (card.get("tags") or [])]
                cur = conn.execute(
                    "INSERT INTO notes(source, ordinal, title, body, text, tags, sector, pinned) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        card["source"],
                        card["ordinal"],
                        card["title"],
                        card["body"],
                        # Заголовок входит в индексируемый текст дважды: это то,
                        # чем заметку называют, и совпадение по нему должно
                        # весить больше, чем совпадение где-то в середине
                        # абзаца. FTS5 без отдельных колонок иначе не умеет.
                        f"{card['title']}\n{card['title']}\n{card['body']}",
                        " ".join(tags),
                        sectors[i] if i < len(sectors) else "",
                        1 if PINNED_TAG in tags else 0,
                    ),
                )
                ids.append(int(cur.lastrowid))
            # external-content FTS не чистится каскадом за DELETE FROM notes:
            # 'rebuild' — единственный способ не оставить в индексе призраков
            # удалённых записей, которые всплыли бы в выдаче пустыми строками.
            conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
            edges = [
                (ids[src], ids[dst])
                for src, dsts in out_links.items()
                for dst in dsts
                if src < len(ids) and dst < len(ids)
            ]
            if edges:
                conn.executemany(
                    "INSERT OR IGNORE INTO links(src, dst) VALUES (?, ?)", edges
                )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('entries_digest', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (digest,),
            )
        return True

    # -- чтение --------------------------------------------------------------

    def stats(self, *, source: Optional[str] = None) -> Dict[str, Any]:
        """Сколько заметок, сколько связей, какие секторы — для шапки промпта.

        ``source`` сужает счёт до одного стора: MEMORY.md и USER.md переходят
        на поиск независимо друг от друга (у них разные объёмы и разная
        природа), и шапка каждого должна описывать его, а не оба сразу.
        """
        conn = self.connect()
        where, params = ("", [])
        if source:
            where, params = (" WHERE source = ?", [source])
        counts = dict(
            conn.execute(
                f"SELECT source, COUNT(*) AS n FROM notes{where} GROUP BY source",
                params,
            ).fetchall()
        )
        sector_where = " WHERE sector != ''" if not source else " WHERE sector != '' AND source = ?"
        sectors = [
            (r["sector"], r["n"])
            for r in conn.execute(
                f"SELECT sector, COUNT(*) AS n FROM notes{sector_where} "
                "GROUP BY sector ORDER BY n DESC, sector",
                params,
            ).fetchall()
        ]
        if source:
            n_links = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM links l JOIN notes n ON n.id = l.src "
                    "WHERE n.source = ?",
                    [source],
                ).fetchone()["n"]
            )
        else:
            n_links = int(conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"])
        return {
            "memory": int(counts.get("memory", 0) or 0),
            "user": int(counts.get("user", 0) or 0),
            "total": int(sum(counts.values())) if counts else 0,
            "links": n_links,
            "sectors": sectors,
        }

    def pinned(self, *, source: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self.connect()
        where, params = ("", [])
        if source:
            where, params = (" AND n.source = ?", [source])
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT {_NOTE_COLS} FROM notes n WHERE n.pinned = 1{where} "
                "ORDER BY n.source, n.ordinal",
                params,
            ).fetchall()
        ]

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        hops: int = 1,
        sources: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Заметки, относящиеся к запросу. Пустой список — честный ответ.

        Порядок: BM25 по лексическому совпадению, затем один шаг по
        ``[[вики-ссылкам]]`` от найденного. Связанные заметки идут ПОСЛЕ
        совпавших и с пометкой ``via``, потому что их привёл не запрос, а
        автор заметки — и читателю выдачи это должно быть видно.

        Если по словам не нашлось ничего — пробуются синонимы
        (:mod:`agent.memory_recall_synonyms`). Именно в таком порядке: пока
        лексика справляется, выдача и время не меняются вообще.
        """
        from digit_cli.kb.lexical import build_fts_query, content_terms

        terms = content_terms(query or "")
        if not terms:
            return []
        expr = build_fts_query(terms)
        if not expr:
            return []

        conn = self.connect()
        names = [str(s) for s in sources] if sources else []
        where_source = ""
        if names:
            where_source = f" AND n.source IN ({','.join('?' * len(names))})"
        rows = self._match(conn, expr, where_source, names, top_k)
        if not rows:
            rows = self._by_synonym(conn, terms, where_source, names, top_k)

        hits: List[Dict[str, Any]] = []
        seen: set[int] = set()
        for r in rows:
            note = dict(r)
            note["score"] = -float(r["score"])  # bm25: меньше — лучше
            note["via"] = ""
            hits.append(note)
            seen.add(int(r["id"]))

        if hops > 0 and hits:
            hits.extend(self._expand(conn, hits, seen, limit=top_k, sources=names))
        return hits

    def _match(
        self,
        conn: sqlite3.Connection,
        expr: str,
        where_source: str,
        names: Sequence[str],
        top_k: int,
    ) -> List[sqlite3.Row]:
        """Один запрос к FTS5. Кривое выражение — пустая выдача, не исключение."""
        params: List[Any] = [expr, *names, top_k]
        try:
            return conn.execute(
                f"SELECT {_NOTE_COLS}, bm25(notes_fts) AS score "
                "FROM notes_fts f JOIN notes n ON n.id = f.rowid "
                f"WHERE notes_fts MATCH ?{where_source} "
                "ORDER BY score LIMIT ?",
                params,
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # Кривой FTS-запрос не должен ронять ход: без памяти агент
            # работает хуже, без ответа — вообще никак.
            logger.debug("recall FTS query failed (%s): %s", expr, exc)
            return []

    def _by_synonym(
        self,
        conn: sqlite3.Connection,
        terms: Sequence[str],
        where_source: str,
        names: Sequence[str],
        top_k: int,
    ) -> List[sqlite3.Row]:
        """Второй заход — по синонимам, и только по заголовкам заметок.

        Почему вообще второй заход, а не расширение первого: подмешивать
        синонимы в каждый запрос значит платить точностью там, где лексика и
        так справилась. Замер: молчание на запросах «этого в памяти нет»
        падало с 9/10 до 7/10, а «расписание автобусов» через синоним
        «расписание→план» приводило заметку про отпуск по тегу ``#планы``.

        Почему только по заголовку: заголовок — это то, чем заметка
        называется. Синоним в заголовке значит «заметка про это», синоним
        где-то в теле — что слово случайно встретилось рядом. С этим фильтром
        молчание вернулось к 9/10, а синонимы дают 8 из 8.

        Ограничение задаётся FTS (``title:``), а не отбором прочитанных строк,
        и это не стилистика. Отбирать после ``ORDER BY bm25 LIMIT`` бесполезно:
        заметка, у которой синоним стоит в заголовке, проигрывает по bm25
        заметкам, где в теле совпало больше термов, — на проверке она оказалась
        41-й из 41 и не попадала ни в какой разумный пул.
        """
        from agent.memory_recall_synonyms import synonyms
        from digit_cli.kb.lexical import build_fts_query

        alts = synonyms(terms)
        if not alts:
            return []
        expr = build_fts_query(alts)
        if not expr:
            return []
        return self._match(conn, f"title:({expr})", where_source, names, top_k)

    def _expand(
        self,
        conn: sqlite3.Connection,
        hits: List[Dict[str, Any]],
        seen: set,
        *,
        limit: int,
        sources: Sequence[str] = (),
    ) -> List[Dict[str, Any]]:
        """Один шаг по связям от найденного, в обе стороны.

        Обратные ссылки берутся наравне с прямыми: заметка, на которую
        ссылается найденная, и заметка, которая ссылается на найденную, —
        одинаково «читай вместе». Именно ради этой симметрии
        :mod:`agent.memory_links` вообще строит беклинки.
        """
        src_ids = [int(h["id"]) for h in hits]
        placeholders = ",".join("?" * len(src_ids))
        rows = conn.execute(
            f"SELECT {_NOTE_COLS}, l.src AS from_id "
            "FROM links l JOIN notes n ON n.id = l.dst "
            f"WHERE l.src IN ({placeholders}) "
            "UNION ALL "
            f"SELECT {_NOTE_COLS}, l.dst AS from_id "
            "FROM links l JOIN notes n ON n.id = l.src "
            f"WHERE l.dst IN ({placeholders})",
            src_ids + src_ids,
        ).fetchall()
        by_id = {int(h["id"]): h for h in hits}
        allowed = set(sources)
        out: List[Dict[str, Any]] = []
        for r in rows:
            nid = int(r["id"])
            if nid in seen or len(out) >= limit:
                continue
            if allowed and r["source"] not in allowed:
                continue
            seen.add(nid)
            note = dict(r)
            note.pop("from_id", None)
            note["score"] = 0.0
            note["via"] = by_id.get(int(r["from_id"]), {}).get("title", "")
            out.append(note)
        return out


# ---------------------------------------------------------------------------
# Отображение выдачи
# ---------------------------------------------------------------------------


def format_hits(hits: Sequence[Dict[str, Any]], *, max_chars: int = 2000) -> str:
    """Выдача в текст для подкладывания в ход.

    Записи идут дословно. Урезание — только по границе заметки, никогда
    внутри неё: половина факта опаснее его отсутствия, потому что выглядит
    как целый факт.

    Обрамление — по-английски, как и весь остальной промпт: заметки могут быть
    на любом языке, а вот инструкции вокруг них не должны внезапно менять язык
    посреди системного промпта.
    """
    if not hits:
        return ""
    lines: List[str] = []
    used = 0
    shown = 0
    for hit in hits:
        body = str(hit.get("body", "")).strip()
        if not body:
            continue
        label = "USER" if hit.get("source") == "user" else "MEMORY"
        via = str(hit.get("via") or "")
        head = f"[{label}]" if not via else f"[{label} · linked from \"{via}\"]"
        block = f"{head}\n{body}"
        if used + len(block) > max_chars and shown:
            break
        lines.append(block)
        used += len(block) + 2
        shown += 1
    if not lines:
        return ""
    return "\n\n".join(lines)


def format_digest(
    stats: Dict[str, Any],
    pinned: Sequence[Dict[str, Any]],
    *,
    target: str = "memory",
    max_sectors: int = 12,
) -> str:
    """Шапка для системного промпта: что в сторе есть, а не что в нём написано.

    Это и есть весь постоянный расход памяти на промпт. Заметки в него не
    входят — они приходят на конкретный запрос. Исключение — закреплённые:
    они здесь именно потому, что не должны зависеть от того, спросили о них
    или нет.

    Текст английский — как и весь системный промпт вокруг. Заметки внутри
    могут быть на любом языке, но инструкция модели не должна менять язык
    посреди промпта.
    """
    total = int(stats.get(target if target in ("memory", "user") else "total", 0) or 0)
    if not total:
        return ""
    filename = "USER.md" if target == "user" else "MEMORY.md"
    parts: List[str] = []
    sectors = list(stats.get("sectors") or [])[:max_sectors]
    sector_text = ", ".join(f"{name} ({n})" for name, n in sectors) if sectors else "—"
    parts.append(
        f"{total} notes in {filename}, {stats.get('links', 0)} links between them.\n"
        f"Sectors: {sector_text}."
    )
    parts.append(
        "Full note text is NOT in this prompt — there is too much of it. The "
        "notes relevant to the user's message are attached to it inside "
        "<memory-context>. If what you need isn't there, search for it yourself: "
        'memory(action="search", query="…"). A note missing from the attachment '
        "does NOT mean it isn't stored."
    )
    if pinned:
        body = "\n§\n".join(str(p.get("body", "")).strip() for p in pinned if p.get("body"))
        if body:
            parts.append(
                f"Pinned notes (#{PINNED_TAG}) — these always apply:\n{body}"
            )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Провайдер
# ---------------------------------------------------------------------------


class BuiltinRecallProvider(MemoryProvider):
    """Встроенная память как провайдер: путь для локального хранилища.

    Механизм подкладывания контекста в ход у Digit уже был — им пользуются
    внешние провайдеры (honcho, mem0, hindsight) через
    :class:`agent.memory_manager.MemoryManager`. Не хватало ровно одного:
    провайдера, который ищет по **своим** файлам и никуда не ходит. Он
    называется ``builtin``, и менеджер знает это имя — вызывает его prefetch
    синхронно, без потока и таймаута, потому что локальный SQLite не может
    зависнуть на сети.

    ``system_prompt_block()`` пуст намеренно: шапку рисует
    ``MemoryStore._render_block``, чтобы у памяти остался ОДИН блок в промпте
    с прежним заголовком — на него завязана проверка удержания промпта при
    сжатии контекста (``_cached_prompt_reflects_builtin_memory``).
    """

    def __init__(
        self,
        store: Any,
        *,
        top_k: int = 6,
        max_chars: int = 2000,
        hops: int = 1,
    ) -> None:
        self._store = store
        self._top_k = int(top_k)
        self._max_chars = int(max_chars)
        self._hops = int(hops)
        self.last_search_ms: float = 0.0

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return self._store is not None and self._store.recall is not None

    def initialize(self, session_id: str, **kwargs) -> None:
        # Индекс уже синхронизирован при загрузке стора; трогать здесь нечего.
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        # Поиск живёт в существующем инструменте ``memory`` (action="search").
        # Отдельный инструмент стоил бы ещё одной схемы в каждом запросе ради
        # действия, которое концептуально принадлежит памяти.
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        index = getattr(self._store, "recall", None)
        if index is None or not query:
            return ""
        # Стор, который целиком лежит в системном промпте, из выдачи
        # исключается: подкладывать заметку, которую модель и так видит, —
        # платить за неё дважды и путать её повтором.
        targets = self._store.recall_targets()
        if not targets:
            return ""
        started = time.perf_counter()
        try:
            hits = index.search(
                query, top_k=self._top_k, hops=self._hops, sources=targets
            )
        except Exception as exc:
            logger.debug("builtin recall prefetch failed: %s", exc)
            return ""
        self.last_search_ms = (time.perf_counter() - started) * 1000.0
        text = format_hits(hits, max_chars=self._max_chars)
        if not text:
            return ""
        return f"MEMORY — stored notes matching this message:\n\n{text}"
