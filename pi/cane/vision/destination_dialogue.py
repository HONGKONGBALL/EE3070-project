"""Choose a walking destination by talking with Grok, without looking at a screen.

Grok only interprets what the user said. AMap supplies the places, the Pi checks every choice
against the places it read out, and K1 starts the route. Grok never plans a route or gives
directions. When the cloud is unavailable, the dialogue falls back to a plain AMap search.

The utterance comes from a K2 turn or follows the wake phrase. After the wake phrase it can also
be a question about the view: wants() is the local guess, and Grok may answer "other".
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import re
import time
import unicodedata

from ..voice.destinations import spoken_place_query
from .assistant_provider import AssistantError


LOG = logging.getLogger(__name__)
MAX_CANDIDATES = 3
HISTORY_TURNS = 6
IDLE_RESET_S = 120.0
ACTIONS = {"search", "choose", "ask", "cancel", "confirm"}
# Only offered to Grok for an utterance that followed the wake phrase.
OTHER_NOTE = ("这句话是用户用语音唤醒手杖后直接说的，也可能与目的地无关，例如询问眼前的景物、能不能往前走，或者闲聊。"
              '如果它与选择或取消目的地无关，只输出 {"action": "other"}。')

SYSTEM_PROMPT = """你是盲人智能手杖上的目的地助手。用户看不见屏幕，只能听你说话、按手杖上的按键。
你唯一的任务是帮用户确定一个步行目的地。你不能规划路线，不能给出方向，也不能判断道路是否安全。

每次只输出一个 JSON 对象，不要输出任何其他文字：
{"action": "search|choose|ask|cancel|confirm", "query": "", "choice": 0, "reply": ""}

- search：用户说出了想去的地点、店名或地点类型。query 填适合在地图里搜索的简短中文关键词，
  去掉“我想去”“附近的”“最近的”之类的说法，例如“海岸城”“麦当劳”“地铁站”“香港城市大学”。
  如果用户换了一个新地点，也用 search。
- choose：系统已经读出候选地点，用户选了其中一个（例如“第二个”“去那个麦当劳”“最近的那个”）。
  choice 填候选编号，从 1 开始，只能填候选列表里有的编号。
- ask：听不懂、或者信息不够，需要追问。reply 填一句简短的中文追问，不超过 25 个字。
- cancel：用户说不去了、取消、停止导航。
- confirm：用户表示同意、确认、出发。

