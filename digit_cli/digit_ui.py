"""Small RU/EN copy layer for Digit's first visible CLI surface."""

from __future__ import annotations

from agent.i18n import get_language


_COPY = {
    "en": {
        "welcome": "Digit is ready. Describe a task in plain language; /help shows commands.",
        "compact_title": "Digit — AI agent by Digitable",
        "capabilities_title": "What Digit can do",
        "capability_code": "Code & files — read, edit, run, and verify",
        "capability_web": "Web — search, open pages, and automate the browser",
        "capability_tasks": "Tasks — delegate work, schedule jobs, and track progress",
        "capability_media": "Media — understand images and create visual assets",
        "capability_digitable": "Digitable — courses, tools, portal, and Workbench",
        "start_title": "Start here",
        "start_prompt": "Describe the outcome you want in your own words.",
        "start_commands": "/help commands · /model model · /tools details · /skills knowledge",
        "summary": "{tools} tools · {skills} skills available",
        "tip_label": "Tip",
        "model_missing": "no model configured — run /model or digit setup",
        "context": "{context} context",
        "session": "Session: {session}",
        "resume_title": "Resume this session with:",
        "session_label": "Session:",
        "title_label": "Title:",
        "duration_label": "Duration:",
        "messages_label": "Messages:",
        "tokens_label": "Tokens:",
        "token_counts": "{total} (in {input}, out {output}, cache {cache}, reasoning {reasoning})",
        "message_counts": "{messages} ({users} user, {tools} tool calls)",
        "duration_hms": "{hours}h {minutes}m {seconds}s",
        "duration_ms": "{minutes}m {seconds}s",
        "duration_s": "{seconds}s",
        "local_model_missing": "Local model `{model}` is not installed. Run `ollama pull {model}`, then retry.",
    },
    "ru": {
        "welcome": "Digit готов. Опишите задачу обычными словами; /help покажет команды.",
        "compact_title": "Digit — ИИ-агент Digitable",
        "capabilities_title": "Что умеет Digit",
        "capability_code": "Код и файлы — читать, изменять, запускать и проверять",
        "capability_web": "Интернет — искать, открывать страницы и управлять браузером",
        "capability_tasks": "Задачи — делегировать работу, планировать и следить за прогрессом",
        "capability_media": "Медиа — понимать изображения и создавать визуальные материалы",
        "capability_digitable": "Digitable — курсы, утилиты, портал и Workbench",
        "start_title": "С чего начать",
        "start_prompt": "Опишите желаемый результат своими словами.",
        "start_commands": "/help команды · /model модель · /tools подробно · /skills знания",
        "summary": "Доступно: {tools} инструментов · {skills} навыков",
        "tip_label": "Совет",
        "model_missing": "модель не настроена — используйте /model или digit setup",
        "context": "контекст {context}",
        "session": "Сеанс: {session}",
        "resume_title": "Продолжить этот сеанс:",
        "session_label": "Сеанс:",
        "title_label": "Название:",
        "duration_label": "Длительность:",
        "messages_label": "Сообщения:",
        "tokens_label": "Токены:",
        "token_counts": "{total} (вход: {input}, выход: {output}, кэш: {cache}, рассуждение: {reasoning})",
        "message_counts": "{messages} (ваших: {users}, вызовов инструментов: {tools})",
        "duration_hms": "{hours} ч {minutes} мин {seconds} с",
        "duration_ms": "{minutes} мин {seconds} с",
        "duration_s": "{seconds} с",
        "local_model_missing": "Локальная модель `{model}` не установлена. Выполните `ollama pull {model}`, затем повторите запрос.",
    },
}


def digit_ui_text(key: str, *, language: str | None = None, **values) -> str:
    """Return startup copy in Russian or English, falling back to English."""
    lang = language or get_language()
    selected = _COPY["ru"] if str(lang).lower().startswith("ru") else _COPY["en"]
    return selected[key].format(**values)
