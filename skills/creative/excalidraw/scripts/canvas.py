#!/usr/bin/env python3
"""``canvas.py`` — открыть тот же самый ``.excalidraw``, который правит агент.

ЗАЧЕМ ЭТО НУЖНО
---------------
Задача холста звучит так: владелец рисует грубые сущности, Digit доводит их до
схемы, владелец продолжает править. Половина этой петли уже была:
``scripts/revise.py`` читает файл и правит его по элементам, ничего не ломая.
Второй половины не было вовсе — у человека не было способа открыть **тот же
файл**.

Способ «перетащить на excalidraw.com» этой петли не даёт, и не по мелочи:
сайт открывает *копию*. Сохранение оттуда кладёт новый файл в загрузки, а не
туда, откуда он взят; чтобы агент увидел правку, её надо переносить руками
после каждого штриха. Петля, в которой один участник работает с файлом, а
второй с его копией, — это не двусторонняя правка, а обмен версиями.

Поэтому здесь свой холст: локальный сервер на 127.0.0.1 отдаёт страницу с
Excalidraw (MIT, положен целиком в ``canvas/vendor``, сеть не нужна), а
страница читает и пишет ровно тот путь, который передан в командной строке.

ЧЕГО ЗДЕСЬ БОЯТСЯ
-----------------
Файл общий, и оба участника пишут в него асинхронно. Отсюда три решения,
каждое против молчаливой потери чужой работы:

1. **Запись атомарна.** Пишем во временный файл рядом и делаем ``os.replace``.
   Иначе агент, читающий файл ровно в момент сохранения из браузера, получает
   половину JSON — и разбирает это как «файл сломан».

2. **Сохранение сверяет отпечаток.** Браузер присылает вместе с документом
   sha256 того состояния, из которого он исходил. Если на диске уже другое
   (агент успел поправить), сохранение отклоняется с кодом 409 и текущим
   содержимым — страница показывает это владельцу, а не затирает молча.
   Это ровно та же забота, что и ``version``/``versionNonce`` в ``revise.py``,
   только на уровне файла целиком.

3. **Незнакомые ключи верхнего уровня переживают сохранение.** Браузерный
   сериализатор Excalidraw знает ``type``/``version``/``source``/``elements``/
   ``appState``/``files`` и выкидывает всё, чего не знает. Если в документе
   лежит что-то ещё, сервер возвращает это на место. Симметрично тому, как
   ``revise.py`` бережёт неизвестные поля элементов.

Формат записи (``indent=2``, ``ensure_ascii=False``, перевод строки в конце)
совпадает с ``revise.py`` намеренно: два писателя в один файл должны давать
одинаковое оформление, иначе каждый ход петли — это ещё и диф на весь файл.

ПОЧЕМУ ЭТО СКРИПТ НАВЫКА, А НЕ КОМАНДА ``digit``
------------------------------------------------
По лестнице следа (AGENTS.md, «The Footprint Ladder») возможность, которая
выражается shell-командой, остаётся shell-командой: холст нужен внутри одного
навыка, и схема инструментов модели, за которую платит каждый вызов API, из-за
него расти не должна.

ДОСТУП
------
Сервер слушает только 127.0.0.1 и требует токен из URL в заголовке
``X-Canvas-Token``. Это не паранойя: без токена любая открытая в том же
браузере страница могла бы отправить POST на ``http://127.0.0.1:<порт>/api/doc``
и переписать файл владельца — ответ ей бы не показали (CORS), но запись бы
прошла. Нестандартный заголовок вынуждает браузер спросить разрешение
предварительным запросом, на который мы не отвечаем. Заодно проверяется
заголовок ``Host`` — против подстановки чужого имени, которое резолвится в
127.0.0.1.

ЗАПУСК
------
    canvas.py DIAGRAM.excalidraw               # откроет браузер
    canvas.py DIAGRAM.excalidraw --no-browser  # напечатает URL и будет ждать
    canvas.py DIAGRAM.excalidraw --port 8931   # фиксированный порт

Коды возврата: 0 — штатное завершение, 2 — плохие аргументы.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: Каталог со страницей и вложенным Excalidraw. Лежит рядом со скриптом, а не
#: ищется по рабочему каталогу: холст должен запускаться из любого места.
CANVAS_DIR = Path(__file__).resolve().parent.parent / "canvas"

#: Пустой документ для случая «файла ещё нет». Ровно те же ключи, что пишет
#: приложение, чтобы первый же обмен с диском не выглядел как чужой формат.
EMPTY_DOCUMENT: Dict[str, Any] = {
    "type": "excalidraw",
    "version": 2,
    "source": "digit",
    "elements": [],
    "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
    "files": {},
}

#: Что отдаём по расширению. Список закрытый: сервер раздаёт каталог с чужим
#: кодом, и угадывать типы по неизвестному расширению здесь незачем.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


class Refused(RuntimeError):
    """Действие не выполнено, и причину стоит напечатать."""


# --------------------------------------------------------------------------
# Файл на диске
# --------------------------------------------------------------------------


def fingerprint(path: Path) -> str:
    """sha256 содержимого файла; пустая строка, если файла нет.

    Отпечаток берётся по байтам, а не по ``mtime``: у mtime разрешение целой
    секунды на части файловых систем, а агент и человек вполне могут писать в
    один и тот же файл в пределах секунды — тогда «файл не менялся» было бы
    неправдой ровно в том случае, ради которого проверка и заведена.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise Refused(f"не читается {path}: {exc}") from exc
    return hashlib.sha256(data).hexdigest()


