"""Rule-based routing and the boundary for the future navigation service."""

from dataclasses import dataclass
import re
from typing import Protocol


SAFETY_QUERY = re.compile(
    r"\b(?:safe|okay|ok) (?:for me )?to (?:cross|walk|go|enter|proceed|continue|keep walking)\b"
    r"|\b(?:can|could|should|may) i (?:cross|walk|go|enter|proceed|continue|keep walking)\b"
    r"|\b(?:is|does) (?:the|this|that|my) (?:way|path|route|crossing|walkway|hallway|doorway)\b[^?]{0,40}"
    r"\b(?:safe|clear|passable)\b"
    r"|\bam i clear to (?:cross|walk|go|enter|proceed)\b"
    # 前面可以走吗 / 能不能过去 / 这条路走得通吗 / 前面有没有障碍
    r"|(?:能不能|可不可以|可以|能否|能)(?:直接|继续|往前|向前|直|再){0,3}(?:走|过去|过吗|过马路|通过|前进|通行)"
    r"|走得通|走得了|过得去|安全吗|安不安全|有没有障碍|有障碍吗|挡(?:住|着|路)"
)


@dataclass(frozen=True)
class NavigationReply:
    success: bool
    message: str


class NavigationAdapter(Protocol):
    def plan_route(self, destination: str) -> NavigationReply: ...
    def stop_navigation(self) -> NavigationReply: ...
    def get_next_instruction(self) -> NavigationReply: ...
    def get_navigation_status(self) -> NavigationReply: ...


class MockNavigationAdapter:
    def plan_route(self, destination):
        return NavigationReply(False, "Navigation is unavailable.")

    def stop_navigation(self):
        return NavigationReply(False, "There is no active route.")

    def get_next_instruction(self):
        return NavigationReply(False, "There is no active route.")

    def get_navigation_status(self):
        return NavigationReply(False, "There is no active route.")


def normalized(text):
    return " ".join(text.replace("’", "'").strip(" .?!。？！").casefold().split())


def route_command(text):
    spoken = text.strip(" .?!。？！")
    key = normalized(text)
    destination = re.fullmatch(r"(?:navigate to\s+|take me to\s+|导航去\s*)(.+)", spoken, re.IGNORECASE)
    if destination and destination[1].strip():
        place = destination[1].strip()
        return "plan", "CityU Library" if normalized(place) in {"city you library", "cityu library"} else place
    if SAFETY_QUERY.search(key):
        return "visual_safety", None
    if key in {"stop navigation", "cancel navigation", "停止导航", "取消导航"}:
        return "stop", None
    if key in {"what's my next turn", "whats my next turn", "what is my next turn", "my next turn", "next turn",
               "下一步怎么走"}:
        return "next", None
    if key in {"where am i", "how far to my destination", "how far is my destination", "我在哪里", "离目的地还有多远"}:
        return "status", None
    if re.fullmatch(r"(?:(?:what's|what is) (?:my|the) )?(?:navigation|route) status", key):
        return "status", None
    if re.search(r"\b(?:navigat\w*|destination|next turn)\b|导航|目的地", key):
        return "unsupported_navigation", None
    return "vision", None


def confirmed(text):
    return normalized(text.replace(",", " ")) in {"yes", "yes please", "confirm", "确认", "是", "是的"}
