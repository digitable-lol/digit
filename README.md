# Digit

**Digit** — открытый локальный ИИ-агент Digitable для терминала, Desktop,
мессенджеров и ACP-совместимых редакторов. Он говорит по-русски и по-английски,
знает структуру портала Digitable, курсов, Workbench и утилит
[`tools.digitable.life`](https://tools.digitable.life/).

[Сайт](https://courses.digitable.life/digit/) ·
[Курсы](https://courses.digitable.life/courses/) ·
[Workbench](https://courses.digitable.life/workbench/) ·
[Issues](https://github.com/digitable-lol/digit/issues)

## Что отличает Digit

- знания о Digitable поставляются как отдельные проверяемые skills;
- создаёт и проверяет исполняемые FTS-спецификации через CLI или MCP;
- по умолчанию используется фирменная тема Digitable и идентичность Digit;
- локальные пресеты рассчитаны на Ollama, LM Studio и Apple Silicon;
- данные, память, ключи и профили лежат отдельно в `~/.digit`;
- команды `digit`, `digit-agent` и `digit-acp` являются основными;
- обновление идёт из [`digitable-lol/digit`](https://github.com/digitable-lol/digit),
  а не из чужого installer/update-канала.

Digit основан на MIT-лицензированном
[Hermes Agent](https://github.com/NousResearch/hermes-agent) и сохраняет
совместимость с его ядром.
## Установка

macOS через Homebrew:

```bash
brew install digitable-lol/tap/digit
```

macOS, Linux и WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/digitable-lol/digit/main/scripts/install.sh | bash
```

Windows PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/digitable-lol/digit/main/scripts/install.ps1)
```

После установки:

```bash
digit setup
digit
```

`digit setup` сначала предлагает русский или английский язык. Рекомендуемый
сценарий настраивает локальную Qwen3.5 4B через Ollama без аккаунта, API-ключа
и облачного провайдера. Язык можно выбрать сразу командой
`digit setup --language ru` или `digit setup --language en`; расширенные
провайдеры остаются в отдельном сценарии настройки.

Установщик клонирует официальный репозиторий Digit, создаёт изолированное
окружение и добавляет команды в пользовательский `PATH`. Повторный запуск
обновляет существующую установку без перезаписи пользовательских настроек.

## Модели

Digit не требует облачного аккаунта конкретного вендора. Базовый путь —
локальная модель:

```bash
# Сначала установите Ollama: https://ollama.com/
ollama pull qwen3.5:4b
digit model digit-local
```

Готовые локальные пресеты:

| Пресет | Модель | Для чего |
| --- | --- | --- |
| `digit-local-small` | Qwen3.5 2B | 8 ГБ памяти, быстрые короткие задачи |
| `digit-local` | Qwen3.5 4B | основной вариант для M1/M2/M3/M4 |
| `digit-local-plus` | Qwen3.5 9B | более сильное рассуждение при 16+ ГБ |
| `digit-gemma` | Gemma 3 4B | альтернативная мультимодальная модель |

### Собственная модель

`digit-router` — модель, обученная под этот агент: она выбирает, какую из 95
утилит вызвать и какой фрагмент корпуса относится к вопросу. Содержание ответа
приходит из утилиты, дословной цитаты или сертификата FTS, поэтому 0,6 млрд
параметров здесь достаточно — модели не нужно ничего знать.

```bash
scripts/local-router.sh          # скачает GGUF и поднимет llama-server
digit model digit-router
```

Замеренное на 250 задачах: ложные ответы 9,2 %, осознанный отказ на red-team
91,3 %. Есть вариант на 1,7 млрд (`digit-router-plus`), но втрое больший размер
покупает 0,6 п.п. отказа — разбор в
[карточке модели](https://huggingface.co/digitable-lol/digit-router-0.6b).

Модель — маршрутизатор, а не источник фактов. Поставить её отвечать напрямую
можно, и тогда она будет выдумывать ровно как любая другая модель такого
размера: гарантию даёт конструкция вокруг, а не веса.

Можно подключить любой свой OpenAI-совместимый endpoint или API-провайдер через
`digit model`. Готовый аккаунт у стороннего hosted-сервиса не требуется и не
показывается как рекомендуемая интеграция.

Подробнее: [выбор моделей](docs/digitable/models.md).

## Где работает

```bash
digit                         # интерактивный CLI
digit --tui                   # полноэкранный терминальный интерфейс
digit desktop                 # Desktop
digit gateway                 # Telegram, Discord, Slack и другие каналы
digit-acp                      # Zed, JetBrains и другие ACP-клиенты
digit dashboard               # локальная web-панель
```

Skills с картой экосистемы находятся в репозитории:

- `digit` — идентичность, язык, приватность и маршрутизация;
- `digitable-portal` — сервисы и домены Digitable;
- `digitable-courses` — учебные маршруты и материалы;
- `digitable-tools` — каталог утилит `tools.digitable.life`;
- `digitable-workbench` — темы, шаблоны и инженерный процесс.

## Workbench

Бесплатная инструкция подключения Digit опубликована в
[каталоге интеграций Workbench](https://courses.digitable.life/workbench/integrations/#integration-digit).
Покупка Workbench для установки или запуска Digit не нужна. Платный пакет
добавляет темы, Compendium и расширенные инженерные шаблоны.

## Хранилище и миграция

Новая установка использует:

```text
~/.digit/
├── config.yaml
├── .env
├── SOUL.md
├── memories/
├── sessions/
├── skills/
└── digit/
```

Digit намеренно не импортирует `~/.digit` автоматически: там могут находиться
секреты и память другого агента. Если вы раньше запускали Digit поверх старого
каталога и хотите перенести данные, сначала закройте оба агента, затем выполните:

```bash
mkdir -p ~/.digit
cp -a ~/.digit/. ~/.digit/
```

Проверьте `~/.digit/.env` и `~/.digit/config.yaml`, после чего запустите
`digit doctor`. Для Docker и нестандартных установок совместимый override
`DIGIT_HOME` продолжает работать.

## Обновление и разработка

Установка через Homebrew обновляется вместе с остальными пакетами:

```bash
brew upgrade digitable-lol/tap/digit
```

Для установки через скрипт используйте встроенное обновление:

```bash
digit update
```

Установка из исходников:

```bash
git clone https://github.com/digitable-lol/digit.git
cd digit
uv venv venv --python 3.11
uv pip install --python venv/bin/python -e '.[all]'
venv/bin/digit
```

Тесты запускаются через репозиторный wrapper:

```bash
scripts/run_tests.sh tests/test_digit_home.py tests/skills/test_digit_distribution.py -q
```

## Лицензия

[MIT](LICENSE). Изменения Digitable и исходный upstream сохраняют атрибуцию,
указанную в истории Git и лицензии проекта.


## Происхождение

Digit — производная работа от <!-- rebrand:keep -->
[Hermes Agent](https://github.com/NousResearch/hermes-agent), <!-- rebrand:keep -->
Copyright (c) 2025 Nous Research, распространяемого под лицензией MIT.
Текст лицензии сохранён без изменений в [LICENSE](LICENSE); указание авторства
Nous Research удалению не подлежит.

Совместимость с ядром сохраняется на уровне архитектуры, но старые команды
удалены. Полный разбор того, что именно сломано снаружи репозитория, кого это
задевает (Zed, Digitable Workbench, Buzz Desktop) и что делать авторам
клиентов — в [BREAKING.md](BREAKING.md). Порядок отката —
в [docs/digitable/rebrand-rollback.md](docs/digitable/rebrand-rollback.md).

Замены:

| Было | Стало |
|---|---|
| `hermes` | `digit` | <!-- rebrand:keep -->
| `hermes-agent` | `digit-agent` | <!-- rebrand:keep -->
| `hermes-acp` | `digit-acp` | <!-- rebrand:keep -->

Переменные окружения `HERMES_*` продолжают работать один минорный релиз: <!-- rebrand:keep -->
они автоматически переносятся на имена `DIGIT_*` с предупреждением.
Каталог данных переехал в `~/.digit`; при первом запуске Digit покажет
команду для переноса, но ничего не скопирует сам — там могут лежать
учётные данные.

Идентификаторы моделей не переименованы: `hermes-4-405b`, <!-- rebrand:keep -->
`NousResearch/Hermes-3-Llama-3.1-70B` и другие — это внешние имена, <!-- rebrand:keep -->
которые уходят в API провайдеров.

Фраза пробуждения осталась `hey hermes`. <!-- rebrand:keep --> Это не название,
которое мы вольны выбрать: так помечена обученная модель openWakeWord,
которая лежит в `tools/wakewords/` и на эту фразу и срабатывает. Переименовать
строку — значит либо обещать в интерфейсе фразу, которой детектор не знает,
либо (на движке `sherpa`, где фраза и есть распознаваемый текст) поменять
то, что нужно произносить, не дав подходящей модели. «Hey Digit» появится
тогда, когда будет обучена модель `hey_digit`; это отдельная работа, а не
переименование.
