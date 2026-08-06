"""``digit workbench`` — каталог интеграций Digitable Workbench, доступный агенту.

ЗАЧЕМ ЭТА КОМАНДА ВООБЩЕ ЕСТЬ
-----------------------------
Каталог интеграций Workbench — это 5 категорий карточек в
``data/workbench-integrations.toml`` чекаута ``courses``. До сих пор он
существовал ровно в одном виде: как публичная страница, которую генерирует
Hugo. Агент, которого просят «положи палитру Digitable в neovim», страницу
прочитать не может, поэтому отвечал по памяти — то есть выдумывал путь
назначения и шаги. А в карточках лежит ровно то, что выдумать нельзя:
``dest`` (куда именно кладутся файлы), ``steps`` (порядок подключения),
``verify`` (чем проверить) и ``caveat`` (чем эта цель отличается от
остальных — например, что тему Discord ставит только клиентский мод и это
против ToS).

Ступень лестницы следа (AGENTS.md, «The Footprint Ladder») здесь вторая:
возможность выражается shell-командой, поэтому агент вызывает
``digit workbench …`` под навыком ``workbench-integrations``, а схема
инструментов модели, которую оплачивает каждый вызов API, не растёт.

ПОЧЕМУ КАТАЛОГ НЕ КОПИРУЕТСЯ ВНУТРЬ DIGIT
-----------------------------------------
Соблазн положить снимок TOML в дерево Digit велик — тогда команда работает
без чекаута ``courses``. Но карточки правятся вместе с генератором тем, и
копия расходится с источником МОЛЧА: команда продолжает отвечать, просто
вчерашними шагами. Ошибка такого рода не видна ни в одном прогоне. Поэтому
читаем чекаут, а когда его нет — говорим об этом и не отвечаем вовсе. Тот же
выбор уже сделан в ``digit kb``: корпус там тоже живёт в чужих чекаутах, и
местоположение ``courses`` берётся отсюда же (см. :func:`_courses_candidates`),
чтобы знание о том, где лежит репозиторий, оставалось в одном месте.

ПОЧЕМУ ``caveat`` ПЕЧАТАЕТСЯ ВСЕГДА
-----------------------------------
В самом TOML про это написано прямым текстом: ключ ``caveat`` включает у
карточки жёлтую метку и видимое предупреждение, «прятать его нельзя». У
одиннадцати карточек он есть, и содержание у него не косметическое:
«против ToS», «нужна своя подпись расширения», «GPL-3.0-only, в платный архив
не кладётся». Краткий вывод, который опустил бы предупреждение, — это не
экономия строк, а неверный ответ, поэтому :func:`render_list` помечает такие
карточки, а :func:`render_show` печатает предупреждение целиком.

ПОЧЕМУ ЧИСЛО КАРТОЧЕК НИГДЕ НЕ ЗАПИСАНО
---------------------------------------
Ни в коде, ни в тестах нет числа 52. Каталог считает себя сам — и страницы
Hugo, и эта команда. Проверка вида «карточек ровно 52» краснела бы на каждой
новой цели, ничего при этом не защищая (AGENTS.md: «Behavior contracts over
snapshots»).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Путь к каталогу внутри чекаута ``courses``.
CATALOG_REL = Path("data") / "workbench-integrations.toml"


class CatalogNotFound(RuntimeError):
    """Файл каталога не удалось найти ни одним из способов."""


class CatalogError(RuntimeError):
    """Файл найден, но прочитать его как каталог нельзя."""


class UnknownIntegration(LookupError):
    """В каталоге нет карточки с таким идентификатором."""

    def __init__(self, wanted: str, suggestions: Sequence[str] = ()) -> None:
        super().__init__(wanted)
        self.wanted = wanted
        self.suggestions = tuple(suggestions)


# --------------------------------------------------------------------------
# Модель каталога
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Integration:
    """Одна карточка каталога.

    Поля названы как в TOML, кроме ``open_guide`` (там ``openGuide``) — это
    единственное место, где имя переводится, и переводится оно потому, что
    camelCase в питоновском атрибуте читается как опечатка.

    ``extra`` хранит ключи, которых эта версия кода не знает. Каталог правит
    не Digit, а автор страницы Workbench, и новый ключ там появится раньше,
    чем здесь; ``--json`` отдаёт карточку вместе с ``extra``, поэтому
    добавленное поле доедет до вызывающего, а не потеряется по дороге.
    """

    id: str
    name: str
    category: str
    category_title: str
    gist: str = ""
    files: str = ""
    dest: str = ""
    steps: Tuple[str, ...] = ()
    snippet: str = ""
    note: str = ""
    verify: str = ""
    badge: str = ""
    caveat: str = ""
    open_guide: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def availability(self) -> str:
        """«Открыто» против «в архиве» — ровно то различие, которое каталог
        подписывает ключом ``openGuide``: у пяти карточек файлов палитры нет,
        это открытые руководства, и обещать за них содержимое платного архива
        нельзя."""
        return "open guide" if self.open_guide else "in archive"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "category_title": self.category_title,
            "gist": self.gist,
            "files": self.files,
            "dest": self.dest,
            "steps": list(self.steps),
            "availability": self.availability,
        }
        for key in ("snippet", "note", "verify", "badge", "caveat"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload

    def haystack(self) -> str:
        """Текст, по которому ищет ``search``.

        Сюда входят шаги и предупреждение, а не только имя: запрос «куда
        класть тему для tmux» и запрос «что нельзя ставить из-за ToS» одинаково
        обязаны находить карточку, а второй в имени не встречается ни разу.
        """
        parts = [
            self.id, self.name, self.category, self.category_title,
            self.gist, self.files, self.dest, self.note, self.verify,
            self.badge, self.caveat, *self.steps,
        ]
        return "\n".join(p for p in parts if p).casefold()


@dataclass(frozen=True)
class Catalog:
    path: Path
    integrations: Tuple[Integration, ...]
    categories: Tuple[Tuple[str, str], ...]

    def get(self, wanted: str) -> Integration:
        key = (wanted or "").strip().casefold()
        for item in self.integrations:
            if item.id.casefold() == key:
                return item
        # Подсказки строятся по вхождению подстроки в обе стороны: и «vim» →
        # neovim, и «neovim-nightly» → neovim. Без них ответ «нет такой цели»
        # заставляет вызывающего перечитывать весь список ради опечатки.
        near = [
            i.id for i in self.integrations
            if key and (key in i.id.casefold() or i.id.casefold() in key
                        or key in i.name.casefold())
        ]
        raise UnknownIntegration(wanted, near[:5])

    def search(self, query: str) -> List[Integration]:
        needle = (query or "").strip().casefold()
        if not needle:
            return list(self.integrations)
        return [i for i in self.integrations if needle in i.haystack()]

    def in_category(self, category: str) -> List[Integration]:
        key = (category or "").strip().casefold()
        return [i for i in self.integrations if i.category.casefold() == key]


# --------------------------------------------------------------------------
# Где лежит каталог
# --------------------------------------------------------------------------


def _courses_candidates() -> Tuple[Path, ...]:
    """Обычные места чекаута ``courses``, взятые у ``digit kb``.

    Импорт ленивый и обёрнут: ``digit_cli.kb.indexer`` тянет за собой
    ``urllib``/``ssl`` (клиент эмбеддингов) и стоит около 50 мс, а
    ``digit workbench --help`` этого платить не должен. Если модуль почему-то
    не импортируется, остаёмся без подсказок из kb, но не падаем: ниже есть
    поиск вверх по дереву.
    """
    try:
        from digit_cli.kb import indexer
    except Exception:  # pragma: no cover - kb недоступен только при поломке дерева
        return ()

    roots: List[Path] = []
    env = os.environ.get(indexer.COURSES_REPO.env_var, "").strip()
    if env:
        roots.append(Path(env).expanduser())
    roots.extend(indexer.COURSES_REPO.default_roots)
    return tuple(roots)


def find_catalog(explicit: Optional[str] = None,
                 *,
                 start: Optional[Path] = None,
                 config: Optional[Dict[str, Any]] = None) -> Path:
    """Найти файл каталога.

    Порядок: ``--catalog`` → ``config.yaml`` (``workbench.catalog``) → чекаут
    ``courses`` там, где его ищет ``digit kb`` → обход вверх от рабочего
    каталога.

    Нового ``DIGIT_*``-переменного окружения здесь не заводится: AGENTS.md
    оставляет ``.env`` секретам, а поведенческая настройка живёт в
    ``config.yaml``. ``DIGIT_KB_CORPUS`` читается не как своя переменная, а
    как уже существующая настройка корпуса kb — она указывает на тот же самый
    чекаут, и заставлять пользователя объявлять его дважды было бы враньём про
    «одно место истины».

    ``explicit`` принимает и файл, и каталог: разница между «путь к TOML» и
    «путь к чекауту courses» для вызывающего несущественна, а ошибка из-за неё
    обидна.
    """
    for candidate in (
        explicit,
        ((config or {}).get("workbench") or {}).get("catalog") or None,
    ):
        if not candidate:
            continue
        path = Path(os.path.expanduser(str(candidate)))
        resolved = path / CATALOG_REL if path.is_dir() else path
        if not resolved.is_file():
            raise CatalogNotFound(f"{resolved} is not a file")
        return resolved

    for root in _courses_candidates():
        resolved = root / CATALOG_REL
        if resolved.is_file():
            return resolved

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        for resolved in (directory / CATALOG_REL,
                         directory / "courses" / CATALOG_REL):
            if resolved.is_file():
                return resolved

    raise CatalogNotFound(
        f"No {CATALOG_REL} found in the courses checkout, in {here}, or in any "
        f"parent. The catalog is deliberately not vendored into Digit — a copy "
        f"would go stale silently. Point at it with "
        f"`digit workbench --catalog PATH` or set `workbench.catalog` in "
        f"config.yaml."
    )


# --------------------------------------------------------------------------
# Чтение каталога
# --------------------------------------------------------------------------

#: Ключи карточки, которые эта версия кода раскладывает по полям. Всё
#: остальное едет в ``Integration.extra`` — включая ``snippetAfter``: это
#: подсказка вёрстки страницы (после какого шага вставить фрагмент), которую
#: команда не толкует, и записывать её в «известные» значило бы соврать, что
#: она учтена.
_KNOWN_ITEM_KEYS = frozenset({
    "id", "name", "gist", "files", "dest", "steps", "snippet",
    "note", "verify", "badge", "caveat", "openGuide",
})


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def load_catalog(path: Path) -> Catalog:
    """Прочитать TOML и проверить то, без чего ответ был бы неверным.

    Проверяются ровно две вещи, и обе — про правильность ответа, а не про
    форму файла:

    * у карточки обязаны быть ``id``, ``name``, ``dest`` и непустые ``steps``.
      Карточка без ``dest`` или без шагов существует только чтобы занять место
      в списке: агент, получивший её, вынужден догадываться — то есть ровно то
      поведение, которое команда и убирает;
    * ``id`` уникален по всему каталогу, а не внутри категории. ``show <id>``
      адресует карточку одним словом, и два ``vscode`` в разных категориях
      сделали бы ответ зависящим от порядка чтения файла.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"could not read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{path} is not valid TOML: {exc}") from exc

    categories = raw.get("category")
    if not isinstance(categories, list) or not categories:
        raise CatalogError(
            f"{path} has no [[category]] tables — this is not the Workbench "
            f"integrations catalog."
        )

    items: List[Integration] = []
    cat_pairs: List[Tuple[str, str]] = []
    seen: Dict[str, str] = {}

    for cat in categories:
        if not isinstance(cat, dict):
            raise CatalogError(f"{path}: a [[category]] entry is not a table")
        cat_id = _text(cat.get("id")).strip()
        cat_title = _text(cat.get("title")).strip() or cat_id
        if not cat_id:
            raise CatalogError(f"{path}: a [[category]] entry has no id")
        cat_pairs.append((cat_id, cat_title))

        for entry in cat.get("item") or ():
            if not isinstance(entry, dict):
                raise CatalogError(f"{path}: an item in {cat_id} is not a table")
            item_id = _text(entry.get("id")).strip()
            name = _text(entry.get("name")).strip()
            dest = _text(entry.get("dest")).strip()
            steps = tuple(
                _text(s).strip() for s in (entry.get("steps") or ()) if _text(s).strip()
            )
            missing = [
                key for key, value in (
                    ("id", item_id), ("name", name), ("dest", dest), ("steps", steps),
                ) if not value
            ]
            if missing:
                where = item_id or name or f"<unnamed in {cat_id}>"
                raise CatalogError(
                    f"{path}: card '{where}' is missing {', '.join(missing)}. A "
                    f"card without a destination or steps cannot be acted on."
                )
            if item_id in seen:
                raise CatalogError(
                    f"{path}: duplicate card id '{item_id}' in categories "
                    f"'{seen[item_id]}' and '{cat_id}'. Ids address cards, so "
                    f"they have to be unique across the whole catalog."
                )
            seen[item_id] = cat_id

            items.append(Integration(
                id=item_id,
                name=name,
                category=cat_id,
                category_title=cat_title,
                gist=_text(entry.get("gist")).strip(),
                files=_text(entry.get("files")).strip(),
                dest=dest,
                steps=steps,
                snippet=_text(entry.get("snippet")).strip("\n"),
                note=_text(entry.get("note")).strip(),
                verify=_text(entry.get("verify")).strip(),
                badge=_text(entry.get("badge")).strip(),
                caveat=_text(entry.get("caveat")).strip(),
                open_guide=bool(entry.get("openGuide")),
                extra={k: v for k, v in entry.items() if k not in _KNOWN_ITEM_KEYS},
            ))

    return Catalog(path=path, integrations=tuple(items), categories=tuple(cat_pairs))


