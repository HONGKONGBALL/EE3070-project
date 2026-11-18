"""Spoken command parsing cannot silently select ambiguous places."""

import pytest
from cane.config import Destination
from cane.voice.destinations import (
    match_destination,
    parse_text_intent,
    spoken_place_query,
)

DESTS = [Destination("d1", "城市大学", ["城大", "city university"]), Destination("d2", "九龙塘站", ["九龙塘"])]


@pytest.mark.parametrize("text,kind", [("取消", "cancel"), ("cancel", "cancel"), ("确认", "confirm"), ("yes", "confirm"), ("重试", "retry"), ("say again", "retry")])
def test_command_intent_is_explicit(text, kind):
    # Given an exact command phrase.
    # When parsing user speech.
    intent = parse_text_intent(text, DESTS)
    # Then it remains a command with no destination attached.
    assert intent.kind == kind
    assert intent.destination is None


@pytest.mark.parametrize("text", ["大学", "城", "不要去城大", "城大还是九龙塘", "城大九龙塘", "不确认", "yes city university"])
def test_ambiguous_or_negative_speech_does_not_select_destination(text):
    # Given incomplete, negative or conflicting wording.
    # When matching presets.
    destination = match_destination(text, DESTS)
    # Then no destination is selected implicitly.
    assert destination is None


def test_equal_destination_aliases_are_ambiguous():
    # Given two configured places with the same spoken alias.
    dests = [Destination("a", "东门", ["入口"]), Destination("b", "西门", ["入口"])]
    # When that alias is spoken.
    destination = match_destination("入口", dests)
    # Then configuration order does not choose for the user.
    assert destination is None


@pytest.mark.parametrize("text,kind", [("確認", "confirm"), ("確認導航", "confirm"), ("停止", "cancel"), ("不要了", "cancel"), ("no", "cancel"), ("再試一次", "retry")])
def test_traditional_commands_and_negative_confirmation_are_explicit(text, kind):
    # Given a traditional-Chinese command or an explicit rejection.
    # When the intent is parsed.
    intent = parse_text_intent(text, DESTS)
    # Then it controls the pending choice without accidentally selecting a place.
    assert intent.kind == kind
    assert intent.destination is None


@pytest.mark.parametrize(("text", "query"), [
    ("去附近最近的健身房", "健身房"),
    ("帶我去離我最近的九龍塘站", "九龍塘站"),
    ("take me to the nearest City University please", "City University"),
    ("附近最近的儿子健身房", "儿子健身房"),
    ("我想去附近最近的 247 健身房", "24/7 Fitness"),
    ("24/7 fitness", "24/7 Fitness"),
    ("２４７健身房", "24/7 Fitness"),
    ("附近最近的二 四 七 健身房", "24/7 Fitness"),
    ("二十四七健身房", "24/7 Fitness"),
    ("附近最近的二四七路", "二四七路"),
    ("247", "247"),
    ("附近最近的", None),
    ("附近最近的不要去的 247 健身房", None),
    ("don't go to 24/7 fitness", None),
])
def test_spoken_place_query_extracts_keywords(text: str, query: str | None) -> None:
    assert spoken_place_query(text) == query
