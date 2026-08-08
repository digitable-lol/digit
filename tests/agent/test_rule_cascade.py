"""Первый каскад маршрутизации внутри хода.

Главное свойство под проверкой одно и то же во всех тестах ниже: каскад,
который не сработал, обязан вернуть None и НЕ закрыть ход. Отказ правил не
имеет права стать отказом пользователю — ровно за этим каскад и построен
поверх слоя, у которого излишний отказ 58,8 % против 13,0 % у модели.
"""

from __future__ import annotations

import json

import pytest

from agent import rule_cascade
from agent.turn_summary import TurnSummaryCollector, format_turn_summary
from digit_cli.ruleparse import dependency_available
from digit_cli.ruleparse.cascade import RuleDecision

needs_dict = pytest.mark.skipif(
    not dependency_available(),
    reason="нет pymorphy3: каскад в этой среде всегда уступает модели",
)

#: Снято до автопатча фикстуры ниже — иначе тест выключателя проверял бы заглушку.
_REAL_IS_ENABLED = rule_cascade.is_enabled


class FakeAgent:
    """Ровно те поля агента, которых касается каскад, и ни одного больше.

    `provider` объявлен локальным намеренно: с версии, где каскад включается
    сам только позади локальной модели, это поле решает, сработает он вообще
    или нет. Двойник без провайдера проверял бы выключённый каскад.
    """

    def __init__(self, tools=("tools_execute",), provider="llamacpp", base_url=""):
        self.valid_tool_names = set(tools)
        self.session_id = "s-1"
        self.persisted = []
        self.provider = provider
        self.base_url = base_url

    def _persist_session(self, messages, history):
        self.persisted.append(list(messages))


@pytest.fixture(autouse=True)
def _cascade_on(monkeypatch):
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr(rule_cascade, "is_enabled", lambda agent=None: True)


def _route_to(tool_id, args):
    return lambda _q: RuleDecision(routed=True, tool_id=tool_id, args=dict(args),
                                   score=30.0, latency_ms=1.3)


def _executor(payload):
    calls = []

    def fake(name, fn_args, **kwargs):
        calls.append((name, fn_args))
        return payload

    fake.calls = calls
    return fake


# ---------------------------------------------------------------------------
# Уступки
# ---------------------------------------------------------------------------
def test_unparsed_query_cedes_and_does_not_close_the_turn(monkeypatch):
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        lambda _q: RuleDecision(routed=False, declined="no-parse",
                                                reason="не разобрал"))
    messages = [{"role": "user", "content": "что такое морфизм?"}]
    agent = FakeAgent()

    assert rule_cascade.try_turn(agent, "что такое морфизм?", messages, []) is None
    # Ход не тронут: ни ответа, ни записи в сессию. Дальше говорит модель.
    assert messages == [{"role": "user", "content": "что такое морфизм?"}]
    assert agent.persisted == []
    assert agent._last_rule_cascade["outcome"] == "ceded"


def test_cedes_when_the_executor_tool_is_not_enabled_this_turn(monkeypatch):
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("hash-text", {"clearText": "x"}))
    agent = FakeAgent(tools=("read_file",))

    assert rule_cascade.try_turn(agent, "посчитай md5 от x", [], []) is None
    assert agent._last_rule_cascade["detail"] == "no-executor"


def test_cedes_when_the_public_tool_has_no_executable_counterpart(monkeypatch):
    # text-diff есть в публичном каталоге, но исполняемого аналога у него нет.
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("text-diff", {"_operands": ["a", "b"]}))
    agent = FakeAgent()

    assert rule_cascade.try_turn(agent, "сравни a и b", [], []) is None
    assert agent._last_rule_cascade["detail"] == "no-core-tool"


def test_invalid_args_from_the_executor_is_a_cession_not_an_answer(monkeypatch):
    """Мост имён неполон намеренно: незнакомое имя обязано стать уступкой.

    tools-core проверяет аргументы по схеме самой утилиты и на чужое имя
    отвечает структурной ошибкой, а не догадкой. Каскад читает её так же,
    как неразобранный запрос: молчит и отдаёт ход модели. Именно это делает
    безопасной поставку моста без переименования аргументов.
    """
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("hash-text", {"clearText": "x", "algorithm": "MD5"}))
    payload = json.dumps({"ok": False, "code": "invalid_args",
                          "error": "must have required property 'text'"})
    monkeypatch.setattr("model_tools.handle_function_call", _executor(payload))
    messages = []
    agent = FakeAgent()

    assert rule_cascade.try_turn(agent, "посчитай md5 от x", messages, []) is None
    assert messages == []
    assert agent._last_rule_cascade["detail"] == "executor-declined"


