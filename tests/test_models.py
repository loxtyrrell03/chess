from __future__ import annotations

import math

import chess
import pytest

from chess_trainer.models import Orientation, Snapshot, normalize_piece_code


def test_piece_aliases_are_normalized() -> None:
    assert normalize_piece_code("wp") == "P"
    assert normalize_piece_code("black-knight") == "n"
    assert normalize_piece_code("Q") == "Q"
    assert normalize_piece_code("not-a-piece") == ""


def test_snapshot_parses_extension_schema() -> None:
    message = {
        "v": 1,
        "type": "position.snapshot",
        "pageId": "tab-9",
        "seq": 12,
        "gameKey": "bot-4",
        "positionHash": "abc",
        "page": {"url": "https://www.chess.com/play/computer", "visible": True, "focused": True},
        "board": {
            "pieces": {"e1": "wk", "e8": "bk", "a2": "white-pawn"},
            "orientation": "black",
            "orientationConfidence": 0.98,
            "playerColor": "black",
            "rect": {"left": 1, "top": 2, "width": 800, "height": 800},
        },
        "moves": ["e4", "e5"],
        "game": {"side_to_move": "white"},
    }

    snapshot = Snapshot.from_message(message)

    assert snapshot.page_id == "tab-9"
    assert snapshot.orientation is Orientation.BLACK
    assert snapshot.pieces == {"e1": "K", "e8": "k", "a2": "P"}
    assert snapshot.board_rect and snapshot.board_rect.width == 800
    assert snapshot.side_to_move is chess.WHITE


def test_snapshot_rejects_non_finite_orientation_confidence() -> None:
    message = {
        "type": "position.snapshot",
        "seq": 1,
        "board": {
            "pieces": {"e1": "wk", "e8": "bk"},
            "orientation": "white",
            "orientationConfidence": "nan",
        },
    }

    with pytest.raises(ValueError, match="orientation confidence"):
        Snapshot.from_message(message)

    assert math.isnan(float("nan"))  # Documents why ordinary float coercion is insufficient.


def test_snapshot_accepts_renderer_history_without_piece_nodes() -> None:
    snapshot = Snapshot.from_message(
        {
            "type": "position.snapshot",
            "pageId": "renderer-tab",
            "seq": 2,
            "gameKey": "bot-9",
            "page": {"url": "https://www.chess.com/play/computer"},
            "board": {"startingFen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
            "moves": ["e4"],
        }
    )
    assert not snapshot.pieces
    assert snapshot.moves == ("e4",)


def test_snapshot_reads_current_fen_separately_from_history_root() -> None:
    current_fen = "4k3/8/8/8/8/8/8/4K3 b - - 7 23"
    snapshot = Snapshot.from_message(
        {
            "type": "position.snapshot",
            "page": {"url": "https://www.chess.com/analysis"},
            "board": {"currentFen": current_fen},
        }
    )

    assert snapshot.current_fen == current_fen
    assert snapshot.starting_fen is None
