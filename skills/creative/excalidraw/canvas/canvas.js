/* Холст: страница читает и пишет тот же файл .excalidraw, который правит агент.
 *
 * Вся сложность здесь — не в рисовании (его целиком делает Excalidraw), а в
 * том, что у файла два писателя. Отсюда три правила, каждое написано против
 * конкретной молчаливой потери работы:
 *
 * 1. Сохраняем не по всякому onChange. Excalidraw зовёт onChange и на выделение,
 *    и на панораму, и на зум. Если писать файл по каждому такому событию, файл
 *    будет меняться, когда владелец просто смотрит на схему, — а агент увидит
 *    череду правок, которых не было. Признаком правки считается подпись сцены:
 *    id + version элементов. version Excalidraw двигает сам и только при
 *    настоящем изменении элемента.
 *
 * 2. Сохраняем с отпечатком того состояния, из которого исходили. Сервер
 *    сравнит его с диском и откажет, если агент успел поправить файл. Отказ —
 *    это не ошибка сохранения, а вопрос владельцу, чью версию оставить.
 *
 * 3. Подтягиваем правки агента, только когда у владельца нет несохранённого.
 *    Иначе updateScene затёр бы то, что он рисует прямо сейчас, — то есть
 *    ровно то, чего мы боимся, но со своей стороны.
 */

