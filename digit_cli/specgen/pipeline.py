"""Путь «человек попросил спецификацию → генератор → компилятор → ответ».

Здесь одно правило, и оно определяет всю форму модуля: НИ ОДИН символ вывода
модели не попадает человеку, не пройдя настоящий компилятор. Не «обычно
проходит», не «мы проверили на выборке» — каждый показанный документ проверен
в тот момент, когда его показывают.

Почему так строго при 99,9 %. Потому что 99,9 % — это утверждение о выборке из
1 500 заданий, а человек перед экраном получает ОДИН документ. Разница между
«скорее всего компилируется» и «скомпилирован вот сейчас» — это ровно разница
между спецификацией и текстом, похожим на спецификацию. Второе Digit показывать
не должен: цена ошибки тут не «некрасиво», а «человек унёс в работу документ,
который не собирается».

Отсюда же три исхода вместо двух. Прошло — показываем. Не прошло ни за одну
попытку — отказываем, называя ступень и диагностику. Проверить нечем — отдельный
отказ, потому что «не проверено» и «проверено и плохо» чинят разные люди:
первое оператор, второе автор просьбы.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from digit_cli.specgen import gate, model
from digit_cli.specgen.gate import GateUnavailable, Verdict
from digit_cli.specgen.model import GeneratorUnavailable

#: Сколько раз пробовать. Первая попытка жадная — именно на ней измерено
#: качество. Повторять её бессмысленно: жадное декодирование детерминировано, и
#: вторая такая попытка вернёт тот же текст и ту же ошибку. Поэтому повтор идёт
#: с температурой и другим зерном — это другой образец, а не второй шанс тому
#: же.
#:
#: Почему две, а не три. Живой прогон интегрированного пути на 150 заданиях:
#: 145 прошли ворота, все 145 — с первой попытки. Все пять отказов — это
#: FTS_EXAMPLE_MISMATCH, то есть расчёт, чей собственный пример не сходится, и
#: ни один из пяти не выправился за два дополнительных образца. Это ожидаемо:
#: пересэмплирование того же задания не чинит арифметику. Третья попытка на
#: этом корпусе стоила по ~14 с на отказ и не спасла ничего.
#:
#: Перемерено на весах v2 (переобученный адаптер, ревизия 2 корпуса) — те же
#: 145 из 150, те же пять отказов, и это ТЕ ЖЕ ПЯТЬ ЗАДАНИЙ, что у v1. Вывод о
#: числе попыток от смены весов не зависит, и это не совпадение: отказ здесь
#: арифметический, а не выборочный.
#:
#: Что на том же прогоне изменилось — теорема. Заданий, просящих расчёт и
#: теорему сразу, в этих 150 тринадцать. У v1 обе конструкции получились в 0 из
#: 13; у v2 — во всех 10, что дошли до ворот (три из тринадцати попали в те
#: самые пять отказов). Теорема есть во всех 39 прошедших документах, где её
#: просили, против 29 у v1. Примеров исполнено 311, сошлись все 311.
#:
#: Почему повтор вообще остался. Есть второй класс отказов, которого этот
#: корпус не задел: оборванная на потолке генерация и просто неудачный образец.
#: Его повтор чинит, и заранее отличить его от неверной арифметики ворота не
#: могут — они видят только приговор.
DEFAULT_ATTEMPTS = 2

#: Температура повторов. Не «побольше творчества», а минимальный сдвиг, при
#: котором образец отличается от жадного.
RETRY_TEMPERATURE = 0.7


@dataclass(frozen=True)
class Attempt:
    """Одна генерация и приговор воротам над ней."""

    number: int
    fts: str
    verdict: Verdict
    telemetry: dict
    seconds: float


@dataclass
class Outcome:
    """Чем кончился путь. Ровно один из трёх исходов."""

    ok: bool
    fts: str | None
    verdict: Verdict | None
    attempts: list[Attempt] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def attempts_used(self) -> int:
        return len(self.attempts)


def ensure_server(*, autostart: bool = True, on_progress=None) -> None:
    """Убедиться, что генератор поднят. Иначе — названный отказ.

    Веса докачиваются молча ровно никогда: 1,2 ГиБ по сети в ответ на просьбу
    «напиши спецификацию» — не то, чего ждут от команды. Если весов нет,
    человеку называется команда, которая их поставит.
    """
    if model.healthy():
        return
    from digit_cli import local_model as lm

    spec = lm.SPECGEN_WEIGHTS
    path = lm.weights_path(spec)
    ready = path.is_file() and path.stat().st_size == spec.size_bytes
    if not (autostart and ready and lm.find_llama_server() is not None):
        raise GeneratorUnavailable(
            "генератор спецификаций не запущен. Поднять: "
            "`digit local start --model specgen` "
            f"({spec.size_bytes / 2**30:.1f} ГиБ весов при первом запуске)"
        )
    try:
        lm.start_server(spec, on_progress=on_progress)
    except lm.LocalModelError as error:
        raise GeneratorUnavailable(f"генератор не поднялся: {error}") from error


def write_spec(
    request: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    autostart: bool = True,
    on_progress=None,
) -> Outcome:
    """Написать спецификацию по просьбе и провести её через ворота.

    Возвращает Outcome. Бросает :class:`GeneratorUnavailable` или
    :class:`GateUnavailable`, если проверка не могла состояться вовсе — это
    третий исход, и путать его с отказом ворот нельзя.
    """
    if not request.strip():
        raise ValueError("просьба пустая — писать нечего")
    # Компилятор проверяется ДО генерации, а не после. Иначе человек ждёт
    # минуту работы модели ради сообщения «проверить нечем», которое было
    # известно с самого начала.
    gate.flang_dist()
    ensure_server(autostart=autostart, on_progress=on_progress)

    started = time.monotonic()
    tried: list[Attempt] = []
    for number in range(1, max(1, attempts) + 1):
        greedy = number == 1
        attempt_started = time.monotonic()
        if on_progress and number > 1:
            on_progress(f"попытка {number}: прошлая не прошла ворота, беру другой образец")
        text, telemetry = model.generate(
            request,
            temperature=0.0 if greedy else RETRY_TEMPERATURE,
            seed=None if greedy else number,
        )
        verdict = gate.check(text)
        tried.append(
            Attempt(
                number=number,
                fts=text,
                verdict=verdict,
                telemetry=telemetry,
                seconds=time.monotonic() - attempt_started,
            )
        )
        if verdict.ok:
            return Outcome(
                ok=True,
                fts=text,
                verdict=verdict,
                attempts=tried,
                caveats=caveats_for(verdict, telemetry, request),
                seconds=time.monotonic() - started,
            )

    return Outcome(
        ok=False,
        fts=None,
        verdict=tried[-1].verdict,
        attempts=tried,
        caveats=[],
        seconds=time.monotonic() - started,
    )


def caveats_for(verdict: Verdict, telemetry: dict, request: str = "") -> list[str]:
    """Что человек обязан знать, глядя на прошедший ворота документ.

    Зелёный приговор без этих строк — самая дорогая форма вранья в этой
    команде: он звучит как «спецификация правильная», а означает «форма верна и
    объявленные примеры сошлись». Между этими двумя утверждениями помещается
    целый документ не о том, о чём просили.

    ``request`` нужен ровно одной оговорке — про необязательные поля: чтобы
    сказать «просили пометить, а не помечено», надо знать, что просили. По
    умолчанию пустой, и тогда эта оговорка молчит, а не гадает.
    """
    notes: list[str] = []
    shape = verdict.shape or {}

    if verdict.examples_ran:
        notes.append(
            f"Примеры исполнены настоящим интерпретатором: {verdict.examples_passed} "
            f"из {verdict.examples_total} сошлись."
        )
    else:
        notes.append(
            "Примеров в документе нет — исполнять было нечего. Проверены разбор "
            "и семантика, но не поведение расчёта."
        )

    # Документ без расчёта и без теоремы компилятор принимает — объявление
    # объектов и морфизмов законно само по себе, и в проверочном корпусе таких
    # 34 из 150. Но именно в этой форме мимо ворот проходит вырожденный ответ:
    # ступень с примерами пропускается за отсутствием примеров, и «проверено»
    # начинает означать только «разобралось». Поймано живым прогоном на
    # свободно сформулированной просьбе — модель вернула морфизм из одних
    # комментариев, и он прошёл. Утверждение здесь строго о документе, без
    # догадок о намерении: что человек хотел расчёт, ворота знать не могут.
    if not shape.get("utilities") and not shape.get("proposition"):
        notes.append(
            "ВНИМАНИЕ: документ ничего не вычисляет и ничего не утверждает — "
            "в нём только объявления. Если вы просили расчёт или доказательство, "
            "их здесь нет, и ступень с примерами была пропущена за их "
            "отсутствием. Просьбу стоит переписать по образцу из справки "
            "`digit spec --help`."
        )

    # Здесь стояла оговорка «рядом с расчётом теоремы не будет»: у прежних весов
    # (v1) теорема отсутствовала на всех 129 holdout-заданиях, просивших расчёт и
    # теорему сразу. Дыру в данных закрыли, адаптер переобучили на ревизии 2
    # корпуса, и теперь теорема стоит в 129 из 129 таких документов и в 453 из
    # 453 по всему holdout. Оговорка снята вместе со своим тестом: повторять её
    # значило бы предупреждать о поведении, которого больше нет.
    #
    # Её место заняла другая измеренная слабость — и это не замена одного
    # оправдания другим, а то же правило: слабость, которую видно в числах,
    # обязана быть названа вслух.
    #
    # Необязательные поля. Задание, помечающее два поля как «необязательное»,
    # выходит верным в 2 случаях из 108 (у прежних весов было 21 из 108). Причина
    # в обучающем распределении: необязательное поле встречается там всегда ровно
    # по одному на документ — 797 строк из 11 000, и ни одной с двумя. Компилятор
    # здесь бессилен: поле, объявленное обязательным вместо необязательного, —
    # совершенно валидный документ, отвергать нечего.
    #
    # Счёт по слову — грубая мерка, и ошибается она в одну сторону: слово может
    # попасться в прозе, и тогда оговорка появится там, где всё в порядке.
    # Обратная ошибка — промолчать о недостающей пометке — дороже, поэтому
    # порог выбран так, а в самой оговорке названы оба числа, чтобы человек
    # рассудил сам, а не поверил на слово.
    asked_optional = request.lower().count("необязательн")
    if asked_optional and shape.get("optionalFields", 0) < asked_optional:
        notes.append(
            f"Необязательными помечено полей: {shape.get('optionalFields', 0)}, "
            f"а в задании их {asked_optional}. Это известная слабость "
            "генератора, а не вывод о задаче: в обучающих данных нет ни одного "
            "документа с двумя необязательными полями, и на проверочных "
            "заданиях модель расставляет пометку верно в 2 случаях из 108. "
            "Ворота этого поймать не могут — обязательное поле вместо "
            "необязательного даёт валидный документ. Проверьте строки «иногда "
            "является» глазами и дописывайте недостающие руками."
        )

    if verdict.warnings:
        codes = ", ".join(sorted({str(w.get("code")) for w in verdict.warnings}))
        notes.append(
            f"Компилятор принял документ, но с замечаниями: {codes}. "
            "Отказом они не являются, читать их стоит."
        )

    if not telemetry.get("grammar"):
        notes.append(
            "Грамматика FTS не найдена, декодирование шло без ограничения "
            "(поставить `digit mcp install fts-gate`). На приговор это не "
            "влияет — решает компилятор, — но синтаксических промахов больше."
        )

    notes.append(
        "Ворота проверяют форму документа и его собственные примеры. Что "
        "документ описывает именно вашу задачу, они проверить не могут — это "
        "остаётся за вами."
    )
    return notes


def render(outcome: Outcome, *, show_fts: bool = True) -> str:
    """Человеческий ответ по исходу. Один текст на CLI и на инструмент агента.

    Одна функция, а не две похожие: расхождение между тем, что видит человек в
    терминале, и тем, что агент вставляет в разговор, — это способ показать
    непроверенное под видом проверенного в одной из двух веток.
    """
    lines: list[str] = []
    if outcome.ok and outcome.verdict is not None:
        shape = outcome.verdict.shape or {}
        if show_fts and outcome.fts:
            lines.append(outcome.fts)
            lines.append("")
        parts = [f"категория «{shape.get('category', '')}»"]
        if shape.get("structures"):
            parts.append(f"объектов и структур: {shape['structures']}")
        if shape.get("functors"):
            parts.append(f"морфизмов: {shape['functors']}")
        if shape.get("utilities"):
            parts.append(
                f"расчётов: {shape['utilities']} "
                f"(правил {shape.get('rules', 0)}, свойств {shape.get('properties', 0)})"
            )
        if shape.get("proposition"):
            parts.append("теорема есть")
        lines.append("ПРОВЕРЕНО КОМПИЛЯТОРОМ: " + "; ".join(parts))
        lines.append(
            f"Ступени: compile → validate → testUtilities. Попыток: "
            f"{outcome.attempts_used}, время {outcome.seconds:.1f} с."
        )
        for note in outcome.caveats:
            lines.append(f"  · {note}")
        return "\n".join(lines)

    verdict = outcome.verdict
    lines.append("СПЕЦИФИКАЦИИ НЕТ. Показывать непроверенный текст я не буду.")
    if verdict is not None:
        lines.append(
            f"Ни одна из {outcome.attempts_used} попыток не прошла ворота. "
            f"Последняя упала на ступени «{verdict.stage_ru}»."
        )
        if verdict.code:
            lines.append(f"Диагностика: {verdict.code}")
        if verdict.detail:
            lines.append(f"  {verdict.detail}")
    stages = ", ".join(
        f"{attempt.number}: {attempt.verdict.stage}/{attempt.verdict.code or '—'}"
        for attempt in outcome.attempts
    )
    if stages:
        lines.append(f"Попытки: {stages}")
    lines.append(
        "Чаще всего помогает более подробная просьба: назовите объект, его "
        "поля с типами, правила расчёта и хотя бы один пример «вход → результат»."
    )
    return "\n".join(lines)


__all__ = [
    "Attempt",
    "Outcome",
    "GateUnavailable",
    "GeneratorUnavailable",
    "DEFAULT_ATTEMPTS",
    "caveats_for",
    "ensure_server",
    "render",
    "write_spec",
]
