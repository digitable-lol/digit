"""``digit portrait`` — посмотреть портрет: что накоплено и чем подтверждается.

Команда существует потому, что портрет нарочно не уезжает в системный промпт:
раз его никто не видит по дороге, должен быть способ посмотреть его прямо. И
она же — единственное место, где догадка (слой 3) вообще показывается, и
показывается только тому, кто сидит за терминалом.

    digit portrait                    обзор: три слоя, разделённые явно
    digit portrait style              слой 1 подробно, с числом наблюдений
    digit portrait decisions [слова]  слой 2: решения, дословно, со ссылками
    digit portrait draft "<вопрос>"   слой 3: бриф + помеченный слот догадки
    digit portrait ingest FILE...     набить портрет из готовых транскриптов
    digit portrait forget [--session] убрать из портрета лишнее
    digit portrait on | off           включить/выключить накопление
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


def _load():
    from agent.portrait import decisions as decisions_mod
    from agent.portrait import store, style as style_mod
    return store, style_mod, decisions_mod


def _profile_and_records():
    store, style_mod, decisions_mod = _load()
    stats = store.read_json(store.STYLE_FILE)
    return style_mod.profile(stats), decisions_mod.load()


# ---------------------------------------------------------------------------
# Показ
# ---------------------------------------------------------------------------

def _cmd_show(args: argparse.Namespace) -> int:
    from agent.portrait.observer import summary_text
    from agent.portrait import store

    profile, records = _profile_and_records()
    if getattr(args, "json", False):
        print(json.dumps(_as_json(profile, records), ensure_ascii=False, indent=2))
        return 0
    if profile.messages == 0 and not records:
        print(
            "Портрет пуст.\n"
            "Накопление включается командой `digit portrait on` и идёт само, "
            "по ходу разговора.\n"
            "Готовые транскрипты можно влить сразу: "
            "`digit portrait ingest <файл.jsonl>`."
        )
        return 0
    print(summary_text(profile, records))
    _print_privacy_note()
    return 0


def _print_privacy_note() -> None:
    from agent.portrait import store

    path = store.portrait_dir()
    note = f"\nЛежит в {path} (только владелец). В системный промпт не уходит."
    if not store.permissions_ok():
        note += "\n⚠ Права на директорию шире 0700 — портрет виден другим на этой машине."
    print(note)


def _cmd_style(args: argparse.Namespace) -> int:
    from agent.portrait.provenance import render

    profile, _ = _profile_and_records()
    if getattr(args, "json", False):
        print(json.dumps(_style_json(profile), ensure_ascii=False, indent=2))
        return 0
    print(render(profile))
    if profile.lexicon:
        print("\nОтличительность (лог-шансы к фону из ответов ассистента):")
        for term, z, count in profile.lexicon[:20]:
            print(f"  {term:<20} z={z:6.2f}  ×{count}")
    return 0


def _cmd_decisions(args: argparse.Namespace) -> int:
    from agent.portrait import decisions as decisions_mod
    from agent.portrait.provenance import Origin, label

    _, records = _profile_and_records()
    query = " ".join(getattr(args, "query", []) or [])
    found = decisions_mod.search(records, query) if query else records
    if not getattr(args, "all", False):
        found = [r for r in found if not r.superseded_by]
    if getattr(args, "json", False):
        print(json.dumps([r.to_row() for r in found], ensure_ascii=False, indent=2))
        return 0
    if not found:
        print(
            "Ничего не найдено. Поиск лексический — синонимов не понимает; "
            "попробуйте другие слова, прежде чем считать, что решений не было."
        )
        return 0
    print(f"[{label(Origin.RECORD)}]  найдено {len(found)}")
    for record in found:
        print()
        print(record.as_text())
    return 0


def _cmd_draft(args: argparse.Namespace) -> int:
    from agent.portrait import decisions as decisions_mod
    from agent.portrait.conjecture import brief_for_model, build

    question = " ".join(getattr(args, "question", []) or []).strip()
    if not question:
        print("Нужен вопрос: digit portrait draft \"<вопрос>\"", file=sys.stderr)
        return 2
    profile, records = _profile_and_records()
    draft = build(question, profile, decisions_mod.search(records, question))
    if getattr(args, "outside", False):
        # То же самое, но глазами не владельца: остаются только записи со
        # ссылками. Ключ существует, чтобы разницу можно было увидеть, а не
        # прочитать про неё.
        print(draft.for_outside())
        return 0
    print(draft.for_owner())
    print("\n--- бриф для модели, которая будет заполнять догадку ---\n")
    print(brief_for_model(draft))
    return 0


# ---------------------------------------------------------------------------
# Наполнение из готовых транскриптов
# ---------------------------------------------------------------------------

def iter_transcript(path: Path) -> Iterator[Tuple[str, str, List[str], Optional[int]]]:
    """Выдать ``(роль, текст, инструменты, время)`` из JSONL-транскрипта.

    Понимает две раскладки: ``{"type":"user","message":{"content":...}}``
    (Claude Code / Digit) и плоскую ``{"role":"user","content":...}``.
    Служебные вставки в угловых скобках отбрасываются: портрет должен
    измерять человека, а не обвязку вокруг него.

    Имена вызванных инструментов нужны не для полноты: по ним запись
    решения получает свидетельство «сделано». Без них весь корпус
    выглядел бы как «сказано», и разница между произнесённым и
    сделанным, ради которой она заведена, не проверялась бы ни на чём.

    Время берётся из строки транскрипта, а не «сейчас». Ссылка «вы решили
    это тогда-то» — половина ценности записи; проставить в неё момент
    заливки значит соврать в самой проверяемой её части.
    """
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            role = row.get("type") or row.get("role") or ""
            message = row.get("message") if isinstance(row.get("message"), dict) else row
            if role not in ("user", "assistant"):
                continue
            content = message.get("content")
            text = _flatten(content)
            tools = _tool_names(content) if role == "assistant" else []
            if not text and not tools:
                continue
            if role == "user" and text.startswith("<"):
                continue
            yield role, text, tools, _timestamp(row)


def _timestamp(row: Dict[str, Any]) -> Optional[int]:
    raw = row.get("timestamp") or row.get("ts") or row.get("created_at")
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw:
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _tool_names(content: Any) -> List[str]:
    if not isinstance(content, list):
        return []
    names: List[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool_use" and part.get("name"):
            names.append(str(part["name"]))
        function = part.get("function")
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def _has_repeated_act(stats: Dict[str, Any], style_mod) -> bool:
    """Повторилась ли хоть одна формулировка речевого акта нужное число раз.

    Считается по сырым счётчикам, а не через ``profile()``: пересобирать
    полный отчёт после каждого сообщения — это пересчёт лог-шансов по всему
    словарю на каждом шаге, ради одного логического значения.
    """
    for bucket in (stats.get("owner", {}).get("acts") or {}).values():
        if any(n >= style_mod.MIN_ACT_OCCURRENCES for n in bucket.values()):
            return True
    return False


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Набить портрет готовыми транскриптами.

    Нужен дважды: чтобы портрет не начинался с нуля у человека, у которого
    уже год переписки, и чтобы накопление можно было **измерить** — на
    настоящих сообщениях, а не на придуманном примере.
    """
    store, style_mod, decisions_mod = _load()

    paths: List[Path] = []
    for raw in args.paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.jsonl")))
        else:
            paths.append(path)
    if args.limit_files:
        paths = paths[: args.limit_files]

    stats = store.read_json(store.STYLE_FILE) or style_mod.blank()
    if stats.get("version") != style_mod.SCHEMA_VERSION:
        stats = style_mod.blank()

    user_msgs = 0
    skipped_templates = 0
    found_total: List[Any] = []
    first_decision_at: Optional[int] = None
    first_act_at: Optional[int] = None
    #: Шаблонные рассылки. В архиве транскриптов роль «user» носит не только
    #: человек: оркестраторы и подагенты шлют туда сгенерированные брифы
    #: сотнями. На живом корпусе это видно сразу — одно и то же начало
    #: сообщения встречается 468 раз, и портрет начинает измерять не
    #: владельца, а шаблон. Человек не повторяет свой зачин дословно;
    #: первые два вхождения проходят, дальше это уже рассылка.
    openings: Dict[str, int] = {}

    def flush(pending, session: str, name: str, tools: List[str]) -> None:
        """Извлечь решения из отложенного сообщения, зная, чем ход кончился.

        Сообщение разбирается не сразу: свидетельство «сделано» приходит
        ПОСЛЕ него — из инструментов, которые агент вызвал в ответ.
        """
        nonlocal first_decision_at
        if pending is None:
            return
        index, text, when = pending
        found = decisions_mod.extract(
            text,
            session_id=session,
            ts=when,
            source=f"ingest:{name}",
            tools_used=tools,
        )
        if found:
            found_total.extend(found)
            if first_decision_at is None:
                first_decision_at = index

    for path in paths:
        session = path.stem
        pending: Optional[Tuple[int, str, Optional[int]]] = None
        tools: List[str] = []
        for role, text, called, when in iter_transcript(path):
            if role == "assistant":
                if text:
                    style_mod.observe(stats, text, side="baseline")
                tools.extend(called)
                continue
            flush(pending, session, path.name, tools)
            tools = []
            if args.dedupe:
                opening = " ".join(text.split())[:120].lower()
                openings[opening] = openings.get(opening, 0) + 1
                if openings[opening] > 2:
                    skipped_templates += 1
                    pending = None
                    continue
            user_msgs += 1
            style_mod.observe(stats, text, side="owner")
            if first_act_at is None and _has_repeated_act(stats, style_mod):
                first_act_at = user_msgs
            pending = (user_msgs, text, when)
        flush(pending, session, path.name, tools)

    acted = sum(1 for r in found_total if r.evidence == "сделано")
    if args.dry_run:
        profile = style_mod.profile(stats)
    else:
        store.write_json(store.STYLE_FILE, stats)
        # По времени, а не по имени файла. Отмена прежнего решения работает
        # в том порядке, в каком записи ложатся в журнал, а файлы обходятся
        # по алфавиту — на архиве это переставило бы «передумал» и
        # «решил», и действующим осталось бы отменённое.
        found_total.sort(key=lambda record: record.ts)
        decisions_mod.append(found_total)
        profile = style_mod.profile(store.read_json(store.STYLE_FILE))

    report = {
        "файлов": len(paths),
        "сообщений владельца": user_msgs,
        "отброшено как шаблонная рассылка": skipped_templates,
        "первое решение — на сообщении": first_decision_at,
        "повтор формулировки речевого акта — на сообщении": first_act_at,
        "стиль стал устойчивым на сообщении": (
            style_mod.MIN_MESSAGES_ESTABLISHED if profile.established else None
        ),
        "решений извлечено": len(found_total),
        "на 100 сообщений": round(100.0 * len(found_total) / user_msgs, 1) if user_msgs else 0.0,
        "подтверждено действиями (сделано)": acted,
        "только произнесено (сказано)": len(found_total) - acted,
        "сообщений в стиле": profile.messages,
        "речевых актов": {k: len(v) for k, v in profile.acts.items()},
        "записано": not args.dry_run,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0


# ---------------------------------------------------------------------------
# Забыть
# ---------------------------------------------------------------------------

def _cmd_forget(args: argparse.Namespace) -> int:
    store, _style_mod, _decisions_mod = _load()

    if args.all:
        if not args.yes:
            print("Это удалит портрет целиком. Повторите с -y.", file=sys.stderr)
            return 2
        removed = store.wipe()
        print(f"Удалено файлов портрета: {removed}.")
        return 0
    if not args.session:
        print("Укажите --session <id> или --all.", file=sys.stderr)
        return 2
    rows = [
        row for row in store.iter_lines(store.DECISIONS_FILE)
        if not (row.get("t") == "d" and str(row.get("session", "")).startswith(args.session))
    ]
    kept = store.rewrite_lines(store.DECISIONS_FILE, rows)
    print(
        f"Из журнала решений убраны записи сессии {args.session}; осталось строк: {kept}.\n"
        "Слой стиля — агрегат, из него запись одной сессии не вычитается: "
        "если нужно убрать и её, это `--all`."
    )
    return 0


# ---------------------------------------------------------------------------
# Включение
# ---------------------------------------------------------------------------

def _cmd_toggle(args: argparse.Namespace, enabled: bool) -> int:
    from digit_cli.config import set_config_value

    try:
        set_config_value("portrait.enabled", "true" if enabled else "false")
    except Exception as exc:
        print(f"Не удалось записать конфиг: {exc}", file=sys.stderr)
        return 1
    if enabled:
        print(
            "Накопление портрета включено. Каждый завершённый ход разговора "
            "теперь измеряется.\n"
            "Данные — только локально; в системный промпт и во внешние "
            "провайдеры памяти портрет не уходит."
        )
    else:
        print("Накопление портрета выключено. Уже накопленное не удалено — "
              "для этого `digit portrait forget --all -y`.")
    return 0


def _as_json(profile, records) -> Dict[str, Any]:
    return {
        "style": _style_json(profile),
        "decisions": [r.to_row() for r in records],
        "conjecture": {
            "stored": False,
            "note": "догадка не хранится; собирается на запрос",
        },
    }


def _style_json(profile) -> Dict[str, Any]:
    return {
        "origin": "style",
        "messages": profile.messages,
        "established": profile.established,
        "median_chars": profile.median_chars,
        "p90_chars": profile.p90_chars,
        "median_sentence_words": profile.median_sentence_words,
        "question_rate": profile.question_rate,
        "imperative_rate": profile.imperative_rate,
        "latin_share": profile.latin_share,
        "emoji_per_message": profile.emoji_per_message,
        "acts": {k: v for k, v in profile.acts.items()},
        "lexicon": [{"term": t, "z": z, "count": n} for t, z, n in profile.lexicon],
        "openers": profile.openers,
    }


# ---------------------------------------------------------------------------
# Разбор аргументов
# ---------------------------------------------------------------------------

def register_cli(parent: argparse.ArgumentParser) -> None:
    parent.add_argument("--json", action="store_true", help="Выдать JSON и выйти.")
    parent.set_defaults(func=_cmd_show)

    sub = parent.add_subparsers(dest="portrait_action")

    p_style = sub.add_parser("style", help="Слой 1: измеренная манера речи.")
    p_style.add_argument("--json", action="store_true")
    p_style.set_defaults(func=_cmd_style)

    p_dec = sub.add_parser(
        "decisions",
        help="Слой 2: прежние решения владельца, дословно, со ссылками.",
    )
    p_dec.add_argument("query", nargs="*", help="Слова для поиска (пусто — все).")
    p_dec.add_argument("--all", action="store_true", help="Показать и отменённые.")
    p_dec.add_argument("--json", action="store_true")
    p_dec.set_defaults(func=_cmd_decisions)

    p_draft = sub.add_parser(
        "draft",
        help="Слой 3: бриф для ответа в манере владельца + слот догадки.",
    )
    p_draft.add_argument("question", nargs="*")
    p_draft.add_argument(
        "--outside",
        action="store_true",
        help="Показать то же глазами не владельца: только решения со ссылками.",
    )
    p_draft.set_defaults(func=_cmd_draft)

    p_ing = sub.add_parser("ingest", help="Набить портрет из JSONL-транскриптов.")
    p_ing.add_argument("paths", nargs="+", help="Файлы или директории с *.jsonl.")
    p_ing.add_argument("--limit-files", type=int, default=0)
    p_ing.add_argument(
        "--no-dedupe",
        dest="dedupe",
        action="store_false",
        help=(
            "Не отбрасывать повторяющиеся зачины. По умолчанию сообщение, "
            "чьё начало уже встречалось дважды, пропускается: в архивах роль "
            "«user» носят и шаблонные брифы оркестраторов."
        ),
    )
    p_ing.set_defaults(dedupe=True)
    p_ing.add_argument("--dry-run", action="store_true", help="Посчитать, но не писать.")
    p_ing.add_argument("--json", action="store_true")
    p_ing.set_defaults(func=_cmd_ingest)

    p_forget = sub.add_parser("forget", help="Убрать из портрета сессию или всё.")
    p_forget.add_argument("--session", default="", help="Префикс id сессии.")
    p_forget.add_argument("--all", action="store_true")
    p_forget.add_argument("-y", "--yes", action="store_true")
    p_forget.set_defaults(func=_cmd_forget)

    p_on = sub.add_parser("on", help="Включить накопление портрета.")
    p_on.set_defaults(func=lambda a: _cmd_toggle(a, True))
    p_off = sub.add_parser("off", help="Выключить накопление портрета.")
    p_off.set_defaults(func=lambda a: _cmd_toggle(a, False))


def cmd_portrait(args: argparse.Namespace) -> int:
    return _cmd_show(args)
