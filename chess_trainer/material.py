from __future__ import annotations

from dataclasses import dataclass

import chess


# Conventional material values are intentionally used instead of an engine
# evaluation. Auto-network selection describes the pieces physically present,
# not whether positional compensation happens to make the position playable.
PIECE_VALUES_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}

# These are handicap-equivalent bands, not claims that the installed networks
# model every material composition independently. BT4 handles ordinary
# imbalances; T1 handles the three middle bands; LQO is reserved for a genuine
# near-full queen deficit.
MINOR_ODDS_THRESHOLD_CP = 300
ROOK_ODDS_THRESHOLD_CP = 500
QUEEN_FOR_MINOR_THRESHOLD_CP = 600
QUEEN_ODDS_THRESHOLD_CP = 800


@dataclass(frozen=True, slots=True)
class MaterialAssessment:
    """Player-relative physical material used for coarse network selection."""

    player_counts: tuple[int, int, int, int, int]
    opponent_counts: tuple[int, int, int, int, int]
    player_value_cp: int
    opponent_value_cp: int
    balance_cp: int
    deficit_cp: int
    queen_count_deficit: int
    odds_mode: str

    @property
    def network_family(self) -> str:
        if self.odds_mode == "none":
            return "bt4"
        if self.odds_mode == "queen":
            return "lqo"
        return "t1"


def assess_material(
    board: chess.Board | None,
    player_color: chess.Color | None,
) -> MaterialAssessment | None:
    """Return a complete, player-relative material assessment.

    The net balance automatically accounts for exchanges and compensation:
    losing a rook while taking a minor is only a two-pawn deficit, equal trades
    cancel, and pieces captured from the opponent reduce (rather than create)
    the player's deficit. Promotions and underpromotions need no special case
    because the live piece counts already contain the promoted piece.
    """

    if board is None or player_color is None:
        return None
    opponent_color = not player_color
    piece_types = (
        chess.PAWN,
        chess.KNIGHT,
        chess.BISHOP,
        chess.ROOK,
        chess.QUEEN,
    )
    player_counts = tuple(len(board.pieces(piece_type, player_color)) for piece_type in piece_types)
    opponent_counts = tuple(len(board.pieces(piece_type, opponent_color)) for piece_type in piece_types)
    player_value = sum(
        count * PIECE_VALUES_CP[piece_type]
        for count, piece_type in zip(player_counts, piece_types, strict=True)
    )
    opponent_value = sum(
        count * PIECE_VALUES_CP[piece_type]
        for count, piece_type in zip(opponent_counts, piece_types, strict=True)
    )
    balance = player_value - opponent_value
    deficit = max(0, -balance)
    queen_count_deficit = max(0, opponent_counts[-1] - player_counts[-1])
    mode = _mode_for_deficit(deficit, queen_count_deficit)
    return MaterialAssessment(
        player_counts=player_counts,
        opponent_counts=opponent_counts,
        player_value_cp=player_value,
        opponent_value_cp=opponent_value,
        balance_cp=balance,
        deficit_cp=deficit,
        queen_count_deficit=queen_count_deficit,
        odds_mode=mode,
    )


def detect_odds_mode(board: chess.Board | None, player_color: chess.Color | None) -> str:
    """Map live material to the closest honestly available odds network mode."""

    assessment = assess_material(board, player_color)
    return assessment.odds_mode if assessment is not None else "none"


def _mode_for_deficit(deficit_cp: int, queen_count_deficit: int) -> str:
    if deficit_cp < MINOR_ODDS_THRESHOLD_CP:
        return "none"
    if deficit_cp < ROOK_ODDS_THRESHOLD_CP:
        return "knight"
    if deficit_cp < QUEEN_FOR_MINOR_THRESHOLD_CP:
        return "rook"
    if deficit_cp < QUEEN_ODDS_THRESHOLD_CP:
        return "queen_for_knight"
    if queen_count_deficit:
        return "queen"
    # LQO is specifically queen-odds trained. A huge deficit made from rooks,
    # minors, or pawns is still better represented by the general T1 odds net.
    return "queen_for_knight"