(function () {
  "use strict";

  var h = React.createElement;
  var Lib = window.ExcalidrawLib;

  var TOKEN = new URLSearchParams(window.location.search).get("t") || "";
  var SAVE_DEBOUNCE_MS = 800;
  var POLL_MS = 2000;

  var statusEl = document.getElementById("status");
  var bannerEl = document.getElementById("banner");

  var api = null;
  // Отпечаток состояния, из которого исходит вкладка. Всё сохранение опирается
  // на него: пока он совпадает с диском, писать безопасно.
  var baseFingerprint = "";
  // Подпись сцены на момент последней удачной записи.
  var savedSignature = "";
  // Дешёвый ключ последнего разобранного onChange — чтобы не сериализовать
  // сцену на каждое движение мыши.
  var lastCheapKey = "";
  // Ключ 'source' берём из файла: сериализатор Excalidraw подставляет туда
  // origin страницы, а он у нас меняется с портом при каждом запуске — файл
  // отличался бы от вчерашнего одним этим полем.
  var documentSource = "digit";
  var saveTimer = null;
  var saving = false;
  var paused = false; // расхождение с диском: до ответа владельца не пишем
  var polling = null;

  function setStatus(text, state) {
    statusEl.textContent = text;
    if (state) {
      statusEl.setAttribute("data-state", state);
    } else {
      statusEl.removeAttribute("data-state");
    }
  }

  function request(path, options) {
    options = options || {};
    options.headers = Object.assign({ "X-Canvas-Token": TOKEN }, options.headers || {});
    return fetch(path, options);
  }

  /* Что считается правкой — два уровня, и оба нужны.
   *
   * Дешёвый ключ (id + version элементов и те поля appState, которые вообще
   * попадают в файл) отсекает подавляющее большинство вызовов onChange:
   * выделение, панораму, зум. Их Excalidraw шлёт десятками в секунду, и
   * сериализовать сцену на каждый было бы расточительно.
   *
   * Но одного version мало. Excalidraw двигает version и при чисто служебной
   * нормализации: восстановление сцены при открытии всегда пересобирает
   * текстовые элементы, и version у них растёт, хотя на холсте ничего не
   * изменилось. Если верить только ему, каждое открытие файла — это запись,
   * и файл, который владелец лишь открыл посмотреть, уходит в историю как
   * правка. Поэтому решает содержательная подпись: те же элементы без полей
   * учёта изменений (version, versionNonce, updated). */
  var DOCUMENT_APP_STATE = [
    "viewBackgroundColor", "gridSize", "name",
    "exportBackground", "exportEmbedScene", "exportScale", "exportWithDarkMode",
  ];
  var BOOKKEEPING = { version: true, versionNonce: true, updated: true };

  function appStatePart(appState) {
    return DOCUMENT_APP_STATE.map(function (key) {
      return key + "=" + JSON.stringify((appState || {})[key]);
    }).join(",");
  }

  function cheapKey(elements, appState, files) {
    var parts = [];
    for (var i = 0; i < elements.length; i++) {
      parts.push(elements[i].id + ":" + elements[i].version + (elements[i].isDeleted ? ":d" : ""));
    }
    return parts.join(",") + "|" + Object.keys(files || {}).sort().join(",") +
      "|" + appStatePart(appState);
  }

  function signature(elements, appState, files) {
    var parts = [];
    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      var copy = {};
      Object.keys(el).sort().forEach(function (key) {
        if (!BOOKKEEPING[key]) copy[key] = el[key];
      });
      parts.push(JSON.stringify(copy));
    }
    return parts.join("\n") + "|" + Object.keys(files || {}).sort().join(",") +
      "|" + appStatePart(appState);
  }

  function currentDocument() {
    var elements = api.getSceneElements();
    var appState = api.getAppState();
    var files = api.getFiles();
    var doc = JSON.parse(Lib.serializeAsJSON(elements, appState, files, "local"));
    doc.source = documentSource;
    return { doc: doc, signature: signature(elements, doc.appState, files) };
  }

  function scheduleSave() {
    if (paused) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(save, SAVE_DEBOUNCE_MS);
  }

  /* force=true — владелец ответил «оставить моё», увидев расхождение. Тогда
   * сохраняем без сверки отпечатка, но только по его нажатию: молча такого
   * никогда не делаем. */
  function save(force) {
    if (saving) {
      scheduleSave();
      return;
    }
    var snapshot = currentDocument();
    saving = true;
    setStatus("сохраняю…", "dirty");
    request("/api/doc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document: snapshot.doc,
        base: force ? null : baseFingerprint,
      }),
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { status: response.status, payload: payload };
        });
      })
      .then(function (result) {
        saving = false;
        if (result.status === 200) {
          baseFingerprint = result.payload.fingerprint;
          savedSignature = snapshot.signature;
          hideBanner();
          paused = false;
          setStatus("сохранено");
          return;
        }
        if (result.status === 409) {
          // Файл на диске ушёл вперёд — правил агент. Решает владелец.
          showConflict(result.payload.fingerprint);
          return;
        }
        setStatus("не сохранено: " + (result.payload.error || result.status), "error");
      })
      .catch(function (error) {
        saving = false;
        setStatus("нет связи с холстом: " + error, "error");
      });
  }

  function hideBanner() {
    bannerEl.hidden = true;
    bannerEl.textContent = "";
  }

  function showConflict() {
    paused = true;
    setStatus("не сохранено", "dirty");
    bannerEl.hidden = false;
    bannerEl.textContent = "";
    var text = document.createElement("span");
    text.textContent =
      "Файл на диске изменил кто-то другой (обычно это агент), а у вас есть " +
      "несохранённые правки. Оставить можно только одну версию.";
    var takeTheirs = document.createElement("button");
    takeTheirs.textContent = "Взять версию с диска";
    takeTheirs.onclick = function () {
      hideBanner();
      paused = false;
      reload();
    };
    var keepMine = document.createElement("button");
    keepMine.textContent = "Оставить мою и записать";
    keepMine.onclick = function () {
      hideBanner();
      paused = false;
      save(true);
    };
    bannerEl.appendChild(text);
    bannerEl.appendChild(takeTheirs);
    bannerEl.appendChild(keepMine);
  }

  /* Элементы с диска нельзя отдавать приложению как есть.
   *
   * initialData прогоняет их через restore сам, а updateScene — нет: он ждёт
   * уже полноценные элементы. Агент же пишет минимум полей (это правильно —
   * см. revise.py), и у текста, написанного агентом, нет ни lineHeight, ни
   * originalText. Такой элемент лежит в файле, но на холст не попадает: ни
   * ошибки, ни следа. Проверено вживую — правка агента доезжала, а
   * добавленная им подпись появлялась только после перезагрузки вкладки.
   *
   * Побочно это же нужно для подписи сцены: сравнивать надо восстановленные
   * элементы с восстановленными, иначе первое же открытие файла выглядит как
   * правка и сохранение переписывает файл, который владелец только открыл. */
  function restored(elements) {
    return Lib.restoreElements(elements || [], null);
  }

  function applyDocument(doc, fingerprint) {
    var appState = Object.assign({}, doc.appState || {});
    // collaborators в сохранённом файле — обычный объект, а внутри приложения
    // это Map; передавать его в updateScene нельзя.
    delete appState.collaborators;
    var elements = restored(doc.elements);
    api.updateScene({ elements: elements, appState: appState });
    if (doc.files && Object.keys(doc.files).length) {
      api.addFiles(Object.keys(doc.files).map(function (k) { return doc.files[k]; }));
    }
    baseFingerprint = fingerprint;
    savedSignature = signature(elements, doc.appState, doc.files || {});
    lastCheapKey = cheapKey(elements, doc.appState, doc.files || {});
    if (doc.source) documentSource = doc.source;
  }

  function reload() {
    return request("/api/doc")
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (payload.error) {
          setStatus("файл не читается: " + payload.error, "error");
          return;
        }
        applyDocument(payload.document, payload.fingerprint);
        setStatus("обновлено с диска");
      });
  }

  /* Опрос диска. Он нужен ради главного в этой петле: владелец должен увидеть
   * доводку агента, не перезагружая вкладку и не нажимая ничего. */
  function startPolling() {
    polling = setInterval(function () {
      if (saving) return;
      request("/api/fingerprint")
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          if (!payload.fingerprint || payload.fingerprint === baseFingerprint) return;
          var snapshot = currentDocument();
          if (snapshot.signature !== savedSignature) {
            if (!paused) showConflict();
            return;
          }
          reload();
        })
        .catch(function () { /* сервер закрыли — молчим, статус скажет при записи */ });
    }, POLL_MS);
  }

  request("/api/doc")
    .then(function (r) { return r.json(); })
    .then(function (payload) {
      if (payload.error) {
        setStatus("файл не читается: " + payload.error, "error");
        return;
      }
      var doc = payload.document;
      documentSource = doc.source || "digit";
      baseFingerprint = payload.fingerprint;
      var initialElements = restored(doc.elements);
      savedSignature = signature(initialElements, doc.appState, doc.files || {});
      lastCheapKey = cheapKey(initialElements, doc.appState, doc.files || {});
      var appState = Object.assign({}, doc.appState || {});
      delete appState.collaborators;
      var root = ReactDOM.createRoot(document.getElementById("canvas"));
      root.render(
        h(
          "div",
          { style: { width: "100%", height: "100%" } },
          h(Lib.Excalidraw, {
            langCode: "ru-RU",
            initialData: {
              elements: initialElements,
              appState: appState,
              files: doc.files || {},
              scrollToContent: true,
            },
            excalidrawAPI: function (instance) { api = instance; },
            onChange: function (elements, liveAppState, files) {
              var key = cheapKey(elements, liveAppState, files);
              if (key === lastCheapKey) return;
              lastCheapKey = key;
              if (signature(elements, liveAppState, files) === savedSignature) return;
              setStatus("есть несохранённое", "dirty");
              scheduleSave();
            },
          })
        )
      );
      setStatus(payload.exists ? "открыт" : "новый файл, ещё не записан");
      startPolling();
    })
    .catch(function (error) {
      setStatus("холст не отвечает: " + error, "error");
    });
})();
