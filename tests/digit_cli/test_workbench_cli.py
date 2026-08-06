"""Тесты ``digit workbench`` — каталога интеграций Workbench.

Проверяются не счастливые пути, а три обещания, ради которых команда написана:

* каталог не подделывается — когда чекаута ``courses`` нет, команда отвечает
  ошибкой, а не выдуманным путём назначения;
* карточка без ``dest`` или без шагов отвергается на чтении, потому что
  «сделай, как в карточке» по ней выполнить нельзя;
* ``caveat`` виден всегда и стоит выше шагов.

Числа каталога нигде не заморожены. Тесты, которые ходят в настоящий чекаут,
проверяют отношения (у каждой карточки есть путь назначения, идентификаторы
уникальны), а не «карточек 52»: карточки добавляются, и проверка-снимок
покраснела бы на этом, ничего не защитив.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from digit_cli.workbench_cli import (
    Catalog,
    CatalogError,
    CatalogNotFound,
    find_catalog,
    load_catalog,
    render_list,
    render_show,
    workbench_command,
)

# --------------------------------------------------------------------------
# Приспособления
# --------------------------------------------------------------------------

#: Каталог-образец. Форма взята с настоящего файла: две категории, карточка с
#: предупреждением, карточка-руководство без файлов палитры (``openGuide``) и
#: ключ ``snippetAfter``, которого код не толкует.
_FIXTURE = """
[[category]]
id = "editors"
title = "Редакторы и IDE"

  [[category.item]]
  id = "neovim"
  name = "Neovim"
  gist = "colorscheme на чистом Lua"
  files = "3 файла в `colors/`"
  dest = "~/.config/nvim/colors/"
  steps = [
    "Скопируйте схемы в `~/.config/nvim/colors/`.",
    "Включите truecolor в `init.lua`.",
  ]
  snippetAfter = 2
  snippet = '''
vim.opt.termguicolors = true
'''
  verify = ":colorscheme digitable-focus-carbon"
  note = "Менеджер плагинов не нужен."

[[category]]
id = "apps"
title = "Приложения"

  [[category.item]]
  id = "discord"
  name = "Discord"
  gist = "тема клиента через мод"
  files = "3 файла `.theme.css`"
  dest = "папка тем BetterDiscord"
  badge = "требует мод, против ToS"
  caveat = "Перекрасить клиент можно только клиентским модом, а его использование нарушает условия обслуживания."
  steps = ["Откройте папку тем и скопируйте файл."]

  [[category.item]]
  id = "digit"
  name = "Digit"
  gist = "агент в терминале"
  files = "открытый гайд"
  dest = "конфиг Digit"
  openGuide = true
  steps = ["Прочитайте гайд."]
