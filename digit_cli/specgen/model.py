"""Разговор с обученным генератором спецификаций.

Модель — LoRA поверх Qwen3-1.7B, обученная писать документы FTS и ничего
больше. Она подаётся через тот же llama-server, которым Digit уже поднимает
локальные веса, но НА СВОЁМ ПОРТУ: генератор вспомогательный и обязан жить
рядом с основной моделью агента, а не вместо неё.

Три вещи здесь не «настройки», а условия, при которых измерены 99,4 %:

* системная реплика слово в слово та, что была при обучении. Модель на 1,7
  млрд параметров переучена под одну задачу; чужой системный промпт для неё —
  другое распределение, и обещанные числа к нему не относятся;
* ``enable_thinking=False``. Обучение шло по шаблону без блока размышления;
  включённый вернёт ``<think>`` в ответ, которого грамматика не примет, а
  ворота не поймут;
* жадное декодирование на первой попытке. 99,4 % измерены именно так, и
  первая попытка обязана быть той, про которую есть число.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

#: Слово в слово из обучения (scripts/train_lora.py, scripts/gen.py на gpu).
#: Менять эту строку — значит отменять измеренные 99,4 %.
SYSTEM_PROMPT = (
    "Ты пишешь исполняемые спецификации на языке FTS (русская поверхность). "
    "Ответ — только текст спецификации, без пояснений и без ограждений кода."
)

#: Форма задания, на которой модель обучалась, — и это НЕ косметика.
#:
#: Живой прогон: на свободно сформулированной просьбе («Заказ: сумма — деньги,
#: срочная — признак; расчёт доставки: если сумма больше 5000, бесплатно»)
#: генератор вернул вырожденный документ — морфизм из одних комментариев, без
#: единого правила и примера. Он прошёл компилятор: комментарии внутри морфизма
#: синтаксически законны, а ступень с примерами пропускается, когда примеров
#: нет. То же самое содержание, поданное в форме ниже, дало расчёт с двумя
#: правилами, свойством и двумя примерами, и оба примера сошлись.
#:
#: Причина не в «плохом промпте», а в распределении: все обучающие задания были
#: структурированными техзаданиями, и просьба в свободной форме для этой модели
#: — другая задача, к которой измеренные 99,4 % не относятся. Поэтому форма
#: задания здесь не рекомендация по стилю, а условие применимости замера.
BRIEF_SHAPE = """Задача: описать «НАЗВАНИЕ ОБЛАСТИ» на FTS

## Данные
объект «Имя»:
* «поле» — число | деньги | строка | текст | дата | признак | состояние
* «необязательное поле» — деньги, необязательное

