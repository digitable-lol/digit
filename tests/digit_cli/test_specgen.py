"""Генератор спецификаций за настоящим компилятором.

Тесты закрывают ровно те места, где ошибка стоит дороже всего и где живой
прогон её не поймает.

Первое и главное — что непроверенный текст НЕ МОЖЕТ попасть человеку. Это не
абстрактная осторожность: на живом прогоне со свободно сформулированной
просьбой генератор вернул документ из одних комментариев, и он прошёл
компилятор. Значит «показываем только прошедшее» — единственное, что отделяет
спецификацию от текста, похожего на спецификацию, и проверять это надо
машиной, а не глазами.

Второе — окно контекста. Пресет `digit-router` в своё время приехал в
репозиторий неработоспособным как основная модель именно из-за окна, и тест
про это уже есть; генератор наступает на те же грабли (40 960 против 64 000).

Третье — форма задания. Она выглядит как косметика, а на деле это условие
применимости замера: то же содержание в свободной форме даёт документ, который
компилируется и ничего не считает.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
from digit_cli import local_model as lm
from digit_cli.specgen import gate, model, pipeline
from digit_cli.specgen.gate import Verdict

needs_compiler = pytest.mark.skipif(
    not gate.available(),
    reason="компилятора FTS нет: поставить fts-gate или задать DIGIT_FTS_HOME",
)


# ---------------------------------------------------------------------------
# Веса: генератор — вспомогательная модель, и это обязано быть видно
# ---------------------------------------------------------------------------


def test_specgen_is_never_offered_as_the_main_model():
    """Окно 40 960 меньше требуемых 64 000 — как у роутера.

    Полагаться на то, что llama-server сам обрежет, нельзя: b10295 режет, а
    сборка 0.18.0 на то же самое окно только предупреждает и идёт выделять
    KV-кэш под запрошенный размер. Порог держит Digit.
    """
    assert lm.SPECGEN_WEIGHTS.role != "chat"
    assert lm.SPECGEN_WEIGHTS.context_length < MINIMUM_CONTEXT_LENGTH
    assert lm.SPECGEN_WEIGHTS.note, "вспомогательная роль должна быть объяснена словами"


def test_specgen_does_not_evict_the_main_model():
    """Вспомогательная модель обязана жить рядом с основной, а не вместо неё."""
    assert lm.SPECGEN_WEIGHTS.port != lm.CHAT_WEIGHTS.port
    assert lm.CHAT_WEIGHTS.port == lm.DEFAULT_PORT


def test_config_never_promises_more_window_than_is_allocated():
    """context_length в конфиге не должен превышать реально выделенный слот."""
    config = lm.local_model_config(lm.SPECGEN_WEIGHTS)
    assert config["context_length"] <= lm.SPECGEN_WEIGHTS.serve_context
    assert f":{lm.SPECGEN_WEIGHTS.port}/" in config["base_url"]


def test_server_command_allocates_the_working_window_not_the_training_one():
    """За неиспользуемое окно платят памятью каждую секунду работы сервера."""
    cmd = lm.build_server_command(
        Path("/bin/llama-server"), Path("/w.gguf"), lm.SPECGEN_WEIGHTS,
        lm.SPECGEN_WEIGHTS.port,
    )
    ctx = int(cmd[cmd.index("--ctx-size") + 1])
    assert ctx == lm.SPECGEN_WEIGHTS.serve_context
    assert ctx < lm.SPECGEN_WEIGHTS.context_length


def test_autostart_does_not_mistake_the_generator_for_the_configured_model():
    """Опознание по одному имени файла подняло бы сервер не на том порту."""
    config = {
        "model": {
            "default": Path(lm.SPECGEN_WEIGHTS.filename).name,
            "base_url": f"http://127.0.0.1:{lm.DEFAULT_PORT}/v1",
        }
    }
    assert lm.configured_local_spec(config) is None


def test_model_name_and_role_both_select_the_same_weights():
    """`--model specgen` и `--model specgen-qwen3-1.7b` — одно и то же."""
    assert lm.resolve_weights("specgen") is lm.SPECGEN_WEIGHTS
    assert lm.resolve_weights(lm.SPECGEN_WEIGHTS.key) is lm.SPECGEN_WEIGHTS
    assert lm.resolve_weights(None) is lm.CHAT_WEIGHTS
    assert lm.resolve_weights("router") is lm.ROUTER_WEIGHTS


# ---------------------------------------------------------------------------
# Главный инвариант: непроверенное не показывается
# ---------------------------------------------------------------------------


def _verdict(ok: bool, **kw) -> Verdict:
    kw.setdefault("stage", "ok" if ok else "compile")
    return Verdict(ok=ok, **kw)


def test_a_refused_outcome_never_leaks_the_text_it_refused():
    """Отказ не должен содержать саму спецификацию — даже под оговоркой.

    Оговорка не спасает: текст, похожий на спецификацию, скопируют в работу, а
    предупреждение над ним — нет.
    """
    bad = "категория «Х»\n  это не компилируется"
    outcome = pipeline.Outcome(
        ok=False,
        fts=None,
        verdict=_verdict(False, code="PARSE_ERROR", detail="ожидалось объявление"),
        attempts=[
            pipeline.Attempt(1, bad, _verdict(False, code="PARSE_ERROR"), {}, 1.0)
        ],
    )
    rendered = pipeline.render(outcome)
    assert bad not in rendered
    assert "это не компилируется" not in rendered
    assert "СПЕЦИФИКАЦИИ НЕТ" in rendered
    assert "PARSE_ERROR" in rendered


def test_the_tool_refuses_instead_of_writing_its_own_specification(monkeypatch):
    """Недоступный генератор — отказ, а не «напишу сам».

    Собственный FTS основной модели выглядит ровно так же, как проверенный, и
    подмена была бы неотличима именно там, где она опаснее всего.
    """
    import tools.spec_tool as spec_tool

    def boom(*args, **kwargs):
        raise model.GeneratorUnavailable("сервер не поднят")

    monkeypatch.setattr(pipeline, "write_spec", boom)
    answer = json.loads(spec_tool.write_spec_tool("задание"))
    assert answer["verified"] is False
    assert "error" in answer
    assert "fts" not in answer


def test_the_tool_hands_the_caveats_to_the_agent(monkeypatch):
    """Спецификация без оговорок будет пересказана как проверенная целиком."""
    import tools.spec_tool as spec_tool

    verdict = _verdict(True, examples_ran=True, examples_total=2, examples_passed=2,
                       shape={"category": "Х", "utilities": 1})
    monkeypatch.setattr(
        pipeline, "write_spec",
        lambda *a, **k: pipeline.Outcome(
            ok=True, fts="категория «Х»", verdict=verdict,
            attempts=[pipeline.Attempt(1, "категория «Х»", verdict, {"grammar": True}, 1.0)],
            caveats=pipeline.caveats_for(verdict, {"grammar": True}),
        ),
    )
    answer = json.loads(spec_tool.write_spec_tool("задание"))
    assert answer["verified"] is True
    assert answer["caveats"], "оговорки обязаны доехать до агента"


# ---------------------------------------------------------------------------
# Оговорки: измеренная слабость обязана быть названа
# ---------------------------------------------------------------------------


def test_a_calculation_without_a_theorem_is_no_longer_flagged():
    """Прежняя оговорка про теорему снята, и снята не молча.

    У весов v1 теорема отсутствовала на всех 129 holdout-заданиях, просивших
    расчёт и теорему сразу; отсюда и была оговорка. Адаптер переобучен на
    ревизии 2 корпуса, теорема стоит в 129 из 129 и в 453 из 453 по всему
    holdout. Предупреждать о поведении, которого больше нет, — это тратить
    внимание человека на ложную тревогу, а рядом стоят настоящие.
    """
    verdict = _verdict(True, examples_ran=True, examples_total=1, examples_passed=1,
                       shape={"utilities": 1, "proposition": False})
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}, "задание"))
    assert "еорем" not in notes


def test_a_missing_optional_mark_is_named_not_normalised():
    """Задание просит два необязательных поля — документ помечает одно.

    Измеренная слабость: 2 верных из 108 на проверочных заданиях, потому что в
    обучающих данных нет документов с двумя необязательными полями. Компилятор
    её поймать не может — обязательное поле вместо необязательного даёт
    валидный документ, — и молчание выдало бы пробел за норму.
    """
    verdict = _verdict(True, examples_ran=True, examples_total=1, examples_passed=1,
                       shape={"utilities": 1, "proposition": True, "optionalFields": 1})
    request = ("объект «Кампания»:\n"
               "* «дата старта» — дата, необязательное\n"
               "* «клики» — число, необязательное\n")
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}, request))
    assert "слабость" in notes
    assert "иногда является" in notes


def test_optional_marks_all_present_produce_no_such_note():
    verdict = _verdict(True, examples_ran=True, examples_total=1, examples_passed=1,
                       shape={"utilities": 1, "proposition": True, "optionalFields": 2})
    request = "«дата старта» — дата, необязательное\n«клики» — число, необязательное\n"
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}, request))
    assert "слабость" not in notes


def test_without_the_request_the_optional_caveat_stays_silent():
    """Оговорка сравнивает документ с заданием. Нет задания — нет утверждения:
    гадать «наверное, просили» она не имеет права."""
    verdict = _verdict(True, examples_ran=True, examples_total=1, examples_passed=1,
                       shape={"utilities": 1, "proposition": True, "optionalFields": 0})
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}))
    assert "слабость" not in notes


def test_a_document_that_computes_nothing_is_flagged_loudly():
    """Ступень с примерами пропускается, когда примеров нет.

    Именно в эту щель на живом прогоне прошёл вырожденный документ из одних
    комментариев: он компилируется, а «проверено» про него означает только
    «разобралось».
    """
    verdict = _verdict(True, examples_ran=False, shape={"utilities": 0, "proposition": False})
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}))
    assert "ничего не вычисляет" in notes


def test_the_limit_of_the_gate_is_always_stated():
    """Соответствие документа задаче ворота проверить не могут — и говорят это."""
    verdict = _verdict(True, examples_ran=True, examples_total=1, examples_passed=1,
                       shape={"utilities": 1, "proposition": True})
    notes = " ".join(pipeline.caveats_for(verdict, {"grammar": True}))
    assert "именно вашу задачу" in notes


# ---------------------------------------------------------------------------
# Форма задания и повторы
# ---------------------------------------------------------------------------


def test_the_training_suffix_is_never_doubled():
    """Приписанный дважды хвост сам становится тем, чего в обучении не было."""
    once = model._as_task("задание\n\n" + model.TASK_SUFFIX)
    assert once.count(model.TASK_SUFFIX) == 1
    assert model._as_task("задание").count(model.TASK_SUFFIX) == 1


def test_the_brief_shape_is_carried_to_both_doors():
    """Форма задания — условие применимости замера, а не совет по стилю.

    Она обязана дойти и до человека в терминале, и до агента: свободная
    формулировка даёт документ, который компилируется и ничего не считает.
    """
    import tools.spec_tool as spec_tool

    assert "## Расчёт" in model.BRIEF_SHAPE
    assert model.BRIEF_SHAPE in spec_tool.WRITE_SPEC_SCHEMA["description"]


def test_the_first_attempt_is_the_one_the_number_was_measured_at(monkeypatch):
    """99,9 % измерены на жадном декодировании — первая попытка обязана быть им.

    Повторы идут с температурой и разным зерном: жадное декодирование
    детерминировано, и второй такой заход вернул бы тот же текст и ту же ошибку.
    """
    calls: list[dict] = []

    def fake_generate(request, **kw):
        calls.append(kw)
        return "текст", {"grammar": True}

    monkeypatch.setattr(model, "generate", fake_generate)
    monkeypatch.setattr(gate, "flang_dist", lambda *a, **k: Path("/dist"))
    monkeypatch.setattr(gate, "check", lambda text: _verdict(False, code="PARSE_ERROR"))
    monkeypatch.setattr(pipeline, "ensure_server", lambda **kw: None)

    outcome = pipeline.write_spec("задание", attempts=3)

    assert outcome.ok is False
    assert outcome.fts is None
    assert len(calls) == 3
    assert calls[0]["temperature"] == 0.0 and calls[0]["seed"] is None
    assert all(c["temperature"] > 0 for c in calls[1:])
    assert len({c["seed"] for c in calls[1:]}) == len(calls) - 1


def test_a_passing_attempt_stops_the_loop(monkeypatch):
    monkeypatch.setattr(model, "generate", lambda request, **kw: ("текст", {"grammar": True}))
    monkeypatch.setattr(gate, "flang_dist", lambda *a, **k: Path("/dist"))
    monkeypatch.setattr(gate, "check", lambda text: _verdict(True, shape={"utilities": 1}))
    monkeypatch.setattr(pipeline, "ensure_server", lambda **kw: None)

    outcome = pipeline.write_spec("задание", attempts=3)
    assert outcome.ok is True and outcome.attempts_used == 1 and outcome.fts == "текст"


def test_the_compiler_is_checked_before_the_model_runs(monkeypatch):
    """Иначе человек ждёт минуту генерации ради «проверить нечем»."""
    def no_compiler(*args, **kwargs):
        raise gate.GateUnavailable("нет компилятора")

    def must_not_run(*args, **kwargs):  # pragma: no cover — вызов был бы провалом
        raise AssertionError("генератор не должен запускаться без компилятора")

    monkeypatch.setattr(gate, "flang_dist", no_compiler)
    monkeypatch.setattr(model, "generate", must_not_run)
    with pytest.raises(gate.GateUnavailable):
        pipeline.write_spec("задание")


# ---------------------------------------------------------------------------
# Ворота на настоящем компиляторе
# ---------------------------------------------------------------------------

_GOOD = """категория «Доставка»

  объект «Заказ»
    «сумма заказа» является деньгами

  утилита «Рассчитать стоимость доставки»
    принимает «Заказ»
    возвращает деньги
    начинает с 300

    правило «Бесплатная доставка при крупном заказе»
      если «сумма заказа» не меньше 5000
      то добавить -300

    пример «Обычный заказ»
      дано «сумма заказа» равна 1000
      ожидается результат равен 300
