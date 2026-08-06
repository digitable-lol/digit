#!/usr/bin/env bash
# Поднять собственную модель Digitable локально и настроить на неё Digit.
#
#   scripts/local-router.sh            # скачать, запустить, напечатать настройку
#   scripts/local-router.sh --stop     # остановить
#   scripts/local-router.sh --status   # что сейчас запущено
#
# Что это за модель. `digit-router-0.6b` — маршрутизатор: он выбирает, какую
# утилиту вызвать и какой фрагмент корпуса относится к вопросу. Содержание
# ответа он не сочиняет и сочинять не должен — оно приходит из детерминированной
# утилиты, дословной цитаты или сертификата FTS. Это единственная причина, по
# которой модель на 0.6B здесь вообще уместна: ей не нужно ничего знать.
#
# Почему 0.6B, а не 1.7B. Обе обучены одним прогоном и обе опубликованы. На
# замере 250 задач втрое больший размер покупает 0.6 п.п. осознанного отказа
# (90.7 % против 91.3 %) и 4 п.п. маршрутизации. За 424 МиБ против 1.2 ГиБ это
# невыгодно, поэтому по умолчанию берётся меньшая; `--model 1.7b` даёт вторую.

set -Eeuo pipefail

model="0.6b"
port="${DIGIT_ROUTER_PORT:-8127}"
action="start"

while [ $# -gt 0 ]; do
  case "$1" in
    --model) model="${2:?--model требует значение: 0.6b или 1.7b}"; shift 2 ;;
    --port)  port="${2:?--port требует значение}"; shift 2 ;;
    --stop)   action="stop"; shift ;;
    --status) action="status"; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

case "$model" in
  0.6b) repo="digitable-lol/digit-router-0.6b" ;;
  1.7b) repo="digitable-lol/digit-router-1.7b" ;;
  *) echo "модель бывает 0.6b или 1.7b, а не «$model»" >&2; exit 2 ;;
esac

dir="${DIGIT_HOME:-$HOME/.digit}/models"
PIDFILE="$dir/router-$model.pid"

alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

if [ "$action" = "status" ]; then
  if alive; then
    echo "роутер $model работает: pid $(cat "$PIDFILE"), порт $port"
  else
    echo "роутер $model не запущен"
  fi
  exit 0
fi

if [ "$action" = "stop" ]; then
  if alive; then
    kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
    echo "остановлен"
  else
    echo "и так не запущен"
  fi
  exit 0
fi

alive && { echo "уже работает на порту $port (pid $(cat "$PIDFILE"))"; exit 0; }

command -v llama-server >/dev/null || {
  cat >&2 <<'EOF'
Нужен llama-server из llama.cpp — он и есть тот OpenAI-совместимый сервер,
к которому подключается Digit.

  brew install llama.cpp          # macOS
  # или сборка из исходников: github.com/ggml-org/llama.cpp

Ollama тоже подойдёт (`ollama serve`), но GGUF придётся импортировать
вручную через Modelfile, поэтому по умолчанию берётся llama.cpp.
EOF
  exit 1
}

mkdir -p "$dir"

# Берётся v3, а не v2, хотя v3 — известная регрессия по маршрутизации. Причина
# в каталоге: он вырос до 95 утилит, и модель, обученная на прежнем составе,
# часть из них физически не может выбрать — для неё их не существует. Лучше
# осторожный роутер, который видит весь набор, чем точный, который слеп на
# четверть. Разбор обеих версий — в карточке модели, § 1.5.
#
# Квантизация Q5_K_M, а не «покрупнее»: на ней сняты числа карточки. Другая
# соберётся и запустится, но тогда измеренные проценты относятся не к тому
# файлу, который у вас работает. Q4_K_M-imat опубликован «для полноты» и
# использовать его карточка прямо не советует.
file="router-$model-v3-Q5_K_M.gguf"
path="$dir/$file"

if [ ! -f "$path" ]; then
  echo "Скачиваю $repo → $path"
  if command -v huggingface-cli >/dev/null; then
    huggingface-cli download "$repo" "gguf/$file" --local-dir "$dir" >/dev/null
    mv "$dir/gguf/$file" "$path" && rmdir "$dir/gguf" 2>/dev/null || true
  else
    curl -fL --progress-bar -o "$path" \
      "https://huggingface.co/$repo/resolve/main/gguf/$file"
  fi
fi

# --parallel 1: роутер вызывается по одному запросу за раз, очередь ему не нужна.
# --ctx-size 8192: индекс категорий укладывается в 536 токенов, схемы одной
# категории — в пару тысяч; восьми тысяч хватает с запасом на историю.
llama-server \
  --model "$path" \
  --port "$port" \
  --ctx-size 8192 \
  --parallel 1 \
  --threads "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 8)" \
  --log-disable >"$dir/router-$model.log" 2>&1 &

echo $! > "$PIDFILE"

# Ждём готовности, а не печатаем «запущено» сразу: сервер поднимает веса
# несколько секунд, и совет «настройте Digit» до этого момента приводит к
# отказу соединения на первом же запросе.
for _ in $(seq 60); do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    cat <<EOF

Роутер $model поднят на http://127.0.0.1:$port

Настройка — в ~/.digit/config.yaml. Правится файлом, а не командой:
\`digit model\` спрашивает провайдера интерактивно и своего endpoint не знает.

  model:
    provider: "llamacpp"
    base_url: "http://127.0.0.1:$port/v1"
    default: "$file"

Проверить, что сервер отвечает:

  curl -s http://127.0.0.1:$port/v1/models

Остановить: $0 --stop

И то, ради чего всё: роутер выбирает инструмент, а содержание ответа даёт
утилита, цитата из корпуса или сертификат FTS. Если поставить эту модель
источником фактов — 0.6B выдумает их с тем же усердием, что любая другая.
EOF
    exit 0
  fi
  sleep 1
done

echo "сервер не ответил за минуту, смотрите $dir/router-$model.log" >&2
exit 1