## Расчёт «Название расчёта»
расчёт «Название расчёта»: принимает «Имя», возвращает деньги, начальное значение 0
* правило «Название правила»: если «поле» не меньше 100, то прибавить 500
* свойство «Название свойства»: результат не менее 0
* пример «Название примера»: «поле» = 0 → результат 0
* пример «Сработало правило»: «поле» = 100 → результат 500"""

#: Хвост задания из обучающих примеров. Все обучающие задания заканчивались
#: этой строкой, и задание, пришедшее без неё, — это слегка другое
#: распределение. Строка дописывается, а не подменяет задание, и только если
#: её ещё нет: приписанная дважды, она сама становится тем, чего в обучении не
#: было.
TASK_SUFFIX = "Ответ — исходный текст спецификации FTS."

#: Потолок длины ответа. Самый длинный обучающий документ уложился в 1 200
#: новых токенов, здесь запас. Потолок обязателен: грамматика конечна, но не
#: коротка — она допускает предложения куда длиннее любого разумного бюджета,
#: поэтому останов остаётся за вызывающей стороной.
MAX_TOKENS = 1500

ENV_BASE_URL = "DIGIT_SPECGEN_URL"
ENV_GRAMMAR = "DIGIT_FTS_GRAMMAR"
ENV_GATE_HOME = "DIGIT_FTS_GATE_HOME"
DEFAULT_GATE_HOME = "~/.digit/mcp-servers/fts-gate"

_TIMEOUT = float(os.environ.get("DIGIT_SPECGEN_TIMEOUT", "300"))

_FENCE = re.compile(r"```(?:[a-zA-Z]*)\n(.*?)```", re.S)


class GeneratorUnavailable(RuntimeError):
    """Генератор не отвечает. Отказ, а не тихая замена другой моделью.

    Подставить сюда основную модель агента было бы худшим из возможных
    решений: измеренные 99,4 % относятся к обученному генератору, и ответ
    другой модели, выданный под тем же заголовком, — это обещание, за которым
    нет замера.
    """


def base_url() -> str:
    """Куда стучаться. Порт свой, потому что модель вспомогательная."""
    explicit = os.environ.get(ENV_BASE_URL, "").strip()
    if explicit:
        return explicit.rstrip("/")
    from digit_cli.local_model import SPECGEN_PORT

    return f"http://127.0.0.1:{SPECGEN_PORT}/v1"


def healthy(timeout: float = 2.0) -> bool:
    """Отвечает ли сервер генератора."""
    health = base_url().rsplit("/v1", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=timeout) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:
        return False


def grammar_path() -> Path | None:
    """Файл грамматики FTS для constrained decoding, если он на месте.

    Копии грамматики в этом дереве нет и не будет. Она выведена из разборщика
    и живёт в fts-gate рядом со своим чекером; вторая копия разошлась бы с
    разборщиком молча — то есть начала бы запрещать конструкции, которые
    компилятор принимает. Отсутствие грамматики не отказ: она удешевляет
    синтаксис, а решает всё равно компилятор.
    """
    explicit = os.environ.get(ENV_GRAMMAR, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    gate_home = Path(os.environ.get(ENV_GATE_HOME) or DEFAULT_GATE_HOME).expanduser()
    path = gate_home / "grammars" / "fts.gbnf"
    return path if path.is_file() else None


def _load_grammar() -> str | None:
    path = grammar_path()
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _as_task(request: str) -> str:
    """Довести задание до той формы, в которой модель его видела при обучении."""
    body = request.rstrip()
    if TASK_SUFFIX in body:
        return body + "\n"
    return f"{body}\n\n{TASK_SUFFIX}\n"


def clean(text: str) -> str:
    """Снять ограждение кода и блок размышления, если модель их всё-таки выдала.

    Под грамматикой ни того, ни другого быть не может. Без грамматики — может,
    и тогда компилятор споткнётся об `````fts`` вместо содержания.
    """
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip("\n")


def generate(
    request: str,
    *,
    temperature: float = 0.0,
    seed: int | None = None,
    max_tokens: int = MAX_TOKENS,
    use_grammar: bool = True,
) -> tuple[str, dict]:
    """Одна генерация. Возвращает текст спецификации и телеметрию запроса."""
    body: dict = {
        "model": "specgen",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _as_task(request)},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Шаблон Qwen3 без явного указания вставляет блок размышления. Обучение
        # шло без него — значит и здесь без него.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if seed is not None:
        body["seed"] = seed
    grammar = _load_grammar() if use_grammar else None
    if grammar:
        body["grammar"] = grammar

    request_obj = urllib.request.Request(
        f"{base_url()}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=_TIMEOUT) as resp:  # noqa: S310
            answer = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # TimeoutError отдельно от URLError не случайно: сокет, вышедший за
        # timeout уже ПОСЛЕ соединения, поднимает голый TimeoutError мимо
        # иерархии urllib, и без него человек получал трейсбек вместо ответа
        # (поймано живым прогоном на загруженной машине: генерация на
        # процессоре не уложилась в 300 с).
        raise GeneratorUnavailable(
            f"генератор не ответил на {base_url()} за {_TIMEOUT:.0f} с: {error}. "
            f"Поднять: `digit local start --model specgen`; сменить адрес — "
            f"{ENV_BASE_URL}; продлить ожидание — DIGIT_SPECGEN_TIMEOUT"
        ) from error

    choice = answer["choices"][0]
    usage = answer.get("usage") or {}
    telemetry = {
        "grammar": bool(grammar),
        "temperature": temperature,
        "seed": seed,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        # llama.cpp говорит "length", когда упёрлись в потолок. Обрезанный
        # документ почти всегда падает на компиляции, но причина у него другая,
        # и назвать её честнее, чем показать синтаксическую ошибку в конце.
        "finish_reason": choice.get("finish_reason"),
    }
    return clean(choice["message"]["content"] or ""), telemetry
