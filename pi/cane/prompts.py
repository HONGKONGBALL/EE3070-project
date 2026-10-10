"""Catalogue of pre-generated prompts in Mandarin (cmn), Cantonese (yue), English (en).

Every safety prompt is a local pre-recorded clip. Sentences are kept short:
the warning distance is computed from the LONGEST of the three languages
(see required_warning_distance and tools/check_lead_time.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Priority


@dataclass(frozen=True)
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
    # --- system ---------------------------------------------------------
    PromptDef("boot_ready", _P.SYSTEM, "系统就绪", "準備好", "Ready", "ok"),
    PromptDef("sensor_ok", _P.SYSTEM, "传感器恢复", "感應器恢復", "Sensors back", "ok"),
    PromptDef("battery_low", _P.SYSTEM, "电量低", "電量低", "Battery low"),
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
    """Nearest bucket at or below the distance (never over-promise)."""
    best = DISTANCE_BUCKETS_M[0]
    for b in DISTANCE_BUCKETS_M:
        if b <= distance_m:
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
