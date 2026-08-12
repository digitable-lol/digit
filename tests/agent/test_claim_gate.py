"""Гейт проверки суждений: выключен по умолчанию, молчит без компилятора.

Сторож держит ровно те свойства, которые делают гейт безопасным для чужого
хода. Главное из них — ВЫКЛЮЧЕН: пока владелец не решил, где живёт SPEC.fts и
на каких поверхностях гейт полезен, включённое умолчание выдавало бы за
измеренное то, что не измерено.

Проверка суждений здесь НЕ подделывается: там, где нужен приговор компилятора,
подменяется `digit_cli.claimcheck`, потому что предмет теста — политика, а не
компилятор. Там, где нужен настоящий компилятор, тест отмечен и пропускается
без него (у большинства машин его нет — это и есть исходное состояние, ради
которого гейт молчит).
"""

from __future__ import annotations

import pytest

from agent import claim_gate
from agent.claim_gate import (
    build_claim_gate_nudge,
    candidate_statements,
    claim_gate_enabled,
    evaluate,
    spec_source_for_turn,
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("DIGIT_CLAIM_GATE", "DIGIT_CLAIM_GATE_SPEC"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --- переключатель -------------------------------------------------------


def test_gate_is_off_by_default(clean_env):
    """Пустой конфиг — гейт выключен. Это главное свойство файла."""
    assert claim_gate_enabled({}) is False
    assert claim_gate_enabled({"agent": {}}) is False


def test_default_config_ships_the_switch_off():
    """Умолчание поставки тоже False, а не «auto» и не отсутствие ключа.

    Отсутствие ключа читалось бы как «забыли», и следующий, кто заведёт
    «auto», не увидит, что решение было принято.
    """
    from digit_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["agent"]["claim_gate"] is False
    assert DEFAULT_CONFIG["agent"]["claim_gate_spec"] == ""


def test_env_and_config_can_enable(clean_env):
    assert claim_gate_enabled({"agent": {"claim_gate": True}}) is True
    assert claim_gate_enabled({"agent": {"claim_gate": "on"}}) is True
    clean_env.setenv("DIGIT_CLAIM_GATE", "1")
    assert claim_gate_enabled({"agent": {"claim_gate": False}}) is True


def test_env_off_beats_config_on(clean_env):
    """Явное «выключить» сильнее конфига — иначе гейт нечем погасить на месте."""
    clean_env.setenv("DIGIT_CLAIM_GATE", "off")
    assert claim_gate_enabled({"agent": {"claim_gate": True}}) is False


# --- откуда берётся спецификация ----------------------------------------


def test_no_spec_source_means_silence(clean_env):
    """Гейт не ищет *.fts сам. Нет названного источника — нет проверки."""
    assert spec_source_for_turn(None, {}) is None
    assert evaluate("если сумма больше 1000 то скидка 10 процентов", None) == claim_gate.SILENT


def test_turn_held_spec_wins_over_config(clean_env, tmp_path):
    """Спецификация, которую ход держит в руках, важнее настройки.

    Иначе гейт проверял бы ответ про документ, который агент только что
    прочитал, против чужого файла из конфига — и назвал бы это проверкой.
    """
    named = tmp_path / "spec.fts"
    named.write_text("из конфига", encoding="utf-8")
    clean_env.setenv("DIGIT_CLAIM_GATE_SPEC", str(named))

    class Agent:
        _turn_claim_spec_source = "из хода"

    assert spec_source_for_turn(Agent(), {}) == "из хода"
    assert spec_source_for_turn(None, {}) == "из конфига"


def test_unreadable_named_spec_is_nothing_to_check_not_a_failure(clean_env, tmp_path):
    clean_env.setenv("DIGIT_CLAIM_GATE_SPEC", str(tmp_path / "нет-такого.fts"))
    assert spec_source_for_turn(None, {}) is None


# --- предфильтр суждений -------------------------------------------------


def test_only_rule_shaped_sentences_are_checked():
    text = (
        "Готово, я поправил файл. "
        "Если сумма заказа больше 1000, то скидка 10 процентов. "
        "Дальше можно запускать тесты."
    )
    got = candidate_statements(text)
    assert len(got) == 1
    assert got[0].startswith("Если сумма заказа больше 1000")


def test_prose_answer_costs_nothing():
    """Обычный ответ агента не даёт ни одного суждения — значит ни одной проверки."""
    assert candidate_statements("Готово. Я обновил README и прогнал тесты.") == []


def test_statement_count_is_bounded():
    one = "Если сумма больше 1000 то скидка 10 процентов. "
    assert len(candidate_statements(one * 40)) == claim_gate.MAX_STATEMENTS


# --- три исхода ----------------------------------------------------------

_SPEC = "любой текст спецификации"
_CLAIM = "Если сумма больше 1000, то скидка 40 процентов."


#: Скомпилированный документ в той форме, в которой его отдаёт компилятор.
#: Схема из него строится НАСТОЯЩАЯ (claimcheck.document.schema_of_document),
#: подменяется только приговор — предмет теста политика, а не компилятор.
_DOCUMENT = {
    "category": "Продажи",
    "structures": [
        {"name": "Заказ", "fields": [{"name": "сумма", "type": "Деньги"}]},
    ],
    "utilities": [
        {"name": "Скидка", "input": "Заказ", "output": "Деньги",
         "initial": None, "rules": [], "properties": [], "examples": []},
    ],
}


def _fake_claimcheck(monkeypatch, outcome: str, extra: dict | None = None):
    """Подменить слой проверки, оставив политику настоящей."""
    import sys
    import types

    module = types.ModuleType("digit_cli.claimcheck")
    module.RuntimeMissing = RuntimeError
    module.runtime_available = lambda: True
    module.schema_of = lambda source: _DOCUMENT
    # Перевод документа в схему берётся НАСТОЯЩИЙ — тот же, что у
    # digit rule-check. Подделать его значило бы проверять политику против
    # собственной выдумки о том, что такое схема.
    from digit_cli.claimcheck.document import schema_of_document
    module.schema_of_document = schema_of_document

    def answer(statements, schema, category, **kw):
        payload = {"outcome": outcome, "note": "ГРАНИЦА: проверена выводимость."}
        payload.update(extra or {})
        answer.seen = (list(statements), category)
        return payload

    module.answer = answer
    _install(monkeypatch, module)
    return answer


def _install(monkeypatch, module):
    """Подменить слой проверки и в sys.modules, и атрибутом пакета.

    Одного sys.modules не хватает: импорт вида «from digit_cli import
    claimcheck» берёт АТРИБУТ уже импортированного пакета, а не запись в
    sys.modules, — и тест молча мерил бы настоящий компилятор вместо
    подменённого.
    """
    import sys

    import digit_cli

    monkeypatch.setitem(sys.modules, "digit_cli.claimcheck", module)
    monkeypatch.setattr(digit_cli, "claimcheck", module, raising=False)


def test_nothing_to_check_is_silent(monkeypatch):
    """Компилятора нет — ни нуджа, ни строки пользователю."""
    import sys
    import types

    module = types.ModuleType("digit_cli.claimcheck")
    module.RuntimeMissing = RuntimeError
    module.runtime_available = lambda: False
    _install(monkeypatch, module)

    verdict = evaluate(_CLAIM, _SPEC)
    assert verdict == claim_gate.SILENT
    assert verdict.nudge is None and verdict.provenance is None


def test_not_formalized_is_silent(monkeypatch):
    _fake_claimcheck(monkeypatch, "не_формализовано")
    verdict = evaluate(_CLAIM, _SPEC)
    assert verdict.outcome == "не_формализовано"
    assert verdict.nudge is None
    assert verdict.provenance is None


def test_proven_wrong_returns_the_turn_with_the_counterexample(monkeypatch):
    _fake_claimcheck(monkeypatch, "проверено_неверно", {
        "reading": ["сумма > 1000 -> скидка = 40 %"],
        "failed_check": {"stage": "свойство", "code": "FTS_UTILITY_PROPERTY",
                         "detail": "нарушено свойство «Скидка ограничена»; "
                                   "контрпример: сумма = 50000"},
    })
    verdict = evaluate(_CLAIM, _SPEC)
    assert verdict.outcome == "проверено_неверно"
    assert verdict.nudge
    assert "контрпример: сумма = 50000" in verdict.nudge
    assert "FTS_UTILITY_PROPERTY" in verdict.nudge
    assert "Скидка ограничена" in verdict.nudge
    # Граница печатается и в отказе.
    assert "ГРАНИЦА" in verdict.nudge


def test_proven_right_prints_provenance_with_the_limit(monkeypatch):
    _fake_claimcheck(monkeypatch, "проверено_верно", {
        "reading": ["сумма >= 50000 -> прибавить 3 % от суммы"],
    })
    verdict = evaluate(_CLAIM, _SPEC)
    assert verdict.outcome == "проверено_верно"
    assert verdict.nudge is None
    assert verdict.provenance
    assert "ПРОВЕРЕНО И ВЕРНО" in verdict.provenance
    assert "прочитано так" in verdict.provenance
    # Зелёный без границы выглядел бы сильнее, чем он есть.
    assert "ГРАНИЦА" in verdict.provenance


def test_a_broken_checker_cannot_break_the_turn(monkeypatch):
    """Fail-open: исключение изнутри проверки — это молчание, а не падение хода."""
    import sys
    import types

    module = types.ModuleType("digit_cli.claimcheck")
    module.RuntimeMissing = RuntimeError

    def boom():
        raise OSError("компилятор упал")

    module.runtime_available = boom
    _install(monkeypatch, module)
    assert evaluate(_CLAIM, _SPEC) == claim_gate.SILENT


def test_nudge_budget_is_bounded(monkeypatch):
    _fake_claimcheck(monkeypatch, "проверено_неверно",
                     {"failed_check": {"detail": "контрпример: сумма = 50000"}})
    assert build_claim_gate_nudge(answer_text=_CLAIM, spec_source=_SPEC, attempts=0)
    assert build_claim_gate_nudge(
        answer_text=_CLAIM, spec_source=_SPEC,
        attempts=claim_gate.MAX_CLAIM_GATE_NUDGES) is None


# --- точка встраивания ---------------------------------------------------


def test_gate_is_wired_into_the_turn_after_the_answer_is_built():
    """Гейт стоит в цепочке гейтов хода, а не рядом с каскадом правил.

    Каскад отвечает ВМЕСТО модели и стоит до неё; проверка суждений оценивает
    то, что модель уже сказала. Тест держит именно этот порядок: место гейта
    внутри ветки, где final_msg уже собран, и после трёх соседей.
    """
    from pathlib import Path

    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    assert "from agent.claim_gate import (" in source
    assert "claim_check_failed" in source
    at_claim = source.index("claim_gate_enabled()")
    at_verify = source.index("verify_on_stop_enabled()")
    at_kanban = source.index("build_kanban_stop_nudge")
    at_final = source.index("final_msg = agent._build_assistant_message")
    assert at_final < at_verify < at_kanban < at_claim


# --- настоящий компилятор ------------------------------------------------

_DISCOUNT = "skills/software-development/fts/templates/discount.fts"


def _real_compiler_available() -> bool:
    try:
        from digit_cli import claimcheck

        return bool(claimcheck.runtime_available())
    except Exception:
        return False


@pytest.mark.skipif(not _real_compiler_available(),
                    reason="компилятора FTS нет: DIGIT_FTS_GATE_HOME не указывает на сборку")
@pytest.mark.parametrize("statement,expected", [
    ("Если сумма не меньше 50000, то добавить 3 процента от поля сумма.", "проверено_верно"),
    ("Если сумма не меньше 50000, то добавить 40 процентов от поля сумма.", "проверено_неверно"),
])
def test_end_to_end_against_the_real_compiler(statement, expected):
    """Оба содержательных исхода сняты настоящим компилятором, а не подделкой.

    Тест пропускается там, где компилятора нет, и это правильное поведение
    сторожа: отсутствие компилятора — исходное состояние продукта, ради
    которого гейт и молчит.
    """
    from pathlib import Path

    spec = Path(_DISCOUNT).read_text(encoding="utf-8")
    verdict = evaluate(statement, spec)
    assert verdict.outcome == expected
    if expected == "проверено_неверно":
        assert verdict.nudge and "КОНТРПРИМЕР И ПРИЧИНА" in verdict.nudge
        assert "контрпример: сумма = 50000" in verdict.nudge
    else:
        assert verdict.provenance and "ПРОВЕРЕНО И ВЕРНО" in verdict.provenance
