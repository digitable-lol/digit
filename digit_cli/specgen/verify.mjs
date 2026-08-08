#!/usr/bin/env node
// Ворота для сгенерированной спецификации: настоящий компилятор FTS и ничего кроме.
//
// NDJSON на вход ({id, fts}), NDJSON на выход. Три ступени подряд, в этом порядке:
//
//   compile        — разбор исходного текста в документ
//   validate       — семантические проверки над документом
//   testUtilities  — исполнение объявленных примеров настоящим интерпретатором
//
// ПОЧЕМУ ровно эти три и почему они не свои. Генератор — модель на 1,7 млрд
// параметров; единственное, что отличает её вывод от правдоподобного текста, —
// приговор того же компилятора, которым Digitable считает всё остальное.
// Вторая реализация «что такое валидная спецификация» обесценила бы приговор
// ровно там, где он дороже всего: на редких конструкциях, которые никто не
// смотрит глазами. Поэтому компилятор здесь внешний процесс, а не порт.
//
// ПОЧЕМУ здесь НЕТ fts-gate (детектора логических ошибок). Гейт отвечает на
// вопрос «не построен ли вывод с ошибкой рассуждения», а спецификация,
// написанную по просьбе человека, ничего не выводит — она объявляет. Пропустить
// её через ворота для вывода значило бы отказывать за отсутствие того, чего
// человек и не просил. Проверку логических ошибок делает `digit rule-check` над
// уже объявленной схемой — там она к месту.
//
// Пути к компилятору приходят снаружи (gate.py), а не вбиты сюда: компилятор
// ставит пользователь и кладёт куда хочет.

import { createInterface } from "node:readline"

const FLANG = process.env.DIGIT_FTS_DIST
if (!FLANG) {
  process.stderr.write("DIGIT_FTS_DIST обязан быть задан\n")
  process.exit(2)
}
const { compile } = await import(`${FLANG}/parser.js`)
const { validate } = await import(`${FLANG}/validate.js`)
const { testUtilities } = await import(`${FLANG}/utility.js`)

// Что за документ получился. Нужно не для приговора, а для честного ответа
// человеку: «здесь расчёт и три примера» — это то, что он обязан прочитать
// перед тем, как поверить зелёному. Отдельно считается теорема: у обученной
// модели есть измеренная слабость — она не пишет теорему рядом с утилитой, и
// её отсутствие обязано быть НАЗВАНО, а не молча сойти за норму.
function shapeOf(document) {
  const utilities = document.utilities ?? []
  return {
    category: document.category ?? "",
    structures: (document.structures ?? []).length,
    functors: (document.functors ?? []).length,
    utilities: utilities.length,
    utilityNames: utilities.map((u) => u.name),
    rules: utilities.reduce((n, u) => n + (u.rules ?? []).length, 0),
    properties: utilities.reduce((n, u) => n + (u.properties ?? []).length, 0),
    proposition: Boolean(document.proposition),
  }
}

function runCheck(record) {
  const out = { ok: false, stage: null }

  // --- ступень 1: compile -------------------------------------------------
  let document
  try {
    document = compile(record.fts)
  } catch (error) {
    out.stage = "compile"
    out.code = error?.diagnostic?.code ?? error?.code ?? "PARSE_ERROR"
    out.detail = String(error?.message ?? error).slice(0, 400)
    return out
  }

  // --- ступень 2: validate ------------------------------------------------
  let checked
  try {
    checked = validate(document, { coverage: true })
  } catch (error) {
    out.stage = "validate"
    out.code = error?.diagnostic?.code ?? error?.code ?? "VALIDATE_THROW"
    out.detail = String(error?.message ?? error).slice(0, 400)
    return out
  }
  const errors = checked.diagnostics.filter((d) => d.severity === "error")
  // Предупреждения не отказ, но и не пустяк: непокрытая ветка и нарушенное
  // свойство приезжают именно так. Они уходят наверх списком кодов, чтобы
  // человек увидел их рядом со спецификацией, а не узнал позже.
  out.warnings = checked.diagnostics
    .filter((d) => d.severity !== "error")
    .map((d) => ({ code: d.code, severity: d.severity, message: String(d.message ?? "").slice(0, 200) }))
  if (!checked.valid || errors.length > 0) {
    out.stage = "validate"
    out.code = errors[0]?.code ?? "FTS_INVALID"
    out.detail = errors.map((d) => `${d.code}: ${d.message}`).join(" | ").slice(0, 400)
    return out
  }
  const built = checked.document ?? document
  out.shape = shapeOf(built)

  // --- ступень 3: testUtilities -------------------------------------------
  // testUtilities БРОСАЕТ на документе без утилит и на утилитах без примеров
  // (FTS_NO_UTILITIES / FTS_NO_UTILITY_EXAMPLES). Для нас это не отказ:
  // документ из одних объектов и теоремы — законная спецификация, исполнять в
  // ней просто нечего. Считать «нечего исполнять» провалом значило бы
  // отказывать за форму, которую сам компилятор считает верной. Поэтому
  // ступень запускается ровно тогда, когда ей есть над чем работать, а факт
  // запуска отдаётся отдельным полем — «примеры прошли» и «примеров не было»
  // человек обязан различать.
  const utilities = built.utilities ?? []
  const hasExamples = utilities.some((u) => (u.examples ?? []).length > 0)
  out.examplesRan = false
  out.examplesTotal = 0
  out.examplesPassed = 0
  if (hasExamples) {
    let tested
    try {
      tested = testUtilities(built)
    } catch (error) {
      out.stage = "examples"
      out.code = error?.diagnostic?.code ?? error?.code ?? "EXAMPLE_THROW"
      out.detail = String(error?.message ?? error).slice(0, 400)
      return out
    }
    out.examplesRan = true
    out.examplesTotal = tested.total
    out.examplesPassed = tested.passed
    if (!tested.valid) {
      const bad = tested.results.find((r) => !r.passed)
      out.stage = "examples"
      out.code = "FTS_EXAMPLE_MISMATCH"
      out.detail =
        `${bad.utility}/${bad.example}: ожидалось ${bad.expected}, ` +
        `получено ${bad.actual ?? bad.error}`
      return out
    }
  }

  out.stage = "ok"
  out.ok = true
  return out
}

const buffer = []
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const record = JSON.parse(line)
  let payload
  try {
    payload = runCheck(record)
  } catch (error) {
    payload = {
      ok: false,
      stage: "internal",
      code: "HARNESS_ERROR",
      detail: String(error?.message ?? error).slice(0, 300),
    }
  }
  buffer.push(JSON.stringify({ id: record.id, ...payload }))
  if (buffer.length >= 200) {
    process.stdout.write(buffer.join("\n") + "\n")
    buffer.length = 0
  }
}
if (buffer.length) process.stdout.write(buffer.join("\n") + "\n")
