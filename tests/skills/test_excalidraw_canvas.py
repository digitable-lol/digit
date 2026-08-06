"""Контракты холста: человек и агент правят один и тот же файл.

``revise.py`` закрывает сторону агента. Здесь проверяется вторая сторона —
``canvas.py``, локальный сервер, из которого владелец открывает **тот же**
файл, а не его копию. Все тесты про одно: ни один из двух писателей не должен
молча потерять работу другого.
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "skills/creative/excalidraw/scripts/canvas.py"
CANVAS = REPO / "skills/creative/excalidraw/canvas"


def _load_module():
    spec = importlib.util.spec_from_file_location("excalidraw_canvas", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canvas = _load_module()


DOCUMENT = {
    "type": "excalidraw",
    "version": 2,
    "source": "https://excalidraw.com",
    "elements": [
        {
            "id": "box1", "type": "rectangle", "x": 100, "y": 100,
            "width": 200, "height": 100, "version": 42, "seed": 1968410350,
            "customData": {"ownerNote": "нарисовано вручную"},
        }
    ],
    "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
    "files": {},
    "digitTask": "DGT-DIGIT-07",
}

#: Ровно то, что отдаёт сериализатор Excalidraw: фиксированный набор ключей
#: верхнего уровня и ничего сверх него.
FROM_BROWSER = {
    "type": "excalidraw",
    "version": 2,
    "source": "digit",
    "elements": [dict(DOCUMENT["elements"][0], x=140, version=43)],
    "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
    "files": {},
}


@pytest.fixture
def diagram(tmp_path) -> Path:
    # Отдельный каталог: часть тестов смотрит, что рядом с файлом ничего не
    # осталось, а в общий tmp_path кладут своё и другие фикстуры набора.
    folder = tmp_path / "диаграммы"
    folder.mkdir()
    path = folder / "d.excalidraw"
    path.write_text(
        json.dumps(DOCUMENT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------
# Файл
# --------------------------------------------------------------------------


def test_opening_a_missing_file_does_not_create_it(tmp_path):
    """Открыть — не значит записать. Пустой холст показывается из памяти."""
    path = tmp_path / "нет.excalidraw"
    document = canvas.load_document(path)
    assert document["elements"] == []
    assert not path.exists()


def test_a_key_the_browser_does_not_know_survives_a_save(diagram):
    """Сериализатор приложения знает свои шесть ключей и выбрасывает остальные.

    Если бы сервер писал присланное как есть, метка задачи в документе исчезала
    бы при первом же сохранении из браузера — и никто бы этого не заметил.
    """
    saved, _ = canvas.save_from_browser(
        diagram, FROM_BROWSER, canvas.fingerprint(diagram)
    )
    assert saved
    on_disk = json.loads(diagram.read_text(encoding="utf-8"))
    assert on_disk["digitTask"] == "DGT-DIGIT-07"
    assert on_disk["elements"][0]["x"] == 140


def test_a_save_from_a_stale_tab_is_refused_and_changes_nothing(diagram):
    """Вкладка исходила из вчерашнего состояния — значит, правил агент."""
    stale = canvas.fingerprint(diagram)
    canvas.write_document(diagram, {**DOCUMENT, "elements": []})  # правка агента
    after_agent = diagram.read_text(encoding="utf-8")

    saved, result = canvas.save_from_browser(diagram, FROM_BROWSER, stale)

    assert not saved
    assert diagram.read_text(encoding="utf-8") == after_agent
    # Отказ обязан вернуть то, что на диске: иначе владельцу нечего показать.
    assert result["document"]["elements"] == []
    assert result["fingerprint"] == canvas.fingerprint(diagram)


def test_the_owner_can_insist_and_that_requires_saying_so(diagram):
    """``base is None`` — это нажатая владельцем кнопка, а не путь по умолчанию."""
    canvas.write_document(diagram, {**DOCUMENT, "elements": []})
    saved, _ = canvas.save_from_browser(diagram, FROM_BROWSER, None)
    assert saved
    assert json.loads(diagram.read_text(encoding="utf-8"))["elements"][0]["x"] == 140


def test_the_write_leaves_no_temporary_file_behind(diagram):
    canvas.save_from_browser(diagram, FROM_BROWSER, canvas.fingerprint(diagram))
    leftovers = [p.name for p in diagram.parent.iterdir() if p.name != diagram.name]
    assert leftovers == []


def test_a_failed_write_leaves_neither_temporary_file_nor_damaged_original(
    diagram, monkeypatch
):
    """Падение посреди записи не должно ни портить файл, ни сорить рядом."""
    original = diagram.read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise OSError("диск кончился")

    monkeypatch.setattr(canvas.os, "replace", explode)
    with pytest.raises(OSError):
        canvas.write_document(diagram, FROM_BROWSER)

    assert diagram.read_text(encoding="utf-8") == original
    assert [p.name for p in diagram.parent.iterdir()] == [diagram.name]


def test_the_two_writers_format_the_file_the_same_way(diagram):
    """Оформление совпадает с ``revise.py`` — иначе каждый ход петли даёт диф
    на весь файл, и в истории не видно, что именно поменялось."""
    spec = importlib.util.spec_from_file_location(
        "excalidraw_revise", REPO / "skills/creative/excalidraw/scripts/revise.py"
    )
    revise = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revise)

    document = json.loads(diagram.read_text(encoding="utf-8"))
    by_revise = diagram.parent / "a.excalidraw"
    revise.save(document, by_revise)
    by_canvas = diagram.parent / "b.excalidraw"
    canvas.write_document(by_canvas, document)

    assert by_canvas.read_text(encoding="utf-8") == by_revise.read_text(encoding="utf-8")


def test_the_fingerprint_reads_content_and_not_the_clock(diagram):
    """Два разных содержимого — два разных отпечатка, и наоборот.

    Разрешение mtime на части файловых систем — целая секунда, а агент и
    человек вполне пишут в один файл внутри одной секунды.
    """
    first = canvas.fingerprint(diagram)
    text = diagram.read_text(encoding="utf-8")
    diagram.write_text(text, encoding="utf-8")
    assert canvas.fingerprint(diagram) == first

    diagram.write_text(text.replace("100", "101"), encoding="utf-8")
    assert canvas.fingerprint(diagram) != first


def test_a_missing_file_has_an_empty_fingerprint(tmp_path):
    assert canvas.fingerprint(tmp_path / "нет.excalidraw") == ""


def test_a_broken_file_is_refused_with_the_reason(tmp_path):
    path = tmp_path / "d.excalidraw"
    path.write_text("{не json", encoding="utf-8")
    with pytest.raises(canvas.Refused) as exc:
        canvas.load_document(path)
    assert "не JSON" in str(exc.value)


def test_something_that_is_not_a_diagram_is_refused(diagram):
    with pytest.raises(canvas.Refused):
        canvas.save_from_browser(diagram, {"type": "excalidraw"}, None)


# --------------------------------------------------------------------------
# Сервер
# --------------------------------------------------------------------------


@pytest.fixture
def server(diagram):
    httpd, url = canvas.build_server(diagram, quiet=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base, token = url.split("/?t=")
    yield base, token, diagram
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _call(base, path, token=None, data=None, host=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method="POST" if data is not None else "GET",
    )
    if token is not None:
        request.add_header("X-Canvas-Token", token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if host is not None:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, _body(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _body(error.read())


def _body(raw: bytes):
    """Статика отдаётся не JSON'ом, и для тестов это не ошибка."""
    try:
        return json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def test_the_api_needs_the_token_from_the_url(server):
    """Без токена страница из другой вкладки могла бы переписать файл владельца.

    Ответ ей бы не показали (CORS), но запись бы прошла — а это ровно та потеря
    работы, от которой всё здесь и защищает.
    """
    base, token, _ = server
    assert _call(base, "/api/doc")[0] == 403
    assert _call(base, "/api/doc", data={"document": FROM_BROWSER})[0] == 403
    assert _call(base, "/api/doc", token=token)[0] == 200


