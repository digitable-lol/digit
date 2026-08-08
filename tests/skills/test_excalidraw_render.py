"""Контракты показа ``.excalidraw``.

Правка боится испортить файл, показ — солгать о нём. Ложь у картинки одна и та
же в любом виде: на ней чего-то нет, а выглядит она целой. Поэтому почти каждый
тест здесь про то, что элемент дошёл до вывода — сам или заглушкой с именем
типа, — и про то, что подпись стоит там, где её увидит владелец, а не там, где
её оставило приложение до пересчёта.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "skills/creative/excalidraw/scripts/render.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("excalidraw_render", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _load_module()


def _document() -> dict:
    """Документ в том виде, в каком его отдаёт приложение: фигура, привязанная
    к ней подпись со СТАРЫМИ координатами, стрелка с подписью и привязками."""
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": [
            {"id": "box1", "type": "rectangle", "x": 100, "y": 100,
             "width": 200, "height": 80, "backgroundColor": "#a5d8ff",
             "fillStyle": "solid", "strokeColor": "#1e1e1e", "strokeWidth": 2,
             "roundness": {"type": 3}, "seed": 12345, "version": 7,
             "boundElements": [{"id": "label1", "type": "text"},
                               {"id": "arrow1", "type": "arrow"}]},
            # x/y здесь заведомо не там, где подпись окажется: приложение
            # пересчитывает их при загрузке.
            {"id": "label1", "type": "text", "x": 4000, "y": 4000,
             "width": 10, "height": 10, "text": "Клиент", "originalText": "Клиент",
             "containerId": "box1", "fontSize": 20, "fontFamily": 1,
             "textAlign": "center", "verticalAlign": "middle"},
            {"id": "arrow1", "type": "arrow", "x": 300, "y": 140,
             "width": 120, "height": 0, "points": [[0, 0], [120, 0]],
             "endArrowhead": "arrow", "strokeColor": "#1e1e1e", "strokeWidth": 2,
             "startBinding": {"elementId": "box1"},
             "endBinding": {"elementId": "box2"},
             "boundElements": [{"id": "label2", "type": "text"}]},
            {"id": "label2", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "text": "HTTP", "containerId": "arrow1",
             "fontSize": 16, "fontFamily": 1, "textAlign": "center"},
            {"id": "box2", "type": "ellipse", "x": 420, "y": 100, "width": 180,
             "height": 80, "backgroundColor": "#b2f2bb", "fillStyle": "hachure",
             "strokeColor": "#1e1e1e", "strokeWidth": 2,
             "boundElements": [{"id": "label3", "type": "text"}]},
            {"id": "label3", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "text": "Шлюз", "containerId": "box2",
             "fontSize": 20, "fontFamily": 1, "textAlign": "center",
             "verticalAlign": "middle"},
        ],
        "appState": {"viewBackgroundColor": "#ffffff"},
    }


def _svg(document: dict) -> str:
    return render.to_svg(document)


def _root(document: dict):
    return ET.fromstring(_svg(document))


def _viewbox(document: dict):
    return [float(v) for v in _root(document).get("viewBox").split()]


# --------------------------------------------------------------------------
# Ничего не пропадает молча
# --------------------------------------------------------------------------


def test_незнакомый_тип_рисуется_заглушкой_и_называется_в_легенде():
    """Пустое место на схеме неотличимо от пустого места в схеме."""
    document = _document()
    document["elements"].append(
        {"id": "emb", "type": "embeddable", "x": 100, "y": 300,
         "width": 200, "height": 100})
    svg = _svg(document)
    assert "embeddable" in svg, "тип, который нечем нарисовать, исчез с картинки"
    assert render.legend(document)["unsupported"] == ["embeddable"]


def test_заглушка_попадает_в_габариты_холста():
    """Иначе рамка окажется за краем, и «нечем нарисовать» снова станет ничем."""
    document = _document()
    document["elements"].append(
        {"id": "emb", "type": "embeddable", "x": 100, "y": 600,
         "width": 200, "height": 100})
    x0, y0, width, height = _viewbox(document)
    assert y0 + height >= 700


def test_удалённый_элемент_не_рисуется_и_считается_отдельно():
    """``isDeleted`` не показывает и приложение — это не потеря, но и не тишина."""
    document = _document()
    document["elements"].append(
        {"id": "gone", "type": "rectangle", "x": 5000, "y": 5000,
         "width": 40, "height": 40, "isDeleted": True})
    data = render.legend(document)
    assert data["deleted"] == 1
    assert data["unsupported"] == []
    x0, y0, width, height = _viewbox(document)
    assert x0 + width < 5000, "удалённый элемент раздул холст"


def test_все_рисуемые_элементы_помещаются_в_холст():
    document = _document()
    x0, y0, width, height = _viewbox(document)
    for element in document["elements"]:
        if element.get("type") == "text" and element.get("containerId"):
            continue
        ex0, ey0, ex1, ey1 = render.element_box(element)
        assert x0 <= ex0 and ey0 >= y0
        assert ex1 <= x0 + width and ey1 <= y0 + height


# --------------------------------------------------------------------------
# Подпись стоит там, где её увидит владелец
# --------------------------------------------------------------------------


def test_подпись_берёт_место_у_фигуры_а_не_у_себя():
    """Собственные x/y подписи в файле устаревают: приложение пересчитывает их
    при загрузке. Рисовать по ним — показывать не ту схему."""
    document = _document()
    root = _root(document)
    texts = [node for node in root.iter() if node.tag.endswith("text")]
    client = next(node for node in texts if node.text == "Клиент")
    assert 100 <= float(client.get("x")) <= 300, "подпись уехала за фигурой из файла"
    assert 100 <= float(client.get("y")) <= 180


def test_подпись_привязанной_к_фигуре_не_раздувает_холст():
    """Тот же дефект с другой стороны: холст по устаревшим координатам подписи
    вырастает в разы, схема съезжает в угол пустого поля."""
    x0, y0, width, height = _viewbox(_document())
    assert width < 700 and height < 300


def test_подпись_стрелки_встаёт_на_её_середину():
    document = _document()
    root = _root(document)
    http = next(node for node in root.iter()
                if node.tag.endswith("text") and node.text == "HTTP")
    assert 340 <= float(http.get("x")) <= 380


def test_под_подписью_стрелки_есть_фон():
    """Приложение разрывает линию под подписью. Разрыва здесь нет, поэтому
    надпись, иначе перечёркнутая линией, кладётся на фон документа."""
    document = _document()
    document["appState"]["viewBackgroundColor"] = "#fffce8"
    rects = [node for node in _root(document).iter()
             if node.tag.endswith("rect") and node.get("fill") == "#fffce8"]
    # Первый такой прямоугольник — сам холст, второй — подложка под «HTTP».
    assert len(rects) == 2
    assert float(rects[1].get("width")) < 100


def test_многострочная_подпись_идёт_строками_вниз():
    document = _document()
    for element in document["elements"]:
        if element["id"] == "label1":
            element["text"] = "Клиент\nмобильный"
    root = _root(document)
    lines = [node for node in root.iter()
             if node.tag.endswith("text") and node.text in ("Клиент", "мобильный")]
    assert len(lines) == 2
    first, second = sorted(lines, key=lambda node: float(node.get("y")))
    assert first.text == "Клиент"
    assert float(second.get("y")) - float(first.get("y")) == pytest.approx(25, abs=1)


def test_свободный_текст_рисуется_по_своим_координатам():
    document = _document()
    document["elements"].append(
        {"id": "title", "type": "text", "x": 100, "y": 40, "width": 200,
         "height": 30, "text": "Схема", "fontSize": 24, "fontFamily": 1})
    root = _root(document)
    title = next(node for node in root.iter()
                 if node.tag.endswith("text") and node.text == "Схема")
    assert float(title.get("x")) == 100


# --------------------------------------------------------------------------
# Легенда: то, что читается без картинки
# --------------------------------------------------------------------------


def test_легенда_показывает_связь_подписями_а_не_идентификаторами():
    data = render.legend(_document())
    assert data["links"] == [
        {"id": "arrow1", "from": "Клиент", "to": "Шлюз", "text": "HTTP"}]


def test_стрелка_без_привязки_считается_отдельно_а_не_выдаётся_за_связь():
    """Такая стрелка выглядит связью и связью не является: подвинут фигуру —
    она останется на месте. Показывать её как «? → ?» значит прятать дефект."""
    document = _document()
    document["elements"].append(
        {"id": "loose", "type": "arrow", "x": 100, "y": 250, "width": 100,
         "height": 0, "points": [[0, 0], [100, 0]]})
    data = render.legend(document)
    assert data["unbound_arrows"] == 1
    assert [link["id"] for link in data["links"]] == ["arrow1"]


def test_надписи_идут_в_порядке_чтения():
    document = _document()
    document["elements"].append(
        {"id": "title", "type": "text", "x": 100, "y": 40, "width": 200,
         "height": 30, "text": "Схема", "fontSize": 24})
    data = render.legend(document)
    assert [item["text"] for item in data["labels"]] == [
        "Схема", "Клиент", "Шлюз"]


def test_подпись_фигуры_не_повторяется_отдельной_строкой():
    """Надпись на стрелке уже прочитана в разделе связей, где от неё больше
    толку: там видно, что именно она соединяет."""
    data = render.legend(_document())
    assert [item["id"] for item in data["labels"]] == ["box1", "box2"]


def test_подпись_непривязанной_стрелки_из_легенды_не_пропадает():
    document = _document()
    document["elements"] += [
        {"id": "loose", "type": "arrow", "x": 100, "y": 250, "width": 100,
         "height": 0, "points": [[0, 0], [100, 0]],
         "boundElements": [{"id": "loose_t", "type": "text"}]},
        {"id": "loose_t", "type": "text", "x": 0, "y": 0, "width": 10,
         "height": 10, "text": "как-то так", "containerId": "loose",
         "fontSize": 16},
    ]
    data = render.legend(document)
    assert "как-то так" in [item["text"] for item in data["labels"]]


def test_легенда_печатается_даже_когда_картинку_показать_нечем(monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    with pytest.raises(render.Refused) as excinfo:
        render.show(_document())
    assert "chafa" in str(excinfo.value)
    assert render.format_legend(render.legend(_document())).strip()


# --------------------------------------------------------------------------
# Свойства вывода
# --------------------------------------------------------------------------


def test_svg_разбирается_как_xml_с_опасными_символами_в_подписи():
    document = _document()
    for element in document["elements"]:
        if element["id"] == "label1":
            element["text"] = 'A < B & "C"'
    root = ET.fromstring(_svg(document))
    assert any(node.text == 'A < B & "C"' for node in root.iter())


def test_вывод_воспроизводим_побайтово():
    """Иначе SVG нельзя ни сравнить дифом, ни положить в тест как ожидание."""
    document = _document()
    assert render.to_svg(document) == render.to_svg(_document())


def test_порядок_элементов_сохраняется_он_же_z_порядок():
    document = _document()
    svg = _svg(document)
    body = svg[svg.index("</defs>"):]
    assert body.index('fill="#a5d8ff"') < body.index("Клиент") < body.index("<ellipse")


def test_штриховка_отличается_от_сплошной_заливки():
    """У Excalidraw штрихованная фигура заметно светлее сплошной, и на схеме
    этим различают «есть заливка» и «залито намеренно»."""
    svg = _svg(_document())
    assert "<pattern" in svg
    assert 'fill="#a5d8ff"' in svg, "сплошная заливка стала штриховкой"


def test_картинка_ни_на_что_не_ходит_наружу():
    """Показ обязан работать без сети — как и холст рядом."""
    svg = _svg(_document())
    outside = svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in outside and "https://" not in outside


def test_пустой_документ_даёт_картинку_а_не_падение():
    document = {"type": "excalidraw", "elements": [], "appState": {}}
    root = ET.fromstring(render.to_svg(document))
    assert root.get("viewBox")
    assert render.legend(document)["elements"] == 0


def test_показ_не_трогает_исходный_файл(tmp_path):
    import json
    path = tmp_path / "d.excalidraw"
    raw = json.dumps(_document(), ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    render.main(["legend", str(path)])
    render.main(["svg", str(path), "--out", str(tmp_path / "d.svg")])
    assert path.read_text(encoding="utf-8") == raw


# --------------------------------------------------------------------------
# Отказы
# --------------------------------------------------------------------------


def test_не_json_отклоняется_с_причиной(tmp_path):
    path = tmp_path / "broken.excalidraw"
    path.write_text("{нет", encoding="utf-8")
    with pytest.raises(render.Refused) as excinfo:
        render.load_document(path)
    assert "не JSON" in str(excinfo.value)


def test_размер_картинки_разбирается_и_проверяется():
    assert render.terminal_size("60x30") == (60, 30)
    with pytest.raises(render.Refused):
        render.terminal_size("широкая")


def test_команда_показа_несёт_запрошенный_размер(tmp_path):
    argv = render.chafa_argv(tmp_path / "d.svg", 64, 20)
    assert argv[0] == "chafa"
    assert "64x20" in argv
