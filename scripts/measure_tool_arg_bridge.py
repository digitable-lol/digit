#!/usr/bin/env python3
"""Сколько маршрутов каскада ДОХОДИТ ДО ОТВЕТА — до моста имён и после.

Зачем отдельный замер
---------------------
Харнесс eval/ измеряет ВЫБОР инструмента и аргументов, а не исполнение:
адаптер там описывает вызов словами и утилиту не запускает. Значит цифру
«сколько запросов каскад закрыл сам» он показать не может в принципе. Она
меряется здесь — настоящим бинарём tools-core, тем же, который отвечает
пользователю.

Две политики перевода, одна и та же выборка запросов:

  passthrough  как было до моста: публичный slug переводится в идентификатор
               утилиты, аргументы уходят под своими именами. Совпало имя —
               исполнилось, не совпало — tools-core вернул invalid_args.
  bridge       как стало: agent/tool_arg_bridge.py переводит и утилиту, и
               каждый аргумент, а на неподтверждённый аргумент отказывается
               переводить вызов целиком.

Запуск:

    python3 scripts/measure_tool_arg_bridge.py \\
        --tasks /path/to/eval/tasks/tool_routing.json

Путь к исполнителю берётся так же, как его берёт установщик MCP-сервера:
$DIGIT_TOOLS_CORE_HOME/dist/digit-tools-mcp, по умолчанию
~/.digit/mcp-servers/tools-core/dist/digit-tools-mcp.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.rule_cascade import NO_CORE_TOOL  # noqa: E402
from agent.tool_arg_bridge import translate  # noqa: E402

# Политика ДО моста, сохранённая дословно ради воспроизводимости цифры «было».
# Работать по ней больше нельзя (см. agent/tool_arg_bridge.py, разбор шести
# отвергнутых инструментов), но без неё «стало лучше» нечем подтвердить.
LEGACY_PUBLIC_TO_CORE = {
    'ascii-text-drawer': 'ascii_art_generate', 'base-converter': 'integer_base_convert',
    'base64-file-converter': 'base64_encode', 'base64-string-converter': 'base64_encode',
    'basic-auth-generator': 'basic_auth_header', 'bcrypt': 'bcrypt_hash',
    'benchmark-builder': 'benchmark_stats', 'bip39-generator': 'bip39_generate',
    'case-converter': 'case_convert', 'chmod-calculator': 'chmod_calculate',
    'color-converter': 'color_convert', 'crontab-generator': 'crontab_describe',
    'date-converter': 'date_time_convert',
    'docker-run-to-docker-compose-converter': 'docker_run_to_compose',
    'email-normalizer': 'email_normalize', 'emoji-picker': 'emoji_search',
    'encryption': 'encrypt_text', 'eta-calculator': 'eta_calculate',
    'hash-text': 'hash_text', 'hmac-generator': 'hmac_generate',
    'html-entities': 'html_escape', 'http-status-codes': 'http_status_lookup',
    'iban-validator-and-parser': 'iban_validate', 'ipv4-address-converter': 'ipv4_convert',
    'ipv4-range-expander': 'ipv4_range_expand',
    'ipv4-subnet-calculator': 'ipv4_subnet_calculate',
    'ipv6-ula-generator': 'ipv6_ula_generate', 'json-diff': 'json_diff',
    'json-minify': 'json_minify', 'json-prettify': 'json_prettify',
    'json-to-csv': 'json_to_csv', 'json-to-toml': 'json_to_toml',
    'json-to-xml': 'json_to_xml', 'json-to-yaml-converter': 'json_to_yaml',
    'jwt-parser': 'jwt_parse', 'list-converter': 'list_convert',
    'lorem-ipsum-generator': 'lorem_ipsum_generate',
    'mac-address-generator': 'mac_address_generate',
    'mac-address-lookup': 'mac_address_lookup', 'markdown-to-html': 'markdown_to_html',
    'math-evaluator': 'math_evaluate', 'mime-types': 'extension_to_mime',
    'numeronym-generator': 'numeronym_generate', 'otp-generator': 'otp_generate_totp',
    'password-strength-analyser': 'password_strength',
    'percentage-calculator': 'percentage_calculate',
    'phone-parser-and-formatter': 'phone_parse', 'qrcode-generator': 'qr_code_generate',
    'random-port-generator': 'random_port_generate', 'regex-tester': 'regex_test',
    'roman-numeral-converter': 'roman_to_arabic',
    'rsa-key-pair-generator': 'rsa_keypair_generate', 'safelink-decoder': 'safelink_decode',
    'slugify-string': 'slugify', 'sql-prettify': 'sql_format',
    'string-obfuscator': 'string_obfuscate',
    'svg-placeholder-generator': 'svg_placeholder_generate',
    'temperature-converter': 'temperature_convert', 'text-statistics': 'text_statistics',
    'text-to-binary': 'text_to_binary', 'text-to-nato-alphabet': 'text_to_nato',
    'text-to-unicode': 'text_to_unicode', 'token-generator': 'token_generate',
    'toml-to-json': 'toml_to_json', 'toml-to-yaml': 'toml_to_yaml',
    'ulid-generator': 'ulid_generate', 'url-encoder': 'url_encode',
    'url-parser': 'url_parse', 'user-agent-parser': 'user_agent_parse',
    'uuid-generator': 'uuid_generate', 'wifi-qrcode-generator': 'wifi_qr_code_generate',
    'xml-formatter': 'xml_format', 'xml-to-json': 'xml_to_json',
    'yaml-prettify': 'yaml_prettify', 'yaml-to-json-converter': 'yaml_to_json',
    'yaml-to-toml': 'yaml_to_toml',
}


def default_binary() -> str:
    home = os.environ.get("DIGIT_TOOLS_CORE_HOME") or "~/.digit/mcp-servers/tools-core"
    return str(pathlib.Path(home).expanduser() / "dist" / "digit-tools-mcp")


class Executor:
    """Минимальный клиент stdio-MCP к бинарю tools-core."""

    def __init__(self, binary: str):
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "measure", "version": "1"}})
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

    def execute(self, core_id: str, args: dict) -> dict:
        res = (self._rpc("tools/call", {"name": "tools_execute", "arguments": {
            "tool_id": core_id, "args": args, "timeout_ms": 30000}}).get("result") or {})
        got = res.get("structuredContent")
        if got is None:
            got = json.loads((res.get("content") or [{}])[0].get("text", "{}"))
        return got

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.terminate()


def measure(tasks, executor, policy: str) -> dict:
    counts: collections.Counter = collections.Counter()
    rows = []
    from digit_cli.ruleparse import route

    for task in tasks:
        decision = route(task["query"])
        if not decision.routed:
            counts["не разобрано правилами"] += 1
            rows.append((task["id"], decision.tool_id, "no-parse"))
            continue
        args = dict(decision.args)
        if policy == "bridge":
            bridged = translate(decision.tool_id or "", args)
        else:
            core = LEGACY_PUBLIC_TO_CORE.get(decision.tool_id or "")
            bridged = (core, args) if core else None
        if bridged is None:
            key = ("нет исполняемого аналога" if decision.tool_id in NO_CORE_TOOL
                   else "перевод не подтверждён")
            counts[key] += 1
            rows.append((task["id"], decision.tool_id, "no-translation"))
            continue
        core_id, core_args = bridged
        out = executor.execute(core_id, core_args)
        if out.get("ok"):
            counts["ИСПОЛНЕНО"] += 1
            rows.append((task["id"], decision.tool_id, "ok"))
        else:
            counts[f'исполнитель отверг: {out.get("code")}'] += 1
            rows.append((task["id"], decision.tool_id, f'reject:{out.get("code")}'))
    return {"counts": counts, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="json-файл задач измерительного набора")
    ap.add_argument("--binary", default=default_binary())
    ap.add_argument("--json", help="куда сложить построчный результат")
    args = ap.parse_args()

    raw = json.load(open(args.tasks, encoding="utf-8"))
    tasks = raw["tasks"] if isinstance(raw, dict) else raw
    tasks = [t for t in tasks if t.get("type") == "tool_routing"]

    executor = Executor(args.binary)
    try:
        before = measure(tasks, executor, "passthrough")
        after = measure(tasks, executor, "bridge")
    finally:
        executor.close()

    print(f"задач маршрутизации: {len(tasks)}\n")
    for name, got in (("БЫЛО (без моста аргументов)", before), ("СТАЛО (мост)", after)):
        print(name)
        for key, n in got["counts"].most_common():
            print(f"    {key:34s} {n}")
        print()

    moved = [(a[0], a[1]) for a, b in zip(after["rows"], before["rows"])
             if a[2] != b[2]]
    print("изменившиеся маршруты:")
    for task_id, tool in moved:
        b = dict((r[0], r[2]) for r in before["rows"])[task_id]
        a = dict((r[0], r[2]) for r in after["rows"])[task_id]
        print(f"    {task_id:20s} {tool:38s} {b} -> {a}")

    if args.json:
        json.dump({"before": {"counts": dict(before["counts"]), "rows": before["rows"]},
                   "after": {"counts": dict(after["counts"]), "rows": after["rows"]}},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
