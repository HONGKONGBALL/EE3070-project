from check_lead_time import check
from gen_prompts import prompt_texts

from cane.config import Destination, HazardCfg, TfminiCfg
from cane.prompts import CATALOG

SAFETY = [pid for pid, p in CATALOG.items() if p.priority.is_safety]


def durs(v):
    return {lang: {pid: v for pid in SAFETY} for lang in ("cmn", "yue", "en")}


def test_short_clips_pass():
    rows = check(durs(0.6), HazardCfg())
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_longest_language_decides():
    d = durs(0.6)
    d["yue"]["obstacle_front"] = 1.2
    row = next(r for r in check(d, HazardCfg()) if r.prompt_id == "obstacle_front")
    assert row.longest_lang == "yue"
    assert row.need_m == 1.0 * (0.25 + 1.2 + 0.5) + 0.3


def test_steep_tfmini_tilt_is_too_late():
    rows = check(durs(0.6), HazardCfg(), tf=TfminiCfg(tilt_down_deg=35))
    row = next(r for r in rows if r.prompt_id == "drop_ahead")
    assert not row.ok and "sensor" in row.reason


def test_too_long_flagged():
    d = durs(0.6)
    d["en"]["drop_ahead"] = 1.8
    row = next(r for r in check(d, HazardCfg()) if r.prompt_id == "drop_ahead")
    assert not row.ok and "clip" in row.reason and "sensor" in row.reason


def test_missing_language_flagged():
    d = durs(0.6)
    del d["en"]["stop_now"]
    row = next(r for r in check(d, HazardCfg()) if r.prompt_id == "stop_now")
    assert not row.ok and "en" in row.reason


def test_prompt_texts_include_destinations():
    dests = [Destination("dest1", "香港城市大学", ["城大", "city university"])]
    assert prompt_texts("cmn", dests)["dest_dest1"] == "香港城市大学"
    assert prompt_texts("en", dests)["dest_dest1"] == "City University"
    assert prompt_texts("yue", dests)["stop_now"] == "停"
