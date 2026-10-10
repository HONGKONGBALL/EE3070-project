import pytest

from cane.config import Destination
from cane.voice.asr import TextRecognizer
from cane.voice.destinations import match_destination, normalise

DESTS = [
    Destination("dest1", "香港城市大学", ["城大", "城市大学", "city university", "cityu"]),
    Destination("dest2", "九龙塘站", ["九龙塘", "kowloon tong"]),
]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我想去城大", "dest1"),
        ("去九龍塘", "dest2"),
        ("Kowloon Tong please", "dest2"),
        ("City University", "dest1"),
        ("九龙糖站", "dest2"),  # ASR homophone error
        ("铜锣湾", None),
        ("", None),
    ],
)
def test_match(text, expected):
    d = match_destination(text, DESTS)
    assert (d.id if d else None) == expected


def test_normalise():
    assert normalise("  九龍 塘！") == "九龙塘"


def test_text_recognizer():
    assert TextRecognizer("城大").transcribe("x.wav") == "城大"
