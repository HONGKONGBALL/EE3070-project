"""Rule-based routing and the boundary for the future navigation service."""

from dataclasses import dataclass
import re
from typing import Protocol


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
    if key in {"stop navigation", "cancel navigation", "停止导航", "取消导航"}:
        return "stop", None
    if key in {"what's my next turn", "whats my next turn", "what is my next turn", "my next turn", "next turn",
               "下一步怎么走"}:
        return "next", None
    if key in {"where am i", "how far to my destination", "how far is my destination", "我在哪里", "离目的地还有多远"}:
        return "status", None
    if re.fullmatch(r"(?:(?:what's|what is) (?:my|the) )?(?:navigation|route) status", key):
        return "status", None
    if re.search(r"\b(?:navigat\w*|destination|route|next turn|how far)\b|导航|目的地", key):
        return "unsupported_navigation", None
    return "vision", None


def confirmed(text):
    return normalized(text.replace(",", " ")) in {"yes", "yes please", "confirm", "确认", "是", "是的"}