def test_a_foreign_host_header_is_refused(server):
    base, token, _ = server
    status, _ = _call(base, "/api/doc", token=token, host="canvas.example")
    assert status == 403


def test_the_static_root_cannot_be_escaped(server):
    base, _, _ = server
    # urllib нормализует путь, поэтому запрос собирается вручную.
    import http.client

    host, port = base.rsplit(":", 1)
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
    connection.request("GET", "/../../../etc/hostname")
    assert connection.getresponse().status == 403
    connection.close()


def test_the_page_and_the_vendored_bundle_are_served(server):
    base, _, _ = server
    for path in ("/", "/canvas.js", "/canvas.css", "/vendor/excalidraw.production.min.js"):
        status, _ = _call(base, path)
        assert status == 200, path


def test_a_conflicting_save_answers_409_with_what_is_on_disk(server):
    base, token, diagram = server
    stale = canvas.fingerprint(diagram)
    canvas.write_document(diagram, {**DOCUMENT, "elements": []})

    status, payload = _call(
        base, "/api/doc", token=token, data={"document": FROM_BROWSER, "base": stale}
    )
    assert status == 409
    assert payload["document"]["elements"] == []


def test_a_clean_save_answers_200_and_the_new_fingerprint(server):
    base, token, diagram = server
    status, payload = _call(
        base,
        "/api/doc",
        token=token,
        data={"document": FROM_BROWSER, "base": canvas.fingerprint(diagram)},
    )
    assert status == 200
    assert payload["fingerprint"] == canvas.fingerprint(diagram)


