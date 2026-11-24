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

from test_psyche_cloud import Harness, SaidOnce


ORIGIN = (114.17, 22.336)


def place(index, metres):
    return Place(f"p{index}", f"麦当劳{index}号店", f"地址{index}", 114.17, 22.336, metres)


class FakeNavigation:
    def __init__(self, places=(), *, error=None):
        self.places, self.error = list(places), error
        self.queries, self.staged = [], []
        self.cleared = self.cancelled = 0
        self.active = False

    def find_places(self, query, language=None):
        self.queries.append(query)
        self.languages = [*getattr(self, "languages", []), language]
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


@pytest.mark.parametrize("text", ["第二个", "是第二个", "就是第二个吧", "那就第二个", "我选二", "2"])
def test_plain_ways_of_saying_an_ordinal_need_no_cloud(text):
    navigation = FakeNavigation([place(1, 820), place(2, 310)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("麦当劳")
    dialogue.handle(text)
    assert navigation.staged == [place(1, 820)] and len(grok.calls) == 1


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


ZH = {"answer_language": "zh", "question_language": "zh"}


def test_place_after_the_wake_phrase_runs_the_dialogue_without_cloud_vision(monkeypatch):
    navigation = FakeNavigation([place(1, 450)])
    replies = ScriptedGrok({"action": "search", "query": "地铁站"})
    with Harness(monkeypatch, navigation=navigation, config=ZH, chat=replies.chat,
                 detector=SaidOnce("我想去最近的地铁站")) as h:
        assert h.idle.wait(3)
    assert navigation.queries == ["地铁站"]
    assert h.spoken == ["只找到一个地点。目的地，麦当劳1号店，约450米。按K1开始导航。"]
    assert not h.questions
    assert "与目的地无关" not in replies.calls[0][-2]["content"]  # a clear request is not second-guessed


def test_question_after_the_wake_phrase_is_answered_from_the_camera(monkeypatch):
    prompts = []

    def describe(question, jpeg, language, *, request_id=None, prompt=None):
        prompts.append(prompt)
        return "前面有一把椅子。"

    replies = ScriptedGrok()
    with Harness(monkeypatch, describe, navigation=FakeNavigation(), config=ZH, chat=replies.chat,
                 detector=SaidOnce("前面有什么")) as h:
        assert h.idle.wait(3)
    assert h.spoken == ["前面有一把椅子。"]
    assert not replies.calls
    assert "NAVIGATE" in prompts[0]


def test_grok_hands_a_place_it_was_asked_about_over_to_the_dialogue(monkeypatch):
    navigation = FakeNavigation([place(1, 450)])
    replies = ScriptedGrok({"action": "search", "query": "星巴克"})
    with Harness(monkeypatch, lambda *args, **options: "NAVIGATE", navigation=navigation, config=ZH,
                 chat=replies.chat, detector=SaidOnce("星巴克")) as h:
        assert h.idle.wait(3)
    assert navigation.queries == ["星巴克"]
    assert h.spoken == ["只找到一个地点。目的地，麦当劳1号店，约450米。按K1开始导航。"]


def test_dialogue_passes_a_question_that_only_sounded_like_a_place_to_the_camera(monkeypatch):
    prompts = []

    def describe(question, jpeg, language, *, request_id=None, prompt=None):
        prompts.append(prompt)
        return "手机在你左手边的桌子上。"

    navigation = FakeNavigation()
    replies = ScriptedGrok({"action": "other"})
    with Harness(monkeypatch, describe, navigation=navigation, config=ZH, chat=replies.chat,
                 detector=SaidOnce("我的手机在哪")) as h:
        assert h.idle.wait(3)
    assert "与目的地无关" in replies.calls[0][-2]["content"]
    assert h.spoken == ["手机在你左手边的桌子上。"]
    assert prompts == [None]  # no second hand-over
    assert not navigation.queries and not navigation.cleared


def test_spoken_choice_after_the_wake_phrase_picks_from_the_places_read_out(monkeypatch):
    navigation = FakeNavigation([place(1, 450), place(2, 300)])
    replies = ScriptedGrok()
    with Harness(monkeypatch, navigation=navigation, config=ZH, chat=replies.chat,
                 detector=SaidOnce("第二个")) as h:
        dialogue = h.assistant.dialogue
        dialogue.candidates, dialogue.last_turn = (place(2, 300), place(1, 450)), dialogue.clock()
        assert h.idle.wait(3)
    assert navigation.staged == [place(1, 450)]
    assert not replies.calls and not h.questions


def test_walk_question_after_the_wake_phrase_never_goes_to_the_dialogue(monkeypatch):
    replies = ScriptedGrok()
    with Harness(monkeypatch, navigation=FakeNavigation(), config=ZH, chat=replies.chat,
                 detector=SaidOnce("前面可以过去吗")) as h:
        assert h.idle.wait(3)
    assert h.spoken == ["只凭一个画面，我无法判断能不能安全通过。"]
    assert not replies.calls


@pytest.mark.parametrize("text", ["我想去最近的地铁站", "带我去麦当劳", "导航到海岸城", "附近有便利店吗", "厕所在哪",
                                  "去香港城市大学", "取消", "不去了"])
def test_wordings_that_mean_a_place(text):
    assert DestinationDialogue(ScriptedGrok(), FakeNavigation()).wants(text)


@pytest.mark.parametrize("text", ["前面有什么", "这是什么颜色", "帮我看看桌上有什么", "第一个", "他过去了吗", "今天星期几"])
def test_wordings_that_do_not_mean_a_place(text):
    assert not DestinationDialogue(ScriptedGrok(), FakeNavigation()).wants(text)


@pytest.mark.parametrize("text", ["附近有便利店吗", "厕所在哪", "我的手机在哪"])
def test_wordings_that_leave_doubt_are_decided_by_grok(text):
    dialogue = DestinationDialogue(ScriptedGrok(), FakeNavigation())
    assert dialogue.wants(text) and not dialogue.wants(text, surely=True)


def test_other_is_only_accepted_after_the_wake_phrase():
    assert parse_reply('{"action": "other"}', 0, allow_other=True)[0] == "other"
    with pytest.raises(AssistantError):
        parse_reply('{"action": "other"}', 0)


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


# --- English at K2 -----------------------------------------------------------------------------

def station(index, metres):
    return Place(f"e{index}", f"Station {index}", "", 114.17, 22.336, metres)


def test_english_request_is_answered_in_english():
    navigation = FakeNavigation([station(1, 820), station(2, 310), station(3, 1530)])
    grok = ScriptedGrok({"action": "search", "query": "MTR station"})
    dialogue = DestinationDialogue(grok, navigation)
    # As the recognizer writes it: upper case, with a slip.
    first = dialogue.handle("I WANT TO GO TO THE NEAREST EMR STATION")
    assert navigation.queries == ["MTR station"]
    assert first == ("I found 3 places. 1: Station 2, about 310 metres. 2: Station 1, about 820 metres. "
                     "3: Station 3, about 1.5 kilometres. Press K2 and say which one.")
    # Grok got the English instructions and the state in English.
    assert grok.calls[0][0]["content"].startswith("You are the destination assistant")
    assert grok.calls[0][-2]["content"] == "There are no candidate places yet."
    # A spoken number needs no cloud.
    assert dialogue.handle("THE SECOND ONE.") == "Destination: Station 1, about 820 metres. Press K1 to start."
    assert dialogue.handle("Yes, please.") == "Press K1 to start."
    assert navigation.staged == [station(1, 820)] and len(grok.calls) == 1
    assert dialogue.language == "en"
    # The map is asked for names in the language the request was spoken in.
    assert navigation.languages == ["en"]


@pytest.mark.parametrize("text", ["the second one", "Second.", "number two", "2", "I'll take the second one",
                                  "option two please", "TWO"])
def test_plain_english_ordinals_need_no_cloud(text):
    navigation = FakeNavigation([station(1, 820), station(2, 310)])
    grok = ScriptedGrok({"action": "search", "query": "MTR station"})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("MTR station")
    dialogue.handle(text)
    assert navigation.staged == [station(1, 820)] and len(grok.calls) == 1


def test_english_choice_outside_the_list_and_cancel():
    navigation = FakeNavigation([station(1, 100), station(2, 200)])
    grok = ScriptedGrok({"action": "search", "query": "MTR station"}, {"action": "choose", "choice": 7},
                        {"action": "confirm"})
    dialogue = DestinationDialogue(grok, navigation)
    dialogue.handle("MTR station")
    assert dialogue.handle("the seventh one") == "There are only 2 places. Say a number from 1 to 2."
    assert dialogue.handle("okay let's go") == "No destination is chosen yet. Tell me where you want to go."
    assert dialogue.handle("Never mind.") == "All right, cancelled."
    assert not navigation.staged and len(grok.calls) == 3


def test_reply_language_follows_each_utterance_unless_it_is_fixed():
    navigation = FakeNavigation([place(1, 100), place(2, 200)])
    grok = ScriptedGrok({"action": "search", "query": "麦当劳"}, {"action": "search", "query": "McDonald's"},
                        {"action": "search", "query": "麦当劳"})
    dialogue = DestinationDialogue(grok, navigation)
    assert dialogue.handle("麦当劳").startswith("找到2个地点。")
    assert dialogue.handle("McDonald's").startswith("I found 2 places.")
    assert grok.calls[1][0]["content"].startswith("You are the destination assistant")
    # The language K2 is set to wins over what was heard.
    assert dialogue.handle("McDonald's", language="zh").startswith("找到2个地点。")
    assert grok.calls[2][0]["content"].startswith("你是盲人智能手杖上的目的地助手")


def test_map_failure_is_spoken_in_english_for_an_english_turn():
    navigation = FakeNavigation(error="还没有收到手机定位。请在手机上开始共享定位。")
    dialogue = DestinationDialogue(ScriptedGrok({"action": "search", "query": "hospital"}), navigation)
    assert dialogue.handle("the nearest hospital") == (
        "There is no phone location yet. Start sharing your location in the phone app.")
    nothing = DestinationDialogue(ScriptedGrok({"action": "search", "query": "hospital"}), FakeNavigation())
    assert nothing.handle("the nearest hospital") == "I found no hospital nearby. Please try other words."


def test_english_request_is_searched_directly_when_the_cloud_fails():
    navigation = FakeNavigation([station(1, 450)])
    dialogue = DestinationDialogue(ScriptedGrok(AssistantError("provider_timeout")), navigation)
    assert dialogue.handle("Take me to Festival Walk") == (
        "The cloud assistant is unavailable, so I searched directly. I found one place. "
        "Destination: Station 1, about 450 metres. Press K1 to start.")
    assert navigation.queries == ["Festival Walk"]


def test_english_wordings_count_as_destination_requests():
    dialogue = DestinationDialogue(ScriptedGrok(), FakeNavigation())
    assert dialogue.wants("Take me to the library") and dialogue.wants("navigate to Festival Walk")
    assert dialogue.wants("Cancel navigation.") and dialogue.wants("how do I get to the station")
    assert dialogue.wants("where is the nearest toilet") and not dialogue.wants("where is the nearest toilet", surely=True)
    assert not dialogue.wants("what is in front of me") and not dialogue.wants("is the way ahead clear")


def test_distances_are_spoken_in_the_language_of_the_turn():
    assert spoken_distance(314, "en") == "about 310 metres" and spoken_distance(1530, "en") == "about 1.5 kilometres"
    assert spoken_distance(4, "en") == "about 10 metres" and spoken_distance(None, "en") == ""
    assert spoken_distance(314) == "约310米"


def k2_turn(monkeypatch, transcript, config, replies, places):
    """One K2 turn through the assistant. Returns what was spoken with the language of its voice."""
    navigation = FakeNavigation(places)
    navigation.submit_destination = lambda text: pytest.fail("legacy destination path used")
    said = []
    with Harness(monkeypatch, transcript=transcript, navigation=navigation, config=config,
                 chat=ScriptedGrok(*replies).chat) as h:
        monkeypatch.setattr(h.assistant, "_speak",
                            lambda text, **kwargs: said.append((text, h.assistant._reply_language)) or True)
        h.ask("navigation_button")
    return said


def test_k2_set_to_auto_answers_english_in_english_and_mandarin_in_mandarin(monkeypatch):
    auto = {**ZH, "destination_language": "auto"}
    said = k2_turn(monkeypatch, "TAKE ME TO FESTIVAL WALK", auto, [{"action": "search", "query": "Festival Walk"}],
                   [station(1, 450)])
    assert said == [("I found one place. Destination: Station 1, about 450 metres. Press K1 to start.", "en")]
    said = k2_turn(monkeypatch, "我想去海岸城", auto, [{"action": "search", "query": "海岸城"}], [place(1, 450)])
    assert said == [("只找到一个地点。目的地，麦当劳1号店，约450米。按K1开始导航。", "zh")]


def test_k2_without_the_setting_keeps_the_assistant_language(monkeypatch):
    # A Mandarin cane hears English: it still answers in Mandarin, as before.
    said = k2_turn(monkeypatch, "TAKE ME TO FESTIVAL WALK", ZH, [{"action": "search", "query": "Festival Walk"}],
                   [station(1, 450)])
    assert said == [("只找到一个地点。目的地，Station 1，约450米。按K1开始导航。", "zh")]


def test_k2_set_to_english_on_a_mandarin_cane(monkeypatch):
    from cane.vision.local_speech import LocalSpeech

    def transcribe(recognizer, pcm):
        assert recognizer.language == "en"
        return "THE NEAREST HOSPITAL"

    monkeypatch.setattr(LocalSpeech, "transcribe", transcribe)
    said = k2_turn(monkeypatch, "THE NEAREST HOSPITAL", {**ZH, "destination_language": "en"},
                   [{"action": "search", "query": "hospital"}], [station(1, 450), station(2, 900)])
    assert said == [("I found 2 places. 1: Station 1, about 450 metres. 2: Station 2, about 900 metres. "
                     "Press K2 and say which one.", "en")]


def test_english_cane_gets_the_dialogue_once_k2_has_a_language(monkeypatch):
    # Without destination_language an English assistant keeps the legacy path (tested above).
    said = k2_turn(monkeypatch, "THE NEAREST HOSPITAL", {"destination_language": "auto"},
                   [{"action": "search", "query": "hospital"}], [station(1, 450)])
    assert said == [("I found one place. Destination: Station 1, about 450 metres. Press K1 to start.", "en")]


def test_unknown_k2_language_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="destination_language"):
        Harness(monkeypatch, config={**ZH, "destination_language": "fr"})


def test_request_after_the_wake_phrase_follows_its_language_when_k2_takes_both(monkeypatch):
    navigation = FakeNavigation([station(1, 450)])
    replies = ScriptedGrok({"action": "search", "query": "Festival Walk"})
    said = []
    with Harness(monkeypatch, navigation=navigation, config={**ZH, "destination_language": "auto"},
                 chat=replies.chat, detector=SaidOnce("TAKE ME TO FESTIVAL WALK")) as h:
        monkeypatch.setattr(h.assistant, "_speak",
                            lambda text, **kwargs: said.append((text, h.assistant._reply_language)) or True)
        assert h.idle.wait(3)
    assert said == [("I found one place. Destination: Station 1, about 450 metres. Press K1 to start.", "en")]
    # A Mandarin cane without the setting answers the same request in Mandarin, as before.
    said.clear()
    with Harness(monkeypatch, navigation=FakeNavigation([station(1, 450)]), config=ZH,
                 chat=ScriptedGrok({"action": "search", "query": "Festival Walk"}).chat,
                 detector=SaidOnce("TAKE ME TO FESTIVAL WALK")) as h:
        monkeypatch.setattr(h.assistant, "_speak",
                            lambda text, **kwargs: said.append((text, h.assistant._reply_language)) or True)
        assert h.idle.wait(3)
    assert said == [("只找到一个地点。目的地，Station 1，约450米。按K1开始导航。", "zh")]
