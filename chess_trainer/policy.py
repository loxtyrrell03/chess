from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class PageMode(StrEnum):
    COMPUTER = "computer"
    ANALYSIS = "analysis"
    PRACTICE = "practice"
    HUMAN_GAME = "human_game"
    COMPETITIVE_PUZZLE = "competitive_puzzle"
    LICHESS = "lichess"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    mode: PageMode
    can_analyze: bool
    can_execute: bool
    reason: str


def classify_page(url: str) -> PolicyDecision:
    """Keep the site-use boundary in one auditable place.

    Analysis overlays are enabled on supported HTTPS Chess.com and Lichess pages.
    """

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or hostname not in {
        "chess.com", "www.chess.com", "lichess.org", "www.lichess.org"
    }:
        return PolicyDecision(PageMode.UNKNOWN, False, False, "Unexpected site origin")
    path = parsed.path.lower().rstrip("/") or "/"

    def route(prefix: str) -> bool:
        return path == prefix or path.startswith(prefix + "/")

    if hostname in {"lichess.org", "www.lichess.org"}:
        if any(route(prefix) for prefix in ("/analysis", "/study", "/editor")):
            return PolicyDecision(PageMode.ANALYSIS, True, False, "Lichess analysis workspace")
        return PolicyDecision(PageMode.LICHESS, True, True, "Supported HTTPS Lichess route")
    if route("/analysis"):
        return PolicyDecision(PageMode.ANALYSIS, True, False, "Chess.com analysis workspace")

    return PolicyDecision(PageMode.COMPUTER, True, True, "All supported HTTPS chess.com routes are permitted")


def site_label(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    return "Lichess" if hostname in {"lichess.org", "www.lichess.org"} else "Chess.com"
