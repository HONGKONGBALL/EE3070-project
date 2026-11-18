"""Conservative preset matching and explicit spoken command intents."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..config import Destination

_T2S: Final = str.maketrans({"龍": "龙", "東": "东", "門": "门", "學": "学", "鐵": "铁", "廣": "广", "場": "场",
                            "區": "区", "灣": "湾", "國": "国", "醫": "医", "園": "园", "車": "车", "認": "认", "試": "试", "確": "确", "導": "导", "說": "说"})
_CANCEL: Final = {"取消", "停止", "结束", "不要了", "no", "stop", "停止导航", "取消导航", "唔去", "cancel", "stopnavigation", "never mind", "nevermind"}
_CONFIRM: Final = {"确认", "确认目的地", "确认导航", "开始导航", "确定", "是", "好", "yes", "confirm", "ok", "okay"}
_RETRY: Final = {"重试", "再试一次", "重来", "again", "再说一次", "重新识别", "retry", "tryagain", "sayagain"}
_PREFIXES: Final = ("我想去", "我想要去", "带我去", "我要去", "导航到", "去", "takemeto", "iwanttogoto", "iwantto", "navigateto", "goto")
_SUFFIXES: Final = ("please", "谢谢", "唔该")
_NEGATIVE: Final = ("不要", "不去", "别去", "唔去", "取消", "不是", "唔系", "不确认", "dont", "donot", "cancel")
_SEARCH_PREFIX: Final = re.compile(
    r"^(?:我想要去|我想去|带我去|帶我去|我要去|导航到|導航到|去|take me to\b|i want to go to\b|go to\b)\s*",
    re.IGNORECASE,
)
_PROXIMITY_PREFIX: Final = re.compile(
    r"^(?:(?:我)?附近的?(?:最近的)?|(?:[离離]我)?最近的|(?:the\s+)?(?:nearest|closest|nearby)\s+)\s*",
    re.IGNORECASE,
)
_FITNESS_BRAND: Final = re.compile(r"(?:247|24\s*/\s*7|二四七|二十四七)\s*(?:健身[房室]|fitness)", re.IGNORECASE)
_ORIGIN_PREFIX: Final = re.compile(
    r"^(?:把)?\s*(?:定位|出发点|出發點|起点|起點)\s*(?:改为|改為|改成|改到|换成|換成|设在|設在)\s*",
)


class TextIntentKind(StrEnum):
    DESTINATION = "destination"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    RETRY = "retry"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TextIntent:
    kind: TextIntentKind
    destination: Destination | None = None


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().translate(_T2S)
    return re.sub(r"[\s\W_]+", "", text)


def _destination_text(text: str) -> str:
    """Remove only known request wrappers, never arbitrary partial names."""
    result = normalise(text)
    for prefix in _PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break
    for suffix in _SUFFIXES:
        if result.endswith(suffix):
            result = result[:-len(suffix)]
            break
    return result


def spoken_place_query(text: str) -> str | None:
    """Extract one positive place query while preserving spaces in place names."""
    query = unicodedata.normalize("NFKC", text).strip(" \t\n.,!?，。！？")
    query = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", query)
    query = _SEARCH_PREFIX.sub("", query, count=1).strip()
    query = _PROXIMITY_PREFIX.sub("", query, count=1)
    query = re.sub(r"(?:\bplease|谢谢|謝謝|唔该|唔該)$", "", query, flags=re.IGNORECASE).strip()
    compact = normalise(query)
    if len(compact) < 2 or len(query) > 80 or any(word in compact for word in _NEGATIVE):
        return None
    if _FITNESS_BRAND.fullmatch(query):
        return "24/7 Fitness"
    return query


def spoken_origin_query(text: str) -> str | None:
    """Recognize an explicit request to change the departure point."""
    phrase = unicodedata.normalize("NFKC", text).strip()
    prefix = _ORIGIN_PREFIX.match(phrase)
    return spoken_place_query(phrase[prefix.end():]) if prefix else None


def asks_for_nearby(text: str) -> bool:
    phrase = unicodedata.normalize("NFKC", text).strip()
    return _PROXIMITY_PREFIX.match(_SEARCH_PREFIX.sub("", phrase, count=1)) is not None


def match_destination(text: str, dests: list[Destination], cutoff: float = 0.75) -> Destination | None:
    """Match a whole alias or a small ASR typo; reject ambiguous/negative speech."""
    t = _destination_text(text)
    if len(t) < 2 or any(word in t for word in _NEGATIVE):
        return None
    ranked: list[tuple[float, Destination]] = []
    for destination in dests:
        score = 0.0
        for alias in [destination.name, *destination.aliases]:
            a = normalise(alias)
            if a == t:
                score = 1.0
                break
            # Partial aliases and concatenated destinations are deliberately refused.
            if min(len(a), len(t)) >= 3 and abs(len(a) - len(t)) <= 1 and a not in t and t not in a:
                score = max(score, difflib.SequenceMatcher(None, a, t).ratio())
        if score >= max(0.75, cutoff):
            ranked.append((score, destination))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.15):
        return None
    return ranked[0][1]


def parse_text_intent(text: str, dests: list[Destination]) -> TextIntent:
    """Parse exact control phrases before matching a preset destination."""
    t = normalise(text)
    if t in _CANCEL:
        return TextIntent(TextIntentKind.CANCEL)
    if t in _CONFIRM:
        return TextIntent(TextIntentKind.CONFIRM)
    if t in _RETRY:
        return TextIntent(TextIntentKind.RETRY)
    destination = match_destination(text, dests)
    if destination is not None:
        return TextIntent(TextIntentKind.DESTINATION, destination)
    return TextIntent(TextIntentKind.UNKNOWN)
