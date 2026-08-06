# Откуда взялись эти файлы

Всё в этом каталоге — чужой код, положенный сюда целиком и намеренно, чтобы
холст открывался без сети. Ничего из этого не правится руками: при обновлении
файлы заменяются целиком по шагам ниже.

| файл | пакет | версия | лицензия |
|---|---|---|---|
| `excalidraw.production.min.js` | `@excalidraw/excalidraw` | 0.17.6 | MIT (`LICENSE.excalidraw.txt`) |
| `excalidraw-assets/**` | `@excalidraw/excalidraw` | 0.17.6 | MIT |
| `react.production.min.js` | `react` | 18.3.1 | MIT (`LICENSE.react.txt`) |
| `react-dom.production.min.js` | `react-dom` | 18.3.1 | MIT |

`excalidraw.production.min.js.LICENSE.txt` и
`excalidraw-assets/vendor-*.LICENSE.txt` собраны сборщиком Excalidraw — это
уведомления пакетов, вкомпилированных внутрь бандла. Удалять их нельзя:
без них раздача бандла нарушает условия этих пакетов.

## Почему 0.17.6, а не 0.18.1

0.18.x — последняя версия, но она **не даёт браузерной сборки**. Её `dist/prod`
это ESM с голыми импортами (`react`, `jotai`, `roughjs/bin/rough`,
`@radix-ui/react-popover`, `perfect-freehand`, ещё с десяток), то есть пакет
рассчитан на сборщик: без npm-установки и шага сборки страница его не загрузит.
0.17.6 — последняя версия с UMD-сборкой (`excalidraw.production.min.js`,
глобали `window.React`, `window.ReactDOM`), поэтому холст здесь это три тега
`<script>` и ноль шагов сборки.

Цена выбора известна и она невелика: формат `.excalidraw` между 0.17 и 0.18 не
ломался, файл, сохранённый здесь, открывается на excalidraw.com и наоборот.
Перейти на 0.18 можно только вместе со сборщиком — это отдельная работа, и
пока её нет, версия зафиксирована здесь честно, а не «примерно последняя».

## Почему локали лежат все 53, а не только `ru-RU`

Локаль подгружается по требованию из `excalidraw-assets/locales/`. Если
положить только русскую, переключатель языка в интерфейсе останется на месте и
будет работать молча-неправильно: выбор любого другого языка даёт 404 и откат
на английский без единого слова. Оставлять в интерфейсе кнопку, которая врёт, —
хуже, чем 1.2 МБ данных. Английский в бандле встроен, отдельного файла у него
нет.

## Как обновить

```bash
npm pack @excalidraw/excalidraw@<версия>   # проверить, что в dist/ есть UMD-сборка
tar xzf excalidraw-excalidraw-<версия>.tgz
cp package/dist/excalidraw.production.min.js            vendor/
cp package/dist/excalidraw.production.min.js.LICENSE.txt vendor/
cp -r package/dist/excalidraw-assets                    vendor/

npm pack react@<версия> react-dom@<версия>
cp package/umd/react.production.min.js      vendor/
cp package/umd/react-dom.production.min.js  vendor/
```

После обновления обязателен прогон `tests/skills/test_excalidraw_canvas.py`:
там есть тест, который держит соответствие между версиями в этой таблице и
тем, что реально лежит в каталоге, — иначе таблица тихо устареет.

Путь к ассетам странице задаёт `window.EXCALIDRAW_ASSET_PATH`. Если его не
задать, бандл ходит за шрифтами и локалями на `unpkg.com`, то есть холст
молча перестаёт быть оффлайновым. Значение выставляется в `index.html`.
