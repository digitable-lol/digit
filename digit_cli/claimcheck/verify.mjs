#!/usr/bin/env node
// Проверочная служба: настоящий компилятор FTS и настоящий детектор ошибок.
//
// NDJSON на вход, NDJSON на выход. Четыре режима в одной записи по полю mode:
//
//   check  — compile → validate → примеры → теорема/сертификат → fts-gate
//   eval   — исполнить утилиту на наборе входных векторов
//   diff   — исполнить ДВЕ спецификации на одних векторах и сравнить результаты
//   schema — снять с УЖЕ объявленной спецификации её структуры и утилиты
//
// Второй реализации семантики FTS в проекте нет: и «что означает разобранное
// правило», и «что означает эталонное» считает один и тот же интерпретатор.

import { createInterface } from "node:readline"

// Пути приходят снаружи (bridge.py), а не вбиты сюда. Раньше это были две
// абсолютные строки в чужой домашний каталог: на площадке экспериментов так
// можно, в поставке — нет, там компилятор ставит пользователь и кладёт куда
// хочет. Падение здесь читаемое и намеренное: без компилятора эта служба не
// умеет ничего, и притворяться работающей ей нечем.
const FLANG = process.env.DIGIT_FTS_DIST
const GATE = process.env.DIGIT_FTS_GATE_DIST
if (!FLANG || !GATE) {
  process.stderr.write("DIGIT_FTS_DIST и DIGIT_FTS_GATE_DIST обязаны быть заданы\n")
  process.exit(2)
}
const { compile } = await import(`${FLANG}/parser.js`)
const { validate } = await import(`${FLANG}/validate.js`)
const { testUtilities, executeUtility } = await import(`${FLANG}/utility.js`)
const { ftsGate } = await import(`${GATE}/gate.js`)

function compileChecked(source) {
  const document = compile(source)
  const checked = validate(document, { coverage: true })
  const errors = checked.diagnostics.filter((d) => d.severity === "error")
  if (!checked.valid || errors.length > 0) {
    const error = new Error(errors.map((d) => `${d.code}: ${d.message}`).join(" | "))
    error.code = errors[0]?.code ?? "FTS_INVALID"
    throw error
  }
  return checked
}

function runCheck(record) {
  const out = { stage: null, ok: false }
  let checked
  try {
    checked = compileChecked(record.fts)
  } catch (error) {
    out.stage = error.code === undefined ? "compile" : "validate"
    out.code = error?.diagnostic?.code ?? error?.code ?? "PARSE_ERROR"
    out.detail = String(error?.message ?? error).slice(0, 400)
    return out
  }
  out.warnings = checked.diagnostics.filter((d) => d.severity !== "error").map((d) => d.code)
  const document = checked.document
  const utilities = document.utilities ?? []
  if (utilities.some((u) => u.examples.length > 0)) {
    let tested
    try {
      tested = testUtilities(document)
    } catch (error) {
      out.stage = "examples"
      out.code = error?.diagnostic?.code ?? "EXAMPLE_THROW"
      out.detail = String(error?.message ?? error).slice(0, 400)
      return out
    }
    out.examplesTotal = tested.total
    out.examplesPassed = tested.passed
    if (!tested.valid) {
      const bad = tested.results.find((r) => !r.passed)
      out.stage = "examples"
      out.code = "FTS_EXAMPLE_MISMATCH"
      out.detail = `${bad.utility}/${bad.example}: ожидалось ${bad.expected}, получено ${bad.actual ?? bad.error}`
      return out
    }
  }
  // Ступень логических ошибок. Отдельным полем: отказ ворот — это утверждение
  // о ПОСТРОЕНИИ вывода, а не о ложности содержания.
  const gate = ftsGate(record.fts, record.context ?? undefined)
  out.gateStatus = gate.status
  if (gate.status !== "certified") {
    out.stage = "gate"
    out.ok = false
    out.code = gate.code
    out.detail = String(gate.detail ?? "").slice(0, 400)
    out.fallacies = (gate.fallacies ?? []).map((f) => ({ code: f.code, kind: f.kind, where: f.where }))
    return out
  }
  out.stage = "ok"
  out.ok = true
  return out
}

