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
отказывает: самостоятельный слой правил отказывает пользователю в 58,8 %
случаев против 13,0 % у модели, и именно этим он платит за свои 96,0 % выбора
инструмента. Каскад берёт выигрыш, не оплачивая счёт: нет разбора — нет и
отказа, просто обычный ход.

Почему исполнение отдано tools-core, а не сделано здесь
------------------------------------------------------
`tools_execute` валидирует аргументы по схеме самой утилиты и на незнакомое
имя возвращает `invalid_args`, а не догадку. Это единственная причина, по
которой мост имён можно поставлять неполным: неизвестное имя аргумента
превращается в уступку модели, а не в неверный ответ. Граница, которую
сторожат 3,8 % ложных ответов, при этом не двигается.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Имя инструмента Digit, через который исполняются утилиты каталога.
#: Приходит от первопартийного MCP-сервера digit-tools (optional-mcps/).
EXECUTOR_TOOL = "tools_execute"

# Мост пространств имён: публичный slug каталога it-tools -> исполняемый
# идентификатор tools-core. Данные, не логика; выведены сопоставлением двух
# каталогов (86 публичных утилит против 94 исполняемых). None означает, что
# исполняемого аналога нет вовсе — такие маршруты каскад не берёт.
#
# ПОЧЕМУ мост только по идентификаторам, без переименования аргументов.
# Имена аргументов у двух каталогов расходятся: у 52 из 76 инструментов, где
# аналог существует, обязательные аргументы tools-core названы иначе
# (`clearText` против `text`, `hashFunction` против `algorithm`). Таблица
# переименований — это утверждение о том, в какой слот утилиты попадает
# значение, и в этом репозитории его никто не измерял. Ошибка в таком
# утверждении даёт не отказ, а неверный ответ с видом проверенного — ровно то,
# что запрещено. Поэтому аргументы уходят как есть: совпало имя — исполнилось,
# не совпало — tools-core вернул invalid_args и каскад уступил модели.
#
# Измеренная цена этого решения: на 100 задачах маршрутизации измерительного
# набора исполняется 23, остальные 72 уступают модели через invalid_args
# (4 не разобрали правила, 1 без исполняемого аналога). Мост аргументов —
# следующий шаг, и он обязан приехать со своим измерением.
PUBLIC_TO_CORE: Dict[str, Optional[str]] = {
    'ascii-text-drawer': 'ascii_art_generate',
    'base-converter': 'integer_base_convert',
    'base64-file-converter': 'base64_encode',
    'base64-string-converter': 'base64_encode',
    'basic-auth-generator': 'basic_auth_header',
    'bcrypt': 'bcrypt_hash',
    'benchmark-builder': 'benchmark_stats',
    'bip39-generator': 'bip39_generate',
    'camera-recorder': None,
    'case-converter': 'case_convert',
    'chmod-calculator': 'chmod_calculate',
    'chronometer': None,
    'color-converter': 'color_convert',
    'crontab-generator': 'crontab_describe',
    'date-converter': 'date_time_convert',
    'device-information': None,
    'docker-run-to-docker-compose-converter': 'docker_run_to_compose',
    'email-normalizer': 'email_normalize',
    'emoji-picker': 'emoji_search',
    'encryption': 'encrypt_text',
    'eta-calculator': 'eta_calculate',
    'git-memo': None,
    'hash-text': 'hash_text',
    'hmac-generator': 'hmac_generate',
    'html-entities': 'html_escape',
    'html-wysiwyg-editor': None,
    'http-status-codes': 'http_status_lookup',
    'iban-validator-and-parser': 'iban_validate',
    'ipv4-address-converter': 'ipv4_convert',
    'ipv4-range-expander': 'ipv4_range_expand',
    'ipv4-subnet-calculator': 'ipv4_subnet_calculate',
    'ipv6-ula-generator': 'ipv6_ula_generate',
    'json-diff': 'json_diff',
    'json-minify': 'json_minify',
    'json-prettify': 'json_prettify',
    'json-to-csv': 'json_to_csv',
    'json-to-toml': 'json_to_toml',
    'json-to-xml': 'json_to_xml',
    'json-to-yaml-converter': 'json_to_yaml',
    'jwt-parser': 'jwt_parse',
    'keycode-info': None,
    'list-converter': 'list_convert',
    'lorem-ipsum-generator': 'lorem_ipsum_generate',
    'mac-address-generator': 'mac_address_generate',
    'mac-address-lookup': 'mac_address_lookup',
    'markdown-to-html': 'markdown_to_html',
    'math-evaluator': 'math_evaluate',
    'mime-types': 'extension_to_mime',
    'numeronym-generator': 'numeronym_generate',
    'og-meta-generator': None,
    'otp-generator': 'otp_generate_totp',
    'password-strength-analyser': 'password_strength',
    'pdf-signature-checker': None,
    'percentage-calculator': 'percentage_calculate',
    'phone-parser-and-formatter': 'phone_parse',
    'qrcode-generator': 'qr_code_generate',
    'random-port-generator': 'random_port_generate',
    'regex-memo': None,
    'regex-tester': 'regex_test',
    'roman-numeral-converter': 'roman_to_arabic',
    'rsa-key-pair-generator': 'rsa_keypair_generate',
    'safelink-decoder': 'safelink_decode',
    'slugify-string': 'slugify',
    'sql-prettify': 'sql_format',
    'string-obfuscator': 'string_obfuscate',
    'svg-placeholder-generator': 'svg_placeholder_generate',
    'temperature-converter': 'temperature_convert',
    'text-diff': None,
    'text-statistics': 'text_statistics',
    'text-to-binary': 'text_to_binary',
    'text-to-nato-alphabet': 'text_to_nato',
    'text-to-unicode': 'text_to_unicode',
    'token-generator': 'token_generate',
    'toml-to-json': 'toml_to_json',
    'toml-to-yaml': 'toml_to_yaml',
    'ulid-generator': 'ulid_generate',
    'url-encoder': 'url_encode',
    'url-parser': 'url_parse',
    'user-agent-parser': 'user_agent_parse',
    'uuid-generator': 'uuid_generate',
    'wifi-qrcode-generator': 'wifi_qr_code_generate',
    'xml-formatter': 'xml_format',
    'xml-to-json': 'xml_to_json',
    'yaml-prettify': 'yaml_prettify',
    'yaml-to-json-converter': 'yaml_to_json',
    'yaml-to-toml': 'yaml_to_toml',
}


def is_enabled() -> bool:
    """Включён ли каскад. По умолчанию — да.

    Умолчание «да» держится не на вере, а на том, что каскад физически не
    может ответить наугад: он отвечает, только когда все обязательные
    аргументы найдены в запросе дословно И исполнитель принял их по своей
    схеме. Выключатель нужен для отладки и для сравнения «с каскадом /
    без каскада» на одной и той же сессии.
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
    if not is_enabled() or not isinstance(user_text, str):
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
        core_id = PUBLIC_TO_CORE.get(decision.tool_id or "")
        if not core_id:
            _record(agent, decision, outcome="ceded", detail="no-core-tool")
            return None

        result = _execute(agent, core_id, dict(decision.args),
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
