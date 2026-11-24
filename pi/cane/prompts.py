"""Catalogue of pre-generated prompts in Mandarin (cmn), Cantonese (yue), English (en).

Every safety prompt is a local pre-recorded clip. Sentences are kept short:
the warning distance is computed from the LONGEST of the three languages
(see required_warning_distance and tools/check_lead_time.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Priority


@dataclass(frozen=True, slots=True)
class PromptDef:
    id: str
    priority: Priority
    cmn: str
    yue: str
    en: str
    vibration: str | None = None  # pattern name understood by the Pico

    def text(self, lang: str) -> str:
        return getattr(self, lang)


_P = Priority
_DEFS = [
    # --- critical -------------------------------------------------------
    PromptDef("stop_now", _P.CRITICAL, "停", "停", "Stop", "stop"),
    PromptDef("drop_ahead", _P.CRITICAL, "前方落差", "前面有落差", "Drop ahead", "stop"),
    PromptDef("sensor_fault", _P.CRITICAL, "传感器故障，请停下", "感應器故障，請停低", "Sensor fault, stop", "fault"),
    PromptDef("pico_lost", _P.CRITICAL, "控制板断开，请停下", "控制板斷咗，請停低", "Controller lost, stop", "fault"),
    PromptDef("power_critical", _P.CRITICAL, "供电异常，请停下", "供電異常，請停低", "Supply unsafe, stop", "fault"),
    # --- safety ---------------------------------------------------------
    PromptDef("obstacle_front", _P.SAFETY, "前方障碍", "前面有嘢", "Obstacle ahead", "near"),
    PromptDef("obstacle_head", _P.SAFETY, "头部障碍", "頭頂有嘢", "Head height", "head"),
    PromptDef("step_up_ahead", _P.SAFETY, "前方台阶", "前面有級", "Step ahead", "near"),
    PromptDef("rough_ground", _P.SAFETY, "路面不平", "地面唔平", "Rough ground", "rough"),
    PromptDef("soft_ground", _P.SAFETY, "软地", "軟地", "Soft ground", "rough"),
    PromptDef("slope_up", _P.SAFETY, "上坡", "上斜", "Uphill"),
    PromptDef("slope_down", _P.SAFETY, "下坡", "落斜", "Downhill"),
    PromptDef("slope_left", _P.SAFETY, "地面向左斜", "地向左斜", "Tilts left"),
    PromptDef("slope_right", _P.SAFETY, "地面向右斜", "地向右斜", "Tilts right"),
    PromptDef("vision_person", _P.SAFETY, "前方有人", "前面有人", "Person ahead"),
    PromptDef("vision_chair", _P.SAFETY, "前方有椅子", "前面有櫈", "Chair ahead"),
    PromptDef("vision_bag", _P.SAFETY, "前方有包", "前面有袋", "Bag ahead"),
    # --- system ---------------------------------------------------------
    PromptDef("boot_ready", _P.SYSTEM, "系统就绪", "準備好", "Ready", "ok"),
    PromptDef("sensor_ok", _P.SYSTEM, "传感器恢复", "感應器恢復", "Sensors back", "ok"),
    PromptDef("battery_low", _P.SYSTEM, "供电偏低", "供電偏低", "Supply low"),
    PromptDef("power_caution", _P.SYSTEM, "供电电压偏低", "供電電壓偏低", "Supply voltage low"),
    PromptDef("power_reduced", _P.SYSTEM, "供电偏低，已降低耗电", "供電偏低，已減少耗電", "Low supply, reducing load"),
    PromptDef("power_restored", _P.SYSTEM, "供电电压恢复", "供電電壓恢復", "Supply voltage restored"),
    PromptDef("nav_paused", _P.SYSTEM, "导航暂停，避障继续", "導航暫停，避障繼續", "Navigation paused, warnings continue"),
    PromptDef("resume_blocked", _P.SYSTEM, "暂不能继续，请停下检查", "暫時未能繼續，請停低檢查", "Cannot continue, stop and check"),
    PromptDef("nav_resumed", _P.SYSTEM, "导航恢复，请重新对正", "導航恢復，請重新對正", "Navigation resumed, align again"),
    PromptDef("help_local", _P.SYSTEM, "已发出本地求助提示，未发送远程消息", "已發出本地求助提示，未發送遙距訊息", "Local help alert only. No message sent"),
    PromptDef("dest_cancelled", _P.SYSTEM, "目的地已取消", "目的地已取消", "Destination cancelled"),
    PromptDef("nav_nothing_to_repeat", _P.SYSTEM, "暂无有效导航提示", "暫無有效導航提示", "No current navigation instruction"),
    PromptDef("scan_unavailable", _P.SYSTEM, "扫描暂不可用，请停下检查传感器", "掃描暫不可用，請停低檢查感應器", "Scan unavailable, stop and check sensors"),
    PromptDef("voice_unavailable", _P.SYSTEM, "语音暂不可用，请用文字输入", "語音暫不可用，請用文字輸入", "Voice unavailable, use text input"),
    PromptDef("vision_unavailable", _P.SYSTEM, "视觉识别不可用", "視覺識別唔可用", "Vision unavailable"),
    PromptDef("net_lost_nav_off", _P.SYSTEM, "网络断开，导航退出", "網絡斷咗，導航停止", "No network, navigation off"),
    PromptDef("test_mode_on", _P.SYSTEM, "测试模式", "測試模式", "Test mode"),
    PromptDef("lang_changed", _P.SYSTEM, "普通话", "廣東話", "English"),
    # --- navigation -----------------------------------------------------
    PromptDef("nav_start", _P.NAV, "开始导航", "開始導航", "Navigation started", "ok"),
    PromptDef("nav_stopped", _P.NAV, "导航结束", "導航結束", "Navigation ended"),
    PromptDef("nav_arrived", _P.NAV, "到达目的地", "到咗目的地", "Arrived", "ok"),
    PromptDef("nav_offroute", _P.NAV, "偏离路线，重新规划", "行錯路，重新規劃", "Off route, rerouting"),
    PromptDef("nav_stop_align", _P.NAV, "请停下对正", "請停低對正", "Stop and face ahead"),
    PromptDef("route_failed", _P.NAV, "路线获取失败", "攞唔到路線", "Route failed"),
    PromptDef("walk_route_unavailable", _P.SYSTEM,
              "地图未找到步行路线。请在安全处停下，联系工作人员确认可通行的入口，或请人陪同、改乘车辆。",
              "地圖搵唔到行路路線。請喺安全位置停低，問工作人員邊個入口行得通，或者請人陪同，改搭車。",
              "The map found no walking route. Stop somewhere safe and ask staff about a usable entrance, an escort, or transport."),
    PromptDef("walk_route_blocked", _P.SYSTEM,
              "这条路线无法全程步行。请在安全处停下，联系现场工作人员确认其他入口，或请人陪同、改乘车辆。",
              "呢條路唔可以全程行路。請喺安全位置停低，問現場工作人員有冇其他入口，或者請人陪同，改搭車。",
              "This route cannot be walked end to end. Stop somewhere safe and ask staff about another entrance, an escort, or transport."),
    PromptDef("walk_route_alternative", _P.SYSTEM,
              "已查到另一条步行路线。请先确认现场通行条件。",
              "搵到另一條行路路線。請先確認現場通行情況。",
              "An alternative walking route is available. Check conditions on site before proceeding."),
    PromptDef("turn_left", _P.NAV, "左转", "轉左", "Turn left", "left"),
    PromptDef("turn_right", _P.NAV, "右转", "轉右", "Turn right", "right"),
    PromptDef("slight_left", _P.NAV, "稍向左", "微微向左", "Bear left", "left"),
    PromptDef("slight_right", _P.NAV, "稍向右", "微微向右", "Bear right", "right"),
    PromptDef("go_straight", _P.NAV, "直行", "直行", "Go straight"),
    PromptDef("turn_around", _P.NAV, "掉头", "掉頭", "Turn around"),
    PromptDef("crossing_ahead", _P.NAV, "前方过街，请停下自行判断，确认后按键",
              "前面過馬路，請停低自己判斷，確認後撳掣",
              "Crossing ahead. Stop, judge it yourself, then press OK", "cross"),
    PromptDef("crossing_confirmed", _P.NAV, "继续导航", "繼續導航", "Continuing"),
    # --- voice input / info ---------------------------------------------
    PromptDef("voice_listening", _P.INFO, "请说目的地", "請講目的地", "Say destination"),
    PromptDef("choose_search_origin", _P.SYSTEM,
              "你想用 iPhone 的当前位置，还是改用一个地点作为出发点？",
              "你想用 iPhone 嘅目前位置，定係改用另一個地方做起點？",
              "Would you like to use your iPhone location, or choose a different starting point?"),
    PromptDef("voice_not_understood", _P.INFO, "没听清，请再说", "聽唔清楚，請再講", "Not understood, again"),
    PromptDef("dest_confirm", _P.INFO, "按确认键出发", "撳確認掣出發", "Press OK to go"),
    PromptDef("no_network", _P.INFO, "没有网络", "冇網絡", "No network"),
]

# distance buckets announced after a turn ("左转" + "20米")
DISTANCE_BUCKETS_M = (5, 10, 20, 30, 50, 100, 200)
for _d in DISTANCE_BUCKETS_M:
    _DEFS.append(PromptDef(f"dist_{_d}", _P.NAV, f"{_d}米", f"{_d}米", f"{_d} metres"))

CATALOG: dict[str, PromptDef] = {p.id: p for p in _DEFS}
assert len(CATALOG) == len(_DEFS), "duplicate prompt id"


def destination_prompt_id(dest_id: str) -> str:
    """Preset destination names are also pre-generated: dest_<id>."""
    return f"dest_{dest_id}"


def distance_clip(distance_m: float) -> str:
    """The largest bucket that is no more than 15% above the distance.

    A turn is announced on the first fix under a threshold, so at 50 m the distance is 49 or 48.
    Rounding down alone called that "30 m", which sends a user who counts steps into the wrong
    opening. A longer distance than the real one is still never promised by more than 15%.
    """
    best = DISTANCE_BUCKETS_M[0]
    for b in DISTANCE_BUCKETS_M:
        if b <= distance_m * 1.15:
            best = b
    return f"dist_{best}"


def required_warning_distance(
    speech_s: float,
    speed_mps: float,
    latency_s: float,
    comprehension_s: float = 0.5,
    margin_m: float = 0.0,
) -> float:
    """Distance at which a warning must start so the user can react in time.

    Appendix B: speed x (sensor latency + clip length + 0.5 s to understand).
    """
    return speed_mps * (latency_s + speech_s + comprehension_s) + margin_m


def longest_duration(durations: dict[str, dict[str, float]], prompt_id: str) -> float:
    """durations[lang][prompt_id] -> seconds. Returns max over languages present."""
    vals = [d[prompt_id] for d in durations.values() if prompt_id in d]
    if not vals:
        raise KeyError(prompt_id)
    return max(vals)
