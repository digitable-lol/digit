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
    },
}


def digit_ui_text(key: str, *, language: str | None = None, **values) -> str:
    """Return startup copy in Russian or English, falling back to English."""
    lang = language or get_language()
    selected = _COPY["ru"] if str(lang).lower().startswith("ru") else _COPY["en"]
    return selected[key].format(**values)
