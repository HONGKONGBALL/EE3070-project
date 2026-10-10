"""Match recognised text to one of the 2-3 preset destinations."""

from __future__ import annotations

import difflib
import re
import unicodedata

from ..config import Destination

# a few traditional -> simplified characters likely in HK place names
_T2S = str.maketrans({"龍": "龙", "東": "东", "門": "门", "學": "学", "鐵": "铁", "廣": "广", "場": "场",
                      "區": "区", "灣": "湾", "國": "国", "醫": "医", "園": "园", "車": "车"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().translate(_T2S)
    return re.sub(r"[\s\W_]+", "", text)


def match_destination(text: str, dests: list[Destination], cutoff: float = 0.6) -> Destination | None:
    t = normalise(text)
    if not t:
        return None
    best: tuple[float, Destination | None] = (0.0, None)
    for d in dests:
        for alias in [d.name, *d.aliases]:
            a = normalise(alias)
            if not a:
                continue
            if a in t or (len(t) >= 2 and t in a):
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, a, t).ratio()
            if score > best[0]:
                best = (score, d)
    return best[1] if best[0] >= cutoff else None
