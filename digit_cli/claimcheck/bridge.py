"""Где живёт настоящий компилятор FTS и настоящий детектор — и как их позвать.

Единственное место в пакете, которое знает про файловую систему и про Digit.
Всё остальное — перенесённый измеренный артефакт, у которого путь к
компилятору был вбит абсолютной строкой в чужой домашний каталог.

ПОЧЕМУ компилятор не приехал в дерево вместе с разбором. Разбор — это питон
без зависимостей, его копия ничего не стоит. Компилятор — это node-пакет под
BSD-2-Clause со своим циклом выпуска, и вторая его копия означала бы вторую
реализацию семантики FTS. Вся затея держится ровно на обратном: «что означает
разобранное правило» обязан считать тот же самый интерпретатор, что считает
всё остальное в Digitable, иначе «проверено» перестаёт значить проверено.
Поэтому он остаётся внешним процессом за границей stdio — тем же, которым
Digit уже пользуется через MCP-сервер fts-gate.

ПОЧЕМУ переменная та же, что у fts-gate. У установленного fts-gate компилятор
уже лежит внутри: гейт объявляет `@digitable/fts` своей зависимостью и не
поднимется без неё. Значит `digit mcp install fts-gate` — единственный шаг
установки, и заводить вторую переменную под тот же каталог значило бы завести
второй способ рассинхронизировать их.

Если ни одного из путей нет, ответ — названный отказ с путями и именами
переменных, а не догадка и не молчание. Догадка здесь стоит дороже всего:
она пришла бы с видом проверенной.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Сам детектор логических ошибок. Совпадает с install.path в
#: optional-mcps/fts-gate/manifest.yaml — двигать вместе.
ENV_GATE_HOME = "DIGIT_FTS_GATE_HOME"
DEFAULT_GATE_HOME = "~/.digit/mcp-servers/fts-gate"

#: Компилятор. Отдельная переменная нужна только тому, у кого flang выложен
#: своей рабочей копией; обычному пользователю хватает установленного гейта.
ENV_FTS_HOME = "DIGIT_FTS_HOME"

#: Пачка проверок на один запуск node. Порог заимствован у verify.mjs, где
#: буфер вывода сбрасывается ровно на этом числе.
_SCRIPT = Path(__file__).with_name("verify.mjs")

#: Компилятор на большой спецификации работает секунды, не минуты. Потолок
#: щедрый, но конечный: зависший node обязан стать отказом, а не вечным ходом.
TIMEOUT_SECONDS = float(os.environ.get("DIGIT_CLAIMCHECK_TIMEOUT", "120"))


class RuntimeMissing(RuntimeError):
    """Компилятора или детектора нет на месте. Отказ, а не деградация.

    Отдельный тип, потому что вызывающая сторона обязана отличать «правило не
    проверено, потому что нечем» от «правило проверено и неверно». Свести их в
    один отказ значит соврать про вторую половину.
    """


@dataclass(frozen=True)
class Runtime:
    """Разрешённые пути до двух внешних кусков."""

    fts_dist: Path
    gate_dist: Path

    def env(self) -> dict[str, str]:
        """Окружение для verify.mjs. Пути передаются, а не угадываются им."""
        environment = dict(os.environ)
        environment["DIGIT_FTS_DIST"] = str(self.fts_dist)
        environment["DIGIT_FTS_GATE_DIST"] = str(self.gate_dist)
        return environment


def _gate_home() -> Path:
    return Path(os.environ.get(ENV_GATE_HOME) or DEFAULT_GATE_HOME).expanduser()


def resolve(strict: bool = True) -> Runtime | None:
    """Найти компилятор и детектор. `None` вместо исключения при strict=False.

    Проверяется не каталог, а конкретные файлы, которые будут импортированы:
    существующий каталог без сборки — это самый неприятный вид «установлено»,
    потому что он ломается позже и в другом месте.
    """
    gate_home = _gate_home()
    gate_dist = gate_home / "dist" / "src"
    fts_home = os.environ.get(ENV_FTS_HOME)
    fts_dist = (
        Path(fts_home).expanduser() / "dist" / "src"
        if fts_home
        else gate_home / "node_modules" / "@digitable" / "fts" / "dist" / "src"
    )

    missing = []
    if not (gate_dist / "gate.js").is_file():
        missing.append(
            f"детектор логических ошибок: нет {gate_dist / 'gate.js'} "
            f"(поставить `digit mcp install fts-gate` либо указать {ENV_GATE_HOME})"
        )
    for name in ("parser.js", "validate.js", "utility.js"):
        if not (fts_dist / name).is_file():
            missing.append(
                f"компилятор FTS: нет {fts_dist / name} "
                f"(он приезжает вместе с fts-gate; своя сборка — {ENV_FTS_HOME})"
            )
            break
    if shutil.which("node") is None:
        missing.append("node не найден в PATH; компилятор FTS — это node-пакет")

    if missing:
        if not strict:
            return None
        raise RuntimeMissing("; ".join(missing))
    return Runtime(fts_dist=fts_dist, gate_dist=gate_dist)


def runtime_available() -> bool:
    """Есть ли чем проверять. Ничего не запускает."""
    return resolve(strict=False) is not None


def run(records: list[dict]) -> list[dict]:
    """Один запуск node на пачку записей. NDJSON туда, NDJSON обратно."""
    if not records:
        return []
    runtime = resolve()
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
    try:
        process = subprocess.run(
            ["node", str(_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=runtime.env(),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeMissing(
            f"компилятор FTS не ответил за {TIMEOUT_SECONDS:.0f} с"
        ) from error
    if process.returncode != 0:
        raise RuntimeMissing(f"verify.mjs: {process.stderr[:500]}")
    return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]


def schema_of(source: str) -> dict:
    """Схема объявленного расчёта — глазами самого компилятора.

    Второго разборщика поверхностного синтаксиса FTS здесь нет и не будет: он
    разошёлся бы с настоящим ровно там, где это дороже всего — на редких
    объявлениях, которые никто не проверяет глазами. Поэтому спецификация
    компилируется, а схема снимается с уже построенного документа.
    """
    answer = run([{"id": "schema", "mode": "schema", "fts": source}])[0]
    if not answer.get("ok"):
        raise RuntimeMissing(
            f"спецификация не компилируется: {answer.get('detail', answer.get('code', ''))}"
        )
    return answer
