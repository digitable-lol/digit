"""Ворота, через которые обязан пройти вывод генератора спецификаций.

Компилятор FTS — внешний node-пакет, и это осознанно. Генератор пишет текст,
похожий на спецификацию; отличить похожий от настоящего умеет ровно один
интерпретатор — тот же, которым Digitable считает всё остальное. Питонов
разборщик FTS здесь не появится: он разошёлся бы с настоящим на редких
конструкциях, то есть ровно там, где расхождение никто не заметит глазами.

Переменные окружения те же, что у `digit rule-check`
(:mod:`digit_cli.claimcheck.bridge`), и это не совпадение, а требование: два
имени под один каталог — это два способа их рассинхронизировать. Отличие одно —
здесь нужен только компилятор, без детектора логических ошибок, поэтому
отсутствие fts-gate не мешает воротам работать, если задан ``DIGIT_FTS_HOME``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Своя рабочая копия flang. Первый по приоритету: у кого она есть, тот хочет
#: проверять именно ею.
ENV_FTS_HOME = "DIGIT_FTS_HOME"

#: Установленный fts-gate. Компилятор приезжает внутри него как зависимость,
#: поэтому обычному пользователю хватает `digit mcp install fts-gate`.
ENV_GATE_HOME = "DIGIT_FTS_GATE_HOME"
DEFAULT_GATE_HOME = "~/.digit/mcp-servers/fts-gate"

_SCRIPT = Path(__file__).with_name("verify.mjs")

#: Компилятор на одной спецификации отрабатывает за десятки миллисекунд. Потолок
#: щедрый, но конечный: зависший node обязан стать названным отказом, а не
#: вечным ожиданием под курсором.
TIMEOUT_SECONDS = float(os.environ.get("DIGIT_SPECGEN_GATE_TIMEOUT", "60"))


class GateUnavailable(RuntimeError):
    """Проверить нечем: нет компилятора или нет node.

    Отдельный тип, потому что вызывающая сторона обязана отличать «спецификация
    проверена и отвергнута» от «спецификация не проверена вовсе». Свести их в
    один отказ значит соврать про вторую половину — и показать человеку текст,
    за который никто не отвечал.
    """


@dataclass(frozen=True)
class Verdict:
    """Приговор воротам над одним текстом."""

    ok: bool
    stage: str
    code: str = ""
    detail: str = ""
    warnings: tuple[dict, ...] = ()
    examples_ran: bool = False
    examples_total: int = 0
    examples_passed: int = 0
    shape: dict = field(default_factory=dict)

    @property
    def stage_ru(self) -> str:
        return {
            "compile": "компилятор",
            "validate": "проверка документа",
            "examples": "исполнение примеров",
            "internal": "сама проверка",
            "ok": "все три ступени",
        }.get(self.stage, self.stage)


def flang_dist(strict: bool = True) -> Path | None:
    """Где лежит собранный компилятор. ``None`` вместо исключения при strict=False.

    Проверяются конкретные файлы, которые будут импортированы, а не каталог:
    существующий каталог без сборки — самый неприятный вид «установлено», он
    ломается позже и в другом месте.
    """
    fts_home = os.environ.get(ENV_FTS_HOME)
    if fts_home:
        candidate = Path(fts_home).expanduser() / "dist" / "src"
    else:
        gate_home = Path(os.environ.get(ENV_GATE_HOME) or DEFAULT_GATE_HOME).expanduser()
        candidate = gate_home / "node_modules" / "@digitable" / "fts" / "dist" / "src"

    missing = [n for n in ("parser.js", "validate.js", "utility.js") if not (candidate / n).is_file()]
    if missing:
        if not strict:
            return None
        raise GateUnavailable(
            f"компилятор FTS не найден: в {candidate} нет {', '.join(missing)}. "
            f"Поставить: `digit mcp install fts-gate`; своя сборка — {ENV_FTS_HOME}=/путь/к/flang"
        )
    if shutil.which("node") is None:
        if not strict:
            return None
        raise GateUnavailable("node не найден в PATH; компилятор FTS — это node-пакет")
    return candidate


def available() -> bool:
    """Есть ли чем проверять. Ничего не запускает."""
    return flang_dist(strict=False) is not None


def check_many(sources: list[str]) -> list[Verdict]:
    """Прогнать пачку текстов через compile → validate → testUtilities.

    Пачкой, а не по одному: каждый запуск node стоит ~120 мс на старт и импорт
    компилятора, и на переборе вариантов это единственная заметная статья.
    """
    if not sources:
        return []
    dist = flang_dist()
    payload = "\n".join(
        json.dumps({"id": index, "fts": text}, ensure_ascii=False)
        for index, text in enumerate(sources)
    ) + "\n"
    environment = dict(os.environ)
    environment["DIGIT_FTS_DIST"] = str(dist)
    try:
        process = subprocess.run(  # noqa: S603 — путь и аргументы формируем сами
            ["node", str(_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise GateUnavailable(
            f"компилятор FTS не ответил за {TIMEOUT_SECONDS:.0f} с"
        ) from error
    if process.returncode != 0:
        raise GateUnavailable(f"verify.mjs: {process.stderr[:500]}")

    by_id: dict[int, dict] = {}
    for line in process.stdout.splitlines():
        if line.strip():
            answer = json.loads(line)
            by_id[int(answer["id"])] = answer
    if len(by_id) != len(sources):
        raise GateUnavailable(
            f"компилятор ответил на {len(by_id)} из {len(sources)} — проверка не состоялась"
        )
    return [_verdict(by_id[index]) for index in range(len(sources))]


def check(source: str) -> Verdict:
    """Прогнать один текст через ворота."""
    return check_many([source])[0]


def _verdict(answer: dict) -> Verdict:
    return Verdict(
        ok=bool(answer.get("ok")),
        stage=str(answer.get("stage") or "internal"),
        code=str(answer.get("code") or ""),
        detail=str(answer.get("detail") or ""),
        warnings=tuple(answer.get("warnings") or ()),
        examples_ran=bool(answer.get("examplesRan")),
        examples_total=int(answer.get("examplesTotal") or 0),
        examples_passed=int(answer.get("examplesPassed") or 0),
        shape=dict(answer.get("shape") or {}),
    )