def load_document(path: Path) -> Dict[str, Any]:
    """Прочитать документ; если файла нет — вернуть пустой (но не создавать)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.loads(json.dumps(EMPTY_DOCUMENT))
    except OSError as exc:
        raise Refused(f"не читается {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} — не JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise Refused(f"{path} — не документ Excalidraw (нет массива 'elements')")
    return document


def merge_unknown_top_level(
    on_disk: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Вернуть ключи верхнего уровня, которых браузер не знает.

    Сериализатор Excalidraw собирает документ из фиксированного набора ключей.
    Всё, что положил в файл кто-то другой (метка задачи, ссылка на материал
    курса, что угодно), в его выводе просто отсутствует — и сохранение из
    браузера стёрло бы это без следа. Порядок ключей сохраняется от входящего
    документа, чужие дописываются в конец.
    """
    merged = dict(incoming)
    for key, value in on_disk.items():
        if key not in merged:
            merged[key] = value
    return merged


def write_document(path: Path, document: Dict[str, Any]) -> str:
    """Атомарно записать документ и вернуть новый отпечаток.

    Временный файл создаётся в том же каталоге, потому что ``os.replace``
    атомарен только внутри одной файловой системы.
    """
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Временный файл не должен пережить неудачу: каталог владельца — не
        # свалка для мусора после падения.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_from_browser(
    path: Path, incoming: Dict[str, Any], base: Optional[str]
) -> Tuple[bool, Dict[str, Any]]:
    """Сохранить документ из браузера, если диск не ушёл вперёд.

    Возвращает ``(True, {"fingerprint": ...})`` при записи и
    ``(False, {"fingerprint": ..., "document": ...})``, если файл на диске уже
    не тот, из которого исходил браузер. Второй случай — не ошибка, а сообщение
    владельцу: агент правил файл, пока вкладка была открыта.

    ``base is None`` означает осознанное «моя версия главная»: владелец нажал
    это в интерфейсе, увидев расхождение.
    """
    if not isinstance(incoming, dict) or not isinstance(incoming.get("elements"), list):
        raise Refused("прислан не документ Excalidraw (нет массива 'elements')")
    current = fingerprint(path)
    if base is not None and base != current:
        return False, {"fingerprint": current, "document": load_document(path)}
    on_disk = load_document(path) if current else {}
    document = merge_unknown_top_level(on_disk, incoming)
    return True, {"fingerprint": write_document(path, document)}


# --------------------------------------------------------------------------
# Сервер
# --------------------------------------------------------------------------


def _content_type(target: Path) -> Optional[str]:
    return _CONTENT_TYPES.get(target.suffix.lower())


