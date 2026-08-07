"""Мост имён аргументов: публичный каталог it-tools -> исполнитель tools-core.

Что это утверждает
------------------
Каждая строка ниже — утверждение вида «значение, которое разбор правил положил
в публичный аргумент P инструмента T, попадает в слот C утилиты tools-core».
Ошибка в таком утверждении даёт не отказ, а НЕВЕРНЫЙ ОТВЕТ С ВИДОМ
ПРОВЕРЕННОГО. Поэтому здесь нет ни одной пары, полученной сравнением имён на
глаз: каждая прошла испытание исполнением (scripts/prove_tool_arg_bridge.py).

Испытание, которым получена таблица
-----------------------------------
  1. СХЕМА. Значение принимается слотом по типу и по enum, а все обязательные
     слоты утилиты заполнены. Схемы читаются у самого бинаря tools-core через
     его же MCP-протокол, а не из документации: исполняет запрос бинарь,
     значит правду о слотах знает только он.
  2. ЭТАЛОН. Вызов через мост даёт результат, совпадающий с эталоном, который
     вычислен НЕЗАВИСИМО от обоих каталогов: стандартной библиотекой Python
     (hashlib, hmac, base64, ipaddress, urllib, html, json) или внешним
     стандартом (тестовые векторы BIP-39, фонетический алфавит НАТО, формат
     полезной нагрузки Wi-Fi QR). Эталон, взятый у tools-core, доказывал бы
     только то, что бинарь равен самому себе.
  3. РАЗЛИЧИМОСТЬ. Ни одна другая раскладка тех же значений по слотам той же
     утилиты не даёт того же эталона. Это главное испытание: перепутанные
     местами `text` и `secret` вызов не роняют — они дают другой, но
     правдоподобный ответ, и поймать их можно только так.

Значения для проб взяты не из головы: это то, что `digit_cli.ruleparse.route()`
реально выдаёт на запросах измерительного набора.

Что испытание отвергло — и почему это ценно
-------------------------------------------
Шесть инструментов не попали в мост, и каждый отвергнут измерением:

  http-status-codes   разбор даёт `search` строкой «418», слот `code` — целое.
                      Тип не подгоняется: подгонка — это второе утверждение
                      поверх первого, и оно тоже никем не измерено.
  chmod-calculator    разбор даёт `permissions` словарём владелец/группа/все,
                      слот ждёт строку «755». Не переименование, а перевод.
  temperature-converter  разбор даёт `scale` русской леммой «фаренгейт», а enum
                      слота `from` — английский. Хуже того, на запросе
                      «в фаренгейтах, 25 градусов цельсия» лемма называет ЦЕЛЬ,
                      а слот — ИСТОЧНИК. Схема отвергла раньше, чем это стало
                      неверным ответом.
  eta-calculator      разбор даёт `timeSpan` числом 5 из «20 штук за 5 минут».
                      Слот `timeSpanMs` — миллисекунды. Оба числа, схема молчит,
                      а ответ разошёлся бы в 60 000 раз: 125 мс вместо 125 минут.
                      Поймано ТОЛЬКО эталоном — тот случай, ради которого
                      сверка с независимым эталоном и заведена.
  email-normalizer    имена совпадают дословно (`emails` -> `emails`), и до
                      этого моста маршрут исполнялся. Но разбор склеивает
                      адреса запятой, а утилита ждёт перевода строки и на
                      запятую возвращает ok=true со строкой «Unable to parse
                      email: …» внутри. Совпадение имён — не совпадение
                      смыслов; маршрут снят с исполнения.
  percentage-calculator  для «X% от Y» обе раскладки (x,y) и (y,x) дают ОДИН И
                      ТОТ ЖЕ ответ: percentOf симметрична. Значит исполнение
                      слот не определяет, и пара объявлена неоднозначной.
                      Вариант «выросло с X до Y» (change) несимметричен,
                      измерен и в мосте остался.

Ещё две пары подрезаны внутри выживших вариантов: `words` и `sentences` у
lorem-ipsum-generator (различимы только в связке, поодиночке исполнение их не
отделяет от `seed`) и `encodeUrlSafe` на ветке раскодирования base64.

Мост — СПИСОК РАЗРЕШЁННОГО, а не список переименований
------------------------------------------------------
`translate()` возвращает None, если хоть один пришедший публичный аргумент не
описан здесь. Не «отбросить лишнее и выполнить»: отброшенный `algorithm`
превратил бы просьбу про md5 в ответ по умолчанию sha256 — неверный ответ с
видом проверенного. Молчание вместо половины ответа.
"""