"""


@pytest.fixture
def catalog_file(tmp_path: Path) -> Path:
    path = tmp_path / "courses" / "data" / "workbench-integrations.toml"
    path.parent.mkdir(parents=True)
    path.write_text(_FIXTURE, encoding="utf-8")
    return path


@pytest.fixture
def catalog(catalog_file: Path) -> Catalog:
    return load_catalog(catalog_file)


class _Args:
    """Разобранный вызов, каким его отдаёт argparse."""

    def __init__(self, sub: str, **kwargs):
        self.workbench_command = sub
        self.catalog = None
        self.json = False
        self.category = None
        self.caveats = False
        for key, value in kwargs.items():
            setattr(self, key, value)


# --------------------------------------------------------------------------
# Каталог не подделывается
# --------------------------------------------------------------------------


def test_catalog_is_not_vendored_into_digit():
    """Копии каталога в дереве Digit быть не должно.

    Копия — это самый дешёвый способ заставить команду работать без чекаута, и
    самый дорогой способ начать отвечать вчерашними шагами: расхождение с
    источником ничем себя не проявляет. Проверка стоит здесь, чтобы следующий
    участник не «починил» отсутствие оффлайна снимком файла.
    """
    repo = Path(__file__).resolve().parent.parent.parent
    copies = [p for p in repo.rglob("workbench-integrations.toml")
              if ".venv" not in p.parts and "node_modules" not in p.parts]
    assert copies == [], f"the catalog was vendored into Digit: {copies}"


def test_missing_checkout_is_an_error_not_a_guess(tmp_path, monkeypatch):
    """Нет чекаута — нет ответа.

    Молчаливый откат на «примерно такой путь» здесь хуже отказа: пользователь
    скопирует файлы не туда и увидит не ошибку, а прежние цвета.
    """
    monkeypatch.setattr(
        "digit_cli.workbench_cli._courses_candidates", lambda: ()
    )
    with pytest.raises(CatalogNotFound) as caught:
        find_catalog(start=tmp_path)
    message = str(caught.value)
    assert "--catalog" in message and "workbench.catalog" in message


def test_explicit_path_accepts_the_file_or_the_checkout(catalog_file: Path):
    assert find_catalog(str(catalog_file)) == catalog_file
    assert find_catalog(str(catalog_file.parent.parent)) == catalog_file


def test_walking_up_finds_the_catalog(catalog_file: Path, monkeypatch):
    """Из подкаталога чекаута каталог обязан находиться сам."""
    monkeypatch.setattr(
        "digit_cli.workbench_cli._courses_candidates", lambda: ()
    )
    deep = catalog_file.parent.parent / "content" / "workbench"
    deep.mkdir(parents=True)
    assert find_catalog(start=deep) == catalog_file


def test_config_key_is_honoured(catalog_file: Path, monkeypatch):
    monkeypatch.setattr(
        "digit_cli.workbench_cli._courses_candidates", lambda: ()
    )
    found = find_catalog(config={"workbench": {"catalog": str(catalog_file)}})
    assert found == catalog_file


# --------------------------------------------------------------------------
# Карточка, по которой нельзя действовать, не карточка
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dropped", ["dest", "steps"])
def test_card_without_destination_or_steps_is_refused(tmp_path, dropped):
    """Карточка без пути назначения или без шагов заставляет догадываться —
    то есть возвращает ровно то поведение, которое команда убирает."""
    body = '[[category]]\nid = "editors"\ntitle = "Редакторы"\n\n' \
           '  [[category.item]]\n  id = "neovim"\n  name = "Neovim"\n'
    if dropped != "dest":
        body += '  dest = "~/.config/nvim/colors/"\n'
    if dropped != "steps":
        body += '  steps = ["Скопируйте файлы."]\n'
    path = tmp_path / "catalog.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(CatalogError) as caught:
        load_catalog(path)
    assert dropped in str(caught.value)


def test_duplicate_ids_are_refused(tmp_path):
    """``show <id>`` адресует карточку одним словом; два одинаковых
    идентификатора сделали бы ответ зависящим от порядка чтения файла."""
    path = tmp_path / "catalog.toml"
    path.write_text(
        '[[category]]\nid = "a"\ntitle = "A"\n'
        '  [[category.item]]\n  id = "vscode"\n  name = "VS Code"\n'
        '  dest = "d"\n  steps = ["s"]\n'
        '[[category]]\nid = "b"\ntitle = "B"\n'
        '  [[category.item]]\n  id = "vscode"\n  name = "VS Code"\n'
        '  dest = "d"\n  steps = ["s"]\n',
        encoding="utf-8",
    )
    with pytest.raises(CatalogError) as caught:
        load_catalog(path)
    assert "duplicate" in str(caught.value)


def test_a_file_that_is_not_the_catalog_says_so(tmp_path):
    path = tmp_path / "other.toml"
    path.write_text('[tool.black]\nline-length = 88\n', encoding="utf-8")
    with pytest.raises(CatalogError) as caught:
        load_catalog(path)
    assert "[[category]]" in str(caught.value)


def test_unknown_keys_survive_into_json(catalog: Catalog):
    """Каталог правит не Digit, и новый ключ появится там раньше, чем здесь.
    Он обязан доехать до вызывающего, а не потеряться при разборе."""
    neovim = catalog.get("neovim")
    assert neovim.extra["snippetAfter"] == 2
    assert neovim.to_dict()["extra"]["snippetAfter"] == 2


# --------------------------------------------------------------------------
# Предупреждение видно всегда
# --------------------------------------------------------------------------


def test_show_prints_the_caveat_above_the_steps(catalog: Catalog):
    """Читатель, начавший выполнять шаги, до конца карточки может не дойти —
    поэтому предупреждение стоит раньше шагов, а не в примечании."""
    body = render_show(catalog.get("discord"))
    assert "CAVEAT" in body
    assert "нарушает условия обслуживания" in body
    assert body.index("CAVEAT") < body.index("steps")


def test_listing_marks_cards_that_carry_a_caveat(catalog: Catalog):
    """Без метки список выглядит однородным, и выбирают из него как из
    равных — а «скопируйте файлы» для такой цели неполно."""
    rendered = render_list(list(catalog.integrations))
    discord_line = next(l for l in rendered.splitlines() if "discord" in l)
    neovim_line = next(l for l in rendered.splitlines() if "neovim" in l)
    assert "!" in discord_line
    assert "!" not in neovim_line
    assert "1 carry a caveat" in rendered


def test_caveats_filter_returns_only_flagged_cards(catalog_file: Path, capsys):
    code = workbench_command(_Args("list", catalog=str(catalog_file),
                                   caveats=True, json=True))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [i["id"] for i in payload] == ["discord"]
    assert payload[0]["caveat"]


def test_json_card_carries_destination_steps_and_caveat(catalog_file: Path, capsys):
    """``--json`` — то, чем карточку читает вызывающий; выбросить из неё
    предупреждение значило бы спрятать его в единственном месте, где его
    точно не увидят глазами."""
    code = workbench_command(_Args("show", catalog=str(catalog_file),
                                   id="discord", json=True))
    assert code == 0
    card = json.loads(capsys.readouterr().out)
    assert card["dest"] == "папка тем BetterDiscord"
    assert card["steps"]
    assert "условия обслуживания" in card["caveat"]


# --------------------------------------------------------------------------
# Поиск и адресация
# --------------------------------------------------------------------------


def test_search_covers_steps_and_caveats_not_just_names(catalog: Catalog):
    """«Что нельзя ставить из-за ToS» не встречается ни в одном имени."""
    assert [i.id for i in catalog.search("ToS")] == ["discord"]
    assert [i.id for i in catalog.search("truecolor")] == ["neovim"]


def test_search_is_case_folded_for_cyrillic(catalog: Catalog):
    """Каталог написан по-русски, и запрос приходит в том регистре, в каком
    его набрал человек. ``lower()`` для кириллицы не хуже, но ``casefold()``
    — то, что здесь обещано, и проверка держит это обещание."""
    assert [i.id for i in catalog.search("СКОПИРУЙТЕ")] == ["neovim", "discord"]
    assert [i.id for i in catalog.search("скопируйте")] == ["neovim", "discord"]


def test_unknown_id_exits_2_and_suggests(catalog_file: Path, capsys):
    """Код 2 отделяет «поправь идентификатор» от кода 1 «поправь чекаут».
    Различать их по тексту сообщения вызывающий не должен."""
    code = workbench_command(_Args("show", catalog=str(catalog_file), id="neovi"))
    assert code == 2
    err = capsys.readouterr().err
    assert "neovim" in err


def test_bare_invocation_lists_instead_of_crashing(catalog_file: Path, capsys):
    """``digit workbench`` без подкоманды разбирается без подпарсера, поэтому
    флагов, объявленных только у ``list``, в пространстве имён нет. Обработчик
    по умолчанию — ``list``, и без умолчаний на родительском парсере самый
    естественный для человека вызов падал трассировкой.

    Проверка идёт через настоящий парсер, а не через самодельный namespace:
    подделка воспроизвела бы форму, которой ошибка как раз и не имеет.
    """
    import argparse

    from digit_cli.workbench_cli import add_parser

    root = argparse.ArgumentParser()
    add_parser(root.add_subparsers(dest="command"))
    args = root.parse_args(["workbench", "--catalog", str(catalog_file)])

    assert args.func(args) == 0
    assert "neovim" in capsys.readouterr().out


def test_unreadable_catalog_exits_1(tmp_path, capsys):
    code = workbench_command(_Args("list", catalog=str(tmp_path / "nope.toml")))
    assert code == 1
    assert "error" in capsys.readouterr().err


def test_open_guide_cards_are_labelled_as_such(catalog: Catalog):
    """Пять карточек — открытые руководства без файлов палитры. Обещать за
    них содержимое платного архива нельзя."""
    assert catalog.get("digit").availability == "open guide"
    assert catalog.get("neovim").availability == "in archive"


# --------------------------------------------------------------------------
# Настоящий каталог
# --------------------------------------------------------------------------


def _real_catalog():
    try:
        return load_catalog(find_catalog())
    except (CatalogNotFound, CatalogError):
        return None


_REAL = _real_catalog()

real_catalog_only = pytest.mark.skipif(
    _REAL is None,
    reason="the courses checkout that holds the catalog is not on this machine",
)


@real_catalog_only
def test_every_real_card_can_be_acted_on():
    """Отношение, а не снимок: сколько бы карточек ни стало, у каждой есть
    путь назначения и шаги, иначе ответ по ней был бы догадкой."""
    for item in _REAL.integrations:
        assert item.dest, item.id
        assert item.steps, item.id


@real_catalog_only
def test_every_real_card_is_addressable_and_findable():
    ids = [i.id for i in _REAL.integrations]
    assert len(ids) == len(set(ids))
    for item in _REAL.integrations:
        assert _REAL.get(item.id) is item
        assert item in _REAL.search(item.id)


@real_catalog_only
def test_real_caveats_reach_the_rendered_card():
    """Ключ ``caveat`` в самом файле помечен как тот, что прятать нельзя."""
    flagged = [i for i in _REAL.integrations if i.caveat]
    assert flagged, "the real catalog has caveats; the parser dropped them"
    for item in flagged:
        body = render_show(item)
        assert "CAVEAT" in body
        assert body.index("CAVEAT") < body.index("  steps")
