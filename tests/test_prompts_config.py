from pathlib import Path

import pytest

from cane.config import Secrets, load_config
from cane.model import Priority
from cane.prompts import CATALOG, distance_clip, longest_duration, required_warning_distance

ROOT = Path(__file__).resolve().parents[1]


def test_every_prompt_has_three_languages():
    for p in CATALOG.values():
        for lang in ("cmn", "yue", "en"):
            assert p.text(lang).strip(), (p.id, lang)


def test_safety_prompts_are_short():
    # long safety sentences eat lead time; keep them terse
    for p in CATALOG.values():
        if p.priority.is_safety:
            assert len(p.cmn) <= 10 and len(p.yue) <= 10 and len(p.en) <= 24, p.id


def test_left_right_prompts_vibrate():
    assert CATALOG["turn_left"].vibration == "left"
    assert CATALOG["turn_right"].vibration == "right"


def test_distance_clip():
    assert distance_clip(3) == "dist_5"
    assert distance_clip(27) == "dist_20"
    assert distance_clip(500) == "dist_200"


def test_lead_time_uses_longest_language():
    d = {"cmn": {"x": 0.6}, "yue": {"x": 0.9}, "en": {"x": 0.7}}
    assert longest_duration(d, "x") == 0.9
    assert required_warning_distance(0.9, 1.0, 0.25, 0.5) == pytest.approx(1.65)


def test_example_config_loads_and_has_no_secrets():
    cfg = load_config(ROOT / "config" / "config.example.toml", env={})
    assert cfg.general.language == "cmn"
    assert len(cfg.nav.destinations) >= 2
    assert cfg.web.diag_host == "127.0.0.1"
    assert cfg.secrets.amap_key is None
    text = (ROOT / "config" / "config.example.toml").read_text()
    assert "key =" not in text.lower().replace("keyfile =", "")


def test_secrets_from_env_and_repr_hides_them():
    s = Secrets.from_env({"AMAP_KEY": "abc123", "CANE_PHONE_TOKEN": "Zq9xW"})
    assert s.amap_key == "abc123"
    assert "abc123" not in repr(s) and "Zq9xW" not in repr(s)


def test_bad_config_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[general]\nlanguage = "fr"\n')
    with pytest.raises(ValueError):
        load_config(p, env={})
    p.write_text('[general]\nlangauge = "cmn"\n')
    with pytest.raises(ValueError):
        load_config(p, env={})


def test_priority_order():
    assert Priority.CRITICAL < Priority.SAFETY < Priority.SYSTEM < Priority.NAV < Priority.INFO
