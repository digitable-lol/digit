#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Прогон тяжёлого набора не здесь, а на большой машине.
#
#   scripts/test-remote.sh                       # весь набор через run_tests.sh
#   scripts/test-remote.sh tests/agent/          # только один каталог
#   scripts/test-remote.sh -- ruff check .       # произвольная команда
#
# Зачем. `scripts/run_tests.sh` поднимает по отдельному процессу `pytest` на
# КАЖДЫЙ файл набора, а файлов под две сотни. На восьми ядрах локальной машины
# это десятки процессов разом и load average в сотню: работать за ней в это
# время нельзя. На `dev` 256 ядер и 499 ГБ, и тот же набор там никому не мешает.
#
# Хост — алиас из ~/.ssh/config, переменная DIGIT_REMOTE (по умолчанию `dev`).
#
# Что уезжает. Рабочее дерево целиком, БЕЗ `.venv`, `node_modules` и кешей: venv
# собирается на месте своим интерпретатором, и везти чужие бинарники не только
# зря, но и вредно. Вместе с деревом уезжает каталог `.git`: часть проверок
# читает историю, и без неё они краснеют на ровном месте. Каталог берётся общий
# (`--git-common-dir`), поэтому скрипт работает и из `git worktree`.
#
# Локальное дерево не меняется ничем из этого скрипта; на хосте меняется только
# ~/$DIGIT_REMOTE_DIR.

set -euo pipefail

HOST="${DIGIT_REMOTE:-dev}"
REMOTE_DIR="${DIGIT_REMOTE_DIR:-digit-remote}"
ROOT=$(git rev-parse --show-toplevel)
GITDIR=$(cd -- "$(git rev-parse --git-common-dir)" && pwd)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "${1:-}" = -- ]; then
  shift
  CMD="$*"
else
  CMD="scripts/run_tests.sh $*"
fi

# `--locked` — как в CI: расхождение uv.lock с pyproject.toml обязано быть
# видно, а не молча разрешаться на ходу. Прогон на разъехавшемся замке измеряет
# не то дерево, которое поедет в CI.
#
# На 9 августа 2026 замок в main РАЗЪЕХАЛСЯ: pyproject.toml менялся в ba94018eb
# позже, чем uv.lock в f580c6c6a, и `uv sync --locked` останавливается здесь же,
# где остановится CI. Чинится это одной командой `uv lock` в отдельном коммите,
# а не тихим послаблением в этом скрипте. Пока замок не пересобран, разовый
# обход — DIGIT_REMOTE_RELOCK=1: замок пересчитывается НА ХОСТЕ, локальное
# дерево не меняется, и прогон становится возможен, не выдавая себя за CI.
if [ -n "${DIGIT_REMOTE_RELOCK:-}" ]; then
  SYNC_MODE=""
  echo "ВНИМАНИЕ: DIGIT_REMOTE_RELOCK=1 — замок пересобирается на хосте, это НЕ прогон, совпадающий с CI"
else
  SYNC_MODE="--locked"
fi

echo "хост: $HOST, каталог: ~/$REMOTE_DIR, ветка: $BRANCH"
echo "команда: $CMD"

ssh "$HOST" "mkdir -p ~/$REMOTE_DIR"

# Дерево. --delete, чтобы удалённая копия была копией, а не наслоением прогонов;
# .venv и node_modules исключены и потому защищены от --delete отдельно.
#
# `.digit-runtime` и `.env*` исключены отдельно и по разным причинам:
# первое — рабочее состояние запущенного агента (там же файлы-замки, которые
# принадлежат ДРУГОЙ учётной записи и нечитаемы для нас: без исключения rsync
# падает с кодом 23 ещё до первого теста), второе — секреты, которым на чужой
# машине делать нечего. Оба каталога и так в .gitignore.
rsync -az --delete \
  --exclude .git --exclude .venv --exclude venv --exclude node_modules \
  --exclude __pycache__ --exclude '*.pyc' --exclude .pytest_cache \
  --exclude .pytest-cache --exclude .ruff_cache --exclude .mypy_cache \
  --exclude .digit-runtime --exclude '.env' --exclude '.env.*' \
  --filter 'protect .venv' --filter 'protect node_modules' \
  "$ROOT/" "$HOST:$REMOTE_DIR/"

# История. Уезжает общий каталог, а HEAD на хосте переставляется на нашу ветку:
# в worktree HEAD общего каталога показывает на ЧУЖУЮ ветку (ту, что в основном
# рабочем дереве), и без этой строки `git status` на хосте объявил бы все файлы
# изменёнными.
rsync -az --delete "$GITDIR/" "$HOST:$REMOTE_DIR/.git/"
ssh "$HOST" "cd $REMOTE_DIR \
  && git config core.bare false \
  && git symbolic-ref HEAD refs/heads/'$BRANCH' \
  && git reset --mixed --quiet \
  && git status --short | head -5"

# Окружение на хосте собирается ровно той командой, что в CI
# (`.github/workflows/tests.yml`, шаг «Install dependencies»), и переписывать её
# здесь своими словами нельзя: смысл этого скрипта — прогон, совпадающий с CI,
# а venv, собранный иначе, даёт другой набор установленных пакетов и другой
# результат. Дополнения сверх all/dev — те, что тесты используют по-настоящему:
# hermetic-режим запрещает доустановку на ходу (DIGIT_DISABLE_LAZY_INSTALLS=1),
# поэтому SDK должны лежать в venv заранее.
#
# `uv sync --locked` ставит точный набор из uv.lock и сам создаёт .venv —
# отдельный `uv venv` не нужен и вреден: на существующем каталоге он не молчит,
# а падает с «A virtual environment already exists», и при `set -e` это обрывало
# бы прогон до первого теста.
#
# Python 3.11 — как в CI. digit требует >=3.11,<3.14, а системный в Ubuntu 26.04
# уже 3.14, поэтому интерпретатор берётся у uv, а не из системы.
#
# ~/.local/bin в PATH — там лежит uv, который ставит devenv/bootstrap.sh, а
# неинтерактивный ssh его PATH не наследует. TZ/LANG/PYTHONHASHSEED задаёт сам
# run_tests.sh, здесь только локаль на случай произвольной команды. Ключи API
# гасятся явно — тем же списком, что в CI: тесты не должны ходить в сеть.
ssh "$HOST" "cd $REMOTE_DIR \
  && export PATH=\$HOME/.local/bin:\$PATH LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  && export OPENROUTER_API_KEY= OPENAI_API_KEY= NOUS_API_KEY= \
  && uv python install 3.11 >/dev/null \
  && uv sync $SYNC_MODE --python 3.11 --extra all --extra dev --extra anthropic \
       --extra mistral --extra fal --extra modal --extra daytona \
       --extra hindsight --extra parallel-web >/dev/null \
  && $CMD"