用户的话来自离线语音识别，可能有错字、同音字或多余的空格，请按读音合理推测。
不要编造地点、地址或距离。地点只能来自系统给你的候选列表。"""

ORDINALS = {"一": 1, "1": 1, "首": 1, "二": 2, "两": 2, "2": 2, "三": 3, "3": 3}
_ORDINAL = re.compile(r"^(?:就|要|去|选|是|就是|那就|我要|我选)?第?([一二两三123])(?:个|家|间|处)?(?:吧|啊|呀)?$")
_CANCEL = {"取消", "算了", "不去了", "不要了", "停止", "停止导航", "取消导航", "结束导航", "不用了"}
# Wordings that mean a place, for an utterance that followed the wake phrase.
_DESTINATION = re.compile(
    r"导航|路线|目的地|出发|前往|怎么走|怎么去|怎么到|带我|送我|陪我去"
    r"|(?:想|要|我们|咱们|我)去(?!看看|看一|问|试)|^去.{2,}|不去了")
# Wordings that often mean a place but also fit a question about the view. Grok decides.
_MAYBE_DESTINATION = re.compile(r"附近|周围|最近的|(?:在哪|在哪里|在哪儿|在什么地方)$")
# Wordings that pick one of the places just read out.
_CHOICE = re.compile(r"第[一二两三123]|[一二两三123](?:个|家|间|号)|那个|这个|最近|最远|近的|远的|确认|确定|就它|好的?$")


@dataclass(frozen=True, slots=True)
class Place:
    id: str
    name: str
    address: str
    lng: float
    lat: float
    distance_m: float | None = None


class PlacesUnavailable(Exception):
    """The place search cannot run. The message is spoken to the user."""


def distance_m(origin, lng, lat):
    """Great-circle distance in metres between two WGS84 points."""
    lng0, lat0 = map(math.radians, origin)
    lng1, lat1 = math.radians(lng), math.radians(lat)
    a = math.sin((lat1 - lat0) / 2) ** 2 + math.cos(lat0) * math.cos(lat1) * math.sin((lng1 - lng0) / 2) ** 2
    return 6_371_000 * 2 * math.asin(min(1.0, math.sqrt(a)))


def spoken_distance(metres):
    if metres is None:
        return ""
    if metres < 1000:
        return f"约{max(10, round(metres / 10) * 10)}米"
    return f"约{metres / 1000:.1f}公里"


def _short_name(name):
    name = " ".join(name.split())
    return name if len(name) <= 24 else name[:24]


def _compact(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s，。！？,.!?、]+", "", text)


def parse_reply(text, candidate_count, *, allow_other=False):
    """Accept only one well-formed action. Anything else is treated as a failed cloud turn."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AssistantError("invalid_reply")
    try:
        data = json.loads(match[0])
    except json.JSONDecodeError:
        raise AssistantError("invalid_reply") from None
    if allow_other and isinstance(data, dict) and data.get("action") == "other":
        return "other", "", 0, ""
    if not isinstance(data, dict) or data.get("action") not in ACTIONS:
        raise AssistantError("invalid_reply")
    action = data["action"]
    query = data.get("query") if isinstance(data.get("query"), str) else ""
    reply = data.get("reply") if isinstance(data.get("reply"), str) else ""
    choice = data.get("choice")
    if action == "search" and not query.strip():
        raise AssistantError("invalid_reply")
    if action == "choose" and not (type(choice) is int and 1 <= choice <= candidate_count):
        # Grok picked a place that was never read out. Never guess: list the valid numbers again.
        return "choose", "", 0, ""
    if action == "ask" and not reply.strip():
        raise AssistantError("invalid_reply")
    return action, " ".join(query.split())[:40], choice if action == "choose" else 0, " ".join(reply.split())[:60]


