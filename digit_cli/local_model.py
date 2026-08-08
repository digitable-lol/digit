"""Локальная модель из коробки: Digit сам ставит llama.cpp и сам качает веса.

Зачем модуль. До него «локальный» путь первого запуска упирался в чужой
установщик: мастер искал `ollama` в PATH и, не найдя, печатал ссылку на
ollama.com и выходил (`setup.py::_run_first_time_local_setup`). Человек,
который хотел просто запустить агента, получал домашнее задание. Здесь тот же
путь доводится до конца силами самого Digit: скачать llama-server, скачать
GGUF, поднять сервер, записать конфиг.

Почему llama.cpp, а не Ollama. Ollama — отдельный демон со своим форматом
хранения: GGUF в него надо импортировать через Modelfile, а для этого он уже
должен быть установлен. llama.cpp публикует готовые бинарники под каждую
платформу (MIT, 10–18 МиБ), читает GGUF с диска как есть и поднимает ровно тот
OpenAI-совместимый эндпоинт, с которым Digit и так умеет работать через
провайдер "custom".

Почему бинарник качается, а не лежит в пакете. Лицензия (MIT) перепаковку
разрешает, но в колесо пришлось бы класть шесть сборок сразу — это +80 МиБ на
каждую установку Digit ради одной, которая пригодится. Апстрим уже хранит их
по одной на платформу, и мы берём нужную.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# llama.cpp: какой релиз и как его проверить
# ---------------------------------------------------------------------------

# Тег прибит гвоздями, а не берётся из /releases/latest. Причина не
# теоретическая: 2026-08-06 в 13:42 UTC у тега b10297 в релизе висел ровно один
# ассет из двадцати пяти — CI ещё догружал остальные. «latest» у llama.cpp не
# атомарен, и автоустановка, которая ему доверяет, ломается в случайные минуты
# дня. Обновление тега — отдельное осознанное действие вместе с пересчётом
# хешей ниже.
LLAMA_CPP_TAG = "b10295"

_RELEASE_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}"

# Апстрим НЕ публикует файл с контрольными суммами (проверено: в релизе 25
# ассетов и ни одного *checksums*). Значит, скачать эталон неоткуда — хеш
# снимается руками при закреплении тега и лежит здесь. Это не «на всякий
# случай»: без него любой, кто может подменить ответ на пути к
# objects.githubusercontent.com, кладёт нам исполняемый файл в $DIGIT_HOME/bin.
#
# Как пересчитать при бампе тега:
#   for a in <ассеты>; do curl -fLsO "$_RELEASE_BASE/$a"; done; sha256sum *
_LLAMA_CPP_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("Linux", "x86_64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-ubuntu-x64.tar.gz",
        "9b0d80c982ed5c8d5633c197f9e351aa9d5c72818146452ad9d2a2a68ff6b45f",
    ),
    ("Linux", "aarch64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-ubuntu-arm64.tar.gz",
        "ed302a1be0bbe0ae01e5ae137abfe055fc5c96b19aa388b9477b7654233b5383",
    ),
    ("Darwin", "arm64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-macos-arm64.tar.gz",
        "eee879ac4b0c9abd4afd1b646e90b59c54dab7e08cea0d8d40b8e6bf9ce43aa1",
    ),
    ("Darwin", "x86_64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-macos-x64.tar.gz",
        "c235c5202cd22db8fbf16a5b584b56f600f09385e8dd1fc4d918e1d096decd3c",
    ),
    ("Windows", "AMD64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-win-cpu-x64.zip",
        "a4287840ad6a2b9ee61ab446ca54132b807456c73ec986368be659925521f041",
    ),
    ("Windows", "ARM64"): (
        f"llama-{LLAMA_CPP_TAG}-bin-win-cpu-arm64.zip",
        "55a3098ea95462803f2f65498511fc3e57fafe721c1acba2507381e55fb93afe",
    ),
}

# Берётся сборка без ускорителей (ubuntu-x64, а не -cuda/-vulkan/-rocm). Она
# запустится везде, где есть нужный libc, и не требует ни драйвера, ни
# рантайма, ни угадывания версии CUDA. Модель на 4B в Q4 на процессоре отвечает
# приемлемо; кому нужна видеокарта, тот ставит свою сборку и указывает её через
# DIGIT_LLAMA_SERVER — этот путь проверяется первым.
_BINARY_NAME = "llama-server.exe" if platform.system() == "Windows" else "llama-server"

_DOWNLOAD_TIMEOUT = 300  # секунды на один HTTP-запрос; веса бывают на гигабайты


class LocalModelError(RuntimeError):
    """Локальный запуск не получился — с человеческим объяснением почему."""


def _digit_home() -> Path:
    from digit_constants import get_digit_home

    return get_digit_home()


def _runtime_dir() -> Path:
    """Куда распаковывается llama.cpp.

    Каталог с тегом в имени, а не один файл: llama-server слинкован с
    libggml*.so через RUNPATH=$ORIGIN (проверено objdump на релизном бинарнике),
    то есть библиотеки обязаны лежать с ним рядом. Тег в имени заодно делает
    бамп версии безопасным — старый каталог остаётся, пока его не уберут.
    """
    return _digit_home() / "bin" / f"llama.cpp-{LLAMA_CPP_TAG}"


def _platform_key() -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        machine = "arm64"
    elif machine in ("aarch64", "arm64") and system != "Darwin":
        machine = "aarch64" if system == "Linux" else "ARM64"
    elif machine in ("x86_64", "AMD64"):
        machine = "AMD64" if system == "Windows" else "x86_64"
    return system, machine


def unsupported_platform_hint() -> str:
    """Что сказать, когда готового бинарника под эту машину апстрим не собирает.

    Список из шести сборок покрывает Linux и macOS на x86_64/arm64 и Windows —
    но не FreeBSD, не musl-дистрибутивы (Alpine), не 32-битные системы и не
    Termux (ассет android-arm64 в релизе есть, но он собран не под Termux и
    отдельно нами не проверялся, поэтому мы его не обещаем). Врать про
    «поддерживается» хуже, чем честно отправить собирать из исходников.
    """
    system, machine = _platform_key()
    return (
        f"Готовой сборки llama.cpp под {system}/{machine} апстрим не публикует.\n"
        "Варианты:\n"
        "  • собрать llama.cpp самому и указать путь:\n"
        "      export DIGIT_LLAMA_SERVER=/путь/к/llama-server\n"
        "  • поставить пакетом (`brew install llama.cpp`) — он попадёт в PATH\n"
        "  • подключить любой OpenAI-совместимый эндпоинт через `digit model`"
    )


def find_llama_server(*, install_if_missing: bool = False) -> Optional[Path]:
    """Найти llama-server. Порядок: явный путь → наш каталог → PATH → скачать.

    DIGIT_LLAMA_SERVER стоит первым намеренно: у кого собрана своя сборка с
    CUDA/Metal, тот не должен получить нашу процессорную вместо неё.
    """
    explicit = os.environ.get("DIGIT_LLAMA_SERVER", "").strip()
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return path
        logger.warning("DIGIT_LLAMA_SERVER=%s не исполняемый файл — игнорирую", explicit)

    managed = _runtime_dir() / _BINARY_NAME
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed

    system = shutil.which("llama-server")
    if system:
        return Path(system)

    if install_if_missing:
        return install_llama_server()
    return None


def install_llama_server(*, force: bool = False, on_progress=None) -> Path:
    """Скачать, проверить хеш и распаковать llama.cpp под текущую платформу."""
    key = _platform_key()
    asset = _LLAMA_CPP_ASSETS.get(key)
    if asset is None:
        raise LocalModelError(unsupported_platform_hint())

    asset_name, expected_sha = asset
    target_dir = _runtime_dir()
    target = target_dir / _BINARY_NAME
    if target.is_file() and not force:
        return target

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="digit-llamacpp-") as tmpdir:
        tmp = Path(tmpdir)
        archive = tmp / asset_name
        if on_progress:
            on_progress(f"Скачиваю llama.cpp {LLAMA_CPP_TAG} ({asset_name})")
        _http_download(f"{_RELEASE_BASE}/{asset_name}", archive)

        actual = _sha256_file(archive)
        if actual.lower() != expected_sha.lower():
            raise LocalModelError(
                f"Хеш {asset_name} не совпал: ожидался {expected_sha}, получен "
                f"{actual}. Файл не распакован."
            )

        staging = tmp / "unpacked"
        staging.mkdir()
        _extract_archive(archive, staging)

        # У tar.gz-сборок всё лежит в одном каталоге llama-<tag>/, у zip под
        # Windows — прямо в корне. Разворачиваем обе формы в плоский каталог,
        # чтобы путь до llama-server не зависел от платформы.
        entries = list(staging.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        if not (root / _BINARY_NAME).is_file():
            raise LocalModelError(
                f"В архиве {asset_name} нет {_BINARY_NAME} — апстрим поменял раскладку"
            )

        # Каталог собирается под временным именем и переименовывается целиком:
        # оборванная распаковка не должна оставить полурабочий рантайм, который
        # при следующем запуске сочтут установленным.
        pending = target_dir.with_name(target_dir.name + ".partial")
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        shutil.move(str(root), str(pending))
        for exe in pending.iterdir():
            if exe.is_file() and exe.suffix.lower() not in (".so", ".dylib", ".dll"):
                try:
                    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        os.replace(pending, target_dir)

    logger.info("llama.cpp %s установлен в %s", LLAMA_CPP_TAG, target_dir)
    return target


def _extract_archive(archive: Path, dest: Path) -> None:
    """Распаковать tar.gz или zip, отказываясь выходить за пределы dest.

    И tarfile, и zipfile честно исполнят имя вида ``../../.ssh/authorized_keys``.
    Хеш выше уже отсекает подмену на лету, но проверка дешёвая, а последствия
    промаха — запись куда угодно от имени пользователя.
    """
    dest_root = os.path.realpath(dest)

    def _safe(name: str) -> bool:
        resolved = os.path.realpath(os.path.join(dest_root, name))
        try:
            return os.path.commonpath([dest_root, resolved]) == dest_root
        except ValueError:
            return False

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            bad = [n for n in zf.namelist() if not _safe(n)]
            if bad:
                raise LocalModelError(f"Архив пытается писать вне каталога: {bad[:3]}")
            zf.extractall(dest_root)
        return

    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                # Симлинки в релизах llama.cpp есть (libggml.so → libggml.so.0),
                # но только относительные и внутрь каталога; всё остальное режем.
                if os.path.isabs(member.linkname) or not _safe(
                    os.path.join(os.path.dirname(member.name), member.linkname)
                ):
                    raise LocalModelError(f"Небезопасная ссылка в архиве: {member.name}")
            if not _safe(member.name):
                raise LocalModelError(f"Архив пытается писать вне каталога: {member.name}")
        # filter="tar", а не "data": сборки llama.cpp содержат относительные
        # симлинки (libggml.so → libggml.so.0), которые "data" вырезает, и
        # llama-server после такой распаковки не находит свои библиотеки.
        try:
            tf.extractall(dest_root, filter="tar")
        except TypeError:  # Python без поддержки filter=
            tf.extractall(dest_root)


# ---------------------------------------------------------------------------
# Веса
# ---------------------------------------------------------------------------


# Порты. Основная модель агента и вспомогательные — на разных: раньше порт был
# один на всё («одновременно две такие модели держать незачем»), и для роутера
# это было верно — он подменял основную модель, а не работал рядом с ней.
# Генератор спецификаций работает именно рядом: агент во время разговора просит
# у него документ и продолжает разговор основной моделью. Один порт означал бы,
# что за спецификацию платят выгрузкой собеседника.
DEFAULT_PORT = 8127
SPECGEN_PORT = 8128


@dataclass(frozen=True)
class LocalWeights:
    """Одни веса в формате GGUF: откуда взять и что от них ждать."""

    key: str
    repo: str
    filename: str
    size_bytes: int
    # n_ctx_train из карточки модели. Не «сколько попросим», а сколько модель
    # реально умеет: llama-server обрезает окно слота до этого числа и никаким
    # флагом обрезку не отключить (проверено на b10295: «the slot context
    # (65536) exceeds the training context of the model (40960) - capping»).
    context_length: int
    role: str  # "chat" — основная модель агента, "router" — вспомогательная
    note: str = ""
    # На каком порту подаётся. У вспомогательной модели он свой — см. выше.
    port: int = DEFAULT_PORT
    # Сколько окна выделять НА САМОМ ДЕЛЕ, если задачи модели заведомо короче
    # её обучающего окна. Не то же, что context_length: там — что модель умеет,
    # здесь — за что мы платим памятью. KV-кэш линеен по окну, и у Qwen3-1.7B
    # полные 40 960 токенов стоят гигабайты ради задачи, которая укладывается в
    # три тысячи. None — «сколько нужно Digit, но не больше, чем модель умеет».
    serve_context: int | None = None

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.filename}"


# Модель по умолчанию для первого запуска. Требования жёсткие и заданы не нами:
# agent/agent_init.py отказывается работать с окном меньше MINIMUM_CONTEXT_LENGTH
# (64 000), потому что схемы инструментов и системный промпт съедают большой
# фиксированный префикс. Это отсекает почти все «маленькие» модели: Qwen3-1.7B
# и Qwen3-4B обучены на 32 768, наш собственный роутер — на 40 960.
# Qwen3-4B-Instruct-2507 обучена на 262 144 и вызывает инструменты — отсюда и
# выбор, а не из соображений «поменьше скачивать».
CHAT_WEIGHTS = LocalWeights(
    key="qwen3-4b-instruct-2507",
    repo="unsloth/Qwen3-4B-Instruct-2507-GGUF",
    filename="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    size_bytes=2_497_281_120,
    context_length=262_144,
    role="chat",
)

# Свой роутер Digitable. Основной моделью агента он быть не может и не должен:
# 40 960 < 64 000, Digit такую конфигурацию отвергает на старте с явной ошибкой.
# Запись здесь нужна, чтобы `digit local start --model router` и первый запуск
# качали веса одним и тем же кодом, а не двумя разными.
ROUTER_WEIGHTS = LocalWeights(
    key="digit-router-0.6b",
    repo="digitable-lol/digit-router-0.6b",
    filename="gguf/router-0.6b-v3-Q5_K_M.gguf",
    size_bytes=444_414_752,
    context_length=40_960,
    role="router",
    note=(
        "Маршрутизатор: выбирает инструмент и фрагмент корпуса, содержание "
        "ответа не сочиняет. Основной моделью Digit работать не может — окно "
        "40 960 меньше требуемых 64 000."
    ),
)

# Генератор спецификаций FTS. Обучен писать документы на языке грамматики
# fts-gate/grammars/fts.gbnf и больше ничего не умеет — на 1 500 проверочных
# заданий 99,4 % его вывода принял настоящий компилятор, а все девять неудач
# оказались синтаксическими. Для сравнения на одних и тех же 150 заданиях: та
# же модель без обучения — 0 из 150, она же со справочником по языку — 15 из
# 150, модель в восемь раз крупнее со справочником — 79 из 150.
#
# Основной моделью агента быть не может по той же причине, что и роутер:
# Qwen3-1.7B обучена на 40 960 (max_position_embeddings в карточке базовой
# модели), а agent/agent_init.py требует не меньше 64 000. Проверено живым
# запуском, и заодно выяснилось, что на обрезку сервером полагаться нельзя:
#   * b10295 (наш закреплённый) на `--ctx-size 64000` действительно урезает,
#     /props отдаёт n_ctx = 40 960;
#   * сборка 0.18.0 то же самое окно НЕ режет — пишет «n_ctx_seq (65536) >
#     n_ctx_train (40960) -- possible training context overflow» и честно идёт
#     выделять KV-кэш на 65 536 (у нас — до отказа по памяти).
# То есть «сервер сам обрежет» — свойство конкретной сборки, а не гарантия, и
# порог обязан держать сам Digit.
SPECGEN_WEIGHTS = LocalWeights(
    key="specgen-qwen3-1.7b",
    repo="digitable-lol/specgen-qwen3-1.7b",
    filename="gguf/specgen-1.7b-v1-Q5_K_M.gguf",
    size_bytes=1_257_879_232,
    context_length=40_960,
    role="specgen",
    note=(
        "Генератор спецификаций FTS: пишет документ по просьбе человека. "
        "Основной моделью Digit работать не может — окно 40 960 меньше "
        "требуемых 64 000. Вывод показывается только после компилятора."
    ),
    port=SPECGEN_PORT,
    # Самое длинное обучающее задание — 747 токенов промпта и 1 200 ответа.
    # 8 192 даёт запас вчетверо; полные 40 960 стоили бы гигабайты KV-кэша за
    # окно, в которое этой модели нечего положить.
    serve_context=8_192,
)

WEIGHTS: dict[str, LocalWeights] = {
    CHAT_WEIGHTS.key: CHAT_WEIGHTS,
    ROUTER_WEIGHTS.key: ROUTER_WEIGHTS,
    SPECGEN_WEIGHTS.key: SPECGEN_WEIGHTS,
}


def weights_dir() -> Path:
    return _digit_home() / "models"


def weights_path(spec: LocalWeights) -> Path:
    # Имя файла без каталогов из репозитория: у нас все GGUF лежат плоско, а
    # `gguf/router-...` в репозитории — деталь его раскладки, не нашей.
    return weights_dir() / Path(spec.filename).name


def ensure_weights(spec: LocalWeights, *, on_progress=None) -> Path:
    """Скачать веса, если их ещё нет. Возвращает путь к готовому файлу."""
    path = weights_path(spec)
    if path.is_file() and path.stat().st_size == spec.size_bytes:
        return path
    if path.is_file():
        # Размер не сошёлся — почти всегда это оборванная прошлая закачка.
        logger.warning("%s весит %d вместо %d, качаю заново", path, path.stat().st_size, spec.size_bytes)
        path.unlink()

    path.parent.mkdir(parents=True, exist_ok=True)
    if on_progress:
        on_progress(f"Скачиваю {spec.filename} ({spec.size_bytes / 2**20:.0f} МиБ)")
    # Во временный файл рядом, потом переименование: прерванная закачка не
    # должна выглядеть как готовые веса при следующем запуске.
    tmp = path.with_suffix(path.suffix + ".part")
    _http_download(spec.url, tmp)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Сервер
# ---------------------------------------------------------------------------

# DEFAULT_PORT и SPECGEN_PORT объявлены выше, рядом с описанием весов: порт —
# свойство роли модели, а не этой секции, и LocalWeights.port на него ссылается.


def _pid_file(port: int) -> Path:
    return _digit_home() / "models" / f"llama-server-{port}.pid"


def _log_file(port: int) -> Path:
    return _digit_home() / "models" / f"llama-server-{port}.log"


def server_pid(port: int = DEFAULT_PORT) -> Optional[int]:
    """PID работающего сервера или None."""
    pid_file = _pid_file(port)
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def server_healthy(port: int = DEFAULT_PORT, *, timeout: float = 2.0) -> bool:
    """Отвечает ли на этом порту llama-server.

    Проверяется /health, а не факт живого процесса: веса грузятся секунды, и
    совет «настройте Digit» до готовности приводит к отказу на первом запросе.
    """
    try:
        with urllib.request.urlopen(  # noqa: S310 — адрес всегда локальный
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_server_command(
    binary: Path, model_path: Path, spec: LocalWeights, port: int, *, threads: int | None = None
) -> list[str]:
    """Собрать командную строку llama-server.

    Все флаги проверены на b10295 через `llama-server --help`.
    """
    # Просить больше n_ctx_train бессмысленно — сервер всё равно обрежет и
    # напишет об этом в лог, зато KV-кэш успеет выделиться под запрошенный
    # размер. Берём столько, сколько нужно Digit, но не больше, чем модель умеет.
    # serve_context перекрывает это там, где задача заведомо короче окна: за
    # неиспользуемое окно платят памятью каждую секунду работы сервера.
    ctx = min(spec.serve_context or _minimum_context(), spec.context_length)
    cmd = [
        str(binary),
        "--model", str(model_path),
        # Без alias сервер отдаёт в /v1/models полный путь к файлу, и model.default
        # в конфиге с ним не совпадает (проверено вживую). Запросы llama-server
        # всё равно исполнит, но всё, что сверяет имя модели, будет мимо.
        "--alias", Path(spec.filename).name,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--ctx-size", str(ctx),
        # Один слот: агент ходит по одному запросу за раз, а KV-кэш делится
        # между слотами — с --parallel 2 каждому достанется половина окна.
        "--parallel", "1",
        # Без --jinja llama-server не применяет шаблон чата модели и не собирает
        # tool_calls. Для агента, который весь состоит из вызовов инструментов,
        # это разница между «работает» и «просто болтает».
        "--jinja",
        # KV-кэш в q8_0, а не f16: на 64k окне у 4B-модели это 4,8 ГиБ против
        # 9,7 ГиБ. Разница между «влезло в ноутбук» и «не влезло».
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        # Квантованный V-кэш llama.cpp принимает только вместе с flash-attention.
        "--flash-attn", "on",
        # По умолчанию llama-server отдаёт Access-Control-Allow-Origin: * и сам
        # предупреждает об этом в логе. Слушаем мы только 127.0.0.1, но этого
        # мало: страница в браузере пользователя ходит на localhost без всяких
        # препятствий, и с открытым CORS она может и гонять модель, и прочитать
        # из /props путь к файлу весов. «localhost» — предусмотренное апстримом
        # значение: заголовок отражается только для локальных источников.
        "--cors-origins", "localhost",
    ]
    if threads:
        cmd += ["--threads", str(threads)]
    return cmd


def _minimum_context() -> int:
    try:
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH

        return int(MINIMUM_CONTEXT_LENGTH)
    except Exception:
        return 64_000


def start_server(
    spec: LocalWeights,
    *,
    port: int | None = None,
    wait_seconds: float = 180.0,
    on_progress=None,
) -> int:
    """Поднять llama-server на этих весах и дождаться готовности. Вернуть PID.

    Порт по умолчанию берётся у самих весов, а не из общей константы: иначе
    вспомогательная модель поднялась бы поверх основной, и «поставил генератор»
    означало бы «потерял собеседника».
    """
    port = spec.port if port is None else port
    if server_healthy(port):
        existing = server_pid(port)
        if existing:
            return existing
        raise LocalModelError(
            f"Порт {port} занят чем-то, что отвечает на /health, но запущено не нами. "
            f"Остановите это или выберите другой порт."
        )

    binary = find_llama_server(install_if_missing=True)
    if binary is None:
        raise LocalModelError(unsupported_platform_hint())
    model_path = ensure_weights(spec, on_progress=on_progress)

    log_path = _log_file(port)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_server_command(binary, model_path, spec, port, threads=os.cpu_count() or 8)

    if on_progress:
        on_progress(f"Поднимаю llama-server на 127.0.0.1:{port}")
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(  # noqa: S603 — путь и аргументы формируем сами
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    _pid_file(port).write_text(str(proc.pid), encoding="utf-8")

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if server_healthy(port, timeout=1.0):
            return proc.pid
        if proc.poll() is not None:
            tail = _log_tail(log_path)
            raise LocalModelError(f"llama-server завершился сразу. Хвост лога:\n{tail}")
        time.sleep(0.25)

    raise LocalModelError(
        f"llama-server не ответил за {wait_seconds:.0f} с. Лог: {log_path}"
    )


def stop_server(port: int = DEFAULT_PORT) -> bool:
    """Остановить наш сервер. True, если было что останавливать."""
    pid = server_pid(port)
    if pid is None:
        _pid_file(port).unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.25)
    _pid_file(port).unlink(missing_ok=True)
    return True


def _log_tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(лог недоступен)"


# ---------------------------------------------------------------------------
# Общее
# ---------------------------------------------------------------------------


def _http_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "digit"})
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            with open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=1 << 20)
    except urllib.error.URLError as exc:
        raise LocalModelError(f"Не удалось скачать {url}: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configured_local_spec(config: dict) -> Optional[LocalWeights]:
    """Веса, на которые указывает конфиг, если он указывает на наш сервер.

    Ключ — совпадение и адреса, и имени файла: `base_url` на 127.0.0.1 сам по
    себе ничего не доказывает (там может стоять чужая сборка llama.cpp или
    vLLM), а поднимать под чужой сервер свой — худшее, что можно сделать.
    """
    model_cfg = config.get("model")
    if not isinstance(model_cfg, dict):
        return None
    base_url = str(model_cfg.get("base_url") or "")
    if f"127.0.0.1:{DEFAULT_PORT}" not in base_url and f"localhost:{DEFAULT_PORT}" not in base_url:
        return None
    name = str(model_cfg.get("default") or "")
    for spec in WEIGHTS.values():
        # Порт обязан совпасть тоже. Вспомогательная модель подаётся на своём;
        # опознать её по одному имени файла значило бы поднять сервер не там,
        # куда смотрит конфиг, и autostart закончился бы отказом соединения.
        if name == Path(spec.filename).name and spec.port == DEFAULT_PORT:
            return spec
    return None


def autostart_if_configured(config: dict, *, on_progress=None) -> bool:
    """Поднять локальный сервер, если конфиг на него указывает, а он не работает.

    llama-server — обычный процесс, а не системная служба: после перезагрузки
    его не будет, и `digit` встретит человека отказом соединения вместо ответа.
    Веса при этом лежат на диске, бинарник тоже — не хватает только команды,
    которую мы и так знаем. Возвращает True, если сервер пришлось поднимать.
    """
    spec = configured_local_spec(config)
    if spec is None or server_healthy(DEFAULT_PORT, timeout=1.0):
        return False
    if weights_path(spec).is_file() is False or find_llama_server() is None:
        # Ничего не докачиваем молча: тихая загрузка на гигабайты при обычном
        # запуске — не то, чего ждут от команды `digit`.
        return False
    try:
        start_server(spec, on_progress=on_progress)
    except LocalModelError as exc:
        logger.warning("Не удалось поднять локальную модель: %s", exc)
        return False
    return True


def resolve_weights(which: str | None) -> LocalWeights:
    """Какие веса имел в виду человек в `--model`.

    Короткое имя роли («router», «specgen») и полный ключ означают одно и то
    же: первое человек напечатает по памяти, второе увидит в `digit local
    status`, и промах между ними не должен молча приводить к запуску не той
    модели.
    """
    name = (which or "chat").strip().lower()
    for spec in WEIGHTS.values():
        if name in (spec.role, spec.key):
            return spec
    return CHAT_WEIGHTS


def local_command(args) -> int:
    """Обработчик `digit local start|stop|status`."""
    action = getattr(args, "local_action", None) or "status"
    # Порт по умолчанию — тот, на котором живёт названная модель. Без этого
    # `digit local stop --model specgen` останавливал бы основную модель.
    default_port = resolve_weights(getattr(args, "model", None)).port
    port = getattr(args, "port", None) or default_port

    if action == "stop":
        if stop_server(port):
            print(f"llama-server на порту {port} остановлен")
        else:
            print(f"llama-server на порту {port} и так не запущен")
        return 0

    if action == "status":
        pid = server_pid(port)
        healthy = server_healthy(port)
        if pid and healthy:
            print(f"llama-server работает: pid {pid}, порт {port}")
        elif healthy:
            print(f"на порту {port} что-то отвечает, но запущено не через `digit local`")
        else:
            print(f"llama-server не запущен (порт {port})")
        binary = find_llama_server()
        print(f"бинарник: {binary or 'не установлен — поставится при `digit local start`'}")
        for spec in WEIGHTS.values():
            path = weights_path(spec)
            ready = path.is_file() and path.stat().st_size == spec.size_bytes
            print(
                f"веса {spec.key}: {'на месте' if ready else 'не скачаны'} "
                f"({spec.size_bytes / 2**30:.1f} ГиБ, окно {spec.context_length}, "
                f"порт {spec.port})"
            )
        return 0

    if action == "start":
        spec = resolve_weights(getattr(args, "model", None))
        if spec.role != "chat":
            # Молчать нельзя: человек, попросивший роутер, скорее всего хочет
            # сделать его основной моделью — а Digit её отвергнет на старте.
            print(f"внимание: {spec.note}")
        try:
            pid = start_server(
                spec, port=port, on_progress=lambda message: print(f"  · {message}")
            )
        except LocalModelError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"llama-server работает: pid {pid}, порт {port}")
        print("настройка модели в config.yaml:")
        for key, value in local_model_config(spec, port).items():
            print(f"  {key}: {value}")
        return 0

    print(f"неизвестная подкоманда: {action}", file=sys.stderr)
    return 2


def local_model_config(spec: LocalWeights, port: int | None = None) -> dict:
    """Секция model: для config.yaml, указывающая на поднятый сервер."""
    return {
        "provider": "custom",
        "default": Path(spec.filename).name,
        "base_url": f"http://127.0.0.1:{spec.port if port is None else port}/v1",
        "api_mode": "chat_completions",
        # Окно записывается явно: /props у llama.cpp отдаёт фактическое, но при
        # выключенном сервере автоопределение упадёт, и Digit решит, что окна нет.
        # Обещать больше, чем реально выделено под слот, нельзя: Digit набьёт
        # запрос под объявленное окно и получит отказ уже на сервере.
        "context_length": min(
            spec.serve_context or _minimum_context(), spec.context_length
        ),
    }
