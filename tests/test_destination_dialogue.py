"""K2 destination dialogue: Grok interprets speech, AMap supplies places, K1 confirms."""
import json
import threading
from types import SimpleNamespace

import pytest

from cane.config import Destination
from cane.vision.assistant_provider import AssistantError
from cane.vision.destination_dialogue import (
    DestinationDialogue, Place, PlacesUnavailable, distance_m, parse_reply, spoken_distance)
from cane.vision.navigation import PiNavigation
from cane.voice.places import PlaceWorkerError, ProviderPlace

from test_psyche_cloud import Harness


ORIGIN = (114.17, 22.336)


def place(index, metres):
    return Place(f"p{index}", f"麦当劳{index}号店", f"地址{index}", 114.17, 22.336, metres)


class FakeNavigation:
    def __init__(self, places=(), *, error=None):
        self.places, self.error = list(places), error
        self.queries, self.staged = [], []
        self.cleared = self.cancelled = 0
        self.active = False

    def find_places(self, query):
        self.queries.append(query)
        if self.error:
            raise PlacesUnavailable(self.error)
        return self.places

    def stage_place(self, chosen):
        self.staged.append(chosen)

    def clear_destination(self):
        self.cleared += 1

    def cancel_destination(self):
        self.cancelled += 1
        return self.active

    def status(self):
        return {"active": self.active}