"""


@needs_compiler
def test_a_real_specification_passes_all_three_stages():
    verdict = gate.check(_GOOD)
    assert verdict.ok and verdict.stage == "ok"
    assert verdict.examples_ran and verdict.examples_passed == verdict.examples_total == 1
    assert verdict.shape["utilities"] == 1


@needs_compiler
def test_unparseable_text_stops_at_the_compiler():
    verdict = gate.check("это вообще не спецификация")
    assert not verdict.ok and verdict.stage == "compile"


@needs_compiler
def test_a_wrong_example_stops_at_the_example_stage():
    """Документ разбирается и валиден, но интерпретатор считает иначе.

    Это единственная ступень, которая ловит НЕВЕРНЫЙ расчёт, а не кривой текст,
    и её пропуск был бы самой дорогой из возможных экономий.
    """
    verdict = gate.check(_GOOD.replace("ожидается результат равен 300",
                                       "ожидается результат равен 999"))
    assert not verdict.ok
    assert verdict.stage == "examples"
    assert verdict.code == "FTS_EXAMPLE_MISMATCH"


@needs_compiler
def test_a_document_without_examples_is_not_failed_for_having_none():
    """Объявление без расчёта — законная спецификация, исполнять в ней нечего.

    testUtilities на таком документе БРОСАЕТ (FTS_NO_UTILITIES); считать это
    отказом значило бы отказывать за форму, которую сам компилятор принимает.
    """
    verdict = gate.check("категория «Х»\n\n  объект «Y»\n    «поле» является числом\n")
    assert verdict.ok
    assert verdict.examples_ran is False and verdict.examples_total == 0


@needs_compiler
def test_the_batch_answers_every_record_it_was_given():
    verdicts = gate.check_many([_GOOD, "мусор", _GOOD])
    assert [v.ok for v in verdicts] == [True, False, True]
