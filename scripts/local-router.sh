#!/usr/bin/env bash
# Совместимость: то же самое умеет `digit local`, и умеет лучше.
#
#   scripts/local-router.sh            → digit local start --model router
#   scripts/local-router.sh --stop     → digit local stop
#   scripts/local-router.sh --status   → digit local status
#
# Почему логика уехала в digit_cli/local_model.py. Скрипт требовал, чтобы
# llama-server уже стоял в системе, и при его отсутствии просто выходил с
# советом «соберите сами» — то есть ровно в тот момент, ради которого его и
# запускали. Ставить бинарник, сверять его хеш и знать, какое окно контекста
# Digit вообще примет, — это работа для кода, который живёт рядом с самим
# Digit, а не для отдельной оболочки, которая обо всём этом не знает.
#
# Отдельно про роутер: основной моделью агента он быть не может — обучен на
# окне 40 960 токенов, а Digit требует не меньше 64 000 и отвергает такую
# конфигурацию на старте. `digit local start --model router` об этом
# предупреждает; запускать его имеет смысл для вспомогательных задач.

set -Eeuo pipefail

action="start"
while [ $# -gt 0 ]; do
  case "$1" in
    --stop)   action="stop"; shift ;;
    --status) action="status"; shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    # --model/--port раньше принимались здесь; теперь их разбирает `digit local`,
    # и передавать их дальше как есть честнее, чем молча ронять.
    *) break ;;
  esac
done

command -v digit >/dev/null || {
  echo "Нужна команда digit — этот скрипт лишь обёртка над \`digit local\`." >&2
  exit 1
}

case "$action" in
  stop)   exec digit local stop "$@" ;;
  status) exec digit local status "$@" ;;
  *)      exec digit local start --model router "$@" ;;
esac
