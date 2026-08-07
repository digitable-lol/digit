"""Цифровой портрет владельца: три слоя, разделённые в коде.

``style``       слой 1 — как владелец формулирует. Числа, снятые с его речи.
``decisions``   слой 2 — что он решал. Дословно, со ссылкой на источник.
``conjecture``  слой 3 — что он мог бы ответить. Догадка; наружу не уходит.

Граница между вторым и третьим держится в :mod:`agent.portrait.provenance`:
слой — атрибут класса, а не поле, и единственная дверь наружу
(``outward_safe``) на догадке бросает исключение, а не пропускает её с
пометкой.

Наполняется портрет :class:`agent.portrait.observer.PortraitObserver` — по
ходу разговора, без отдельной команды «запомни». Читается явно: ``digit
portrait`` или инструмент ``portrait``. В системный промпт не попадает.
"""

from .conjecture import Conjecture, Draft, build, brief_for_model
from .decisions import DecisionRecord, extract, load, search
from .observer import PortraitObserver, summary_text
from .provenance import (
    ConjectureLeak,
    LABELS,
    Origin,
    label,
    outward_safe,
    render,
)
from .store import portrait_dir
from .style import StyleProfile, profile

__all__ = [
    "Conjecture",
    "ConjectureLeak",
    "DecisionRecord",
    "Draft",
    "LABELS",
    "Origin",
    "PortraitObserver",
    "StyleProfile",
    "brief_for_model",
    "build",
    "extract",
    "label",
    "load",
    "outward_safe",
    "portrait_dir",
    "profile",
    "render",
    "search",
    "summary_text",
]