class DestinationDialogue:
    def __init__(self, provider, navigation, *, clock=time.monotonic, idle_reset_s=IDLE_RESET_S):
        self.provider, self.navigation = provider, navigation
        self.clock, self.idle_reset_s = clock, idle_reset_s
        self.history = []
        self.candidates = ()
        self.chosen = None
        self.last_turn = float("-inf")
        self.last_action = ""

    def reset(self):
        self.history.clear()
        self.candidates, self.chosen = (), None

    def status(self):
        return {"candidates": [{"name": place.name, "distance_m": place.distance_m} for place in self.candidates],
                "chosen": self.chosen.name if self.chosen else None, "last_action": self.last_action}

    def wants(self, text, *, surely=False):
        """The local guess for an utterance after the wake phrase: is it about choosing a place?

        With surely, only wordings that leave no doubt count.
        """
        compact = _compact(text)
        if _DESTINATION.search(compact) or compact in _CANCEL:
            return True
        listed = bool(self.candidates) and self.clock() - self.last_turn <= self.idle_reset_s
        if listed and (_ORDINAL.fullmatch(compact) or _CHOICE.search(compact)):
            return True
        return not surely and bool(_MAYBE_DESTINATION.search(compact))

    def handle(self, text, *, request_id=None, open_question=False):
        """Return the sentence to speak for one utterance.

        With open_question the utterance followed the wake phrase and may not be about a place.
        The result is then None when Grok says so, and the dialogue is left untouched.
        """
        now = self.clock()
        if now - self.last_turn > self.idle_reset_s:
            self.reset()
        elif self.chosen is not None and self.navigation.status().get("active"):
            # K1 started the route. The next utterance starts a new choice.
            self.reset()
        previous_turn, self.last_turn = self.last_turn, now
        compact = _compact(text)
        # Short, unambiguous replies are handled locally: faster, and they work without the cloud.
        ordinal = _ORDINAL.fullmatch(compact)
        if ordinal and self.candidates:
            return self._remember(text, "choose", self._choose(ORDINALS[ordinal[1]]))
        if compact in _CANCEL:
            return self._remember(text, "cancel", self._cancel())
        try:
            action, query, choice, reply = parse_reply(
                self.provider.chat(self._messages(text, open_question), request_id=request_id),
                len(self.candidates), allow_other=open_question)
        except AssistantError as error:
            LOG.warning("Destination dialogue fell back to a local search: %s", error)
            return self._remember(text, "fallback", self._fallback(text))
        if action == "other":
            self.last_turn = previous_turn
            return None
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": json.dumps(
            {"action": action, "query": query, "choice": choice, "reply": reply}, ensure_ascii=False)})
        del self.history[:-2 * HISTORY_TURNS]
        self.last_action = action
        if action == "search":
            return self._search(query)
        if action == "choose":
            return self._choose(choice)
        if action == "cancel":
            return self._cancel()
        if action == "confirm":
            if self.chosen is not None:
                return "请按K1开始导航。"
            return "还没有选定目的地。请告诉我你想去哪里。"
        if not reply:
            return "请再说一次你想去哪里。"
        return reply

    def _remember(self, text, action, answer):
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": json.dumps({"action": action}, ensure_ascii=False)})
        del self.history[:-2 * HISTORY_TURNS]
        self.last_action = action
        return answer

    def _messages(self, text, open_question=False):
        if self.candidates:
            lines = [f"{index}. {place.name}（{place.address or '无地址'}，{spoken_distance(place.distance_m)}）"
                     for index, place in enumerate(self.candidates, 1)]
            state = "已读给用户的候选地点：\n" + "\n".join(lines)
        else:
            state = "目前没有候选地点。"
        if self.chosen is not None:
            state += f"\n用户已选定：{self.chosen.name}，等待按 K1 确认。"
        if open_question:
            state += "\n" + OTHER_NOTE
        return [{"role": "system", "content": SYSTEM_PROMPT},
                *self.history,
                {"role": "system", "content": state},
                {"role": "user", "content": text}]

    def _search(self, query):
        self.candidates, self.chosen = (), None
        self.navigation.clear_destination()
        try:
            places = self.navigation.find_places(query)
        except PlacesUnavailable as error:
            return str(error)
        places = sorted(places, key=lambda place: (place.distance_m is None, place.distance_m or 0))[:MAX_CANDIDATES]
        if not places:
            return f"附近没有找到{query}。请换个说法再试。"
        self.candidates = tuple(places)
        if len(places) == 1:
            return "只找到一个地点。" + self._choose(1)
        parts = [f"第{index}个，{_short_name(place.name)}，{spoken_distance(place.distance_m)}"
                 for index, place in enumerate(places, 1)]
        return f"找到{len(places)}个地点。" + "。".join(parts) + "。请按K2说要去第几个。"

    def _choose(self, number):
        if not 1 <= number <= len(self.candidates):
            return f"只有{len(self.candidates)}个候选。请说第一个到第{len(self.candidates)}个。"
        place = self.candidates[number - 1]
        try:
            self.navigation.stage_place(place)
        except PlacesUnavailable as error:
            return str(error)
        self.chosen = place
        distance = spoken_distance(place.distance_m)
        return f"目的地，{_short_name(place.name)}" + (f"，{distance}" if distance else "") + "。按K1开始导航。"

    def _cancel(self):
        self.reset()
        return "已停止导航。" if self.navigation.cancel_destination() else "好的，已取消。"

    def _fallback(self, text):
        query = spoken_place_query(text)
        if query is None:
            return "云端助手暂时不可用。请按K2，直接说出地点名称。"
        return "云端助手暂时不可用，直接为你搜索。" + self._search(query)
