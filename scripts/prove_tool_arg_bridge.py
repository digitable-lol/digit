#!/usr/bin/env python3
"""Доказательство моста имён (agent/tool_arg_bridge.py) ИСПОЛНЕНИЕМ.

Скрипт проверяет ровно то, что поставляется. Пара «публичный аргумент -> слот
tools-core» считается доказанной, если прошла три испытания подряд:

  1. СХЕМА. Значение принимается слотом по типу и по enum, а все обязательные
     слоты утилиты заполнены. Схемы читаются У САМОГО БИНАРЯ через его же
     MCP-протокол: исполняет запрос бинарь, значит правду о слотах знает
     только он, а не документация и не исходники рядом.
  2. ЭТАЛОН. Результат совпадает с эталоном, вычисленным НЕЗАВИСИМО от обоих
     каталогов — стандартной библиотекой Python или внешним стандартом.
     Эталон, взятый у tools-core, доказывал бы только то, что бинарь равен
     самому себе.
  3. РАЗЛИЧИМОСТЬ. Ни одна другая раскладка тех же значений по слотам той же
     утилиты не даёт того же эталона. Это главное испытание: перепутанные
     местами `text` и `secret` вызов не роняют — они дают другой, но
     правдоподобный ответ, и поймать их можно только так.

Отдельный исход — ВЫНУЖДЕННАЯ пара: допустимых раскладок нет вовсе, схема
отсекла все прочие слоты. Тогда класть значение больше некуда, и утверждение о
слоте не догадка даже без эталона.

Кроме моста скрипт прогоняет ОТВЕРГНУТЫХ кандидатов — шесть инструментов,
которые измерение не пропустило. Они проверяются на то, что отвергнуты
по-прежнему. Если tools-core однажды сменит тип слота или формат разделителя,
скрипт скажет «кандидат больше не отвергается» — и решение можно будет принять
заново, глядя на цифры, а не вспоминая.

Запуск (нужен собранный бинарь tools-core):

    DIGIT_TOOLS_CORE_HOME=~/src/tools-core \\
    python3 scripts/prove_tool_arg_bridge.py

Код возврата 1 — хотя бы одна поставляемая пара не подтвердилась.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac as _hmac
import html as _html
import ipaddress
import itertools
import json
import json as _json
import os
import pathlib
import subprocess
import sys
import urllib.parse as _url

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.tool_arg_bridge import BRIDGE, Variant  # noqa: E402

# Кандидаты, которых измерение НЕ пропустило. Держим рядом с пробами: без них
# «почему этих шести нет в мосте» пришлось бы восстанавливать по памяти.
REJECTED: dict[str, tuple[Variant, ...]] = {
    'http-status-codes': (
        Variant(('search',), 'http_status_lookup', {'search': 'code'}),
    ),
    'chmod-calculator': (
        Variant(('permissions',), 'chmod_calculate', {'permissions': 'permissions'}),
    ),
    'temperature-converter': (
        Variant(('value', 'scale'), 'temperature_convert',
                {'value': 'value', 'scale': 'from'}),
    ),
    'eta-calculator': (
        Variant(('unitCount', 'unitPerTimeSpan', 'timeSpan'), 'eta_calculate',
                {'unitCount': 'unitCount', 'unitPerTimeSpan': 'unitPerTimeSpan',
                 'timeSpan': 'timeSpanMs'}),
    ),
    'email-normalizer': (
        Variant(('emails',), 'email_normalize', {'emails': 'emails'}),
    ),
    'percentage-calculator': (
        Variant(('percentageX', 'percentageY'), 'percentage_calculate',
                {'percentageX': 'x', 'percentageY': 'y'}, {'operation': 'percentOf'}),
    ),
}


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


_HI_BIN = " ".join(format(b, "08b") for b in b"Hi")
_HELLO_BIN = " ".join(format(b, "08b") for b in b"Hello")
_JWT_052 = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6Ikl2YW4gUGV0cm92IiwiaWF0IjoxNzM1Njg5NjAwLCJleHAiOjE3"
    "NjcyMjU2MDAsImlzcyI6ImFwaS5kaWdpdGFibGUucnUifQ"
    ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
_JWT_PAYLOAD = _json.loads(base64.urlsafe_b64decode(
    _JWT_052.split(".")[1] + "=" * (-len(_JWT_052.split(".")[1]) % 4)).decode())
_SAFELINK = ("https://eur02.safelinks.protection.outlook.com/?url="
             "https%3A%2F%2Fdigitable.ru%2Fdocs&data=05%7C01%7C&sdata=abc&reserved=0")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_OBF = "sk-live-9f8a7b6c5d4e3f2a"
_STAT = "Мы отправили заказ вчера вечером"
_NET22 = ipaddress.ip_network("10.0.0.0/22")

PROBES: list[dict] = [
    # --- крипто ---------------------------------------------------------
    dict(id="001", public="hash-text",
         args={"clearText": "Привет, мир!", "algorithm": "SHA256"},
         expect={"hash": hashlib.sha256("Привет, мир!".encode()).hexdigest()}),
    dict(id="002", public="hash-text",
         args={"clearText": "password123", "algorithm": "MD5"},
         expect={"hash": hashlib.md5(b"password123").hexdigest()}),
    dict(id="003", public="hash-text",
         args={"clearText": "admin", "algorithm": "SHA1"},
         expect={"hash": hashlib.sha1(b"admin").hexdigest()}),
    dict(id="004", public="hmac-generator",
         args={"plainText": "order-42", "secret": "s3cr3t", "hashFunction": "SHA256"},
         expect={"hmac": _hmac.new(b"s3cr3t", b"order-42", hashlib.sha256).hexdigest()}),
    dict(id="005", public="hmac-generator",
         args={"plainText": '{"id":1}', "secret": "whsec_test", "hashFunction": "SHA512"},
         expect={"hmac": _hmac.new(b"whsec_test", b'{"id":1}', hashlib.sha512).hexdigest()}),
    # bcrypt: соль случайна, эталон снаружи не вычислить. Зато цепочка
    # «захешировать -> сверить» отличает верную раскладку от перепутанной:
    # хеш, поданный как строка, и строка, поданная как хеш, дают отказ.
    dict(id="006", public="bcrypt", produces="bcrypt",
         args={"input": "qwerty123", "saltCount": 4},
         expect={"saltRounds": 4}),
    dict(id="007", public="bcrypt",
         args={"compareString": "qwerty123"},
         args_from={"compareHash": ("bcrypt", "hash")},
         expect={"matches": True}),
    dict(id="012", public="password-strength-analyser",
         args={"password": "Tr0ub4dour&3"},
         expect={"passwordLength": len("Tr0ub4dour&3"), "charsetLength": 94}),
    # Шифрование: эталона снаружи нет — соль случайна. Зато есть круговой
    # прогон: зашифровать и тут же расшифровать тем же ключом. Перепутанные
    # местами текст и ключ круг не замыкают, значит раскладка различима.
    dict(id="013", public="encryption",
         args={"cypherInput": "секретное сообщение", "cypherSecret": "my-key-2024",
               "cypherAlgo": "AES"},
         produces="aes",
         expect={"algorithm": "AES"},
         roundtrip={"core": "decrypt_text", "from": {"encrypted": "encrypted"},
                    "args": {"secret": "my-key-2024", "algorithm": "AES"},
                    "expect": {"decrypted": "секретное сообщение"}}),
    dict(id="013b", public="encryption",
         args={"decryptSecret": "my-key-2024", "decryptAlgo": "AES"},
         args_from={"decryptInput": ("aes", "encrypted")},
         expect={"decrypted": "секретное сообщение"}),
    dict(id="015", public="rsa-key-pair-generator", args={"bits": 512},
         expect={"bits": 512}),
    dict(id="016", public="bip39-generator",
         args={"entropy": "ffffffffffffffffffffffffffffffff"},
         # Официальный тестовый вектор BIP-39 (Trezor english.json).
         expect={"mnemonic": "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong"}),
    dict(id="016b", public="bip39-generator",
         args={"entropy": "00000000000000000000000000000000"},
         expect={"mnemonic": "abandon abandon abandon abandon abandon abandon "
                             "abandon abandon abandon abandon abandon about"}),
    dict(id="098", public="otp-generator", args={"secret": "JBSWY3DPEHPK3PXP"}),

    # --- генераторы -----------------------------------------------------
    dict(id="008", public="token-generator", args={"length": 32},
         expect={"length": 32}),
    dict(id="009", public="uuid-generator", args={"count": 5}, expect_len={"uuids": 5}),
    dict(id="010", public="ulid-generator", args={"amount": 3}, expect_len={"ulids": 3}),
    dict(id="011", public="ulid-generator", args={}),
    dict(id="069", public="mac-address-generator",
         args={"amount": 10, "macAddressPrefix": "00:1A:2B"},
         expect_len={"addresses": 10},
         expect_each_prefix={"addresses": "00:1a:2b"}),
    dict(id="071", public="random-port-generator", args={}),
    dict(id="081", public="lorem-ipsum-generator", args={"paragraphs": 3},
         # Три абзаца, а не три слова и не три предложения: раскладка на
         # соседние слоты wordCount / sentencePerParagraph даёт другой текст.
         expect_paragraphs={"text": 3}),
    dict(id="081b", public="lorem-ipsum-generator",
         args={"paragraphs": 1, "sentences": 2, "words": 5},
         # Предложений ровно два, и в последнем ровно пять слов: раскладка,
         # где 2 и 5 поменялись слотами, даёт пять предложений по два слова.
         expect_paragraphs={"text": 1}, expect_sentences={"text": 2},
         expect_last_sentence_words={"text": 5},
         # Пары `words` и `sentences` из моста ПОДРЕЗАНЫ: поодиночке
         # исполнение не отличает их от слота `seed`, а вместе они различимы
         # только парой — то есть слот каждой по отдельности не определён.
         # Проба сторожит именно отказ переводить такой вызов.
         expect_no_bridge=True),
    dict(id="084", public="emoji-picker", args={}),

    # --- кодеки ---------------------------------------------------------
    dict(id="017", public="base64-string-converter", args={"base64Input": "aGVsbG8="},
         expect={"text": "hello"}),
    dict(id="018", public="base64-string-converter",
         args={"base64Input": "0J/RgNC40LLQtdGCLCDQvNC40YAh"},
         expect={"text": base64.b64decode("0J/RgNC40LLQtdGCLCDQvNC40YAh").decode()}),
    dict(id="019", public="base64-string-converter", args={"textInput": "договор №17"},
         expect={"base64": _b64("договор №17")}),
    dict(id="020", public="base64-string-converter",
         args={"textInput": "a+b/c=d", "encodeUrlSafe": True},
         expect={"base64": base64.urlsafe_b64encode(b"a+b/c=d").decode().rstrip("=")}),
    dict(id="021", public="url-encoder", args={"encodeInput": "поиск по сайту"},
         expect={"encoded": _url.quote("поиск по сайту", safe="")}),
    dict(id="022", public="url-encoder", args={"decodeInput": "%D0%BA%D0%BE%D1%82"},
         expect={"text": "кот"}),
    dict(id="023", public="html-entities", args={"escapeInput": "<script>alert(1)</script>"},
         expect={"escaped": _html.escape("<script>alert(1)</script>", quote=False)}),
    dict(id="024", public="html-entities",
         args={"unescapeInput": "&lt;div class=&quot;box&quot;&gt;"},
         expect={"text": '<div class="box">'}),
    dict(id="025", public="text-to-binary", args={"inputText": "Hi"},
         expect={"binary": _HI_BIN}),
    dict(id="026", public="text-to-binary", args={"inputBinary": _HELLO_BIN},
         expect={"text": "Hello"}),
    dict(id="027", public="text-to-unicode", args={"inputText": "Привет"},
         expect={"unicode": "".join(f"&#{ord(ch)};" for ch in "Привет")}),
    dict(id="028", public="text-to-unicode", args={"inputUnicode": "&#72;&#105;"},
         expect={"text": "Hi"}),
    dict(id="029", public="text-to-nato-alphabet", args={"input": "SMIRNOV"},
         # Фонетический алфавит НАТО — внешний стандарт, не выдумка каталога.
         expect={"nato": "Sierra Mike India Romeo November Oscar Victor"}),
    dict(id="030", public="base-converter",
         args={"input": "255", "inputBase": 10, "outputBase": 16},
         expect={"value": "ff"}),
    dict(id="030b", public="base-converter",
         args={"input": "1010", "inputBase": 2, "outputBase": 10},
         # Асимметричная проба: перепутанные основания дали бы «а» вместо «10».
         expect={"value": "10"}),
    dict(id="037", public="roman-numeral-converter", args={"inputRoman": "MCMXCIV"},
         expect={"number": 1994}),
    dict(id="072", public="basic-auth-generator",
         args={"username": "admin", "password": "secret"},
         expect={"credentials": _b64("admin:secret")}),
    dict(id="073", public="basic-auth-generator",
         args={"username": "monitoring", "password": "P@ssw0rd"},
         expect={"credentials": _b64("monitoring:P@ssw0rd")}),

    # --- форматы --------------------------------------------------------
    dict(id="032", public="date-converter", args={"inputDate": "1735689600"},
         expect={"iso8601": "2025-01-01T00:00:00.000Z", "unix": 1735689600}),
    dict(id="033", public="date-converter", args={"inputDate": "2026-08-02T12:30:00Z"},
         expect={"unix": 1785673800}),
    dict(id="034", public="color-converter", args={"input": "#1e90ff"},
         expect={"hex": "#1e90ff", "rgb": "rgb(30, 144, 255)"}),
    dict(id="035", public="case-converter", args={"input": "user_first_name"},
         expect={"results.uppercase": "USER_FIRST_NAME",
                 "results.camelcase": "userFirstName"}),
    dict(id="036", public="case-converter", args={"input": "Моя Длинная Строка"},
         expect={"results.uppercase": "МОЯ ДЛИННАЯ СТРОКА"}),
    dict(id="038", public="yaml-to-json-converter", args={"input": "name: api\nport: 8080"},
         expect_json={"json": {"name": "api", "port": 8080}}),
    dict(id="039", public="json-to-yaml-converter",
         args={"input": '{"name":"api","port":8080}'},
         expect={"yaml": "name: api\nport: 8080\n"}),
    dict(id="040", public="json-to-toml",
         args={"input": '{"server":{"host":"localhost","port":5432}}'},
         expect_contains={"toml": '[server]'}),
    dict(id="042", public="xml-to-json", args={"input": "<order><id>15</id></order>"},
         expect_contains={"json": "15"}),
    dict(id="043", public="json-to-xml", args={"input": '{"order":{"id":15}}'},
         expect_contains={"xml": "<id>15</id>"}),
    dict(id="044", public="markdown-to-html",
         args={"inputMarkdown": "# Заголовок\n\nтекст **жирный**"},
         expect={"html": "<h1>Заголовок</h1>\n<p>текст <strong>жирный</strong></p>\n"}),
    dict(id="047", public="json-to-csv",
         args={"input": '[{"id":1,"name":"a"},{"id":2,"name":"b"}]'},
         expect={"csv": "id,name\n1,a\n2,b"}),
    dict(id="093", public="json-prettify",
         args={"rawJson": '{"a":1,"b":[2,3]}', "indentSize": 4},
         # Отступ ровно 4: если бы indentSize уехал не в тот слот, ответ был бы
         # с отступом по умолчанию.
         expect={"json": _json.dumps(_json.loads('{"a":1,"b":[2,3]}'), indent=4)}),
    dict(id="090", public="sql-prettify",
         args={"rawSQL": "select id,name from users order by id desc"},
         expect_contains={"sql": "SELECT"}),
    dict(id="100", public="json-diff",
         args={"rawLeftJson": '{"id":1,"status":"new"}',
               "rawRightJson": '{"id":1,"status":"paid"}'},
         # Асимметрия: перепутанные стороны дали бы oldValue=paid, value=new.
         expect={"identical": False, "changeCount": 1,
                 "changes.0.oldValue": "new", "changes.0.value": "paid"}),
    dict(id="045", public="list-converter", args={}),
    # Ниже — утилиты, которых нет в 100 задачах маршрутизации, но которые есть
    # в мосте. Без пробы они уехали бы в поставку недоказанными.
    dict(id="x01", public="toml-to-json", args={"input": 'a = 1\nb = "x"\n'},
         expect_json={"json": {"a": 1, "b": "x"}}),
    dict(id="x02", public="toml-to-yaml", args={"input": "a = 1\n"},
         expect={"yaml": "a: 1\n"}),
    dict(id="x03", public="yaml-to-toml", args={"input": "a: 1\n"},
         expect_contains={"toml": "a = 1"}),
    dict(id="x04", public="json-minify", args={"input": '{ "a" : 1 }'},
         expect={"json": '{"a":1}'}),
    dict(id="x05", public="xml-formatter", args={"input": "<r><a>1</a></r>"},
         expect_contains={"xml": "<a>1</a>"}),
    dict(id="x06", public="yaml-prettify",
         args={"rawYaml": "a:\n  b:\n    c: 1\n", "indentSize": 4},
         # Вложенность и отступ 4: попади indentSize не в тот слот, вложенные
         # ключи встали бы на два пробела.
         expect={"yaml": "a:\n    b:\n        c: 1\n"}),
    dict(id="x07", public="roman-numeral-converter", args={"inputNumeral": 1994},
         expect={"roman": "MCMXCIV"}),
    dict(id="x08", public="list-converter", args={"input": "b\na"},
         expect={"result": "b, a"}),

    # --- сеть и разбор --------------------------------------------------
    dict(id="050", public="crontab-generator", args={"cron": "0 3 * * 1"},
         expect_contains={"description": "03:00"}),
    dict(id="052", public="jwt-parser", args={"rawJwt": _JWT_052},
         # Полезная нагрузка разобрана python-ом из самого токена: сверяем
         # значения заявок, а не форму, в которой их подаёт tools-core.
         expect={"payload.0.value": _JWT_PAYLOAD["sub"],
                 "payload.1.value": _JWT_PAYLOAD["name"],
                 "payload.4.value": _JWT_PAYLOAD["iss"]}),
    dict(id="053", public="url-parser",
         args={"urlToParse": "https://user:pass@example.com:8443/path?a=1&b=2#frag"},
         expect={"hostname": "example.com", "port": "8443", "username": "user"}),
    dict(id="055", public="user-agent-parser", args={"ua": _UA},
         expect={"browser.name": "Chrome", "os.name": "Windows"}),
    dict(id="056", public="iban-validator-and-parser",
         args={"rawIban": "DE89370400440532013000"},
         expect={"valid": True, "countryCode": "DE"}),
    dict(id="058", public="phone-parser-and-formatter",
         args={"rawPhone": "89161234567", "defaultCountryCode": "RU"},
         # Без страны 8916… не разбирается: проба доказывает ОБА слота сразу.
         expect={"e164": "+79161234567", "country": "RU"}),
    dict(id="060", public="safelink-decoder", args={"inputSafeLinkUrl": _SAFELINK},
         expect={"url": "https://digitable.ru/docs"}),
    dict(id="061", public="mime-types", args={"selectedExtension": "webp"},
         expect={"mimeType": "image/webp"}),
    dict(id="062", public="http-status-codes", args={"search": "418"}),
    dict(id="063", public="ipv4-subnet-calculator", args={"ip": "10.0.0.0/22"},
         expect={"networkSize": _NET22.num_addresses,
                 "broadcastAddress": str(_NET22.broadcast_address),
                 "networkMask": str(_NET22.netmask)}),
    dict(id="065", public="ipv4-address-converter", args={"rawIpAddress": "192.168.1.1"},
         expect={"decimal": int(ipaddress.ip_address("192.168.1.1"))}),
    dict(id="067", public="ipv4-range-expander",
         args={"rawStartAddress": "192.168.1.10", "rawEndAddress": "192.168.1.20"},
         # Асимметрия: перепутанные концы дали бы отрицательный размер.
         expect={"oldSize": 11}),
    dict(id="068", public="ipv6-ula-generator", args={"macAddress": "64:16:7f:aa:bb:cc"}),
    dict(id="070", public="mac-address-lookup", args={"macAddress": "00:1A:2B:3C:4D:5E"}),
    dict(id="083", public="email-normalizer",
         args={"emails": "Ivan.Petrov+spam@Gmail.com, ivanpetrov@gmail.com"},
         expect_json={"emails": ["ivanpetrov@gmail.com", "ivanpetrov@gmail.com"]}),
    dict(id="094", public="docker-run-to-docker-compose-converter",
         args={"dockerRun": "docker run -d -p 8080:80 --name web nginx:alpine"},
         expect_contains={"compose": "'8080:80'"}),

    # --- тексты и числа -------------------------------------------------
    dict(id="074", public="slugify-string",
         args={"input": "Как настроить Nginx за 5 минут"},
         expect={"slug": "kak-nastroit-nginx-za-5-minut"}),
    dict(id="075", public="text-statistics", args={"text": _STAT},
         expect={"characterCount": len(_STAT), "wordCount": len(_STAT.split()),
                 "byteSize": len(_STAT.encode())}),
    dict(id="078", public="string-obfuscator",
         args={"str": _OBF, "keepFirst": 6, "keepLast": 4},
         # 6 ≠ 4, поэтому перепутанные keepFirst/keepLast дают другую строку.
         expect={"obfuscated": _OBF[:6] + "*" * (len(_OBF) - 10) + _OBF[-4:]}),
    dict(id="079", public="string-obfuscator", args={"str": "AKIAIOSFODNN7EXAMPLE"}),
    dict(id="080", public="numeronym-generator", args={"word": "internationalization"},
         expect={"numeronym": "i18n"}),
    dict(id="082", public="ascii-text-drawer", args={"input": "DIGIT", "font": "Standard"},
         expect_contains={"art": "____"}),
    dict(id="085", public="chmod-calculator",
         args={"permissions": {"owner": {"read": True, "write": True, "execute": True},
                               "group": {"read": True, "write": False, "execute": True},
                               "public": {"read": False, "write": False, "execute": False}}}),
    dict(id="086", public="math-evaluator", args={"expression": "sqrt(1024) + 5 * 3"},
         expect={"result": 47}),
    dict(id="087", public="math-evaluator", args={"expression": "2^10 / 4"},
         expect={"result": 256}),
    dict(id="088", public="percentage-calculator",
         args={"percentageX": 15, "percentageY": 2400},
         expect={"result": 360}),
    dict(id="089", public="percentage-calculator",
         args={"numberFrom": 1200, "numberTo": 1560},
         # Асимметрия: обратный порядок дал бы −23,08 %, а не 30 %.
         expect={"result": 30}),
    dict(id="096", public="eta-calculator",
         args={"unitCount": 500, "unitPerTimeSpan": 20, "timeSpan": 5},
         # «20 штук за 5 МИНУТ» — 500/20*5 минут = 25 минут = 1 500 000 мс.
         # Публичный аргумент безразмерный: единицу измерения it-tools держит
         # в отдельном поле, которого разбор правил не выдаёт вовсе.
         expect={"durationMs": 500 / 20 * 5 * 60_000}),
    dict(id="095", public="regex-tester",
         args={"regex": r"\d{3}-\d{2}", "text": "заказ 123-45 отгружен"},
         expect={"matchCount": 1, "matches.0.value": "123-45"}),
    dict(id="097", public="qrcode-generator", args={"text": "https://digitable.ru"},
         expect={"text": "https://digitable.ru"}),
    dict(id="099", public="wifi-qrcode-generator",
         args={"ssid": "OfficeGuest", "password": "Welcome2024"},
         # Формат полезной нагрузки Wi-Fi QR — внешний стандарт; перепутанные
         # ssid и пароль дали бы «S:Welcome2024;P:OfficeGuest».
         expect={"payload": "WIFI:T:WPA;S:OfficeGuest;P:Welcome2024;;"}),
    dict(id="046", public="temperature-converter", args={"value": 25, "scale": "фаренгейт"}),
]


# ---------------------------------------------------------------------------
#: Где искать бинарь, если DIGIT_TOOLS_CORE_HOME не задан. Список, а не одна
#: строка: место сборки и место установки — разные каталоги, и дважды подряд
#: замер объявляли невозможным, потому что смотрели только во второй.
BINARY_SEARCH_PATH = (
    "~/.digit/mcp-servers/tools-core",
    "~/src/tools-core",
    "~/projects/digit-ml/tools-core",
)


def default_binary() -> str:
    home = os.environ.get("DIGIT_TOOLS_CORE_HOME")
    if home:
        return str(pathlib.Path(home).expanduser() / "dist" / "digit-tools-mcp")
    for cand in BINARY_SEARCH_PATH:
        path = pathlib.Path(cand).expanduser() / "dist" / "digit-tools-mcp"
        if path.exists():
            return str(path)
    return str(pathlib.Path(BINARY_SEARCH_PATH[0]).expanduser() / "dist" / "digit-tools-mcp")


def require_binary(path: str) -> None:
    """Отсутствие бинаря обязано называть себя, а не падать трассировкой.

    Без этого «замерить нечем» неотличимо от «замер не проводили»: скрипт
    падал FileNotFoundError из глубины subprocess, и вывод не говорил ни где
    искали, ни чем это чинится.
    """
    if pathlib.Path(path).exists():
        return
    looked = "\n".join(f"    {pathlib.Path(c).expanduser() / 'dist' / 'digit-tools-mcp'}"
                        for c in BINARY_SEARCH_PATH)
    sys.exit(
        f"ЗАМЕРИТЬ НЕЧЕМ: исполнителя нет по пути\n    {path}\n"
        f"Искали здесь:\n{looked}\n"
        "Укажите каталог сборки: DIGIT_TOOLS_CORE_HOME=/путь/к/tools-core "
        "(ожидается {DIGIT_TOOLS_CORE_HOME}/dist/digit-tools-mcp)."
    )


class Core:
    """Клиент stdio-MCP к бинарю tools-core: и схемы, и исполнение."""

    def __init__(self, binary: str):
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "prover", "version": "1"}})
        self.p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.p.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id,
                                       "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("исполнитель закрыл stdout")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == self._id:
                return msg

    def _tool(self, name: str, args: dict) -> dict:
        res = (self._rpc("tools/call", {"name": name, "arguments": args})
               .get("result") or {})
        got = res.get("structuredContent")
        if got is None:
            got = json.loads((res.get("content") or [{}])[0].get("text", "{}"))
        return got

    def schemas(self) -> dict:
        """Схемы всех утилит — прочитанные у бинаря, а не из документации."""
        out = {}
        for category in self._tool("tools_categories", {})["categories"]:
            for tool in self._tool("tools_list", {"category": category["id"]})["tools"]:
                out[tool["id"]] = tool["input_schema"]
        return out

    def execute(self, core_id: str, args: dict) -> dict:
        return self._tool("tools_execute", {"tool_id": core_id, "args": args,
                                            "timeout_ms": 30000})

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.terminate()


def type_ok(value, spec: dict) -> bool:
    ty = spec.get("type")
    if ty == "string" and not isinstance(value, str):
        return False
    if ty == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
        return False
    if ty == "number" and not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return False
    if ty == "boolean" and not isinstance(value, bool):
        return False
    if ty == "array" and not isinstance(value, list):
        return False
    if ty == "object" and not isinstance(value, dict):
        return False
    enum = spec.get("enum")
    return enum is None or value in enum


def dig(obj, path: str):
    for part in path.split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


CHECK_KEYS = ("expect", "expect_json", "expect_contains", "expect_len",
              "expect_each_prefix", "expect_paragraphs", "expect_sentences",
              "expect_last_sentence_words", "roundtrip")


def check(res: dict, probe: dict) -> list[str]:
    """Расхождения с эталоном. Пустой список — совпало всё."""
    bad = []
    for path, want in (probe.get("expect") or {}).items():
        got = dig(res, path)
        if got != want:
            bad.append(f"{path}: ждали {want!r}, получили {got!r}")
    for path, want in (probe.get("expect_json") or {}).items():
        got = dig(res, path)
        try:
            parsed = json.loads(got) if isinstance(got, str) else got
        except Exception:
            parsed = got
        if parsed != want:
            bad.append(f"{path}(json): ждали {want!r}, получили {parsed!r}")
    for path, want in (probe.get("expect_contains") or {}).items():
        got = dig(res, path)
        if not isinstance(got, str) or want not in got:
            bad.append(f"{path}: нет подстроки {want!r}")
    for path, want in (probe.get("expect_len") or {}).items():
        got = dig(res, path)
        if not isinstance(got, list) or len(got) != want:
            bad.append(f"{path}: длина не {want}")
    for path, want in (probe.get("expect_each_prefix") or {}).items():
        got = dig(res, path)
        if not isinstance(got, list) or not all(
                isinstance(x, str) and x.startswith(want) for x in got):
            bad.append(f"{path}: не все начинаются с {want!r}")
    for path, want in (probe.get("expect_paragraphs") or {}).items():
        got = dig(res, path)
        n = len([x for x in got.split("\n\n") if x.strip()]) if isinstance(got, str) else -1
        if n != want:
            bad.append(f"{path}: абзацев {n}, а не {want}")
    for path, want in (probe.get("expect_sentences") or {}).items():
        got = dig(res, path)
        n = len([x for x in got.split(".") if x.strip()]) if isinstance(got, str) else -1
        if n != want:
            bad.append(f"{path}: предложений {n}, а не {want}")
    for path, want in (probe.get("expect_last_sentence_words") or {}).items():
        got = dig(res, path)
        n = -1
        if isinstance(got, str):
            parts = [x for x in got.split(".") if x.strip()]
            n = len(parts[-1].split()) if parts else -1
        if n != want:
            bad.append(f"{path}: в последнем предложении слов {n}, а не {want}")
    return bad


def check_all(core: Core, res: dict, probe: dict) -> list[str]:
    """Эталон плюс круговой прогон, если проба его объявила.

    Круг нужен там, где прямого эталона не существует: шифр со случайной солью
    не с чем сравнить снаружи, но «зашифровать ключом и расшифровать им же»
    замыкается только при верной раскладке.
    """
    bad = check(res, probe)
    rt = probe.get("roundtrip")
    if rt is None:
        return bad
    args = dict(rt.get("args") or {})
    for slot, field in (rt.get("from") or {}).items():
        args[slot] = res.get(field)
    out = core.execute(rt["core"], args)
    if not out.get("ok"):
        return bad + [f'круг не замкнулся: {out.get("code")}: {out.get("error")}']
    return bad + check(out.get("result") or {}, {"expect": rt.get("expect")})


def variant_for(table: dict, public: str, args: dict):
    for variant in table.get(public, ()):
        if all(name in args for name in variant.when):
            return variant
    return None


def admissible_layouts(pub_args: dict, props: dict, required: set, const: dict):
    """Все раскладки значений по слотам, допустимые ПО СХЕМЕ."""
    pubs = list(pub_args)
    out = []
    for perm in itertools.permutations(props, len(pubs)):
        cand = dict(zip(pubs, perm))
        if any(not type_ok(pub_args[p], props[c]) for p, c in cand.items()):
            continue
        full = dict(const)
        full.update({c: pub_args[p] for p, c in cand.items()})
        if required - set(full):
            continue
        out.append(cand)
    return out


def judge(core: Core, schemas: dict, variant, pub_args: dict, probe: dict) -> tuple[str, object]:
    """Приговор одной пробе: как именно она подтвердила или отвергла раскладку."""
    schema = schemas[variant.core]
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    const = dict(variant.const or {})
    pairs = {p: variant.args.get(p) for p in pub_args}

    missing = [p for p, c in pairs.items() if c is None]
    if missing:
        return "reject:нет-в-мосте", missing
    bad = [(p, c) for p, c in pairs.items()
           if c not in props or not type_ok(pub_args[p], props[c])]
    if bad:
        return "reject:схема", [f"{p}->{c}: слот не принимает {pub_args[p]!r}"
                                for p, c in bad]
    proposed = dict(const)
    proposed.update({pairs[p]: v for p, v in pub_args.items()})
    if required - set(proposed):
        return "reject:нечем-заполнить", sorted(required - set(proposed))

    out = core.execute(variant.core, proposed)
    if not out.get("ok"):
        return "reject:исполнитель", f'{out.get("code")}: {out.get("error")}'
    res = out.get("result") or {}

    layouts = [c for c in admissible_layouts(pub_args, props, required, const)
               if c != pairs]
    if not any(probe.get(k) for k in CHECK_KEYS):
        return ("forced" if not layouts else "unproven:нет-эталона"), res

    bad = check_all(core, res, probe)
    if bad:
        return "reject:эталон", bad

    rivals = []
    for cand in layouts:
        full = dict(const)
        full.update({c: pub_args[p] for p, c in cand.items()})
        other = core.execute(variant.core, full)
        if other.get("ok") and not check_all(core, other.get("result") or {}, probe):
            rivals.append(cand)
    if rivals:
        return "ambiguous", rivals
    return "proven", res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=default_binary())
    ap.add_argument("--json", help="куда сложить построчный приговор")
    args = ap.parse_args()

    require_binary(args.binary)
    core = Core(args.binary)
    try:
        schemas = core.schemas()
        produced: dict[str, dict] = {}
        shipped, rejected, failures = [], [], []

        for probe in PROBES:
            pub_args = dict(probe["args"])
            for key, (src, field) in (probe.get("args_from") or {}).items():
                pub_args[key] = (produced.get(src) or {}).get(field)

            variant = variant_for(BRIDGE, probe["public"], pub_args)
            table = "мост"
            if probe.get("expect_no_bridge"):
                # Проба на ПОДРЕЗАННУЮ пару: правильный исход — отказ моста
                # переводить вызов целиком, а не удачное исполнение.
                refused = variant is None or any(
                    name not in variant.args for name in pub_args)
                shipped.append({"probe": probe["id"], "public": probe["public"],
                                "core": variant.core if variant else None,
                                "verdict": "подрез сторожится" if refused
                                else "ПОДРЕЗ ПОТЕРЯН", "detail": None})
                if not refused:
                    failures.append(shipped[-1])
                continue
            if variant is None:
                variant = variant_for(REJECTED, probe["public"], pub_args)
                table = "отвергнут"
            if variant is None:
                # Направление не опознано — это не провал, а честная уступка:
                # ровно так мост ведёт себя и в рабочем коде.
                shipped.append({"probe": probe["id"], "public": probe["public"],
                                "verdict": "no-variant"})
                continue

            verdict, detail = judge(core, schemas, variant, pub_args, probe)
            if verdict in {"proven", "forced"} and probe.get("produces"):
                produced[probe["produces"]] = detail
            row = {"probe": probe["id"], "public": probe["public"],
                   "core": variant.core, "verdict": verdict,
                   "detail": detail if verdict.startswith(("reject", "ambig")) else None}
            if table == "мост":
                shipped.append(row)
                if verdict not in {"proven", "forced"}:
                    failures.append(row)
            else:
                rejected.append(row)
    finally:
        core.close()

    print(f"поставляемых пар в мосте: "
          f"{sum(len(v.args) for vs in BRIDGE.values() for v in vs)} "
          f"в {len(BRIDGE)} инструментах, {sum(len(v) for v in BRIDGE.values())} направлениях")
    print(f"проб по мосту: {len(shipped)}")
    for verdict in ("proven", "forced", "no-variant", "подрез сторожится"):
        n = sum(1 for r in shipped if r["verdict"] == verdict)
        if n:
            print(f"    {verdict:22s} {n}")
    for row in failures:
        print(f'    ПРОВАЛ {row["probe"]} {row["public"]} -> {row["core"]}: '
              f'{row["verdict"]} {json.dumps(row["detail"], ensure_ascii=False)[:200]}')

    print(f"\nпроб по отвергнутым кандидатам: {len(rejected)}")
    for row in rejected:
        still = row["verdict"].startswith(("reject", "ambig"))
        mark = "отвергнут по-прежнему" if still else "БОЛЬШЕ НЕ ОТВЕРГАЕТСЯ"
        print(f'    {row["public"]:24s} {mark}: {row["verdict"]} '
              f'{json.dumps(row["detail"], ensure_ascii=False)[:180] if row["detail"] else ""}')

    if args.json:
        json.dump({"shipped": shipped, "rejected": rejected},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
