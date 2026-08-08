"""Инструмент `write_spec`: агент просит спецификацию у обученного генератора.

Почему это инструмент, а не подсказка в системном промпте. Основная модель
агента может написать текст, похожий на FTS, и без всякого инструмента — и
именно поэтому он нужен. Разница не в том, кто складывает слова, а в том, что
здесь между моделью и человеком стоит настоящий компилятор: сюда попадает
только тот документ, который прошёл compile → validate → testUtilities, а не
прошедший не показывается вовсе.

Что инструмент возвращает агенту — ровно то же, что `digit spec` печатает
человеку, включая оговорки. Отдельного «краткого ответа для модели» здесь нет
намеренно: если агенту вернуть спецификацию без оговорки про непроверяемое
соответствие задаче, он перескажет её человеку как проверенную целиком.
"""

from __future__ import annotations

import json


def check_write_spec_requirements() -> bool:
    """Инструмент виден только там, где есть чем проверять.

    Компилятора нет — инструмент не показывается. Показать его без компилятора
    значило бы предложить агенту путь, который всегда кончается отказом
    «проверить нечем»: агент честно попробует, потратит ход и объяснит человеку
    чужую проблему установки.
    """
    try:
        from digit_cli.specgen import gate

        return gate.available()
    except Exception:
        return False


def _brief_shape() -> str:
    from digit_cli.specgen.model import BRIEF_SHAPE

    return BRIEF_SHAPE


WRITE_SPEC_SCHEMA = {
    "name": "write_spec",
    "description": (
        "Write an executable FTS specification using Digitable's own trained "
        "specification generator, and verify it with the real FTS compiler "
        "before returning it.\n\n"
        "WHAT THIS TOOL GUARANTEES\n\n"
        "  Every specification it returns has passed three gates in order: "
        "compile (the FTS parser built the document), validate (no error-level "
        "diagnostics) and testUtilities (the document's own examples were "
        "executed by the real interpreter and matched). Text that fails is "
        "regenerated; text that never passes is refused and no specification "
        "is returned at all.\n\n"
        "WHEN TO USE IT\n\n"
        "  Whenever the user asks for an FTS specification, an executable "
        "domain description, or a calculation with rules and examples. Prefer "
        "it over writing FTS yourself: your own FTS is unverified prose, this "
        "is compiler-checked.\n\n"
        "YOUR JOB IS THE BRIEF — THIS IS THE PART THAT DECIDES THE RESULT\n\n"
        "  The generator is a 1.7B model trained on structured briefs and "
        "nothing else. Handed a loose paraphrase it returns a document that "
        "compiles and computes nothing — measured, not feared. Handed the "
        "shape below, the same content came back as a calculation with rules, "
        "a property and worked examples.\n\n"
        "  So do not forward the user's sentence. Turn the conversation into "
        "the brief: name the object, give every field a type, state each rule "
        "as a condition and an effect, and supply at least one example of "
        "input and expected result. If the user has not said enough to fill "
        "that in, ask them before calling this tool — a guessed field type "
        "becomes a specification that passes the compiler and describes the "
        "wrong thing.\n\n"
        f"{_brief_shape()}\n\n"
        "WHAT IT DOES NOT CHECK\n\n"
        "  That the document describes what the user actually meant. The gates "
        "check form and the document's own examples, nothing about intent. "
        "Relay the caveats that come back — they are part of the answer, not "
        "decoration.\n\n"
        "  In particular: when the result contains a calculation but no "
        "theorem, that is a measured weakness of the generator (no training "
        "document ever carried both at once), not a statement about the task. "
        "Do not present a missing theorem as normal."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": (
                    "The brief, in the shape given above: the domain, the "
                    "object with typed fields, the calculation with its rules, "
                    "properties and worked examples. Compose it from what the "
                    "user told you; do not pass their sentence through."
                ),
            },
            "attempts": {
                "type": "integer",
                "description": (
                    "How many samples to try before refusing. Default 2. The "
                    "first is greedy — the setting the generator was measured "
                    "at. Raising this rarely helps: on a 150-brief live run "
                    "every success came on the first attempt, and no failure "
                    "was recovered by resampling."
                ),
                "default": 2,
            },
        },
        "required": ["request"],
    },
}


def write_spec_tool(request: str, attempts: int | None = None) -> str:
    from digit_cli.specgen import pipeline
    from digit_cli.specgen.gate import GateUnavailable
    from digit_cli.specgen.model import GeneratorUnavailable

    if not (request or "").strip():
        return tool_error("просьба пустая — писать нечего")
    try:
        outcome = pipeline.write_spec(
            request, attempts=attempts or pipeline.DEFAULT_ATTEMPTS
        )
    except (GeneratorUnavailable, GateUnavailable) as error:
        # Отказ, а не деградация к собственному сочинению. Агенту важно
        # различать «проверено и плохо» от «не проверено»: во втором случае
        # правильный ответ человеку — назвать команду установки, а не выдать
        # спецификацию, за которую никто не ручался.
        return tool_error(
            f"генератор спецификаций недоступен: {error}",
            verified=False,
            advice=(
                "Не пишите спецификацию сами вместо него: непроверенный FTS "
                "выглядит так же, как проверенный. Скажите пользователю, что "
                "нужно поднять генератор."
            ),
        )
    except ValueError as error:
        return tool_error(str(error))

    payload = {
        "verified": outcome.ok,
        "report": pipeline.render(outcome),
        "attempts_used": outcome.attempts_used,
        "seconds": round(outcome.seconds, 1),
    }
    if outcome.ok:
        payload["fts"] = outcome.fts
        payload["caveats"] = outcome.caveats
    else:
        payload["failed_stage"] = outcome.verdict.stage if outcome.verdict else "unknown"
        payload["diagnostic"] = outcome.verdict.code if outcome.verdict else ""
    return json.dumps(payload, ensure_ascii=False)


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="write_spec",
    toolset="spec",
    schema=WRITE_SPEC_SCHEMA,
    handler=lambda args, **kw: write_spec_tool(
        request=args.get("request") or "",
        attempts=args.get("attempts"),
    ),
    check_fn=check_write_spec_requirements,
    emoji="📐",
)
