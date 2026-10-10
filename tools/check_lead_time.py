#!/usr/bin/env python3
"""Check warning lead time against the LONGEST language of each safety prompt.

    python tools/check_lead_time.py --audio audio/ --config config/config.toml

Appendix B: warning distance = speed x (sensor latency + clip + 0.5 s to
understand) + margin. If that exceeds what the sensor can see, or a clip is
longer than --max-clip, shorten the sentence (in all three languages).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))

from cane.audio import load_durations  # noqa: E402
from cane.config import HazardCfg, TfminiCfg, load_config  # noqa: E402
from cane.hazard import ground_spot_ahead_m  # noqa: E402
from cane.prompts import CATALOG, required_warning_distance  # noqa: E402


def sensor_ranges(tf: TfminiCfg) -> dict[str, float | None]:
    """How far ahead each safety prompt's trigger can see (m); None = not distance-triggered."""
    spot = ground_spot_ahead_m(tf.mount_height_m, tf.tilt_down_deg) if tf.role == "ground" else None
    return {
        "obstacle_front": 4.0,   # HC-SR04 practical max
        "obstacle_head": 2.0,    # upward sonar, oblique
        "drop_ahead": spot,      # TFmini-S ground spot (while walking)
        "step_up_ahead": spot,
    }


@dataclass
class Row:
    prompt_id: str
    longest_lang: str
    longest_s: float
    need_m: float
    range_m: float | None
    ok: bool
    reason: str


def check(durations: dict[str, dict[str, float]], hz: HazardCfg, max_clip_s: float = 1.5,
          tf: TfminiCfg | None = None) -> list[Row]:
    ranges = sensor_ranges(tf or TfminiCfg())
    rows = []
    for pid, p in CATALOG.items():
        if not p.priority.is_safety:
            continue
        have = {lang: d[pid] for lang, d in durations.items() if pid in d}
        if not have:
            rows.append(Row(pid, "-", 0.0, 0.0, None, False, "missing clip"))
            continue
        lang, dur = max(have.items(), key=lambda kv: kv[1])
        need = required_warning_distance(dur, hz.walking_speed_mps, hz.sensor_latency_s, hz.comprehension_s,
                                         hz.margin_m)
        rng = ranges.get(pid)
        reasons = []
        if len(have) < len(durations):
            reasons.append("missing in " + ",".join(sorted(set(durations) - set(have))))
        if dur > max_clip_s:
            reasons.append(f"clip {dur:.2f}s > {max_clip_s}s")
        if rng is not None and need > rng:
            reasons.append(f"needs {need:.2f} m > sensor {rng} m")
        rows.append(Row(pid, lang, dur, need, rng, not reasons, "; ".join(reasons) or "ok"))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default=str(ROOT / "audio"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--max-clip", type=float, default=1.5)
    args = ap.parse_args(argv)
    cfg = load_config(args.config, env={}) if args.config else load_config(None, env={})
    rows = check(load_durations(args.audio), cfg.hazard, args.max_clip, cfg.tfmini)
    print(f"{'prompt':16} {'longest':8} {'clip s':>6} {'need m':>7} {'range':>6}  result")
    for r in rows:
        rng = "-" if r.range_m is None else f"{r.range_m:.1f}"
        print(f"{r.prompt_id:16} {r.longest_lang:8} {r.longest_s:6.2f} {r.need_m:7.2f} {rng:>6}  {r.reason}")
    bad = [r for r in rows if not r.ok]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