def test_a_crashing_executor_cedes_instead_of_erroring_the_turn(monkeypatch):
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("uuid-generator", {"count": 5}))

    def boom(*_a, **_k):
        raise RuntimeError("MCP-сервер умер")

    monkeypatch.setattr("model_tools.handle_function_call", boom)

    assert rule_cascade.try_turn(FakeAgent(), "дай 5 uuid", [], []) is None


def test_a_crashing_parser_cedes_instead_of_erroring_the_turn(monkeypatch):
    def boom(_q):
        raise RuntimeError("разбор упал")

    monkeypatch.setattr("digit_cli.ruleparse.route", boom)

    assert rule_cascade.try_turn(FakeAgent(), "посчитай md5 от x", [], []) is None


def test_disabled_cascade_never_looks_at_the_query(monkeypatch):
    monkeypatch.setattr(rule_cascade, "is_enabled", lambda agent=None: False)
    seen = []
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        lambda q: seen.append(q) or RuleDecision(routed=False))

    assert rule_cascade.try_turn(FakeAgent(), "посчитай md5 от x", [], []) is None
    assert seen == []


def test_switch_reads_the_environment(monkeypatch):
    # Настоящая функция, снятая до автопатча фикстуры _cascade_on.
    monkeypatch.setenv("DIGIT_RULE_CASCADE", "0")
    assert _REAL_IS_ENABLED() is False
    monkeypatch.setenv("DIGIT_RULE_CASCADE", "1")
    assert _REAL_IS_ENABLED() is True


def test_non_string_message_cedes():
    """Ход с картинками несёт список блоков, а не строку. Это не наш случай."""
    assert rule_cascade.try_turn(FakeAgent(), [{"type": "text"}], [], []) is None


# ---------------------------------------------------------------------------
# Срабатывание
# ---------------------------------------------------------------------------
def test_a_parsed_query_closes_the_turn_without_a_single_model_call(monkeypatch):
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("uuid-generator", {"count": 5}))
    executor = _executor(json.dumps({"ok": True, "result": {"uuids": ["a", "b"]}}))
    monkeypatch.setattr("model_tools.handle_function_call", executor)
    messages = [{"role": "user", "content": "дай 5 uuid"}]
    agent = FakeAgent()

    result = rule_cascade.try_turn(agent, "дай 5 uuid", messages, [])

    assert result is not None
    # Ноль обращений к провайдеру — то самое различие, ради которого каскад есть.
    assert result["api_calls"] == 0
    assert result["completed"] is True
    assert result["answered_by"] == "rules"
    assert result["rule_cascade"]["tool_id"] == "uuid-generator"
    # Публичный slug переведён в исполняемый идентификатор перед вызовом.
    assert executor.calls == [("tools_execute",
                               {"tool_id": "uuid_generate", "args": {"count": 5}})]
    # Ход дописан в историю и сохранён — уступка ничего не теряла, но и
    # срабатывание не должно рвать транскрипт.
    assert messages[-1]["role"] == "assistant"
    assert agent.persisted


def test_the_answer_says_who_produced_it(monkeypatch):
    """Происхождение видно на всякой поверхности, а не только в футере CLI."""
    monkeypatch.setattr("digit_cli.ruleparse.route",
                        _route_to("uuid-generator", {"count": 5}))
    monkeypatch.setattr("model_tools.handle_function_call",
                        _executor(json.dumps({"ok": True, "result": "…"})))

    result = rule_cascade.try_turn(FakeAgent(), "дай 5 uuid", [], [])

    assert result["final_response"].startswith("▸ Отвечено правилом разбора")
    assert "без обращения к модели" in result["final_response"]
    assert "uuid-generator" in result["final_response"]


@needs_dict
def test_end_to_end_with_the_real_rule_layer(monkeypatch):
    """Без подмены разбора: настоящий русский запрос доходит до исполнителя."""
    executor = _executor(json.dumps({"ok": True, "result": {"ulids": ["01H", "01J"]}}))
    monkeypatch.setattr("model_tools.handle_function_call", executor)

    result = rule_cascade.try_turn(FakeAgent(), "сгенерируй 3 ulid", [], [])

    assert result is not None
    assert result["rule_cascade"]["tool_id"] == "ulid-generator"
    assert executor.calls[0][1]["tool_id"] == "ulid_generate"


@needs_dict
def test_a_corpus_question_reaches_the_model_untouched():
    """Слой правил даёт ноль на ответах цитатой — и обязан не мешать модели."""
    assert rule_cascade.try_turn(
        FakeAgent(), "что рассказывают про морфизмы во втором модуле?", [], []) is None


