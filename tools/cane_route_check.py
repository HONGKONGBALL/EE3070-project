#!/usr/bin/env python3
"""Walk a long route against the cane's real navigation code and check every prompt.

A simulated pedestrian reports one GPS fix per second at walking pace along the route AMap
planned, in real time. For every step of the route the report says which prompts the navigator
issued, at what distance before the turn, and whether the speaker actually played them. On the
way the pedestrian also makes the mistakes a blind user can make:

  - walks straight past a turn                      -> is it noticed, and how is it corrected?
  - gets one GPS fix that jumps far off the route   -> it must not trigger a new route
  - turns round and walks back along the route      -> is that noticed?

What is real: the cane's navigator, state machine, audio arbiter, phone HTTPS endpoint and AMap
(with the real key, including every re-plan). What is replaced: the Pico (keys and range frames
are injected), the speaker (what would be played is recorded), the IMU (a stand-in that reports
"standing still", so K1 can confirm the heading).

    AMAP_KEY=... python tools/cane_route_check.py --target-m 3000 --miss-turn-after 900 \
        --glitch-after 1500 --reverse-after 2000

A 3 km route takes about 50 minutes: the walk is in real time, because the audio arbiter's
timing only means something at walking pace. The report is written to route-check/report.txt.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cane_demo_location as L  # noqa: E402
import cane_rehearsal as R  # noqa: E402

TURNS = {"turn_left", "turn_right", "slight_left", "slight_right", "turn_around"}
NAMES = {"turn_left": "左转", "turn_right": "右转", "slight_left": "向左前方", "slight_right": "向右前方",
         "turn_around": "掉头", "go_straight": "直行", "crossing_ahead": "前方路口，按K1确认",
         "nav_arrived": "到达目的地", "nav_offroute": "偏离路线，重新规划", "nav_stop_align": "请停下对正",
         "nav_start": "开始导航", "crossing_confirmed": "已确认"}


def bearing_step(point, heading, metres):
    """The WGS-84 point metres further along a compass heading in radians."""
    lng, lat = point
    return (lng + metres * math.sin(heading) / (111_320 * math.cos(math.radians(lat))),
            lat + metres * math.cos(heading) / 110_540)


def heading_of(a, b):
    return math.atan2((b[0] - a[0]) * math.cos(math.radians(a[1])) * 111_320, (b[1] - a[1]) * 110_540)


class Recorder:
    """Everything the navigator issued and everything the speaker played, with the walker's state."""

    def __init__(self, world):
        self.world, self.cane = world, world.cane
        self.started = time.monotonic()
        self.routes, self.events, self.notes = [], [], []
        self.walked = 0.0
        self.reached = {}  # route index -> the furthest step the navigator placed the walker on
        self.left_at = {}  # route index -> the step at whose end the walker left the route on purpose
        navigator, cane = self.cane.navigator, self.cane
        start, handle = navigator.start, cane._handle_nav_outputs

        def started_route(route, dest):
            self.routes.append(route)
            return start(route, dest)

        def outputs(items):
            position = navigator.position
            for item in items:
                self.events.append(SimpleNamespace(
                    t=self.clock(), route=len(self.routes) - 1, kind=item.kind, prompt=item.prompt_id,
                    extra=list(item.extra_clips), step=position.step if position else None,
                    to_end=position.to_step_end_m if position else None,
                    off=position.off_route_m if position else None, walked=self.walked, spoken=None))
            return handle(items)

        navigator.start, cane._handle_nav_outputs = started_route, outputs

    def clock(self):
        return time.monotonic() - self.started

    def note(self, text):
        self.notes.append((self.clock(), self.walked, text))
        R.log(f"NOTE {text}")

    def heard(self):
        return [(t - self.started, name, text) for t, name, text in self.world.sink.heard]

    def match_speech(self):
        """Pair every issued prompt with the playback that followed it."""
        heard, used = self.heard(), set()
        for event in self.events:
            for index, (t, name, text) in enumerate(heard):
                if index in used or not event.t - 1.0 <= t <= event.t + 12.0:
                    continue
                if name == event.prompt:
                    event.spoken, _ = text, used.add(index)
                    break
            else:
                # A turn without a confirmed heading is replaced by "stop and align".
                for index, (t, name, text) in enumerate(heard):
                    if index not in used and name == "nav_stop_align" and event.t - 1.0 <= t <= event.t + 6.0:
                        event.spoken = "（改播）" + text
                        break