class ScriptedGrok:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, *, request_id=None):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def test_search_reads_the_three_nearest_places_then_a_spoken_choice_is_staged():
    navigation = FakeNavigation([place(1, 820), place(2, 310), place(3, 1530), place(4, 2000)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"},
                        {"action": "choose", "choice": 1})
    dialogue = DestinationDialogue(grok, navigation)

    first = dialogue.handle("我想去附近的麦当劳")
    assert navigation.queries == ["麦当劳"]
    assert first == ("找到3个地点。第1个，麦当劳2号店，约310米。第2个，麦当劳1号店，约820米。"
                     "第3个，麦当劳3号店，约1.5公里。请按K2说要去第几个。")

    second = dialogue.handle("去最近的那个")
    assert navigation.staged == [place(2, 310)]
    assert second == "目的地，麦当劳2号店，约310米。按K1开始导航。"
    # Grok saw the candidates that were read out, in the same order.
    state = grok.calls[1][-2]["content"]
    assert "1. 麦当劳2号店" in state and "3. 麦当劳3号店" in state


def test_ordinal_reply_is_handled_without_the_cloud():
    navigation = FakeNavigation([place(1, 100), place(2, 200)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("麦当劳")
    assert dialogue.handle("第 二 个") == "目的地，麦当劳2号店，约200米。按K1开始导航。"
    assert len(grok.calls) == 1


def test_a_choice_outside_the_list_is_never_staged():
    navigation = FakeNavigation([place(1, 100), place(2, 200)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"}, {"action": "choose", "choice": 7})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("麦当劳")
    assert dialogue.handle("第七个") == "只有2个候选。请说第一个到第2个。"
    assert not navigation.staged


def test_single_result_is_chosen_and_waits_for_k1():
    navigation = FakeNavigation([place(1, 450)])
    dialogue = DestinationDialogue(ScriptedGrok({"action": "search", "query": "海岸城"}), navigation)
    assert dialogue.handle("海岸城") == "只找到一个地点。目的地，麦当劳1号店，约450米。按K1开始导航。"
    assert navigation.staged == [place(1, 450)]


def test_grok_follow_up_question_is_spoken():
    dialogue = DestinationDialogue(ScriptedGrok({"action": "ask", "reply": "你想去哪个商场？"}), FakeNavigation())
    assert dialogue.handle("我想去商场") == "你想去哪个商场？"


def test_voice_confirmation_still_requires_k1():
    navigation = FakeNavigation([place(1, 450)])
    grok = ScriptedGrok({"action": "search", "query": "海岸城"}, {"action": "confirm"})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("海岸城")
    assert dialogue.handle("好的出发") == "请按K1开始导航。"


def test_cancel_is_local_and_stops_an_active_route():
    navigation = FakeNavigation()
    navigation.active = True
    grok = ScriptedGrok()
    assert DestinationDialogue(grok, navigation).handle("取消导航") == "已停止导航。"
    assert navigation.cancelled == 1 and not grok.calls


@pytest.mark.parametrize("failure", [AssistantError("provider_timeout"), "not json", {"action": "fly"}])
def test_cloud_failure_falls_back_to_a_plain_search(failure):
    navigation = FakeNavigation([place(1, 100), place(2, 200)])
    dialogue = DestinationDialogue(ScriptedGrok(failure), navigation)
    answer = dialogue.handle("我想去麦当劳")
    assert navigation.queries == ["麦当劳"]
    assert answer.startswith("云端助手暂时不可用，直接为你搜索。找到2个地点。")


def test_place_search_errors_are_spoken():
    navigation = FakeNavigation(error="还没有收到手机定位。请在手机上开始共享定位。")
    dialogue = DestinationDialogue(ScriptedGrok({"action": "search", "query": "地铁站"}), navigation)
    assert dialogue.handle("地铁站") == "还没有收到手机定位。请在手机上开始共享定位。"


def test_no_results_ask_for_another_name():
    dialogue = DestinationDialogue(ScriptedGrok({"action": "search", "query": "火星"}), FakeNavigation())
    assert dialogue.handle("火星") == "附近没有找到火星。请换个说法再试。"


def test_state_resets_after_idle_and_after_the_route_starts():
    now = [0.0]
    navigation = FakeNavigation([place(1, 100), place(2, 200)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"}, {"action": "ask", "reply": "去哪里？"})
    dialogue = DestinationDialogue(grok, navigation, clock=lambda: now[0], idle_reset_s=60)
    dialogue.handle("麦当劳")
    dialogue.handle("第一个")
    navigation.active = True
    dialogue.handle("你好")
    assert dialogue.candidates == () and dialogue.chosen is None
    assert "目前没有候选地点" in grok.calls[1][-2]["content"]


def test_parse_reply_accepts_json_wrapped_in_text():
    assert parse_reply('好的 {"action": "search", "query": " 海岸 城 "} 。', 0) == ("search", "海岸 城", 0, "")
    with pytest.raises(AssistantError):
        parse_reply('{"action": "search", "query": ""}', 0)


def test_distance_and_spoken_distance():
    assert 1000 < distance_m((114.17, 22.336), 114.18, 22.336) < 1060
    assert spoken_distance(4) == "约10米"
    assert spoken_distance(1234) == "约1.2公里"


def test_k2_tap_runs_the_dialogue_and_speaks_its_answer(monkeypatch):
    navigation = FakeNavigation([place(1, 450)])
    navigation.submit_destination = lambda text: pytest.fail("legacy destination path used")
    replies = ScriptedGrok({"action": "search", "query": "海岸城"})
    with Harness(monkeypatch, transcript="我想去海岸城", navigation=navigation,
                 config={"answer_language": "zh", "question_language": "zh"}, chat=replies.chat) as h:
        h.ask("navigation_button")
    assert h.spoken == ["只找到一个地点。目的地，麦当劳1号店，约450米。按K1开始导航。"]
    assert navigation.staged == [place(1, 450)]


def test_mandarin_assistant_speaks_failures_in_mandarin(monkeypatch):
    def fail(question, jpeg, language, *, request_id=None):
        assert language == "zh"
        raise AssistantError("provider_timeout")

    with Harness(monkeypatch, fail, config={"answer_language": "zh", "question_language": "zh"}) as h:
        h.ask()
    assert h.spoken == ["云端服务超时，请再试一次。"]


def test_english_assistant_keeps_the_legacy_k2_path(monkeypatch):
    destinations = []
    navigation = FakeNavigation()
    navigation.submit_destination = destinations.append
    with Harness(monkeypatch, transcript="CityU Library", navigation=navigation) as h:
        h.ask("navigation_button")
    assert destinations == ["CityU Library"]


class FakeSearch:
    def __init__(self):
        self.id = "search-1"

    def reset(self):
        self.id = None


def pi_navigation(results=None, *, fresh=True, active=False):
    app = SimpleNamespace(
        _state_lock=threading.RLock(), cfg=SimpleNamespace(nav=SimpleNamespace(enabled=True)), amap=object(),
        pi_places=SimpleNamespace(search=results), manual_search_origin=None, pending_dest=None,
        destination_search=FakeSearch(), arbiter=SimpleNamespace(discard_information=lambda: None),
        navigator=SimpleNamespace(active=active), _route_pending=False, stopped=[])
    app._search_origin_point = lambda: ORIGIN if fresh else None
    app._location_fresh = lambda: fresh
    app.stop_navigation = lambda announce=True: app.stopped.append(announce)
    navigation = object.__new__(PiNavigation)
    navigation.controller = app
    return navigation, app


def test_pi_navigation_finds_places_with_distance_and_stages_for_k1():
    found = ProviderPlace(id="B0", name="海岸城", address="文心五路", lng=114.18, lat=22.336,
                          coordinate_frame="WGS84")
    navigation, app = pi_navigation(lambda query, origin: (found,))
    [result] = navigation.find_places("海岸城")
    assert result.name == "海岸城" and 1000 < result.distance_m < 1100
    navigation.stage_place(result)
    assert app.pending_dest == Destination("B0", "海岸城", lng=114.18, lat=22.336, address="文心五路",
                                           coordinate_frame="WGS84")
    assert app.destination_search.id is None  # K1 confirms only when no screen choice is pending


def test_pi_navigation_reports_missing_location_and_search_failure():
    navigation, _ = pi_navigation(lambda query, origin: (), fresh=False)
    with pytest.raises(PlacesUnavailable, match="手机定位"):
        navigation.find_places("海岸城")

    def broken(query, origin):
        raise PlaceWorkerError("down")

    navigation, _ = pi_navigation(broken)
    with pytest.raises(PlacesUnavailable, match="地图搜索失败"):
        navigation.find_places("海岸城")


def test_pi_navigation_cancel_stops_an_active_route_or_drops_the_choice():
    navigation, app = pi_navigation(active=True)
    assert navigation.cancel_destination() and app.stopped == [False]
    navigation, app = pi_navigation()
    app.pending_dest = object()
    assert not navigation.cancel_destination() and app.pending_dest is None
