"""Первый каскад маршрутизации: правила пробуются раньше модели.

Куда это встроено
-----------------
`run_conversation` (agent/conversation_loop.py) вызывает `try_turn` ровно один
раз за ход, сразу после пролога хода и ДО первого обращения к провайдеру. Если
каскад вернул словарь — ход закончен, модель в этом ходу не звалась вообще.
Если вернул None — дальше всё как всегда.

Почему именно там: пролог уже записал сообщение пользователя в историю и в
хранилище сессии, значит уступка модели ничего не теряет, а срабатывание не
рвёт транскрипт. Тот же приём стоит строкой выше — ранний возврат для
`api_mode == "codex_app_server"`.

Три условия срабатывания, и все три обязаны выполниться
-------------------------------------------------------
  1. правила разобрали запрос: назван инструмент И все обязательные аргументы
     найдены в тексте пользователя дословно (digit_cli/ruleparse);
  2. в этом ходу включён исполнитель — MCP-сервер digit-tools, инструмент
     `tools_execute`;
  3. исполнитель принял вызов: аргументы прошли его собственную проверку по
     схеме утилиты.

Не выполнилось любое — каскад молчит и ход идёт к модели. Молчит, а не
отказывает: самостоятельный слой правил отказывает пользователю в 60,4 %
случаев против 13,0 % у модели, и именно этим он платит за свои 96,0 % выбора
инструмента. Каскад берёт выигрыш, не оплачивая счёт: нет разбора — нет и
отказа, просто обычный ход.

Почему исполнение отдано tools-core, а не сделано здесь
------------------------------------------------------
`tools_execute` валидирует аргументы по схеме самой утилиты и на незнакомое
имя возвращает `invalid_args`, а не догадку. Это вторая сеть под мостом имён
(agent/tool_arg_bridge.py): даже если бы мост ошибся именем, ответом стала бы
уступка модели, а не неверный ответ. Первая сеть — сам мост: он пропускает
только те аргументы, чей слот доказан исполнением, и молчит про остальные.

Что мост изменил, в цифрах
--------------------------
На 100 задачах маршрутизации измерительного набора каскад доходил до ответа
23 раза; после моста — 87. Остальные 13: 4 не разобрали правила, 1 без
исполняемого аналога, 7 мост отказался переводить (измерение отвергло —
см. tool_arg_bridge), 1 отверг сам исполнитель (в запросе имя файла
«pyproject.toml» вместо его содержимого). Замер повторяется скриптом
scripts/measure_tool_arg_bridge.py настоящим бинарём.

Цена измерена там же, где и польза, — на всех 400 задачах, каскадом С
ИСПОЛНЕНИЕМ, до моста и после. И она РАЗНАЯ у двух ног, ровно как у самого
каскада (см. `is_enabled`):

  позади локальной модели   ложные ответы 10,0 % -> 9,2 %, утечки режима
                            2 -> 0, выбор инструмента 86 % -> 94 %. Мост
                            выигрывает: правила отвечают там, где мелкая
                            модель отвечала неверно.
  позади сильного шлюза     ложные ответы 1,5 % -> 3,5 %. Мост ПРОИГРЫВАЛ.

Проигрыш был адресный и к именам аргументов отношения не имел: из 400 задач
испортились 8, и ни одной из них не было среди 100 задач маршрутизации. Это
4 многошаговых и 4 red-team запроса, где слой правил брал операнд из САМОЙ
ИНСТРУКЦИИ («посчитай хеш строки» без строки -> `с этим хэшем`; «в треке
«Базы данных» назван порог» -> `Базы данных`). Такие вызовы гасило случайное
несовпадение имён; мост показал, что гасило их не устройство, а удача.

Дефект жил в извлечении аргументов (digit_cli/ruleparse/slots.py) и починен
там же (DGT-DIGIT-10): извлечение теперь отличает НАЗВАННЫЙ операнд от
ОПИСАННОГО и при описанном отказывается разбирать. Переизмерено тем же
харнессом, тем же бинарём:

  позади локальной модели   ложные ответы 9,2 % -> 6,8 %
  позади сильного шлюза     ложные ответы 3,5 % -> 1,0 %, утечки режима 6 -> 2

То есть мост теперь выигрывает у ОБЕИХ ног: 1,0 % против 1,5 % до моста.
Выбор инструмента (93,0 %) и точность аргументов (100,0 %) при этом не
сдвинулись ни на знак — чинилось извлечение, а не маршрутизация.

Мост, тем не менее, не получил своего выключателя: он живёт под выключателем
каскада, который стоит там, где выигрыш измерен.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from agent.tool_arg_bridge import translate

logger = logging.getLogger(__name__)

#: Имя инструмента Digit, через который исполняются утилиты каталога.
#: Приходит от первопартийного MCP-сервера digit-tools (optional-mcps/).
EXECUTOR_TOOL = "tools_execute"

# Публичные утилиты, у которых исполняемого аналога нет вовсе. Список нужен не
# для работы, а для наблюдаемости: «нечего исполнять» и «мост не подтвердил
# вызов» — разные причины уступки, и склеивать их в журнале значит потерять
# ответ на вопрос, куда расти дальше.
NO_CORE_TOOL = frozenset({
    'camera-recorder', 'chronometer', 'device-information', 'git-memo',
    'html-wysiwyg-editor', 'keycode-info', 'og-meta-generator',
    'pdf-signature-checker', 'regex-memo', 'text-diff',
})


def is_enabled(agent=None) -> bool:
    """Включён ли каскад. По умолчанию — везде.

    Умолчание менялось дважды, и оба раза по замеру.

    Сначала каскад включили везде — на том основании, что он отвечает, только
    когда все обязательные аргументы найдены в запросе дословно и исполнитель
    принял их по своей схеме.

    Потом выключили позади сильной модели: замер показал, что каскад не
    заменяет её ошибки, а СКЛАДЫВАЕТСЯ с ними (8 промахов правил плюс 13
    промахов модели дали ровно 21 у связки), и ложные ответы росли 1,5 % →
    3,5 %.

    Теперь включён снова, потому что тот проигрыш оказался НЕ свойством
    каскада. Все 8 испорченных задач были одного класса: слой правил брал
    операнд из самой инструкции — «посчитай хеш строки» без строки давало
    хеш от слов «с этим хэшем». Дефект жил в извлечении аргументов и починен
    там же (DGT-DIGIT-10, digit_cli/ruleparse/slots.py): извлечение отличает
    НАЗВАННЫЙ операнд от ОПИСАННОГО и при описанном отказывается разбирать.

    Переизмерено тем же харнессом, теми же задачами, тем же бинарём:

      позади сильного шлюза     ложные ответы 3,5 % → 1,0 % (до моста 1,5 %),
                                red-team 5,3 % → 1,3 %, утечки режима 6 → 2
      позади локальной модели   ложные ответы 9,2 % → 6,8 %
      отложенный набор          ложные ответы 7,0 % → 4,3 % при нулевой цене
                                в излишнем отказе

    То есть каскад теперь лучше ОБЕИХ ног, а не одной. Выбор инструмента
    (93,0 %) и точность аргументов (100,0 %) не сдвинулись ни на знак —
    чинилось извлечение, а не маршрутизация.

    Цена названа честно: разобранных запросов стало 101 вместо 111, и все
    десять потерянных были ложными ответами. Слой правил стал чаще молчать
    (излишний отказ 58,8 % → 60,4 %), но молчание уходит модели и ничего не
    стоит пользователю.

    Явная настройка (переменная окружения или конфиг) сильнее умолчания в обе
    стороны. `_second_stage_is_local` оставлена: она понадобится, если снова
    найдётся случай, где вторая ступень заведомо сильнее правил.
    """
    raw = os.environ.get("DIGIT_RULE_CASCADE")
    if raw is not None:
        return raw.strip().lower() not in {"0", "false", "off", "no"}
    try:
        from digit_cli.config import load_config

        section = load_config().get("rule_cascade")
        if isinstance(section, dict) and "enabled" in section:
            return bool(section["enabled"])
    except Exception:
        pass
    return True


# Провайдеры, за которыми стоит локальный процесс, а не чужой сервис. Список
# закрытый и совпадает с алиасами, которые digit_cli/model_switch.py ведёт на
# профиль `custom`: llama.cpp, vLLM, локальная Ollama и LM Studio.
_LOCAL_PROVIDERS = frozenset({"custom", "llamacpp", "llama.cpp", "ollama", "vllm", "lmstudio"})

# Адреса, по которым отвечает свой же компьютер. Проверяется хост, а не
# подстрока: `base_url` вида `https://localhost.attacker.example` содержит
# «localhost», но локальным не является.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _second_stage_is_local(agent) -> bool:
    """Стоит ли за второй ступенью локальная модель.

    Неизвестность трактуется как «не локальная»: включить каскад там, где он
    вредит, дороже, чем не включить там, где он помог бы. Первое портит
    главную метрику молча, второе видно как медленный ответ.
    """
    if agent is None:
        return False

    provider = (getattr(agent, "provider", None) or "").strip().lower()
    if provider and provider in _LOCAL_PROVIDERS:
        return True

    base_url = (getattr(agent, "base_url", None) or "").strip()
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse

        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS


def _executor_available(agent) -> bool:
    names = getattr(agent, "valid_tool_names", None) or set()
    return EXECUTOR_TOOL in names


def _execute(agent, core_id: str, args: dict, *, task_id, turn_id) -> Optional[str]:
    """Исполнить утилиту через диспетчер инструментов Digit.

    Возвращает текст результата либо None, если исполнить не вышло. None здесь
    всегда значит «уступаю модели», а не «скажи пользователю, что не вышло»:
    половина ответа хуже, чем обычный ход.
    """
    from model_tools import handle_function_call

    try:
        raw = handle_function_call(
            EXECUTOR_TOOL,
            {"tool_id": core_id, "args": args},
            task_id=task_id,
            turn_id=turn_id,
            session_id=str(getattr(agent, "session_id", "") or ""),
            enabled_tools=sorted(getattr(agent, "valid_tool_names", None) or []),
        )
    except Exception:
        logger.debug("исполнитель каскада упал на %s; уступаю модели", core_id, exc_info=True)
        return None

    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    # tools-core отвечает на неизвестное имя аргумента структурной ошибкой, а
    # не догадкой. Для каскада это НЕ ошибка исполнения, а нормальный сигнал
    # «этот вызов я не подтверждаю» — и единственно правильная реакция на него
    # такая же, как на неразобранный запрос: отдать ход модели.
    lowered = text.lower()
    if '"ok": false' in lowered or "invalid_args" in lowered or "unknown_tool" in lowered:
        return None
    return text


def try_turn(
    agent,
    user_text: Any,
    messages: list,
    conversation_history: Optional[list],
    *,
    task_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Один ход каскада. Словарь — ход закончен; None — ход идёт к модели.

    Исключений не бросает никогда. Каскад, который падает, — это каскад,
    который отказал пользователю вместо модели; любая поломка обязана
    выглядеть как обычный ход.
    """
    if not is_enabled(agent) or not isinstance(user_text, str):
        return None
    started = time.perf_counter()
    try:
        from digit_cli.ruleparse import route
        from digit_cli.ruleparse.cascade import render_answer

        decision = route(user_text)
        if not decision.routed:
            _record(agent, decision, outcome="ceded", detail=decision.declined)
            return None
        if not _executor_available(agent):
            _record(agent, decision, outcome="ceded", detail="no-executor")
            return None
        # Перевод в пространство имён исполнителя: и утилита, и КАЖДЫЙ
        # аргумент. Мост отказывается переводить вызов целиком, если хоть один
        # аргумент не подтверждён измерением, — см. agent/tool_arg_bridge.py.
        bridged = translate(decision.tool_id or "", dict(decision.args))
        if bridged is None:
            detail = ("no-core-tool" if decision.tool_id in NO_CORE_TOOL
                      else "no-arg-bridge")
            _record(agent, decision, outcome="ceded", detail=detail)
            return None
        core_id, core_args = bridged

        result = _execute(agent, core_id, core_args,
                          task_id=task_id, turn_id=turn_id)
        if result is None:
            _record(agent, decision, outcome="ceded", detail="executor-declined")
            return None

        # Происхождение печатается в самом ответе, а не только в футере CLI.
        # Футер есть у одной поверхности из шести: шлюз, ACP, одиночный
        # запуск и пакетный прогон читают только final_response. Строка в
        # теле ответа доезжает всюду, где ответ вообще виден, и переживает
        # копирование в переписку.
        answer = (
            f"▸ Отвечено правилом разбора, без обращения к модели "
            f"({decision.tool_id}, {decision.latency_ms:.1f} мс).\n\n"
            + render_answer(decision, result)
        )
        _record(agent, decision, outcome="answered", detail=core_id)
    except Exception:
        logger.debug("каскад правил упал; уступаю модели", exc_info=True)
        return None

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    messages.append({"role": "assistant", "content": answer})
    try:
        agent._persist_session(messages, conversation_history)
    except Exception:
        logger.debug("не удалось сохранить сессию после ответа каскада", exc_info=True)

    return {
        "final_response": answer,
        "messages": messages,
        # Ноль обращений к провайдеру — это не косметика отчёта, а то самое
        # различие, ради которого каскад существует. Всякий, кто считает
        # стоимость хода, увидит здесь ноль.
        "api_calls": 0,
        "completed": True,
        "failed": False,
        # Происхождение ответа. Ключ читают футер хода в CLI и телеметрия;
        # без него выигрыш каскада невидим и, значит, непроверяем.
        "answered_by": "rules",
        "rule_cascade": {
            **decision.as_telemetry(),
            "core_tool_id": core_id,
            "turn_ms": round(elapsed_ms, 2),
        },
    }


def _record(agent, decision, *, outcome: str, detail: str) -> None:
    """Оставить след решения каскада на агенте — для футера хода и журнала.

    Пишется и уступка тоже. Видеть только срабатывания — значит не иметь
    возможности отличить «каскад не нужен» от «каскад сломан».
    """
    try:
        agent._last_rule_cascade = {
            **decision.as_telemetry(),
            "outcome": outcome,
            "detail": detail,
        }
    except Exception:
        pass
    logger.debug(
        "каскад правил: %s (%s) инструмент=%s за %.2f мс",
        outcome, detail, decision.tool_id, decision.latency_ms,
    )