class CanvasHandler(BaseHTTPRequestHandler):
    """Обработчик на один файл. Состояние — в атрибутах класса-наследника."""

    server_version = "DigitExcalidrawCanvas"
    protocol_version = "HTTP/1.1"

    # Заполняются фабрикой :func:`build_server`.
    diagram_path: Path = Path()
    token: str = ""
    root: Path = CANVAS_DIR
    quiet: bool = False

    # -- служебное --------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Страница обязана видеть файл, а не свой вчерашний снимок.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _host_is_local(self) -> bool:
        """Отсечь имя, которое кто-то завёл на 127.0.0.1 ради доступа к нам."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in {"127.0.0.1", "localhost", "::1", ""}

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Canvas-Token") or ""
        return secrets.compare_digest(supplied, self.token)

    # -- маршруты ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "чужой Host"})
            return
        path, _, _ = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._serve_static(Path("index.html"))
            return
        if path == "/api/doc":
            if not self._authorized():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "нет токена"})
                return
            try:
                document = load_document(self.diagram_path)
            except Refused as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "document": document,
                    "fingerprint": fingerprint(self.diagram_path),
                    "path": str(self.diagram_path),
                    "exists": self.diagram_path.exists(),
                },
            )
            return
        if path == "/api/fingerprint":
            if not self._authorized():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "нет токена"})
                return
            self._send_json(HTTPStatus.OK, {"fingerprint": fingerprint(self.diagram_path)})
            return
        self._serve_static(Path(path.lstrip("/")))

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_is_local():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "чужой Host"})
            return
        path, _, _ = self.path.partition("?")
        if path != "/api/doc":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "нет такого пути"})
            return
        if not self._authorized():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "нет токена"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "нет длины тела"})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"тело не JSON: {exc}"})
            return
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "тело не объект"})
            return
        base = payload.get("base")
        if base is not None and not isinstance(base, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "base не строка"})
            return
        try:
            saved, result = save_from_browser(
                self.diagram_path, payload.get("document"), base
            )
        except Refused as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK if saved else HTTPStatus.CONFLICT, result)

    # -- статика ----------------------------------------------------------

    def _serve_static(self, relative: Path) -> None:
        """Отдать файл из ``canvas/``, не выпуская наружу этого каталога."""
        try:
            target = (self.root / relative).resolve()
            target.relative_to(self.root.resolve())
        except (ValueError, OSError):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "путь наружу"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"нет файла {relative}"})
            return
        content_type = _content_type(target)
        if content_type is None:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "тип файла не раздаётся"})
            return
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)


def build_server(
    diagram_path: Path,
    port: int = 0,
    token: Optional[str] = None,
    root: Path = CANVAS_DIR,
    quiet: bool = False,
) -> Tuple[ThreadingHTTPServer, str]:
    """Поднять сервер на 127.0.0.1 и вернуть его вместе с URL страницы.

    ``port=0`` — свободный порт от системы: холст могут запустить дважды, и
    падение «адрес занят» здесь было бы бессмысленным.
    """
    token = token or secrets.token_urlsafe(16)
    handler = type(
        "BoundCanvasHandler",
        (CanvasHandler,),
        {
            "diagram_path": diagram_path,
            "token": token,
            "root": root,
            "quiet": quiet,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    return httpd, url


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Открыть .excalidraw в браузере: тот же файл, что правит агент.",
    )
    parser.add_argument("diagram", help="путь к файлу .excalidraw")
    parser.add_argument("--port", type=int, default=0, help="порт (0 — любой свободный)")
    parser.add_argument(
        "--no-browser", action="store_true", help="не открывать браузер, только URL"
    )
    parser.add_argument("--quiet", action="store_true", help="без журнала запросов")
    args = parser.parse_args(argv)

    diagram = Path(args.diagram).expanduser()
    if diagram.suffix != ".excalidraw":
        print(
            f"Отказ: {diagram} — ожидалось расширение .excalidraw",
            file=sys.stderr,
        )
        return 2
    if not (CANVAS_DIR / "index.html").is_file():
        print(f"Отказ: нет страницы холста в {CANVAS_DIR}", file=sys.stderr)
        return 2

    try:
        if diagram.exists():
            load_document(diagram)  # сломанный файл лучше показать сразу
        else:
            print(f"Файла нет, будет создан при первом сохранении: {diagram}")
    except Refused as exc:
        print(f"Отказ: {exc}", file=sys.stderr)
        return 2

    httpd, url = build_server(diagram, port=args.port, quiet=args.quiet)
    print(f"Холст: {url}")
    print(f"Файл:  {diagram}")
    print("Остановить: Ctrl+C")
    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлен.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
