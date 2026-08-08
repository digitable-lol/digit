"""``digit spec`` — попросить спецификацию словами и получить проверенную.

    digit spec "Заказ: сумма — деньги, срочный — признак. Расчёт доставки: \\
                если сумма больше 5000, доставка бесплатная, иначе 300"

Что печатается и почему именно это

1. Сама спецификация — и только если она прошла компилятор. Не прошла — её
   в выводе нет вовсе, даже под оговоркой. Оговорка не спасает: текст,
   похожий на спецификацию, будет скопирован в работу, а предупреждение над
   ним — нет.
2. Строка ПРОВЕРЕНО КОМПИЛЯТОРОМ: что за документ получился и какие ступени
   он прошёл. Без неё зелёный ответ неотличим от «модель что-то написала».
3. Оговорки. Главная из них — что именно ворота НЕ проверяют: соответствие
   документа задаче. Вторая, когда к месту, — отсутствие теоремы рядом с
   расчётом: это измеренная слабость генератора, и выдавать её за норму
   нельзя.

Коды возврата, чтобы отказ видел скрипт, а не только глаза:

    0 — проверено и напечатано
    1 — ни одна попытка не прошла ворота
    4 — проверка не состоялась: нет генератора или нет компилятора

Четвёрка отделена от единицы потому, что их чинят разные люди: четвёрку
оператор (поднять сервер, поставить fts-gate), единицу автор просьбы
(сказать подробнее). Двойка не занята намеренно: её отдаёт argparse за ошибку
в самой командной строке.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_NOT_VERIFIED = 1
EXIT_CANNOT_CHECK = 4


def cmd_spec(args) -> int:
    from digit_cli.specgen import pipeline
    from digit_cli.specgen.gate import GateUnavailable
    from digit_cli.specgen.model import GeneratorUnavailable

    request = " ".join(args.request).strip()
    as_json = bool(getattr(args, "json", False))
    # Ход работы идёт в stderr: stdout обязан остаться чистым, чтобы
    # `digit spec ... > заказ.fts` дал файл со спецификацией, а не со
    # спецификацией вперемешку с «поднимаю сервер».
    def progress(message: str) -> None:
        if not as_json:
            print(f"  · {message}", file=sys.stderr)

    try:
        outcome = pipeline.write_spec(
            request,
            attempts=getattr(args, "attempts", None) or pipeline.DEFAULT_ATTEMPTS,
            autostart=not getattr(args, "no_autostart", False),
            on_progress=progress,
        )
    except (GeneratorUnavailable, GateUnavailable) as error:
        print(f"проверка не состоялась: {error}", file=sys.stderr)
        return EXIT_CANNOT_CHECK
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return EXIT_CANNOT_CHECK

    if as_json:
        print(json.dumps(_as_dict(outcome), ensure_ascii=False, indent=1))
        return EXIT_OK if outcome.ok else EXIT_NOT_VERIFIED

    destination = getattr(args, "out", None)
    if outcome.ok and destination:
        # Файл пишется только для прошедшего ворота документа. Записать
        # непрошедший «чтобы посмотреть» — значит оставить на диске файл с
        # расширением .fts, который не собирается, и он переживёт память о том,
        # почему он там оказался.
        Path(destination).write_text((outcome.fts or "") + "\n", encoding="utf-8")
        print(f"спецификация записана: {destination}", file=sys.stderr)
        print(pipeline.render(outcome, show_fts=False))
        return EXIT_OK

    print(pipeline.render(outcome), file=sys.stdout if outcome.ok else sys.stderr)
    return EXIT_OK if outcome.ok else EXIT_NOT_VERIFIED


def _as_dict(outcome) -> dict:
    verdict = outcome.verdict
    return {
        "ok": outcome.ok,
        "fts": outcome.fts,
        "seconds": round(outcome.seconds, 3),
        "attempts": [
            {
                "number": attempt.number,
                "stage": attempt.verdict.stage,
                "code": attempt.verdict.code,
                "detail": attempt.verdict.detail,
                "seconds": round(attempt.seconds, 3),
                "grammar": attempt.telemetry.get("grammar"),
                "temperature": attempt.telemetry.get("temperature"),
                "completion_tokens": attempt.telemetry.get("completion_tokens"),
                "finish_reason": attempt.telemetry.get("finish_reason"),
            }
            for attempt in outcome.attempts
        ],
        "verdict": None
        if verdict is None
        else {
            "stage": verdict.stage,
            "code": verdict.code,
            "detail": verdict.detail,
            "examples_ran": verdict.examples_ran,
            "examples_total": verdict.examples_total,
            "examples_passed": verdict.examples_passed,
            "warnings": list(verdict.warnings),
            "shape": verdict.shape,
        },
        "caveats": outcome.caveats,
    }
