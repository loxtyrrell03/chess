from __future__ import annotations

import chess
import pytest

from chess_trainer.material import assess_material, detect_odds_mode


FULL = "PPPPPPPPNNBBRRQ"


def without(pieces: str, removed: str) -> str:
    for symbol in removed:
        pieces = pieces.replace(symbol, "", 1)
    return pieces


def promoted(pieces: str, promotion: str) -> str:
    return without(pieces, "P") + promotion


def material_board(
    player_pieces: str,
    opponent_pieces: str,
    player_color: chess.Color,
) -> chess.Board:
    board = chess.Board.empty()
    board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
    available = [square for square in chess.SQUARES if square not in {chess.E1, chess.E8}]
    by_color = {
        player_color: player_pieces,
        not player_color: opponent_pieces,
    }
    for color, pieces in by_color.items():
        for symbol in pieces:
            square = available.pop(0) if color == chess.WHITE else available.pop()
            board.set_piece_at(square, chess.Piece.from_symbol(symbol if color == chess.WHITE else symbol.lower()))
    return board


@pytest.mark.parametrize("player_color", [chess.WHITE, chess.BLACK], ids=["white-player", "black-player"])
@pytest.mark.parametrize(
    ("case", "player_pieces", "opponent_pieces", "deficit_cp", "expected"),
    [
        ("equal material", FULL, FULL, 0, "none"),
        ("equal trades", without(FULL, "QRBP"), without(FULL, "QRBP"), 0, "none"),
        ("opponent loss only", FULL, without(FULL, "Q"), 0, "none"),
        ("one pawn", without(FULL, "P"), FULL, 100, "none"),
        ("two pawns", without(FULL, "PP"), FULL, 200, "none"),
        ("three pawns", without(FULL, "PPP"), FULL, 300, "knight"),
        ("full knight", without(FULL, "N"), FULL, 300, "knight"),
        ("full bishop", without(FULL, "B"), FULL, 300, "knight"),
        ("minor for one pawn", without(FULL, "N"), without(FULL, "P"), 200, "knight"),
        ("minor for two pawns", without(FULL, "N"), without(FULL, "PP"), 100, "none"),
        ("bishop versus knight", without(FULL, "B"), without(FULL, "N"), 0, "none"),
        ("exchange rook for minor", without(FULL, "R"), without(FULL, "N"), 200, "none"),
        ("rook versus two minors", without(FULL, "NN"), without(FULL, "R"), 100, "none"),
        ("full rook", without(FULL, "R"), FULL, 500, "rook"),
        ("rook for one pawn", without(FULL, "R"), without(FULL, "P"), 400, "rook"),
        ("rook for two pawns", without(FULL, "R"), without(FULL, "PP"), 300, "rook"),
        ("queen for rook", without(FULL, "Q"), without(FULL, "R"), 400, "queen_for_knight"),
        ("queen for knight", without(FULL, "Q"), without(FULL, "N"), 600, "queen_for_knight"),
        ("queen for bishop", without(FULL, "Q"), without(FULL, "B"), 600, "queen_for_knight"),
        ("queen for bishop and pawn", without(FULL, "Q"), without(FULL, "BP"), 500, "queen_for_knight"),
        ("queen for bishop and two pawns", without(FULL, "Q"), without(FULL, "BPP"), 400, "queen_for_knight"),
        ("queen for bishop and three pawns", without(FULL, "Q"), without(FULL, "BPPP"), 300, "queen_for_knight"),
        ("queen for bishop and four pawns", without(FULL, "Q"), without(FULL, "BPPPP"), 200, "none"),
        ("queen for rook and minor", without(FULL, "Q"), without(FULL, "RN"), 100, "none"),
        ("queen for two pawns", without(FULL, "Q"), without(FULL, "PP"), 700, "queen_for_knight"),
        ("queen for one pawn", without(FULL, "Q"), without(FULL, "P"), 800, "queen"),
        ("full queen", without(FULL, "Q"), FULL, 900, "queen"),
        ("multiple losses with queen trade", without(FULL, "QR"), without(FULL, "Q"), 500, "rook"),
        ("large non-queen deficit", without(FULL, "RBP"), FULL, 900, "queen_for_knight"),
        ("opponent queen promotion", FULL, promoted(FULL, "Q"), 800, "queen"),
        ("opponent rook underpromotion", FULL, promoted(FULL, "R"), 400, "rook"),
        ("opponent knight underpromotion", FULL, promoted(FULL, "N"), 200, "knight"),
        ("player promotion advantage", promoted(FULL, "Q"), FULL, 0, "none"),
    ],
)
def test_material_odds_bands_cover_composition_and_compensation(
    case: str,
    player_pieces: str,
    opponent_pieces: str,
    deficit_cp: int,
    expected: str,
    player_color: chess.Color,
) -> None:
    board = material_board(player_pieces, opponent_pieces, player_color)

    assessment = assess_material(board, player_color)

    assert assessment is not None, case
    assert assessment.deficit_cp == deficit_cp, case
    assert assessment.odds_mode == expected, case
    assert detect_odds_mode(board, player_color) == expected, case


