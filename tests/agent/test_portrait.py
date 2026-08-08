"""Портрет: три слоя и граница между ними.

Главное, что проверяется здесь, — не «извлеклось ли решение», а то, что
слой нельзя подделать и догадку нельзя выпустить наружу. Всё остальное —
качество распознавания, и оно может двигаться; граница двигаться не должна.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from agent.portrait import conjecture as conj_mod
from agent.portrait import decisions as dec_mod
from agent.portrait import forget as forget_mod
from agent.portrait import store as store_mod
from agent.portrait import style as style_mod
from agent.portrait.observer import PortraitObserver
from agent.portrait.provenance import (
    ConjectureLeak,
    Origin,
    label,
    outward_safe,
    render,
)


@pytest.fixture()
def digit_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


# ---------------------------------------------------------------------------
# Граница слоёв
# ---------------------------------------------------------------------------

def test_три_слоя_и_ни_одним_больше():
    assert [o.value for o in Origin] == ["style", "record", "conjecture"]


def test_у_каждого_класса_свой_слой_и_он_не_поле():
    """Слой — атрибут класса. Экземпляр не может объявить себя другим слоем."""
    assert style_mod.StyleProfile.origin is Origin.STYLE
    assert dec_mod.DecisionRecord.origin is Origin.RECORD
    assert conj_mod.Conjecture.origin is Origin.CONJECTURE
    # ...и слой не входит в поля датакласса, поэтому его нельзя ни передать
    # в конструктор, ни сериализовать вместе с записью.
    assert "origin" not in dec_mod.DecisionRecord.__dataclass_fields__
    assert "origin" not in conj_mod.Conjecture.__dataclass_fields__


def test_догадка_наружу_не_уходит():
    guess = conj_mod.Conjecture(question="что делать?", text="я бы не стал")
    with pytest.raises(ConjectureLeak):
        outward_safe([guess])


def test_наружу_проходят_только_записи_решений():
    record = dec_mod.DecisionRecord(
        id="a", quote="переходим на uv", marker="выбор", polarity="do",
        objects=["uv"], evidence="сказано",
    )
    profile = style_mod.StyleProfile(messages=5)
    # Стиль — не ложь, но и не содержание: наружу он тоже не идёт.
    assert outward_safe([record, profile]) == [record]


def test_наружный_вид_черновика_не_содержит_догадки():
    record = dec_mod.DecisionRecord(
        id="a", quote="не используем poetry", marker="запрет", polarity="dont",
        objects=["poetry"], evidence="сделано", evidence_tools=["Edit"],
    )
    draft = conj_mod.build("чем ставить зависимости", style_mod.StyleProfile(messages=30), [record])
    draft.conjecture.text = "СОЧИНЁННЫЙ ТЕКСТ ОТ ЕГО ИМЕНИ"
    outside = draft.for_outside()
    assert "СОЧИНЁННЫЙ" not in outside
    assert conj_mod.BANNER not in outside
    assert "poetry" in outside
    # А владельцу — всё, и догадка под шапкой.
    owner = draft.for_owner()
    assert "СОЧИНЁННЫЙ" in owner
    assert conj_mod.BANNER in owner


def test_рендер_всегда_ставит_метку_слоя():
    record = dec_mod.DecisionRecord(
        id="a", quote="q", marker="выбор", polarity="do", objects=["x"],
        evidence="сказано",
    )
    assert render(record).startswith(f"[{label(Origin.RECORD)}]")

    class Безымянный:
        def as_text(self):
            return "текст"

    with pytest.raises(ValueError):
        render(Безымянный())


def test_догадка_нигде_не_хранится(digit_home):
    """У слоя 3 нет файла и нет писателя — это и есть его отличие от слоя 2."""
    assert not hasattr(store_mod, "CONJECTURES_FILE")
    source = (conj_mod.__file__)
    text = open(source, encoding="utf-8").read()
    for writer in ("append_line", "write_json", "rewrite_lines", "open("):
        assert writer not in text, f"{writer} в модуле догадки — она начала храниться"


# ---------------------------------------------------------------------------
# Слой 2: решил vs сказал мимоходом
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Переходим на uv вместо poetry.",
    "Не будем трогать digit_cli/main.py.",
    "С этого момента коммиты только по-русски.",
    "Никогда не коммить в main напрямую.",
])
def test_решение_распознаётся(text):
    assert dec_mod.extract(text, session_id="s"), text


@pytest.mark.parametrize("text", [
    "А может попробуем pdm?",                    # вопрос
    "Если не заработает, перейдём на pdm.",      # гипотеза
    "Он сказал, что переходим на pdm.",          # чужое решение
    "Давай сделаем.",                            # маркер без предмета
    "Именно это запрещено архитектурой.",        # указательное без предмета
    "«а нельзя разве через logging сделать?»",   # цитата целиком
])
def test_сказанное_мимоходом_не_записывается(text):
    assert dec_mod.extract(text, session_id="s") == [], text


def test_запрет_не_переворачивается_положительным_маркером():
    (record,) = dec_mod.extract("Не будем использовать poetry.", session_id="s")
    assert record.polarity == "dont"


def test_свидетельство_различает_сказанное_и_сделанное():
    said = dec_mod.extract("Переходим на uv.", session_id="s")[0]
    assert said.evidence == "сказано"
    done = dec_mod.extract(
        "Переходим на uv.", session_id="s", tools_used=["Read", "Edit", "Edit"]
    )[0]
    assert done.evidence == "сделано"
    assert done.evidence_tools == ["Edit"]  # без дублей и без читателей


def test_чтение_решением_не_подтверждает():
    record = dec_mod.extract("Переходим на uv.", session_id="s",
                             tools_used=["Read", "Grep", "Bash"])[0]
    assert record.evidence == "сказано"


def test_длинное_техзадание_не_становится_журналом():
    brief = " ".join(
        f"Не трогай `pkg{i}/mod.py` — там другой агент." for i in range(12)
    )
    assert len(dec_mod.extract(brief, session_id="s")) <= dec_mod.MAX_PER_MESSAGE


def test_код_в_сообщении_не_читается_как_речь():
    text = "Смотри:\n```python\nif not x:\n    raise ValueError('никогда не делай так')\n```\nвсё."
    assert dec_mod.extract(text, session_id="s") == []


def test_у_записи_есть_ссылка_на_источник():
    (record,) = dec_mod.extract(
        "Переходим на uv.", session_id="sess-123", ts=1_700_000_000,
        source="cli", tools_used=["Write"],
    )
    citation = record.citation()
    assert "sess-123" in citation and "cli" in citation and "сделано" in citation
    assert record.quote == "Переходим на uv."  # дословно, без пересказа


def test_позднее_решение_отменяет_прежнее_не_стирая_его(digit_home):
    dec_mod.append(dec_mod.extract("Переходим на poetry.", session_id="s1"))
    dec_mod.append(dec_mod.extract("Переходим на uv, а не poetry.", session_id="s2"))
    records = dec_mod.load()
    assert len(records) == 2
    old = [r for r in records if "poetry." in r.quote][0]
    assert old.superseded_by, "прежнее решение должно быть помечено отменённым"
    assert "poetry" in old.quote, "и при этом сохранено дословно"


def test_поиск_находит_решение_по_словоформе(digit_home):
    dec_mod.append(dec_mod.extract(
        "Не будем ставить прокси-сервером nginx.", session_id="s1"))
    found = dec_mod.search(dec_mod.load(), "прокси")
    assert found and "прокси" in found[0].quote


def test_совпадение_по_двум_буквам_совпадением_не_считается(digit_home):
    """«тесты» не находит запись, где есть только слово «те»."""
    dec_mod.append(dec_mod.extract(
        "Правим SKILL.md — в том числе те, что лежат внутри блоков.",
        session_id="s1"))
    assert dec_mod.search(dec_mod.load(), "тесты падают") == []


# ---------------------------------------------------------------------------
# Слой 2: индекс вместо перебора
# ---------------------------------------------------------------------------

def _журнал(digit_home, сколько: int):
    """Журнал из ``сколько`` записей о разных предметах."""
    from agent.portrait import index as index_mod

    records = []
    for i in range(сколько):
        (record,) = dec_mod.extract(
            f"Переходим на `пакет-{i}` вместо `модуль{i}.py`.",
            session_id=f"s{i // 10}", ts=1_700_000_000 + i,
        )
        records.append(record)
    dec_mod.append(records)
    index_mod.forget()
    return records


def test_индексированный_поиск_отвечает_то_же_что_перебор(digit_home):
    """Индекс ускоряет, а не меняет ответ: это проверяемое требование."""
    _журнал(digit_home, 120)
    records = dec_mod.load()
    for query in ("пакет-7", "модуль42.py", "старого решения", "переходим пакет"):
        assert ([r.id for r in dec_mod.find(query)]
                == [r.id for r in dec_mod.search(records, query)]), query


def test_поиск_без_индекса_отвечает_так_же(digit_home, monkeypatch):
    """Сборка SQLite без FTS5 существует — поиск обязан работать и на ней."""
    from agent.portrait import index as index_mod

    _журнал(digit_home, 40)
    records = dec_mod.load()
    monkeypatch.setattr(index_mod, "open_index", lambda: None)
    assert ([r.id for r in dec_mod.find("пакет-7")]
            == [r.id for r in dec_mod.search(records, "пакет-7")])


def test_индекс_переживает_дописывание_журнала(digit_home):
    _журнал(digit_home, 20)
    assert dec_mod.find("пакет-5")
    dec_mod.append(dec_mod.extract(
        "С этого момента ставим свежий-пакет вместо `пакет-5`.", session_id="поздняя"))
    found = dec_mod.find("свежий-пакет")
    assert found and "свежий-пакет" in found[0].quote
    # ...и отмена прежнего решения видна через индекс так же, как через журнал.
    отменено = {r.id for r in dec_mod.load() if r.superseded_by}
    assert отменено, "позднее решение должно было отменить прежнее"
    через_индекс = {r.id for r in dec_mod.find("пакет-5") if r.superseded_by}
    assert через_индекс == отменено & {r.id for r in dec_mod.find("пакет-5")}
    assert через_индекс


def test_испорченный_индекс_не_ломает_поиск(digit_home):
    from agent.portrait import index as index_mod

    _журнал(digit_home, 20)
    assert dec_mod.find("пакет-5")
    index_mod.forget()
    (store_mod.portrait_dir() / store_mod.INDEX_FILE).write_bytes(b"not a database")
    assert dec_mod.find("пакет-5"), "битый индекс должен пересобраться, а не молчать"


def test_правка_журнала_мимо_индекса_замечается(digit_home):
    """Индекс — кэш; расхождение с журналом обязано ловиться отпечатком."""
    from agent.portrait import index as index_mod

    _журнал(digit_home, 20)
    dec_mod.find("пакет-5")
    rows = [row for row in store_mod.iter_lines(store_mod.DECISIONS_FILE)
            if "пакет-5" not in str(row.get("quote", ""))]
    store_mod.rewrite_lines(store_mod.DECISIONS_FILE, rows)
    index_mod.forget()
    assert dec_mod.find("пакет-5") == []


# ---------------------------------------------------------------------------
# Слой 1: измеримое, без типологий
# ---------------------------------------------------------------------------

def test_речевой_акт_записывается_дословной_поверхностью():
    stats = style_mod.blank()
    for _ in range(3):
        style_mod.observe(stats, "мимо, не туда.")
    profile = style_mod.profile(stats)
    assert ("мимо", 3) in profile.acts["отказ"]


def test_разовая_формулировка_характерной_не_считается():
    stats = style_mod.blank()
    style_mod.observe(stats, "мимо.")
    assert style_mod.profile(stats).acts == {}


def test_портрет_объявляет_себя_предварительным_пока_наблюдений_мало():
    stats = style_mod.blank()
    for i in range(3):
        style_mod.observe(stats, f"сделай {i} в digit_cli.")
    profile = style_mod.profile(stats)
    assert profile.established is False
    assert "Предварительно" in profile.as_text()


def test_отличительность_считается_к_фону_а_не_к_частоте():
    stats = style_mod.blank()
    for _ in range(20):
        style_mod.observe(stats, "деплой деплой прокси", side="owner")
        style_mod.observe(stats, "деплой деплой рефакторинг", side="baseline")
    from digit_cli.kb.lexical import stem

    terms = dict((t, z) for t, z, _n in style_mod.distinctive_terms(stats))
    # «деплой» встречается чаще всех, но у обеих сторон — значит не отличает.
    assert terms[stem("прокси")] > terms[stem("деплой")]


def test_блок_кода_не_меняет_стиль():
    with_code = style_mod.blank()
    without = style_mod.blank()
    style_mod.observe(with_code, "смотри:\n```\n" + "x = 1\n" * 40 + "```\nвсё.")
    style_mod.observe(without, "смотри:\nвсё.")
    assert with_code["owner"]["sentences"] == without["owner"]["sentences"]


_ВСТАВЛЕННЫЙ_ЛОГ = """\
Aug 08 10:47:51 sbx-us systemd[3358]: Started app-com.google.Chrome-978961.scope.
● ssh.service - OpenBSD Secure Shell server
     Loaded: loaded (/usr/lib/systemd/system/ssh.service; enabled)
     Active: active (running) since Thu 2026-08-07 06:28:01 UTC