function vectorRun(source, utilityName, vectors) {
  const checked = compileChecked(source)
  const document = checked.document
  return vectors.map((vector) => {
    try {
      return { ok: true, value: executeUtility(document, utilityName, vector) }
    } catch (error) {
      return { ok: false, error: String(error?.message ?? error).slice(0, 200) }
    }
  })
}

function runDiff(record) {
  let gold
  try {
    gold = vectorRun(record.fts_gold, record.utility, record.vectors)
  } catch (error) {
    return { verdict: "gold_broken", detail: String(error?.message ?? error).slice(0, 300) }
  }
  let mine
  try {
    mine = vectorRun(record.fts_mine, record.utility, record.vectors)
  } catch (error) {
    return { verdict: "mine_broken", detail: String(error?.message ?? error).slice(0, 300) }
  }
  const disagreements = []
  let comparable = 0
  for (let index = 0; index < gold.length; index += 1) {
    const left = gold[index]
    const right = mine[index]
    if (!left.ok && !right.ok) continue
    if (!left.ok || !right.ok) {
      disagreements.push({ index, gold: left.ok ? left.value : `!${left.error}`, mine: right.ok ? right.value : `!${right.error}` })
      comparable += 1
      continue
    }
    comparable += 1
    if (!Object.is(left.value, right.value)) {
      disagreements.push({ index, gold: left.value, mine: right.value })
    }
  }
  return {
    verdict: disagreements.length === 0 ? "same" : "differs",
    comparable,
    disagreements: disagreements.slice(0, 5),
    disagreement_count: disagreements.length,
  }
}

function runEval(record) {
  try {
    return { ok: true, results: vectorRun(record.fts, record.utility, record.vectors) }
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error).slice(0, 300) }
  }
}

// Схема существующего расчёта. Нужна затем, чтобы новое правило приезжало К
// объявленным полям, а не восстанавливало их из текста: без известных типов
// «если сумма больше 1000» — это не правило, а догадка о том, что такое сумма.
//
// Отдаются и уже объявленные правила со свойствами и примерами: добавляемое
// правило проверяется В КОНТЕКСТЕ расчёта, иначе непокрытая ветка между новым
// порогом и старым, перекрытое правило и нарушенное свойство останутся
// невидимыми — а это ровно то, ради чего здесь стоит детектор.
function runSchema(record) {
  let checked
  try {
    checked = compileChecked(record.fts)
  } catch (error) {
    return { ok: false, stage: "compile", code: error?.code ?? "PARSE_ERROR",
             detail: String(error?.message ?? error).slice(0, 400) }
  }
  const document = checked.document
  return {
    ok: true,
    category: document.category ?? "",
    structures: document.structures ?? [],
    utilities: (document.utilities ?? []).map((u) => ({
      name: u.name, input: u.input, output: u.output, initial: u.initial,
      rules: u.rules ?? [], properties: u.properties ?? [], examples: u.examples ?? [],
    })),
  }
}

const buffer = []
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity })
for await (const line of rl) {
  if (!line.trim()) continue
  const record = JSON.parse(line)
  let payload
  try {
    if (record.mode === "diff") payload = runDiff(record)
    else if (record.mode === "eval") payload = runEval(record)
    else if (record.mode === "schema") payload = runSchema(record)
    else payload = runCheck(record)
  } catch (error) {
    payload = { stage: "internal", ok: false, code: "HARNESS_ERROR", detail: String(error?.message ?? error).slice(0, 300) }
  }
  buffer.push(JSON.stringify({ id: record.id, ...payload }))
  if (buffer.length >= 400) {
    process.stdout.write(buffer.join("\n") + "\n")
    buffer.length = 0
  }
}
if (buffer.length) process.stdout.write(buffer.join("\n") + "\n")
