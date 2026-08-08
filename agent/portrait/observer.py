"""Портрет наполняется ходом разговора, а не командой «запомни».

Точка подключения — существующая: :class:`agent.memory_provider.MemoryProvider`
и его ``sync_turn``, который :mod:`run_agent` зовёт после каждого завершённого
хода с текстом пользователя, ответом ассистента и списком сообщений. Ничего
нового изобретать не пришлось; понадобился провайдер, который в этот хук не
пишет, а **измеряет**.

Почему именно завершённый ход
-----------------------------
``sync_turn`` не вызывается на прерванных ходах, и это ровно то поведение,
которое портрету нужно. Прерванный ход — это чаще всего оборванная мысль,
которую пользователь тут же переформулирует; записать её как решение значит
занести в портрет то, чего человек не говорил до конца.

Почему это выключено по умолчанию
---------------------------------
Профиль речи не должен начать собираться оттого, что человек обновил пакет.
``portrait.enabled: false`` в конфиге по умолчанию; включается осознанно —
``digit portrait on``.

Что уходит в промпт
-------------------
Ничего. ``prefetch`` возвращает пустую строку всегда. Коммит fb4b45a0d снял с
памяти цену «весь файл в каждом запросе»; заводить её заново ради портрета
— значит платить за него каждым запросом и вынести профиль речи владельца в
каждый лог каждого провайдера модели. Портрет читается явно: инструментом
``portrait`` или командой ``digit portrait``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from . import decisions as decisions_mod
from . import store
from . import style as style_mod
from .conjecture import brief_for_model, build

logger = logging.getLogger(__name__)

TOOL_NAME = "portrait"


class PortraitObserver(MemoryProvider):
    """Слушает ход, наполняет слои 1 и 2. Слой 3 не наполняет никогда."""

    def __init__(self, *, source: str = "cli", enabled: bool = True) -> None:
        self._source = source
        self._enabled = bool(enabled)
        self.observed_turns = 0
        self.recorded_decisions = 0

    @property
    def name(self) -> str:
        return "portrait"

    def is_available(self) -> bool:
        return self._enabled

    def initialize(self, session_id: str, **kwargs) -> None:
        return None

    def system_prompt_block(self) -> str:
        # Пусто намеренно: см. модульную докстроку.
        return ""

    def shutdown(self) -> None:
        """Закрыть индекс решений.

        Не косметика: SQLite в режиме WAL сворачивает журнал в базу при
        закрытии последнего соединения. Брошенное соединение оставляет рядом
        с портретом ``decisions.db-wal`` — файл с теми же цитатами, который
        никто не ждёт и который переживёт процесс.
        """
        from . import index as index_mod

        index_mod.forget()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Один ход — одно наблюдение. Ошибки сюда не выпускаются наружу.

        Портрет — производное наблюдение. Сломать им ход пользователя нельзя
        ни при каких обстоятельствах, поэтому всё тело под ``except``.
        """
        if not self._enabled:
            return
        try:
            self._observe(user_content, assistant_content, session_id, messages)
        except Exception as exc:  # pragma: no cover - защитный контур
            logger.warning("portrait: наблюдение хода не удалось: %s", exc)

    def _observe(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> None:
        user_content = (user_content or "").strip()
        if not user_content:
            return
        stats = store.read_json(store.STYLE_FILE) or style_mod.blank()
        if stats.get("version") != style_mod.SCHEMA_VERSION:
            stats = style_mod.blank()
        # Тот же счёт вторым экземпляром — по одной сессии. Это и есть то,
        # чем «забудь этот разговор» потом вычитается из агрегата: без среза
        # forget чистил бы журнал решений, а словарь и длины оставлял.
        slice_ = None
        if session_id:
            slice_ = store.read_session(session_id) or style_mod.blank()
        for side, text in (("owner", user_content), ("baseline", assistant_content)):
            if not text:
                continue
            style_mod.observe(stats, text, side=side)
            if slice_ is not None:
                style_mod.observe(slice_, text, side=side)
        store.write_json(store.STYLE_FILE, stats)
        if slice_ is not None:
            store.write_session(session_id, slice_)

        found = decisions_mod.extract(
            user_content,
            session_id=session_id,
            source=self._source,
            tools_used=decisions_mod.tools_of_turn(messages),
        )
        if found:
            decisions_mod.append(found)
            self.recorded_decisions += len(found)
        self.observed_turns += 1

    # -- инструмент ---------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [{
            "name": TOOL_NAME,
            "description": (
                "Цифровой портрет владельца — три РАЗДЕЛЬНЫХ слоя. "
                "style: измеренная манера речи. decisions: прежние решения "
                "владельца дословно, со ссылкой на сессию и время — это "
                "единственный слой, который можно показывать кому-то кроме "
                "владельца. draft: бриф для ответа в его манере; содержит "
                "слот догадки, который наружу не уходит никогда."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["view", "style", "decisions", "draft"],
                        "description": "Что показать.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Для decisions — по каким словам искать; для "
                            "draft — вопрос, на который нужен ответ в манере "
                            "владельца."
                        ),
                    },
                },
                "required": ["action"],
            },
        }]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name != TOOL_NAME:
            return json.dumps({"error": f"неизвестный инструмент {tool_name}"},
                              ensure_ascii=False)
        action = str(args.get("action") or "view")
        query = str(args.get("query") or "")
        try:
            return self._dispatch(action, query)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    def _dispatch(self, action: str, query: str) -> str:
        from .provenance import render

        # Журнал читается только там, где он нужен целиком. С запросом за
        # решениями ходит индекс, и весь файл при этом не трогается вообще —
        # ради этого он и заведён.
        if action == "style":
            return render(style_mod.profile(store.read_json(store.STYLE_FILE)))
        if action == "decisions":
            found = (decisions_mod.find(query) if query
                     else decisions_mod.load()[-10:])
            if not found:
                return (
                    "Решений не найдено. Поиск лексический — синонимов не "
                    "понимает; попробуйте другие слова, прежде чем считать, "
                    "что решений не было."
                )
            from .provenance import label
            from .provenance import Origin
            body = "\n\n".join(r.as_text() for r in found)
            return f"[{label(Origin.RECORD)}]\n{body}"
        profile = style_mod.profile(store.read_json(store.STYLE_FILE))
        if action == "draft":
            if not query:
                return "draft требует query — вопрос, на который нужен ответ."
            draft = build(query, profile, decisions_mod.find(query))
            return draft.for_owner() + "\n\n---\n" + brief_for_model(draft)
        # view
        return summary_text(profile, decisions_mod.load())


def summary_text(profile, records) -> str:
    """Обзор портрета: три слоя, разделённые явно."""
    from .provenance import Origin, label, render

    live = [r for r in records if not r.superseded_by]
    acted = [r for r in live if r.evidence == "сделано"]
    lines = [render(profile), ""]
    lines.append(f"[{label(Origin.RECORD)}]")
    lines.append(
        f"Записей: {len(records)} (действующих {len(live)}, отменённых "
        f"{len(records) - len(live)}); подтверждено действиями: {len(acted)}."
    )
    for record in live[-5:]:
        lines.append("")
        lines.append(record.as_text())
    lines.append("")
    lines.append(f"[{label(Origin.CONJECTURE)}]")
    lines.append(
        "Не хранится. Собирается на конкретный вопрос — "
        "`digit portrait draft \"<вопрос>\"` — и живёт ровно один вызов."
    )
    return "\n".join(lines)