from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional, Tuple


class Variant(NamedTuple):
    """Одно направление публичной утилиты.

    У it-tools одно окно делает обе стороны сразу — закодировать и
    раскодировать, — а в tools-core это две разные утилиты. Направление
    опознаётся по НАБОРУ пришедших аргументов, а не по словам запроса: слова
    уже разобраны, и переспрашивать их значило бы гадать второй раз.
    """

    #: публичные аргументы, все из которых обязаны присутствовать
    when: Tuple[str, ...]
    #: идентификатор утилиты tools-core
    core: str
    #: доказанное переименование: публичное имя -> слот tools-core
    args: Dict[str, str]
    #: значение, которого публичная сторона не даёт вовсе; тоже доказано
    const: Dict[str, Any] = {}


#: Публичный slug -> направления. 68 инструментов, 76 направлений, 99 пар.
BRIDGE: Dict[str, Tuple[Variant, ...]] = {
    'ascii-text-drawer': (
        Variant(('input',), 'ascii_art_generate',
                {'font': 'font', 'input': 'text'}),
    ),
    'base-converter': (
        Variant(('input',), 'integer_base_convert',
                {'input': 'value', 'inputBase': 'fromBase', 'outputBase': 'toBase'}),
    ),
    'base64-string-converter': (
        Variant(('base64Input',), 'base64_decode',
                {'base64Input': 'base64'}),
        Variant(('textInput',), 'base64_encode',
                {'encodeUrlSafe': 'urlSafe', 'textInput': 'text'}),
    ),
    'basic-auth-generator': (
        Variant(('username', 'password'), 'basic_auth_header',
                {'password': 'password', 'username': 'username'}),
    ),
    'bcrypt': (
        Variant(('compareHash', 'compareString'), 'bcrypt_compare',
                {'compareHash': 'hash', 'compareString': 'text'}),
        Variant(('input',), 'bcrypt_hash',
                {'input': 'text', 'saltCount': 'saltRounds'}),
    ),
    'bip39-generator': (
        Variant(('entropy',), 'bip39_from_entropy',
                {'entropy': 'entropy'}),
    ),
    'case-converter': (
        Variant(('input',), 'case_convert_all',
                {'input': 'text'}),
    ),
    'color-converter': (
        Variant(('input',), 'color_convert',
                {'input': 'color'}),
    ),
    'crontab-generator': (
        Variant(('cron',), 'crontab_describe',
                {'cron': 'cron'}),
    ),
    'date-converter': (
        Variant(('inputDate',), 'date_time_convert',
                {'inputDate': 'date'}),
    ),
    'docker-run-to-docker-compose-converter': (
        Variant(('dockerRun',), 'docker_run_to_compose',
                {'dockerRun': 'command'}),
    ),
    'emoji-picker': (
        Variant((), 'emoji_search',
                {}),
    ),
    'encryption': (
        Variant(('decryptInput', 'decryptSecret'), 'decrypt_text',
                {'decryptAlgo': 'algorithm', 'decryptInput': 'encrypted', 'decryptSecret': 'secret'}),
        Variant(('cypherInput', 'cypherSecret'), 'encrypt_text',
                {'cypherAlgo': 'algorithm', 'cypherInput': 'text', 'cypherSecret': 'secret'}),
    ),
    'hash-text': (
        Variant(('clearText',), 'hash_text',
                {'algorithm': 'algorithm', 'clearText': 'text'}),
    ),
    'hmac-generator': (
        Variant(('plainText', 'secret'), 'hmac_generate',
                {'hashFunction': 'algorithm', 'plainText': 'text', 'secret': 'secret'}),
    ),
    'html-entities': (
        Variant(('unescapeInput',), 'html_unescape',
                {'unescapeInput': 'escaped'}),
        Variant(('escapeInput',), 'html_escape',
                {'escapeInput': 'text'}),
    ),
    'iban-validator-and-parser': (
        Variant(('rawIban',), 'iban_validate',
                {'rawIban': 'iban'}),
    ),
    'ipv4-address-converter': (
        Variant(('rawIpAddress',), 'ipv4_convert',
                {'rawIpAddress': 'ip'}),
    ),
    'ipv4-range-expander': (
        Variant(('rawStartAddress', 'rawEndAddress'), 'ipv4_range_expand',
                {'rawEndAddress': 'endIp', 'rawStartAddress': 'startIp'}),
    ),
    'ipv4-subnet-calculator': (
        Variant(('ip',), 'ipv4_subnet_calculate',
                {'ip': 'cidr'}),
    ),
    'ipv6-ula-generator': (
        Variant(('macAddress',), 'ipv6_ula_generate',
                {'macAddress': 'macAddress'}),
    ),
    'json-diff': (
        Variant(('rawLeftJson', 'rawRightJson'), 'json_diff',
                {'rawLeftJson': 'left', 'rawRightJson': 'right'}),
    ),
    'json-minify': (
        Variant(('input',), 'json_minify',
                {'input': 'json'}),
    ),
    'json-prettify': (
        Variant(('rawJson',), 'json_prettify',
                {'indentSize': 'indent', 'rawJson': 'json'}),
    ),
    'json-to-csv': (
        Variant(('input',), 'json_to_csv',
                {'input': 'json'}),
    ),
    'json-to-toml': (
        Variant(('input',), 'json_to_toml',
                {'input': 'json'}),
    ),
    'json-to-xml': (
        Variant(('input',), 'json_to_xml',
                {'input': 'json'}),
    ),
    'json-to-yaml-converter': (
        Variant(('input',), 'json_to_yaml',
                {'input': 'json'}),
    ),
    'jwt-parser': (
        Variant(('rawJwt',), 'jwt_parse',
                {'rawJwt': 'jwt'}),
    ),
    'list-converter': (
        Variant(('input',), 'list_convert',
                {'input': 'list'}),
    ),
    'lorem-ipsum-generator': (
        Variant((), 'lorem_ipsum_generate',
                {'paragraphs': 'paragraphCount'}),
    ),
    'mac-address-generator': (
        Variant((), 'mac_address_generate',
                {'amount': 'count', 'macAddressPrefix': 'prefix'}),
    ),
    'mac-address-lookup': (
        Variant(('macAddress',), 'mac_address_lookup',
                {'macAddress': 'macAddress'}),
    ),
    'markdown-to-html': (
        Variant(('inputMarkdown',), 'markdown_to_html',
                {'inputMarkdown': 'markdown'}),
    ),
    'math-evaluator': (
        Variant(('expression',), 'math_evaluate',
                {'expression': 'expression'}),
    ),
    'mime-types': (
        Variant(('selectedExtension',), 'extension_to_mime',
                {'selectedExtension': 'extension'}),
    ),
    'numeronym-generator': (
        Variant(('word',), 'numeronym_generate',
                {'word': 'word'}),
    ),
    'otp-generator': (
        Variant(('secret',), 'otp_generate_totp',
                {'secret': 'secret'}),
    ),
    'password-strength-analyser': (
        Variant(('password',), 'password_strength',
                {'password': 'password'}),
    ),
    'percentage-calculator': (
        Variant(('numberFrom', 'numberTo'), 'percentage_calculate',
                {'numberFrom': 'x', 'numberTo': 'y'}, {'operation': 'change'}),
    ),
    'phone-parser-and-formatter': (
        Variant(('rawPhone',), 'phone_parse',
                {'defaultCountryCode': 'defaultCountry', 'rawPhone': 'phone'}),
    ),
    'qrcode-generator': (
        Variant(('text',), 'qr_code_generate',
                {'text': 'text'}),
    ),
    'random-port-generator': (
        Variant((), 'random_port_generate',
                {}),
    ),
    'regex-tester': (
        Variant(('regex', 'text'), 'regex_test',
                {'regex': 'regex', 'text': 'text'}),
    ),
    'roman-numeral-converter': (
        Variant(('inputRoman',), 'roman_to_arabic',
                {'inputRoman': 'roman'}),
        Variant(('inputNumeral',), 'arabic_to_roman',
                {'inputNumeral': 'number'}),
    ),
    'rsa-key-pair-generator': (
        Variant((), 'rsa_keypair_generate',
                {'bits': 'bits'}),
    ),
    'safelink-decoder': (
        Variant(('inputSafeLinkUrl',), 'safelink_decode',
                {'inputSafeLinkUrl': 'url'}),
    ),
    'slugify-string': (
        Variant(('input',), 'slugify',
                {'input': 'text'}),
    ),
    'sql-prettify': (
        Variant(('rawSQL',), 'sql_format',
                {'rawSQL': 'sql'}),
    ),
    'string-obfuscator': (
        Variant(('str',), 'string_obfuscate',
                {'keepFirst': 'keepFirst', 'keepLast': 'keepLast', 'str': 'text'}),
    ),
    'text-statistics': (
        Variant(('text',), 'text_statistics',
                {'text': 'text'}),
    ),
    'text-to-binary': (
        Variant(('inputBinary',), 'binary_to_text',
                {'inputBinary': 'binary'}),
        Variant(('inputText',), 'text_to_binary',
                {'inputText': 'text'}),
    ),
    'text-to-nato-alphabet': (
        Variant(('input',), 'text_to_nato',
                {'input': 'text'}),
    ),
    'text-to-unicode': (
        Variant(('inputUnicode',), 'unicode_to_text',
                {'inputUnicode': 'unicode'}),
        Variant(('inputText',), 'text_to_unicode',
                {'inputText': 'text'}),
    ),
    'token-generator': (
        Variant((), 'token_generate',
                {'length': 'length'}),
    ),
    'toml-to-json': (
        Variant(('input',), 'toml_to_json',
                {'input': 'toml'}),
    ),
    'toml-to-yaml': (
        Variant(('input',), 'toml_to_yaml',
                {'input': 'toml'}),
    ),
    'ulid-generator': (
        Variant((), 'ulid_generate',
                {'amount': 'count'}),
    ),
    'url-encoder': (
        Variant(('decodeInput',), 'url_decode',
                {'decodeInput': 'encoded'}),
        Variant(('encodeInput',), 'url_encode',
                {'encodeInput': 'text'}),
    ),
    'url-parser': (
        Variant(('urlToParse',), 'url_parse',
                {'urlToParse': 'url'}),
    ),
    'user-agent-parser': (
        Variant(('ua',), 'user_agent_parse',
                {'ua': 'userAgent'}),
    ),
    'uuid-generator': (
        Variant((), 'uuid_generate',
                {'count': 'count'}),
    ),
    'wifi-qrcode-generator': (
        Variant(('ssid', 'password'), 'wifi_qr_code_generate',
                {'password': 'password', 'ssid': 'ssid'}),
    ),
    'xml-formatter': (
        Variant(('input',), 'xml_format',
                {'input': 'xml'}),
    ),
    'xml-to-json': (
        Variant(('input',), 'xml_to_json',
                {'input': 'xml'}),
    ),
    'yaml-prettify': (
        Variant(('rawYaml',), 'yaml_prettify',
                {'indentSize': 'indent', 'rawYaml': 'yaml'}),
    ),
    'yaml-to-json-converter': (
        Variant(('input',), 'yaml_to_json',
                {'input': 'yaml'}),
    ),
    'yaml-to-toml': (
        Variant(('input',), 'yaml_to_toml',
                {'input': 'yaml'}),
    ),
}


def translate(public_tool: str, public_args: dict) -> Optional[tuple[str, dict]]:
    """Публичный вызов -> вызов tools-core. None означает «уступаю модели».

    None возвращается в двух случаях, и оба — отказ утверждать, а не поломка:
      * инструмента нет в мосте либо ни одно направление не опознано;
      * пришёл аргумент, пары для которого не доказано.

    Второе важнее первого. Соблазн «выкинуть незнакомый аргумент и всё-таки
    выполнить» стоит дорого: у половины утилит незнакомым окажется именно тот
    аргумент, ради которого звали (алгоритм хеша, отступ, основание системы
    счисления), а утилита молча возьмёт своё умолчание. Ответ придёт уверенный
    и неправильный. Поэтому лишний аргумент — это уступка целиком.
    """
    variants = BRIDGE.get(public_tool)
    if not variants:
        return None
    for variant in variants:
        if not all(name in public_args for name in variant.when):
            continue
        if any(name not in variant.args for name in public_args):
            return None
        core_args: Dict[str, Any] = dict(variant.const)
        core_args.update({variant.args[name]: value
                          for name, value in public_args.items()})
        return variant.core, core_args
    return None