# ---------------------------------------------------------------------------
# Наблюдаемость
# ---------------------------------------------------------------------------
def test_turn_footer_names_the_author_of_the_answer():
    collector = TurnSummaryCollector()
    collector.begin()
    collector.record_provenance("rules", "hash-text")

    line = collector.render(0.004)

    assert "answered by rules (hash-text)" in line


def test_footer_prints_even_for_a_turn_that_did_nothing_else():
    """Ход каскада длится миллисекунды и не зовёт инструментов.

    По общим правилам футер для такого хода был бы пуст — то есть про самый
    быстрый ход человеку не сказали бы ничего. Отметка происхождения ломает
    это молчание.
    """
    from agent.turn_summary import TurnTally

    silent = format_turn_summary(0.004, TurnTally())
    assert silent == ""

    tally = TurnTally()
    tally.answered_by = "rules"
    assert "answered by rules" in format_turn_summary(0.004, tally)


def test_an_ordinary_turn_is_not_labelled_as_a_rule_answer():
    """Отсутствие поля никогда не должно читаться как «ответило правило»."""
    collector = TurnSummaryCollector()
    collector.begin()
    collector.record_provenance(None)
    assert "answered by" not in collector.render(3.0)


# --------------------------------------------------------------------------
# Умолчание: каскад включается сам только позади локальной модели.
#
# Это не предпочтение, а следствие замера. Каскад не заменяет ошибки второй
# ступени, а складывается с ними: 8 промахов правил плюс 13 промахов модели
# дали ровно 21 у связки. Позади слабой локальной модели правила выигрывают,
# позади сильного шлюза — портят главную метрику (ложные ответы 0,2 % → 4,0 %).
# Поэтому «не знаю, кто вторая ступень» обязано читаться как «не включать».


def test_cascade_is_on_behind_a_remote_gateway_too(monkeypatch):
    """Позади сильной модели каскад тоже включён — с тех пор как починено извлечение.

    Раньше здесь стояло `is False`: замер показывал, что каскад складывает свои
    ошибки с ошибками модели и ложные ответы растут 1,5 % → 3,5 %. Тот проигрыш
    оказался не свойством каскада, а дефектом извлечения аргументов
    (DGT-DIGIT-10): правила брали операнд из самой инструкции. После починки —
    1,0 % против 1,5 % до моста, то есть каскад лучше обеих ног.
    """
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    agent = FakeAgent(provider="openrouter", base_url="https://openrouter.ai/api/v1")
    assert _REAL_IS_ENABLED(agent) is True


def test_cascade_turns_itself_on_for_a_local_provider(monkeypatch):
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    for provider in ("custom", "llamacpp", "ollama", "vllm", "lmstudio"):
        assert rule_cascade._second_stage_is_local(FakeAgent(provider=provider)) is True, provider


def test_a_local_address_counts_even_when_the_provider_is_unnamed(monkeypatch):
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    agent = FakeAgent(provider="", base_url="http://127.0.0.1:8127/v1")
    assert rule_cascade._second_stage_is_local(agent) is True


def test_a_hostname_that_merely_contains_localhost_is_not_local(monkeypatch):
    """`https://localhost.attacker.example` содержит «localhost» и локальным не является.

    Проверяется хост, а не подстрока: иначе чужой домен с таким именем включил
    бы каскад позади сильной модели — ровно там, где он вредит.
    """
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    agent = FakeAgent(provider="", base_url="https://localhost.attacker.example/v1")
    assert rule_cascade._second_stage_is_local(agent) is False


def test_an_explicit_setting_beats_the_default_in_both_directions(monkeypatch):
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    remote = FakeAgent(provider="openrouter")
    local = FakeAgent(provider="llamacpp")

    monkeypatch.setenv("DIGIT_RULE_CASCADE", "1")
    assert _REAL_IS_ENABLED(remote) is True, "явное включение не сработало позади шлюза"

    monkeypatch.setenv("DIGIT_RULE_CASCADE", "off")
    assert _REAL_IS_ENABLED(local) is False, "явное выключение не сработало на локальной модели"


def test_an_unknown_second_stage_reads_as_not_local(monkeypatch):
    monkeypatch.delenv("DIGIT_RULE_CASCADE", raising=False)
    monkeypatch.setattr("digit_cli.config.load_config", lambda: {})
    assert rule_cascade._second_stage_is_local(None) is False
    assert rule_cascade._second_stage_is_local(FakeAgent(provider="", base_url="")) is False