class Walker:
    def __init__(self, world, recorder, args):
        self.w, self.rec, self.args = world, recorder, args
        self.cane, self.nav = world.cane, world.cane.navigator
        self.link = L.Cane(Path.cwd())
        self.random = random.Random(args.seed)
        self.position = tuple(args.origin) if args.origin else L.gcj02_to_wgs84(*L.DEFAULT_ORIGIN_GCJ)
        self.plan, self.plan_route = None, None
        self.heading = 0.0
        self.aligned_presses = self.crossings = 0
        self.heard_seen = 0
        self.done = {"miss": args.miss_turn_after is None, "glitch": args.glitch_after is None,
                     "reverse": args.reverse_after is None}
        self.windows = {}

    # -- reporting a fix ---------------------------------------------------------------
    def report(self, point=None, accuracy=5.0):
        lng, lat = point or self.position
        sigma = self.args.noise_m
        lng, lat = bearing_step((lng, lat), self.random.uniform(0, 2 * math.pi), abs(self.random.gauss(0, sigma)))
        try:
            self.link.report(lng, lat, accuracy)
            self.unreachable = False
        except Exception as error:
            # The computer changed network and the phone endpoint's address is gone: hand the fix
            # to the cane itself, the way the endpoint would. The walk is about the guidance.
            if not getattr(self, "unreachable", False):
                R.log(f"phone endpoint unreachable ({error}); handing fixes to the cane directly")
            self.unreachable = True
            self.direct = getattr(self, "direct", 0) + 1
            self.cane.on_location(lng, lat, accuracy)

    def tick(self, moved=0.0):
        self.rec.walked += moved
        self.report()
        time.sleep(self.args.interval)
        position, index = self.nav.position, len(self.rec.routes) - 1
        if position is not None and index >= 0 and position.off_route_m <= 25:
            self.rec.reached[index] = max(self.rec.reached.get(index, -1), position.step)

    def press_k1(self):
        self.w.pico.press("ok")

    # -- following a route -------------------------------------------------------------
    def adopt_route(self):
        """Follow the cane's current route from the point on it nearest to where the walker stands."""
        route = self.nav.route
        # AMap's route is in GCJ-02. The other services answer in the frame of the fixes.
        shifted = getattr(self.cane.amap, "frame", "gcj02") == "gcj02"
        points = [L.gcj02_to_wgs84(*point) if shifted else tuple(point) for point in route.polyline]
        nearest = min(range(len(points)), key=lambda index: L.metres(points[index], self.position))
        self.plan = L.Walk([self.position] + points[nearest:], self.args.speed)
        self.plan_route = route

    def handle_prompts(self):
        """What the user does on hearing a prompt: stop and press K1 when asked to align or at a crossing."""
        heard = self.w.sink.heard
        new, self.heard_seen = heard[self.heard_seen:], len(heard)
        if self.nav.waiting_crossing is not None:
            self.rec.note("路口：用户停下，约 3 秒后按 K1 确认过街")
            for _ in range(3):
                self.tick()
            self.press_k1()
            self.crossings += 1
            self.tick()
            return True
        if any(name == "nav_stop_align" for _, name, _ in new):
            self.rec.note("听到「请停下对正」：用户停下并按 K1 确认朝向")
            self.tick()
            self.press_k1()
            self.aligned_presses += 1
            self.tick()
            return True
        return False

    def wait_for_route(self, previous, seconds=40):
        """After "off route" the user stands still until the new route is in."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.nav.route is not None and self.nav.route is not previous and not self.cane._route_pending:
                return True
            if not self.nav.active and not self.cane._route_pending:
                return False
            self.tick()
        return False

    # -- the mistakes ------------------------------------------------------------------
    def miss_turn(self):
        """Walk straight on past the turn instead of taking it."""
        route, mark = self.nav.route, len(self.rec.events)
        start = self.rec.clock()
        self.rec.left_at[len(self.rec.routes) - 1] = self.nav.position.step
        self.rec.note(f"【走过头】在转弯处不转弯，沿原方向继续直走（已走 {self.rec.walked:.0f} 米）")
        gone = 0.0
        while gone < 120 and self.nav.route is route and not any(
                event.kind == "reroute" for event in self.rec.events[mark:]):
            step = self.args.speed * self.args.interval
            self.position = bearing_step(self.position, self.heading, step)
            gone += step
            self.tick(step)
        noticed = next((event for event in self.rec.events[mark:] if event.kind == "reroute"), None)
        self.windows["miss"] = SimpleNamespace(start=start, beyond=gone, noticed=noticed, replanned=None,
                                               old=route, new=None, first=None)
        if noticed is None:
            self.rec.note("走出 120 米仍未提示偏离")
            return
        self.rec.note(f"手杖提示偏离：走过转弯点 {gone:.0f} 米、{self.rec.clock() - start:.0f} 秒后；用户停下等待新路线")
        ok = self.wait_for_route(route)
        window = self.windows["miss"]
        window.replanned = self.rec.clock() if ok else None
        window.new = self.nav.route if ok else None
        if ok:
            self.rec.note(f"新路线已生成：{self.nav.route.distance_m:.0f} 米，{len(self.nav.route.steps)} 段")
            self.adopt_route()

    def glitch(self):
        """One fix far away from the route, as a GPS reflection gives."""
        mark, route = len(self.rec.events), self.nav.route
        self.rec.note(f"【GPS 跳点】上报一个偏离路线约 70 米的定位点（已走 {self.rec.walked:.0f} 米）")
        self.report(bearing_step(self.position, self.heading + math.pi / 2, 70.0))
        time.sleep(self.args.interval)
        for _ in range(4):
            self.advance()
        rerouted = self.nav.route is not route or any(event.kind == "reroute" for event in self.rec.events[mark:])
        self.windows["glitch"] = SimpleNamespace(rerouted=rerouted)
        self.rec.note("跳点之后：" + ("触发了重新规划" if rerouted else "没有触发重新规划，导航照常"))

    def reverse(self):
        """Turn round and walk back along the route, then turn again and carry on."""
        mark, start = len(self.rec.events), self.rec.clock()
        heard_mark = len(self.w.sink.heard)
        self.rec.note(f"【走反方向】原地掉头沿来路往回走，最多 80 米（已走 {self.rec.walked:.0f} 米）")
        back = L.Walk([self.position] + self.trail[::-1], self.args.speed)
        gone, told = 0.0, None
        while gone < 80 and not back.done and told is None:
            self.position = back.advance(self.args.interval)
            gone += self.args.speed * self.args.interval
            self.tick(self.args.speed * self.args.interval)
            # A user who hears "turn around" does so.
            told = next((event for event in self.rec.events[mark:] if event.prompt == "turn_around"), None)
        events = [event for event in self.rec.events[mark:]]
        heard = [text for _, _, text in self.w.sink.heard[heard_mark:]]
        self.windows["reverse"] = SimpleNamespace(back=gone, seconds=self.rec.clock() - start, events=events,
                                                  heard=heard, told=told)
        self.rec.note(f"往回走了 {gone:.0f} 米：" + ("手杖提示「掉头」，用户转身继续" if told else "手杖没有提醒"))
        if self.nav.active and self.nav.route is self.plan_route:
            self.adopt_route()

    # -- the walk ----------------------------------------------------------------------
    def advance(self):
        before = self.position
        self.position = self.plan.advance(self.args.interval)
        moved = L.metres(before, self.position)
        if moved > 0.2:
            self.heading = heading_of(before, self.position)
            self.trail.append(self.position)
        self.tick(moved)

    def at_turn(self):
        """True when the walker stands at the end of a step that asks for a turn."""
        position = self.nav.position
        if position is None or position.step >= len(self.nav.route.steps) - 1:
            return False
        step = self.nav.route.steps[position.step]
        return step.maneuver in TURNS and not step.is_crossing and position.to_step_end_m <= 2.5

    def walk(self):
        self.trail = [self.position]
        self.heard_seen = len(self.w.sink.heard)  # the start-up prompts were answered before the walk
        self.adopt_route()
        limit = time.monotonic() + self.args.max_minutes * 60
        while time.monotonic() < limit:
            if not self.nav.active and not self.cane._route_pending:
                return "arrived" if any(event.kind == "arrived" for event in self.rec.events) else "stopped"
            if self.cane._route_pending:
                self.tick()
                continue
            if self.nav.route is not self.plan_route:
                self.adopt_route()
            if self.handle_prompts():
                continue
            walked = self.rec.walked
            if not self.done["miss"] and walked >= self.args.miss_turn_after and self.at_turn():
                self.done["miss"] = True
                self.miss_turn()
                continue
            position = self.nav.position
            if not self.done["glitch"] and walked >= self.args.glitch_after and position and position.to_step_end_m > 60:
                self.done["glitch"] = True
                self.glitch()
                continue
            if (not self.done["reverse"] and walked >= self.args.reverse_after and position
                    and position.to_step_end_m > 90 and len(self.trail) > 60):
                self.done["reverse"] = True
                self.reverse()
                continue
            if self.plan.done:
                self.tick()
                continue
            self.advance()
        return "timeout"


def choose_destination(world, target, tolerance):
    """A place whose walking route from the origin is as close to the target length as AMap offers."""
    cane = world.cane
    origin = cane._to_amap(cane.location[0], cane.location[1])
    best = None
    for query in ("地铁站", "医院", "公园", "学校", "商场", "酒店"):
        try:
            places = world.navigation.find_places(query)
        except Exception as error:
            R.log(f"search {query}: {error}")
            continue
        for place in places:
            if place.distance_m is None or not 0.45 * target <= place.distance_m <= 1.05 * target:
                continue
            time.sleep(0.4)
            try:
                route = cane.amap.walking_route(origin, cane._to_amap(place.lng, place.lat))
            except Exception as error:
                R.log(f"route to {place.name}: {error}")
                continue
            if route.blocks_continuous_walking:
                continue
            gap = abs(route.distance_m - target)
            R.log(f"candidate {place.name}: walking {route.distance_m:.0f} m, {len(route.steps)} steps")
            if best is None or gap < best[0]:
                best = (gap, place, route)
            if gap <= tolerance:
                return place, route
    if best is None:
        raise RuntimeError("no destination at a suitable distance")
    return best[1], best[2]


def step_rows(recorder, index, route, walked_to):
    """One line per step of a route: what was due and what happened."""
    rows = []
    for number, step in enumerate(route.steps):
        events = [event for event in recorder.events if event.route == index and event.step == number]
        last = number == len(route.steps) - 1
        said, before = [], None
        for event in events:
            if event.kind == "prompt" and event.prompt not in ("nav_start", "crossing_confirmed"):
                distance = f"{event.to_end:.0f}米处" if event.to_end is not None else ""
                # The arbiter does not repeat the same sentence within a few seconds.
                event.merged = (not event.spoken and before is not None and before.spoken and event.t - before.t <= 10
                                and (before.prompt, before.extra) == (event.prompt, event.extra))
                said.append(f"{NAMES.get(event.prompt, event.prompt)}@{distance}"
                            + ("✓" if event.spoken else "（与上一句相同，不重复播）" if event.merged else "✗未播"))
                before = event
        crossing = [event for event in recorder.events if event.route == index and event.kind == "crossing"
                    and event.step in (number, number - 1)]
        action = step.action or step.assistant_action or "（无动作）"
        if number > walked_to:
            verdict = "未走到（之后改走新路线）"
        elif last:
            arrived = [event for event in recorder.events if event.route == index and event.kind == "arrived"]
            verdict = ("到达已播报" if arrived and arrived[0].spoken else "到达已发出但未播" if arrived
                       else "未走到终点（之后改走新路线）")
        elif step.is_crossing:
            verdict = ("路口提示已播，K1 确认后继续" if crossing and crossing[0].spoken
                       else "路口提示已发出但未播" if crossing else "✗ 路口没有提示")
        elif step.maneuver is None:
            verdict = "（该段没有转向动作）" if not step.action else f"✗ 动作「{step.action}」没有对应的播报"
        elif not said:
            verdict = "✗ 没有任何提示"
        elif all("✗" not in item for item in said):
            verdict = "全部已播"
        elif any("✓" in item for item in said):
            verdict = "部分已播"
        else:
            verdict = "✗ 发出但都没播"
        rows.append((number + 1, f"{step.distance_m:.0f}", action, NAMES.get(step.maneuver, "—") if step.maneuver else "—",
                     "；".join(said) or "—", verdict))
    return rows


def write_report(recorder, walker, place, outcome, args, path):
    recorder.match_speech()
    out = []
    add = out.append
    add(f"手杖导航逐段核对报告  {time.strftime('%Y-%m-%d %H:%M')}")
    add(f"目的地：{place.name}；首条路线 {recorder.routes[0].distance_m:.0f} 米、{len(recorder.routes[0].steps)} 段；"
        f"步速 {args.speed} 米/秒，每 {args.interval:g} 秒上报一个定位点，定位抖动约 {args.noise_m:g} 米")
    add(f"结果：{ {'arrived': '走到终点，手杖播报到达', 'stopped': '导航中途结束', 'timeout': '超时未走完'}[outcome]}；"
        f"实际步行 {recorder.walked:.0f} 米，用时 {recorder.clock() / 60:.1f} 分钟；共规划路线 {len(recorder.routes)} 次")
    add(f"途中按 K1：确认过街 {walker.crossings} 次，「请停下对正」后确认朝向 {walker.aligned_presses} 次")
    add("")
    problems = []
    for index, route in enumerate(recorder.routes):
        steps = [event.step for event in recorder.events if event.route == index and event.step is not None]
        walked_to = max(steps + [recorder.reached.get(index, -1)])
        walked_to = min(walked_to, recorder.left_at.get(index, walked_to))
        if index == len(recorder.routes) - 1 and outcome == "arrived":
            walked_to = len(route.steps) - 1
        add(f"── 路线 {index + 1}（{route.distance_m:.0f} 米，{len(route.steps)} 段）" + ("" if index == 0 else "，偏离后重新规划"))
        add("段 | 长度(米) | 高德动作 | 应播 | 实际发出（距转弯点距离，✓=喇叭已播） | 结论")
        for row in step_rows(recorder, index, route, walked_to):
            add(" | ".join(str(cell) for cell in row))
            if "✗" in row[5] or "部分" in row[5]:
                problems.append(f"路线 {index + 1} 第 {row[0]} 段（{row[2]}）：{row[5]}；{row[4]}")
        add("")
    add("── 走错之后")
    miss = walker.windows.get("miss")
    if miss:
        if miss.noticed is None:
            add(f"走过头：直走了 {miss.beyond:.0f} 米仍然没有提示偏离。")
            problems.append("走过转弯点后没有提示偏离")
        else:
            add(f"走过头：走过转弯点 {miss.beyond:.0f} 米（{miss.noticed.t - miss.start:.0f} 秒）后提示「偏离路线，重新规划」"
                f"{'，喇叭已播' if miss.noticed.spoken else '，但喇叭没有播出'}。")
            if miss.replanned is None:
                add("  之后没有得到新路线。")
                problems.append("偏离后没有得到新路线")
            else:
                # What the new route said first. Its guidance comes before the first fix, so it belongs to no step.
                after = [event for event in recorder.events if event.t >= miss.noticed.t and event is not miss.noticed
                         and event.kind == "prompt"][:2]
                add(f"  {miss.replanned - miss.noticed.t:.0f} 秒后得到新路线（{miss.new.distance_m:.0f} 米，{len(miss.new.steps)} 段）。"
                    + ("新路线先播：" + "，然后".join(
                        f"「{NAMES.get(event.prompt, event.prompt)}」（偏离提示后 {event.t - miss.noticed.t:.0f} 秒"
                        f"{'' if event.spoken else '，未播出'}）" for event in after) + "。" if after else "之后没有新的提示。"))
                if not after or after[0].t - miss.noticed.t > 10:
                    problems.append("重新规划后 10 秒内没有任何指引")
    glitch = walker.windows.get("glitch")
    if glitch:
        add("GPS 跳点：单个偏离约 70 米的定位点" + ("触发了重新规划（不应该）。" if glitch.rerouted else "没有触发重新规划，导航照常。"))
        if glitch.rerouted:
            problems.append("单个 GPS 跳点触发了重新规划")
    reverse = walker.windows.get("reverse")
    if reverse:
        if reverse.told is not None:
            add(f"走反方向：沿来路往回走了 {reverse.back:.0f} 米（{reverse.seconds:.0f} 秒）后提示「掉头」"
                f"{'，喇叭已播' if reverse.told.spoken else '，但喇叭没有播出'}；用户转身后导航继续。")
            if not reverse.told.spoken:
                problems.append("走反方向的「掉头」提示没有播出")
        else:
            add(f"走反方向：沿来路往回走了 {reverse.back:.0f} 米（{reverse.seconds:.0f} 秒），手杖没有任何提醒。"
                "这段时间喇叭播出的内容：" + ("、".join(reverse.heard) or "无"))
            problems.append("沿路线反方向走时没有任何提醒")
    add("")
    issued = [event for event in recorder.events if event.kind in ("prompt", "crossing", "arrived", "reroute")]
    unspoken = [event for event in issued if not event.spoken and not getattr(event, "merged", False)]
    add(f"── 合计：导航器发出 {len(issued)} 条，喇叭播出 {len(issued) - len(unspoken)} 条，未播 {len(unspoken)} 条")
    for event in unspoken:
        add(f"  未播：{event.t:.0f} 秒，路线 {event.route + 1} 第 {(event.step or 0) + 1} 段，{NAMES.get(event.prompt, event.prompt)}"
            + (f"（距转弯点 {event.to_end:.0f} 米）" if event.to_end is not None else ""))
    add("")
    add("── 发现的问题" if problems else "── 没有发现问题")
    out.extend("  " + problem for problem in problems)
    add("")
    add("── 时间线（秒 | 已走米数 | 事件）")
    timeline = [(t, walked, "★ " + text) for t, walked, text in recorder.notes]
    timeline += [(event.t, event.walked, f"发出 {NAMES.get(event.prompt, event.prompt)}"
                  + (f" {'+'.join(event.extra)}" if event.extra else "")
                  + (f"（第 {event.step + 1} 段，距段末 {event.to_end:.0f} 米，离路线 {event.off:.0f} 米）" if event.step is not None else "")
                  + ("" if event.spoken else " ✗未播")) for event in recorder.events]
    for t, walked, text in sorted(timeline):
        add(f"{t:7.0f} | {walked:6.0f} | {text}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(out) + "\n", "utf-8")
    return problems, out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=str(HERE.parent), help="checkout of the cane code to check")
    parser.add_argument("--target-m", type=float, default=3000.0, help="wanted length of the walking route")
    parser.add_argument("--speed", type=float, default=1.3, help="walking speed in m/s")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between GPS fixes")
    parser.add_argument("--noise-m", type=float, default=2.0, help="GPS jitter (standard deviation, metres)")
    parser.add_argument("--miss-turn-after", type=float, help="walk straight past the first turn after this many metres")
    parser.add_argument("--glitch-after", type=float, help="report one far-off fix after this many metres")
    parser.add_argument("--reverse-after", type=float, help="turn round and walk back after this many metres")
    parser.add_argument("--max-minutes", type=float, default=75.0)
    parser.add_argument("--seed", type=int, default=3070)
    parser.add_argument("--provider", choices=("amap", "ors", "google"), default="amap",
                        help="map service to check (its key: AMAP_KEY, ORS_KEY or GOOGLE_MAPS_KEY)")
    parser.add_argument("--origin", type=lambda text: [float(part) for part in text.split(",")], metavar="LNG,LAT",
                        help="start point in WGS-84, as a phone reports it (default: the demo origin in Shenzhen)")
    parser.add_argument("--report", default=str(HERE.parent / "route-check" / "report.txt"),
                        help="where to write the report (the folder is not tracked by git)")
    args = parser.parse_args()
    key = {"amap": "AMAP_KEY", "ors": "ORS_KEY", "google": "GOOGLE_MAPS_KEY"}[args.provider]
    if not os.environ.get(key):
        parser.error(f"{key} is not set")
    with tempfile.TemporaryDirectory(prefix="cane-route-check-") as directory:
        work = Path(directory).resolve()
        world = R.build(SimpleNamespace(repo=args.repo, address=None, imu="stationary", speech_speed=1.0,
                                        speech_models=None, provider=args.provider), work)
        world.feeder = None
        recorder = Recorder(world)
        walker = Walker(world, recorder, args)
        outcome, place = "stopped", SimpleNamespace(name="?")
        try:
            nav = world.navigation.status
            for _ in range(40):
                walker.tick()
                if nav()["pico_ok"] and nav()["location_valid"]:
                    break
            else:
                raise RuntimeError("the cane never accepted the simulated location")
            place, planned = choose_destination(world, args.target_m, 150.0)
            R.log(f"destination: {place.name}, walking route {planned.distance_m:.0f} m in {len(planned.steps)} steps")
            world.navigation.stage_place(place)
            walker.press_k1()
            for _ in range(30):
                walker.tick()
                if world.cane.navigator.active:
                    break
            else:
                raise RuntimeError("K1 did not start navigation")
            walker.tick()
            walker.press_k1()   # the user holds the cane still and confirms the heading
            walker.tick()
            R.log(f"navigation started; heading confirmed: {world.cane.sm.aligned}")
            outcome = walker.walk()
            for _ in range(6):
                walker.tick()
        except KeyboardInterrupt:
            outcome = "stopped"
        finally:
            world.stop.set()
            if recorder.routes:
                problems, lines = write_report(recorder, walker, place, outcome, args, args.report)
                print("\n".join(lines[:lines.index("── 时间线（秒 | 已走米数 | 事件）")]))
                print(f"report: {args.report}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
