"""Голос Edge подбирается под язык текста.

Регрессия, найденная запуском: голос по умолчанию ``en-US-AriaNeural`` на
русском тексте отдаёт не плохое произношение, а НИЧЕГО — служба закрывает
поток, ``edge-tts`` поднимает ``NoAudioReceived``, на диске остаётся файл в
0 байт. Тот же текст голосом ``ru-RU-SvetlanaNeural`` даёт 20 КБ звука.
То есть «Digit не говорит по-русски» было про полное молчание, а не про
качество.
"""

from __future__ import annotations

import pytest

from tools.tts_tool import (
    DEFAULT_EDGE_VOICE,
    DEFAULT_EDGE_VOICE_CYRILLIC,
    _edge_voice_for_text,
    _looks_cyrillic,
)


def test_cyrillic_text_gets_a_russian_voice():
    assert _edge_voice_for_text("Проверка синтеза речи.") == DEFAULT_EDGE_VOICE_CYRILLIC


def test_latin_text_keeps_the_english_default():
    assert _edge_voice_for_text("A synthesis check.") == DEFAULT_EDGE_VOICE


def test_a_voice_the_user_chose_is_never_overridden():
    """Подменить явно выбранный голос — значит не выполнить просьбу."""
    assert _edge_voice_for_text("Проверка.", "en-GB-SoniaNeural") == "en-GB-SoniaNeural"
    assert _edge_voice_for_text("Проверка.", "ru-RU-DmitryNeural") == "ru-RU-DmitryNeural"


def test_the_baked_in_default_does_not_count_as_a_choice():
    """Загрузчик настроек подставляет ``DEFAULT_EDGE_VOICE`` всем, кто ничего
    не выбирал, поэтому по конфигу «выбрал Арию» и «не выбирал» неразличимы.
    Считаем такое значение невыбранным: терять нечего, ровно эта пара — Ария
    плюс кириллица — и даёт ноль байт."""
    assert _edge_voice_for_text("Проверка.", DEFAULT_EDGE_VOICE) == DEFAULT_EDGE_VOICE_CYRILLIC


@pytest.mark.parametrize(
    "text,cyrillic",
    [
        ("Проверка синтеза", True),
        ("A synthesis check", False),
        # Латиница в русской фразе — обычное дело (названия, версии), и
        # порог по большинству букв не должен на этом опрокидываться.
        ("Сборка Digit версии 2 собрана", True),
        # А английская фраза с одним русским словом остаётся английской.
        ("The build for проект is ready and fully tested", False),
        ("", False),
        ("1234 !!! ...", False),
    ],
)
def test_the_language_test_is_a_majority_of_letters(text, cyrillic):
    assert _looks_cyrillic(text) is cyrillic
