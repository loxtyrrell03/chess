from __future__ import annotations

import pytest

from chess_trainer.policy import PageMode, classify_page


@pytest.mark.parametrize(
    ("url", "mode", "analyze", "execute"),
    [
        ("https://www.chess.com/play/computer", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/game/computer/123", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/analysis", PageMode.ANALYSIS, True, False),
        ("https://www.chess.com/practice/foo", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/game/live/123", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/game/daily/123", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/play/online", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/puzzles/rush", PageMode.COMPUTER, True, True),
        ("https://www.chess.com/home", PageMode.COMPUTER, True, True),
        ("https://chess.com/game/computer", PageMode.COMPUTER, True, True),
        ("https://lichess.org/eZY8AAa5eobo", PageMode.LICHESS, True, True),
        ("https://lichess.org/eZY8AAa5/black", PageMode.LICHESS, True, True),
        ("https://www.lichess.org/analysis", PageMode.ANALYSIS, True, False),
    ],
)
def test_page_policy(url: str, mode: PageMode, analyze: bool, execute: bool) -> None:
    decision = classify_page(url)
    assert decision.mode is mode
    assert decision.can_analyze is analyze
    assert decision.can_execute is execute


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/play/computer",
        "http://www.chess.com/play/computer",
        "https://www.chess.com.evil.test/play/computer",
        "https://example.org/play/computer",
        "https://lichess.org.evil.test/eZY8AAa5",
        "http://lichess.org/eZY8AAa5",
    ],
)
def test_page_policy_requires_exact_https_chess_com_training_route(url: str) -> None:
    decision = classify_page(url)
    assert not decision.can_analyze
    assert not decision.can_execute


@pytest.mark.parametrize(
    "url",
    [
        "https://www.chess.com/analysis",
        "https://lichess.org/analysis/standard/rnbqkbnr",
        "https://lichess.org/study/example",
        "https://lichess.org/editor",
    ],
)
def test_analysis_workspaces_are_classified_separately(url: str) -> None:
    decision = classify_page(url)

    assert decision.mode is PageMode.ANALYSIS
    assert decision.can_analyze
    assert not decision.can_execute