2026-08-01 06:13:06 upgrade distro-info-data:all 0.60ubuntu0.6 0.72-0ubuntu0
[    41.275] (--) Log file renamed from "/var/log/Xorg.pid-1292.log"
drwxrwsr-x+ 40 m mprojects   4096 Aug  8 10:53 .
google-auth               2.55.1
"""


@pytest.mark.parametrize("слово", ["service", "loaded", "active", "systemd",
                                   "upgrade", "google-auth"])
def test_вставленный_вывод_команд_не_попадает_в_словарь(слово):
    """Слово, которого человек не произносил, не может быть характерным для него."""
    stats = style_mod.blank()
    style_mod.observe(stats, f"вот что вывело:\n{_ВСТАВЛЕННЫЙ_ЛОГ}\nчиним.")
    from digit_cli.kb.lexical import stem

    assert stem(слово) not in stats["owner"]["terms"], слово


def test_вставленный_вывод_не_меняет_измеренную_речь():
    с_логом = style_mod.blank()
    без = style_mod.blank()
    style_mod.observe(с_логом, f"смотри вывод:\n{_ВСТАВЛЕННЫЙ_ЛОГ}\nвсё сломалось.")
    style_mod.observe(без, "смотри вывод:\nвсё сломалось.")
    assert с_логом["owner"]["terms"] == без["owner"]["terms"]
    assert с_логом["owner"]["sentences"] == без["owner"]["sentences"]
    assert с_логом["owner"]["latin"] == без["owner"]["latin"]


def test_речь_вокруг_вставленного_вывода_сохраняется():
    stats = style_mod.blank()
    style_mod.observe(stats, f"лог:\n{_ВСТАВЛЕННЫЙ_ЛОГ}\nпочини авторизацию.")
    from digit_cli.kb.lexical import stem

    assert stem("авторизацию") in stats["owner"]["terms"]
    assert stats["owner"]["messages"] == 1


@pytest.mark.parametrize("строка", [
    "Переходим на uv вместо poetry.",
    "Не трогай digit_cli/main.py — там другой агент.",
    "Ставим версию 2.1.0 и на этом останавливаемся.",
    "ok, let's go with uv here",
    "# Заголовок раздела",
])
def test_речь_за_вывод_команд_не_принимается(строка):
    """Распознаватель ошибается предсказуемо — и на речи не срабатывает."""
    assert style_mod.strip_machine_output(строка).strip() == строка.strip()


def test_решения_не_вычитываются_из_вставленного_вывода():
    """Один и тот же вычет на оба слоя: в логе нет ни одного слова владельца."""
    text = ("вот вывод:\n"
            "Aug 08 10:47:51 sbx-us apt[311]: never use /etc/apt/sources.list\n"
            "     Active: active (running) since Thu 2026-08-07 06:28:01 UTC\n")
    assert dec_mod.extract(text, session_id="s") == []


def test_пустой_портрет_называется_пустым():
    assert style_mod.profile(None).is_empty()
    assert style_mod.profile(style_mod.blank()).is_empty()


# ---------------------------------------------------------------------------
# Наполнение по ходу разговора и приватность
# ---------------------------------------------------------------------------

def test_наблюдатель_наполняет_портрет_без_команды_запомни(digit_home):
    observer = PortraitObserver(source="cli")
    observer.sync_turn(
        "Переходим на uv вместо poetry.",
        "Понял, перевожу.",
        session_id="sess-1",
        messages=[
            {"role": "user", "content": "Переходим на uv вместо poetry."},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file"}},
            ]},
        ],
    )
    records = dec_mod.load()
    assert len(records) == 1
    assert records[0].evidence == "сделано"
    assert records[0].session_id == "sess-1"
    assert style_mod.profile(store_mod.read_json(store_mod.STYLE_FILE)).messages == 1


def test_наблюдатель_ничего_не_кладёт_в_промпт(digit_home):
    observer = PortraitObserver()
    assert observer.system_prompt_block() == ""
    assert observer.prefetch("что угодно") == ""


def test_выключенный_наблюдатель_не_пишет_ничего(digit_home):
    PortraitObserver(enabled=False).sync_turn(
        "Переходим на uv.", "ок", session_id="s")
    assert not (digit_home / "portrait").exists()


def test_сломанный_портрет_не_ломает_ход(digit_home, monkeypatch):
    monkeypatch.setattr(store_mod, "write_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("диск")))
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")


def test_портрет_закрыт_от_чужих_глаз(digit_home):
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")
    directory = store_mod.portrait_dir()
    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    assert store_mod.permissions_ok()
    for name in (store_mod.STYLE_FILE, store_mod.DECISIONS_FILE):
        assert stat.S_IMODE((directory / name).stat().st_mode) & 0o077 == 0


def test_портрет_лежит_отдельно_от_памяти(digit_home):
    """Не в memories/: тот каталог уезжает в backup, профили и agent_import."""
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")
    assert store_mod.portrait_dir().name == "portrait"
    assert not (digit_home / "memories" / "style.json").exists()


def test_забыть_сессию_убирает_её_записи(digit_home):
    dec_mod.append(dec_mod.extract("Переходим на uv.", session_id="уйдёт"))
    dec_mod.append(dec_mod.extract("Не трогаем digit_cli/main.py.", session_id="останется"))
    forget_mod.session("уйдёт")
    sessions = {r.session_id for r in dec_mod.load()}
    assert sessions == {"останется"}


# ---------------------------------------------------------------------------
# Забывание одной сессии: и решения, и стиль
# ---------------------------------------------------------------------------

#: Третий разговор нарочно говорит словами, которых нет ни в первом, ни во
#: втором: только так видно, вычлось ли из словаря стиля именно его.
_РАЗГОВОРЫ = {
    "s1": ["Переходим на uv вместо poetry.", "Тесты гоняем через pytest."],
    "s2": ["Не трогаем digit_cli/main.py.", "Коммиты только по-русски."],
    "s3": ["Ставим краснотал вместо бересклета.",
           "Обвязку жимолости выпиливаем совсем."],
}


def _наговорить(*сессии: str) -> None:
    observer = PortraitObserver()
    for session_id in сессии:
        for текст in _РАЗГОВОРЫ[session_id]:
            observer.sync_turn(текст, "принято, делаю", session_id=session_id)


def test_забывание_сессии_вычитает_её_из_стиля(digit_home):
    """Главное про портрет: «забудь этот разговор» обязано работать целиком."""
    _наговорить("s1", "s2", "s3")
    было = style_mod.profile(store_mod.read_json(store_mod.STYLE_FILE))
    assert было.messages == 6

    forget_mod.session("s3")

    stats = store_mod.read_json(store_mod.STYLE_FILE)
    стало = style_mod.profile(stats)
    assert стало.messages == 4, "сообщения забытой сессии остались в счётчике"
    словарь = stats["owner"]["terms"]
    for слово in ("краснотал", "бересклет", "жимолост"):
        assert not any(t.startswith(слово) for t in словарь), (
            f"слово «{слово}» из забытой сессии осталось в словаре стиля")
    # ...а слова оставшихся сессий не пострадали.
    assert any(t.startswith("poetry") for t in словарь)


def test_забывание_сессии_совпадает_с_портретом_без_неё(digit_home):
    """Проверка не «что-то убралось», а «убралось ровно то, что принесла сессия».

    Эталон строится честно: тот же портрет, набранный без третьей сессии.
    Совпадение до счётчика — единственное доказательство, что вычитание
    точное, а не приблизительное.
    """
    _наговорить("s1", "s2", "s3")
    forget_mod.session("s3")
    после = store_mod.read_json(store_mod.STYLE_FILE)

    store_mod.wipe()
    _наговорить("s1", "s2")
    эталон = store_mod.read_json(store_mod.STYLE_FILE)

    for сторона in ("owner", "baseline"):
        for ключ in ("messages", "chars", "words", "sentences", "imperatives"):
            assert после[сторона][ключ] == эталон[сторона][ключ], (сторона, ключ)
        assert после[сторона]["terms"] == эталон[сторона]["terms"], сторона
        assert после[сторона]["openers"] == эталон[сторона]["openers"], сторона


def test_забытая_сессия_не_оставляет_посессионного_среза(digit_home):
    _наговорить("s1", "s2", "s3")
    assert {s["session"] for s in store_mod.iter_session_slices()} == {"s1", "s2", "s3"}
    forget_mod.session("s3")
    assert {s["session"] for s in store_mod.iter_session_slices()} == {"s1", "s2"}


def test_срез_сессии_закрыт_теми_же_правами_что_портрет(digit_home):
    """Срез содержит те же слова владельца — «производное» не значит «открытое»."""
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")
    directory = store_mod.sessions_dir()
    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    for path in directory.glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_наблюдение_без_идентификатора_сессии_названо_вслух(digit_home):
    """Что вычесть нельзя — должно быть сосчитано, а не умолчано."""
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="")
    assert forget_mod.unattributed_messages() == 1
    PortraitObserver().sync_turn("Не трогаем main.py.", "ок", session_id="s")
    assert forget_mod.unattributed_messages() == 1


def test_forget_all_сносит_и_срезы_и_индекс(digit_home):
    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")
    dec_mod.find("uv")  # индекс появляется на первом поиске
    directory = store_mod.portrait_dir()
    assert (directory / store_mod.INDEX_FILE).exists()
    forget_mod.everything()
    assert not (directory / store_mod.INDEX_FILE).exists()
    assert not (directory / store_mod.SESSIONS_DIR).exists()
    assert not (directory / store_mod.STYLE_FILE).exists()


def test_битый_файл_стиля_не_обнуляет_портрет_молча(digit_home):
    directory = store_mod.portrait_dir(create=True)
    (directory / store_mod.STYLE_FILE).write_text("{не json", encoding="utf-8")
    assert store_mod.read_json(store_mod.STYLE_FILE) is None
    store_mod.write_json(store_mod.STYLE_FILE, style_mod.blank())
    assert (directory / (store_mod.STYLE_FILE + ".bad")).exists()


def test_инструмент_портрета_не_шадоуит_ядро(digit_home):
    from toolsets import _DIGIT_CORE_TOOLS

    names = {schema["name"] for schema in PortraitObserver().get_tool_schemas()}
    assert names.isdisjoint(set(_DIGIT_CORE_TOOLS))


def test_портрет_не_занимает_слот_внешнего_провайдера():
    from agent.memory_manager import LOCAL_PROVIDERS, MemoryManager

    assert "portrait" in LOCAL_PROVIDERS
    manager = MemoryManager()
    manager.add_provider(PortraitObserver())
    assert manager.has_external_providers is False


# ---------------------------------------------------------------------------
# Подключение к живому агенту
# ---------------------------------------------------------------------------

class _FakeOpenAI:
    def __init__(self, **kw):
        self.api_key = kw.get("api_key", "test")
        self.base_url = kw.get("base_url", "http://test")

    def close(self):
        pass


def _agent(monkeypatch):
    from run_agent import AIAgent

    monkeypatch.setattr("run_agent.get_tool_definitions", lambda **kw: [])
    monkeypatch.setattr("run_agent.check_toolset_requirements", lambda: {})
    monkeypatch.setattr("run_agent.OpenAI", _FakeOpenAI)
    return AIAgent(
        api_key="test-key", base_url="http://test", provider="openrouter",
        api_mode="chat_completions", max_iterations=1, quiet_mode=True,
        skip_context_files=True, skip_memory=False,
    )


def _write_config(home, enabled: bool) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"portrait:\n  enabled: {'true' if enabled else 'false'}\n  source: cli\n",
        encoding="utf-8",
    )


def test_наблюдатель_подключается_к_агенту_когда_включён(digit_home, monkeypatch):
    _write_config(digit_home, True)
    agent = _agent(monkeypatch)
    manager = agent._memory_manager
    assert manager is not None
    assert manager.get_provider("portrait") is not None
    # ...и при этом не занимает слот внешнего провайдера и не даёт блока в промпт
    assert manager.has_external_providers is False
    assert manager.build_system_prompt() == ""


def test_ход_разговора_наполняет_портрет_через_менеджер(digit_home, monkeypatch):
    """Сквозная проверка «без команды запомни»: ход → менеджер → журнал."""
    _write_config(digit_home, True)
    manager = _agent(monkeypatch)._memory_manager
    manager.sync_all(
        "Переходим на uv вместо poetry.",
        "Понял, перевожу.",
        session_id="sess-live",
        messages=[
            {"role": "user", "content": "Переходим на uv вместо poetry."},
            {"role": "assistant", "tool_calls": [{"function": {"name": "patch"}}]},
        ],
    )
    manager.flush_pending(timeout=10)
    records = dec_mod.load()
    assert [r.quote for r in records] == ["Переходим на uv вместо poetry."]
    assert records[0].evidence == "сделано"
    manager.shutdown_all()


def test_выключенный_портрет_к_агенту_не_подключается(digit_home, monkeypatch):
    _write_config(digit_home, False)
    agent = _agent(monkeypatch)
    manager = agent._memory_manager
    assert manager is None or manager.get_provider("portrait") is None


def test_фоновому_форку_портрет_не_подключается(digit_home, monkeypatch):
    """skip_memory=True — это форк, который говорит не голосом владельца."""
    from run_agent import AIAgent

    _write_config(digit_home, True)
    monkeypatch.setattr("run_agent.get_tool_definitions", lambda **kw: [])
    monkeypatch.setattr("run_agent.check_toolset_requirements", lambda: {})
    monkeypatch.setattr("run_agent.OpenAI", _FakeOpenAI)
    agent = AIAgent(
        api_key="test-key", base_url="http://test", provider="openrouter",
        api_mode="chat_completions", max_iterations=1, quiet_mode=True,
        skip_context_files=True, skip_memory=True,
    )
    manager = agent._memory_manager
    assert manager is None or manager.get_provider("portrait") is None


def test_json_вид_разделяет_слои(digit_home):
    from digit_cli.portrait import _as_json

    PortraitObserver().sync_turn("Переходим на uv.", "ок", session_id="s")
    payload = _as_json(
        style_mod.profile(store_mod.read_json(store_mod.STYLE_FILE)),
        dec_mod.load(),
    )
    assert set(payload) == {"style", "decisions", "conjecture"}
    assert payload["conjecture"]["stored"] is False
    assert payload["style"]["origin"] == "style"
    json.dumps(payload, ensure_ascii=False)  # сериализуемо целиком