def test_unknown_player_side_never_selects_an_odds_network() -> None:
    board = material_board(without(FULL, "Q"), FULL, chess.WHITE)

    assert assess_material(board, None) is None
    assert detect_odds_mode(board, None) == "none"


def test_assessment_exposes_gross_loss_and_exchange_compensation() -> None:
    exchange = material_board(without(FULL, "R"), without(FULL, "N"), chess.WHITE)
    minor_for_pawn = material_board(without(FULL, "B"), without(FULL, "P"), chess.WHITE)

    exchange_assessment = assess_material(exchange, chess.WHITE)
    minor_assessment = assess_material(minor_for_pawn, chess.WHITE)

    assert exchange_assessment is not None
    assert exchange_assessment.gross_deficit_cp == 500
    assert exchange_assessment.compensation_cp == 300
    assert exchange_assessment.deficit_cp == 200
    assert exchange_assessment.odds_mode == "none"
    assert minor_assessment is not None
    assert minor_assessment.gross_deficit_cp == 300
    assert minor_assessment.compensation_cp == 100
    assert minor_assessment.minor_count_deficit == 1
    assert minor_assessment.odds_mode == "knight"


def test_live_material_reversal_is_stateless_and_player_relative() -> None:
    white_down_queen = material_board(without(FULL, "Q"), FULL, chess.WHITE)
    equal_again = material_board(without(FULL, "Q"), without(FULL, "Q"), chess.WHITE)
    black_down_rook = material_board(FULL, without(FULL, "R"), chess.WHITE)

    assert detect_odds_mode(white_down_queen, chess.WHITE) == "queen"
    assert detect_odds_mode(equal_again, chess.WHITE) == "none"
    assert detect_odds_mode(black_down_rook, chess.WHITE) == "none"
    assert detect_odds_mode(black_down_rook, chess.BLACK) == "rook"


@pytest.mark.parametrize(
    ("player_color", "fen", "expected"),
    [
        # A recent 65-move queen-odds game: compensation grew from one pawn to
        # several pieces before equality. The legacy presence-only detector
        # incorrectly stayed on LQO through every position containing Black's
        # queen.
        (chess.WHITE, "r1bqkbnr/pp1p1ppp/2n5/4p3/8/2P5/PP1PPPPP/RNB1KBNR w KQkq - 0 4", "queen"),
        (chess.WHITE, "4n1k1/2b1nr1p/1q4p1/p1Np4/1p1P2P1/P1P2P1R/KP6/2B4R w - - 0 43", "queen_for_knight"),
        (chess.WHITE, "5k2/4n1nR/1q1b1rp1/p2p2N1/1p1P1PP1/P1P5/KP6/2B4R w - - 1 47", "queen_for_knight"),
        (chess.WHITE, "6nR/6n1/1q1b1kp1/p2p4/1p1P1PP1/P1P5/KP6/2B4R w - - 0 50", "queen_for_knight"),
        (chess.WHITE, "6R1/6n1/1q1b1kp1/p2p4/3P1PP1/P1p5/KP6/2B4R w - - 0 51", "none"),
        # The reported late endgame: White has R+N+6P versus R+B+N+5P.
        # Net value is only -2 because of the extra pawn, but the unmatched
        # bishop remains a full-minor structural handicap.
        (chess.WHITE, "8/prp2kpp/5n2/4p3/P7/b1PP1N2/3K1PPP/7R w - - 0 21", "knight"),
    ],
)
def test_recent_live_positions_follow_compensation_and_endgame_composition(
    player_color: chess.Color,
    fen: str,
    expected: str,
) -> None:
    assert detect_odds_mode(chess.Board(fen), player_color) == expected
