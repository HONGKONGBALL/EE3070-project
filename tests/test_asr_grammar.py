"""Vosk destination grammars use actual model words and never drop OOV names."""

import json
from dataclasses import dataclass

import pytest

from cane.voice import asr


@dataclass(frozen=True, slots=True)
class Vocabulary:
    words: frozenset[str]

    def vosk_model_find_word(self, word: str) -> int:
        return 1 if word in self.words else -1


def test_chinese_grammar_splits_destination_into_supported_model_words():
    # Given a model lexicon containing Chinese words rather than the full name.
    vocabulary = Vocabulary(frozenset({"香港", "城市", "大学", "取消", "确认", "重试"}))
    # When the destination grammar is compiled.
    grammar = asr.compile_grammar(["香港城市大学"], "cmn", vocabulary)
    # Then its destination consists entirely of supported model tokens.
    assert grammar is not None
    assert "香港 城市 大学" in json.loads(grammar)


def test_english_grammar_preserves_word_boundaries():
    # Given English vocabulary and a multiword destination alias.
    vocabulary = Vocabulary(frozenset({"city", "university", "cancel", "confirm", "retry"}))
    # When the grammar is compiled.
    grammar = asr.compile_grammar(["City University"], "en", vocabulary)
    # Then English spaces are retained instead of concatenating the name.
    assert grammar is not None
    assert "city university" in json.loads(grammar)


def test_oov_destination_falls_back_to_unrestricted_recognition():
    # Given a known destination and a different name with no valid tokenization.
    vocabulary = Vocabulary(frozenset({"香港", "城市", "大学", "九龙", "站"}))
    # When either requested destination cannot be represented by the model.
    grammar = asr.compile_grammar(["香港城市大学", "九龙塘站"], "cmn", vocabulary)
    # Then it never silently forces the user into the remaining known name.
    assert grammar is None


def test_chinese_segmentation_backtracks_instead_of_greedy_oov():
    # Given a longer prefix that leaves an unknown suffix.
    vocabulary = Vocabulary(frozenset({"研究生", "研究", "生命"}))
    # When a complete valid segmentation exists via the shorter prefix.
    grammar = asr.compile_grammar(["研究生命"], "cmn", vocabulary)
    # Then dynamic programming retains that complete known-word path.
    assert grammar is not None
    assert "研究 生命" in json.loads(grammar)


def test_unknown_controls_do_not_become_invalid_grammar_entries():
    # Given a model that supports a destination but none of the control words.
    vocabulary = Vocabulary(frozenset({"香港", "大学"}))
    # When a destination grammar is compiled.
    grammar = asr.compile_grammar(["香港大学"], "cmn", vocabulary)
    # Then only supported names and the explicit unknown path are supplied.
    assert grammar is not None
    assert json.loads(grammar) == ["香港 大学", "[unk]"]


def test_cantonese_does_not_silently_load_a_mandarin_model(tmp_path):
    # Given a caller asking specifically for Cantonese recognition.
    # When it constructs the offline recognizer.
    # Then the unsupported input language is explicit before loading any model.
    with pytest.raises(asr.ASRUnavailable, match="unsupported.*yue"):
        asr.VoskRecognizer(tmp_path, "yue")
