"""Адрес публикации docker-образа нельзя поменять молча.

Доктрина «Digit не публикует образ» записана не в одном месте, а в трёх, и до
этого файла ни одна проверка их не связывала:

* ``.github/workflows/docker.yml`` — задания ``publish``/``merge`` пушат в
  чужое пространство имён ``nousresearch/hermes-agent`` и гейтятся на
  ``github.repository == 'NousResearch/hermes-agent'``, то есть у нас не
  запускаются никогда;
* ``digit_cli/config.py`` — пользователю на ``digit update`` в докере
  ВОЗВРАЩАЕТСЯ строка ``docker compose build …`` вместо ``docker pull``, и
  причина в комментарии ССЫЛАЕТСЯ на гейт из docker.yml дословно;
* ``docker-compose.yml`` — ``build: .`` + ``image: digit``.

Промах здесь молчалив по устройству. Гейт, ставший ложным, не роняет сборку:
задание просто ПРОПУСКАЕТСЯ, а пропущенное задание в интерфейсе GitHub
зелёное — молчание неотличимо от успеха. Обратная правка так же тиха: снять
гейт или поменять одно слово в имени образа — и наш пуш уйдёт в чужой реестр,
не сказав ни строчки, а config.py продолжит рассказывать пользователю про
поведение, которого уже нет.

Тесты ниже не решают, КУДА публиковать (это решение владельца: GHCR обошёлся
бы без секретов, но меняет доктрину). Они требуют одного — чтобы адрес был
назван явно, был один и тот же во всех трёх местах, и чтобы любая его правка
роняла сборку вместо того, чтобы проходить незамеченной.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO / ".github/workflows/docker.yml"
CONFIG_PY = REPO / "digit_cli/config.py"
COMPOSE = REPO / "docker-compose.yml"

#: Наш репозиторий и чужой. Пишутся здесь дословно: если строка разъедется с
#: workflow, тест обязан покраснеть, а не подстроиться.
OURS = "digitable-lol/digit"
UPSTREAM = "NousResearch/hermes-agent"

#: Адрес публикации — чужое пространство имён, в нижнем регистре, как его
#: пишет Docker Hub.
UPSTREAM_IMAGE = "nousresearch/hermes-agent"

#: Имя локально собираемого образа: наше, без пространства имён, наружу не
#: уходит. То же, что в docker-compose*.yml.
LOCAL_IMAGE = "digit"


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_адрес_публикации_объявлен_один_раз_и_отдельно_от_локального(workflow):
    """Одно имя на две роли — это и есть «перепутать молча». Раньше единственный
    ``IMAGE_NAME`` был и адресом публикации, и тегом локального образа нашего
    задания ``build``: наша сборка носила чужое имя, и правка одной строки env
    меняла сразу обе роли."""
    assert f"PUBLISH_IMAGE_NAME: {UPSTREAM_IMAGE}" in workflow, (
        "адрес публикации должен быть объявлен под собственным именем "
        "PUBLISH_IMAGE_NAME"
    )
    assert f"TEST_IMAGE_NAME: {LOCAL_IMAGE}" in workflow, (
        "локально собираемый образ должен быть объявлен под собственным именем "
        "TEST_IMAGE_NAME"
    )
    assert "IMAGE_NAME: nousresearch" not in workflow.replace(
        "PUBLISH_IMAGE_NAME: nousresearch", ""
    ), "вернулось единственное имя на две роли"


def test_наша_сборка_не_носит_чужого_имени(workflow):
    """Задание ``build`` грузит образ в локальный демон (``load: true``) и
    наружу не отдаёт. Тег на нём обязан быть наш: чужое имя на своей сборке —
    это ровно та путаница, из-за которой адрес публикации и оказался незаметен."""
    build = workflow.split("\njobs:", 1)[1].split("\n  publish:")[0]
    assert UPSTREAM_IMAGE not in build, (
        f"задание build упоминает чужой образ {UPSTREAM_IMAGE!r} — "
        "оно ничего не публикует и не должно его знать"
    )
    # Не только само имя, но и ССЫЛКА на переменную с ним. Без этой строки
    # проверка дырява ровно тем способом, против которого заведена: подставить
    # в тег сборки ``${{ env.PUBLISH_IMAGE_NAME }}`` — и дословного
    # «nousresearch/hermes-agent» в задании нет, а чужое имя на нашей сборке
    # снова есть. Поймано нарочной поломкой при заведении файла.
    assert "PUBLISH_IMAGE_NAME" not in build, (
        "задание build ссылается на переменную адреса публикации — "
        "оно грузит образ локально (load: true) и знать её не должно"
    )
    assert "${{ env.TEST_IMAGE_NAME }}:test" in build


def test_каждое_пушащее_задание_гейтится_на_апстрим(workflow):
    """Пуш разрешён только там, где стоит апстримовый гейт. Проверяется не
    комментарий, а связка: задание, в котором есть ``push=true`` или логин по
    секретам DOCKERHUB_*, обязано нести ``if:`` с апстримовым репозиторием."""
    # Заголовки заданий: две пробела + имя + двоеточие, и только ПОСЛЕ ``jobs:``
    # — на том же отступе стоят триггеры в ``on:`` (push, release,
    # workflow_call), и без отсечки они попадали бы в список заданий.
    parts = re.split(r"^  (\w[\w-]*):$", workflow.split("\njobs:", 1)[1], flags=re.M)[1:]
    jobs = dict(zip(parts[::2], parts[1::2], strict=True))
    assert set(jobs) == {"build", "publish", "merge"}, sorted(jobs)

    for name, body in jobs.items():
        pushes = "push=true" in body or "secrets.DOCKERHUB_" in body
        gated_upstream = f"github.repository == '{UPSTREAM}'" in body
        if pushes:
            assert gated_upstream, (
                f"задание {name!r} публикует, но не гейтится на {UPSTREAM!r}: "
                "пуш ушёл бы в чужой реестр"
            )
        else:
            assert not gated_upstream, (
                f"задание {name!r} ничего не публикует, но прибито к апстриму — "
                "у нас оно не запустится никогда и молча"
            )


def test_наш_репозиторий_никогда_не_адрес_публикации(workflow):
    """Пока владелец не решил иначе, Digit образ не публикует. Появление нашего
    имени рядом с пушем означало бы, что доктрина сменилась побочным
    эффектом."""
    for line in workflow.splitlines():
        if "push=true" in line or "PUBLISH_IMAGE_NAME:" in line:
            assert OURS not in line and "ghcr.io" not in line, (
                f"адрес публикации сменился на наш: {line.strip()!r}. Это "
                "решение владельца — оно меняет доктрину «Digit не публикует "
                "образ», на которую ссылаются digit_cli/config.py и "
                "docker-compose.yml, и требует правки их обоих плюс четырёх "
                "тестов, которые доктрину держат."
            )


def test_config_py_ссылается_на_гейт_который_есть_на_самом_деле(workflow):
    """Пользователю на ``digit update`` в докере config.py возвращает
    ``docker compose build`` и объясняет почему, ЦИТИРУЯ гейт из docker.yml.
    Цитата ничем не проверялась: правка гейта оставила бы config.py
    рассказывать про поведение, которого уже нет."""
    config = CONFIG_PY.read_text(encoding="utf-8")
    quoted = f"``github.repository == '{UPSTREAM}'``"
    assert quoted in config, (
        f"digit_cli/config.py больше не цитирует гейт {quoted} — "
        "либо цитату потеряли, либо доктрину сменили"
    )
    assert f"github.repository == '{UPSTREAM}'" in workflow, (
        "config.py цитирует гейт, которого в docker.yml нет"
    )
    # И само обещание пользователю. Проверяется ЗАПУСКОМ, а не чтением файла:
    # в тексте config.py строка ``docker pull nousresearch/hermes-agent``
    # законно присутствует — в комментарии, объясняющем, что тут стояло раньше
    # и почему убрано. Грепом эти два случая неразличимы, вызовом — различимы.
    from digit_cli.config import (
        format_docker_update_message,
        recommended_update_command_for_method,
    )

    command = recommended_update_command_for_method("docker")
    assert command == "docker compose build && docker compose up -d --force-recreate"
    assert "docker pull" not in command

    message = format_docker_update_message()
    assert f"docker pull {UPSTREAM_IMAGE}" not in message
    assert "docker compose build" in message


def test_compose_собирает_локально_а_не_тянет_образ():
    """Третье место доктрины. ``image: digit`` здесь — то же имя, каким
    workflow тегает локальную сборку."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "build: ." in compose
    assert f"image: {LOCAL_IMAGE}" in compose
    assert UPSTREAM_IMAGE not in compose