# --------------------------------------------------------------------------
# Отрисовка
# --------------------------------------------------------------------------


def _short(text: str, width: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= width else flat[: max(1, width - 1)] + "…"


def render_list(items: Sequence[Integration], *, width: int = 100) -> str:
    """Список карточек.

    Столбец ``!`` — не украшение: он отмечает карточки с ``caveat``, то есть
    те, где «скопируйте файлы» неполно и без чтения предупреждения работать
    нельзя. Без этого столбца список выглядит однородным, и агент выбирает из
    него как из равных.
    """
    if not items:
        return "  no matching integrations"
    gist_width = max(20, width - 46)
    lines = [
        f"  {'id':22}  {'category':10}  !  {'name':16}  gist",
        f"  {'-' * 22}  {'-' * 10}  -  {'-' * 16}  {'-' * min(gist_width, 40)}",
    ]
    for item in items:
        lines.append(
            f"  {_short(item.id, 22):22}  "
            f"{_short(item.category, 10):10}  "
            f"{'!' if item.caveat else ' '}  "
            f"{_short(item.name, 16):16}  "
            f"{_short(item.gist, gist_width)}"
        )
    flagged = sum(1 for i in items if i.caveat)
    lines.append("")
    lines.append(f"  {len(items)} integration(s); {flagged} carry a caveat (!).")
    lines.append("  Full card, with destination and steps: digit workbench show <id>")
    return "\n".join(lines)


def render_show(item: Integration) -> str:
    lines = [
        f"  id          {item.id}",
        f"  name        {item.name}",
        f"  category    {item.category} ({item.category_title})",
        f"  gist        {item.gist or '-'}",
        f"  files       {item.files or '-'}",
        f"  dest        {item.dest}",
        f"  available   {item.availability}",
    ]
    if item.badge:
        lines.append(f"  badge       {item.badge}")
    if item.caveat:
        # Предупреждение стоит ВЫШЕ шагов намеренно: читатель, который начал
        # выполнять шаги, до конца карточки может не дойти.
        lines.append("")
        lines.append("  CAVEAT")
        for line in item.caveat.splitlines():
            lines.append(f"    {line}")
    lines.append("")
    lines.append("  steps")
    for number, step in enumerate(item.steps, start=1):
        lines.append(f"    {number}. {step}")
    if item.snippet:
        lines.append("")
        lines.append("  snippet")
        for line in item.snippet.splitlines():
            lines.append(f"    {line}")
    if item.verify:
        lines.append("")
        lines.append(f"  verify      {item.verify}")
    if item.note:
        lines.append("")
        lines.append("  note")
        for line in item.note.splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def render_categories(catalog: Catalog) -> str:
    lines = [f"  {'category':12}  {'cards':>5}  title", f"  {'-' * 12}  {'-' * 5}  {'-' * 40}"]
    for cat_id, title in catalog.categories:
        count = len(catalog.in_category(cat_id))
        lines.append(f"  {_short(cat_id, 12):12}  {count:>5}  {_short(title, 46)}")
    lines.append("")
    lines.append(f"  {len(catalog.integrations)} integration(s) in {catalog.path}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Обработчики
# --------------------------------------------------------------------------


def _catalog_from_args(args) -> Catalog:
    from digit_cli.config import load_config

    try:
        config = load_config() or {}
    except Exception:
        config = {}
    return load_catalog(find_catalog(getattr(args, "catalog", None), config=config))


def _emit(args, payload: Any, text: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


def _cmd_list(args) -> int:
    catalog = _catalog_from_args(args)
    items = catalog.in_category(args.category) if args.category else list(catalog.integrations)
    if args.category and not items:
        known = ", ".join(cat for cat, _ in catalog.categories)
        print(f"  error: no category '{args.category}'. Known: {known}", file=sys.stderr)
        return 1
    if args.caveats:
        items = [i for i in items if i.caveat]
    return _emit(args, [i.to_dict() for i in items], render_list(items))


def _cmd_show(args) -> int:
    catalog = _catalog_from_args(args)
    item = catalog.get(args.id)
    return _emit(args, item.to_dict(), render_show(item))


def _cmd_search(args) -> int:
    catalog = _catalog_from_args(args)
    items = catalog.search(" ".join(args.query))
    return _emit(args, [i.to_dict() for i in items], render_list(items))


def _cmd_categories(args) -> int:
    catalog = _catalog_from_args(args)
    payload = [
        {"id": cat, "title": title, "cards": len(catalog.in_category(cat))}
        for cat, title in catalog.categories
    ]
    return _emit(args, payload, render_categories(catalog))


_HANDLERS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "search": _cmd_search,
    "categories": _cmd_categories,
}


def workbench_command(args) -> int:
    """Разослать разобранный вызов ``digit workbench``."""
    sub = getattr(args, "workbench_command", None) or "list"
    handler = _HANDLERS.get(sub)
    if handler is None:
        print(f"Unknown workbench subcommand: {sub}", file=sys.stderr)
        print("Run `digit workbench -h` for usage.", file=sys.stderr)
        return 1
    try:
        return handler(args)
    except UnknownIntegration as exc:
        # Код 2 — «такой карточки нет», отдельно от кода 1 («каталог не
        # прочитался»). Разница существенна для вызывающего: в первом случае
        # надо поправить идентификатор, во втором — чекаут или настройку, и
        # различать их по тексту сообщения он не должен.
        print(f"  no integration '{exc.wanted}' in the catalog.", file=sys.stderr)
        if exc.suggestions:
            print(f"  did you mean: {', '.join(exc.suggestions)}", file=sys.stderr)
        print("  List them with: digit workbench list", file=sys.stderr)
        return 2
    except (CatalogError, CatalogNotFound) as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# Парсер
# --------------------------------------------------------------------------


def add_parser(subparsers) -> argparse.ArgumentParser:
    """Зарегистрировать дерево подкоманды ``workbench``."""
    parser = subparsers.add_parser(
        "workbench",
        help="Digitable Workbench integration catalog (destinations, steps, caveats)",
        description=(
            "The Workbench integration catalog, read straight from the courses "
            "checkout, so the destination path and the connection steps for a "
            "target are looked up instead of recalled.\n\n"
            "Cards flagged ! carry a caveat — a target where copying the files "
            "is not the whole story (a client mod against ToS, an extension "
            "that needs your own signature, a component deliberately kept out "
            "of the paid archive). `show` always prints it, above the steps.\n\n"
            "  digit workbench list\n"
            "  digit workbench list --category editors\n"
            "  digit workbench list --caveats\n"
            "  digit workbench show neovim\n"
            "  digit workbench search tmux\n"
            "  digit workbench categories"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--catalog", default=None,
        help="Path to workbench-integrations.toml, or to the courses checkout "
             "holding it (default: workbench.catalog from config.yaml, else the "
             "courses checkout digit kb already knows about, else a "
             "data/workbench-integrations.toml found from the working directory "
             "upwards)",
    )
    sub = parser.add_subparsers(dest="workbench_command")

    p_list = sub.add_parser("list", help="List integrations")
    p_list.add_argument("--category", default=None, help="restrict to one category")
    p_list.add_argument("--caveats", action="store_true",
                        help="only targets that carry a caveat")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")

    p_show = sub.add_parser("show", help="Show one card: destination, steps, caveat")
    p_show.add_argument("id", help="catalog id, e.g. neovim")
    p_show.add_argument("--json", action="store_true")

    p_search = sub.add_parser("search", help="Search cards, including steps and caveats")
    p_search.add_argument("query", nargs="+", help="substring to look for")
    p_search.add_argument("--json", action="store_true")

    p_cats = sub.add_parser("categories", help="List categories and their card counts")
    p_cats.add_argument("--json", action="store_true")

    # Голое ``digit workbench`` разбирается БЕЗ подпарсера, поэтому в
    # пространстве имён нет ни одного флага, который объявлен только у ``list``
    # — а обработчик по умолчанию именно ``list``. Без этих значений самый
    # естественный для человека вызов падал бы трассировкой на первом же
    # ``args.category``. Значения совпадают с умолчаниями подпарсера ``list``,
    # так что явный ``digit workbench list`` ведёт себя ровно так же.
    parser.set_defaults(
        func=workbench_command, category=None, caveats=False, json=False,
    )
    return parser
