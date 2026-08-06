"""Договорённости встроенного навыка ``workbench-integrations``.

Навык нужен ради одного поведения: смотреть путь назначения в каталоге, а не
вспоминать его. Поэтому здесь закреплены части, которые это поведение несут,
и закреплено то, что навык и команда не разъедутся по кодам возврата и по
обращению с ``caveat``.
"""

from pathlib import Path

import pytest

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import SKILL_PROMPT_DESC_LIMIT, parse_frontmatter
from tools.skill_manager_tool import _validate_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/software-development/workbench-integrations/SKILL.md"


@pytest.fixture(scope="module")
def body() -> str:
    return SKILL.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Текст без переносов строк.

    Утверждение о предложении не должно краснеть от переноса по ширине: это
    поймало бы переформатирование, а не потерю смысла.
    """
    return " ".join(text.split())


def test_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/workbench-integrations" in commands
    assert Path(commands["/workbench-integrations"]["skill_md_path"]) == SKILL


def test_frontmatter_passes_the_new_skill_validator(body: str):
    assert _validate_frontmatter(body, new_skill=True) is None

    meta, _ = parse_frontmatter(body)
    assert meta["name"] == "workbench-integrations"
    # Указатель навыков режет описание, и сигнал, по которому навык вообще
    # выбирается, уезжает в обрез — поэтому длина проверяется, а не
    # поддерживается на глаз.
    assert len(meta["description"]) <= SKILL_PROMPT_DESC_LIMIT


def test_skill_documents_the_exit_codes_the_command_actually_uses(tmp_path, body):
    """Навык обещает: 2 — «нет такой карточки», 1 — «каталог не прочитался».

    Если прозе и коду разойтись, агент прочитает прозу и поставит неверный
    диагноз настоящей поломке, поэтому они закреплены вместе.
    """
    from digit_cli.workbench_cli import workbench_command

    catalog = tmp_path / "catalog.toml"
    catalog.write_text(
        '[[category]]\nid = "editors"\ntitle = "Редакторы"\n'
        '  [[category.item]]\n  id = "neovim"\n  name = "Neovim"\n'
        '  dest = "~/.config/nvim/colors/"\n  steps = ["Скопируйте файлы."]\n',
        encoding="utf-8",
    )

    class _Show:
        workbench_command = "show"
        json = False

        def __init__(self, catalog_path, ident):
            self.catalog = catalog_path
            self.id = ident

    assert workbench_command(_Show(str(catalog), "sublime")) == 2
    assert workbench_command(_Show(str(tmp_path / "missing.toml"), "neovim")) == 1
    assert "exits **2**" in body


def test_skill_tells_the_agent_to_look_up_rather_than_recall(body: str):
    """Вся ценность команды в этом одном предложении: путь назначения
    смотрят, а не вспоминают."""
    assert "Look the answer up; do not recall it." in _flat(body)


def test_skill_forbids_summarising_a_card_without_its_caveat(body: str):
    """Краткий пересказ без предупреждения — не короче, а неверно; это самый
    вероятный способ навредить пользователю через эту команду."""
    section = body[body.index("Read the caveat"):]
    assert "not a shorter answer, it is a wrong one" in _flat(section)
    assert "terms of service" in section


def test_skill_explains_why_no_offline_copy_exists(body: str):
    """Иначе следующий участник «починит» отсутствие оффлайна снимком файла,
    и команда начнёт отвечать вчерашними шагами, ничем этого не показывая."""
    section = body[body.index("Where the catalog lives"):]
    assert "is **not** vendored into Digit" in _flat(section)
    assert "silently" in section
    assert "do not fall back to memory" in _flat(section)


def test_skill_forbids_quoting_a_card_count(body: str):
    """Число карточек нигде не записано руками — ни в каталоге, ни в команде.
    Навык, который назовёт его по памяти, вернёт эту ошибку обратно."""
    assert "Do not quote a card count" in _flat(body)
    assert "digit workbench categories" in body


def test_skill_separates_itself_from_the_digitable_workbench_skill(body: str):
    """Два навыка про Workbench в одном дереве: один про встраивание Digit в
    оболочку, другой про каталог интеграций. Не развести их — значит отдать
    выбор случаю."""
    assert "It is not `digitable-workbench`" in _flat(body)
    other = REPO / "skills/software-development/digitable-workbench/SKILL.md"
    assert other.is_file(), "the skill this one distinguishes itself from is gone"


def test_skill_does_not_promise_to_install_anything(body: str):
    """Копирование файлов в ~/.config — изменение машины пользователя, а не
    поиск по каталогу."""
    section = body[body.index("What this skill does not do"):]
    assert "It does not install anything." in _flat(section)