# --------------------------------------------------------------------------
# Оффлайн: обещание, которое легко потерять молча
# --------------------------------------------------------------------------


def test_the_page_points_excalidraw_at_the_vendored_assets():
    """Без ``EXCALIDRAW_ASSET_PATH`` бандл идёт за шрифтами и локалями на
    unpkg.com. Холст при этом работает — пока есть сеть, — и перестаёт быть
    оффлайновым без единого сообщения."""
    page = (CANVAS / "index.html").read_text(encoding="utf-8")
    assert "EXCALIDRAW_ASSET_PATH" in page
    assert '"/vendor/"' in page


def test_nothing_on_the_page_reaches_outside():
    """Ни одной ссылки на внешний адрес в нашем коде страницы."""
    for name in ("index.html", "canvas.js", "canvas.css"):
        text = (CANVAS / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!127\.0\.0\.1)", text), name


def test_the_vendored_files_are_all_there():
    required = [
        "vendor/excalidraw.production.min.js",
        "vendor/excalidraw.production.min.js.LICENSE.txt",
        "vendor/react.production.min.js",
        "vendor/react-dom.production.min.js",
        "vendor/LICENSE.excalidraw.txt",
        "vendor/LICENSE.react.txt",
        "vendor/excalidraw-assets/Virgil.woff2",
        "vendor/excalidraw-assets/Cascadia.woff2",
        "vendor/excalidraw-assets/locales/ru-RU-json-e1f4ed9d2d074f778304.js",
    ]
    for name in required:
        assert (CANVAS / name).is_file(), name
    assert list((CANVAS / "vendor/excalidraw-assets").glob("vendor-*.js")), "чанк бандла"


def test_the_provenance_table_matches_what_is_actually_vendored():
    """Таблица в ``vendor/README.md`` — единственное место, где записано, что
    именно лежит рядом. Без этой проверки она устареет при первом обновлении, и
    никто не узнает, какую версию раздаёт холст."""
    readme = (CANVAS / "vendor/README.md").read_text(encoding="utf-8")
    versions = dict(re.findall(r"`([a-z@/\-]+)` \| ([0-9.]+) \|", readme))
    assert versions["@excalidraw/excalidraw"] == "0.17.6"

    bundle = (CANVAS / "vendor/excalidraw.production.min.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert f'"{versions["@excalidraw/excalidraw"]}"' in bundle

    react = (CANVAS / "vendor/react.production.min.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert versions["react"] in react


def test_elements_from_disk_are_restored_before_they_reach_the_scene():
    """Проверено вживую: без ``restoreElements`` подпись, добавленная агентом,
    лежит в файле, но на холст не попадает — ``updateScene`` ждёт полноценный
    элемент, а агент пишет минимум полей (и правильно делает). Ни ошибки, ни
    следа: текст появлялся только после перезагрузки вкладки."""
    script = (CANVAS / "canvas.js").read_text(encoding="utf-8")
    assert "Lib.restoreElements" in script
    assert re.search(r"var elements = restored\(doc\.elements\);\s*\n\s*api\.updateScene", script)


def test_a_reopened_file_is_not_counted_as_an_edit():
    """Excalidraw двигает ``version`` и при служебной нормализации сцены, а
    значит, по одному ``version`` каждое открытие файла выглядит как правка —
    и файл, который лишь открыли посмотреть, переписывается. Поэтому решение
    принимает содержательная подпись, из которой поля учёта изменений
    исключены."""
    script = (CANVAS / "canvas.js").read_text(encoding="utf-8")
    assert re.search(
        r"var BOOKKEEPING = \{ version: true, versionNonce: true, updated: true \}",
        script,
    )
    assert "if (!BOOKKEEPING[key]) copy[key] = el[key];" in script
