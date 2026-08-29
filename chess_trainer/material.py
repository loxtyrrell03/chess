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
# imbalances; T1 handles the three middle labels; LQO is reserved for a genuine
# near-full queen deficit.
MINOR_ODDS_THRESHOLD_CP = 300
ROOK_ODDS_THRESHOLD_CP = 500
QUEEN_FOR_MINOR_THRESHOLD_CP = 600
QUEEN_ODDS_THRESHOLD_CP = 800
MINOR_WITH_PAWN_COMPENSATION_THRESHOLD_CP = 200
ROOK_WITH_PAWN_COMPENSATION_THRESHOLD_CP = 300

ODDS_MODE_LABELS = {
    "none": "BT4 normal",
    "knight": "T1 minor-equivalent",
    "rook": "T1 rook-equivalent",
    "queen_for_knight": "T1 queen-for-material",
    "queen": "LQO near-full queen",
}

_PIECE_NAMES = ("pawn", "knight", "bishop", "rook", "queen")


@dataclass(frozen=True, slots=True)
class MaterialAssessment:
    """Player-relative physical material used for coarse network selection."""

    player_counts: tuple[int, int, int, int, int]
    opponent_counts: tuple[int, int, int, int, int]
    player_value_cp: int
    opponent_value_cp: int
    balance_cp: int
    deficit_cp: int
    gross_deficit_cp: int
    compensation_cp: int
    minor_count_deficit: int
    rook_count_deficit: int
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

    Net balance accounts for exchanges and compensation, while unmatched piece
    counts retain materially important composition. Thus a rook for a minor is
    only an exchange and stays on BT4, but one fewer minor with only one pawn
    in return remains a real minor handicap even though its net value is two.
    Promotions and underpromotions need no special case because every confirmed
    live position is recounted.
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
    player_minor_count = player_counts[1] + player_counts[2]
    opponent_minor_count = opponent_counts[1] + opponent_counts[2]
    grouped_values = (100, 300, 500, 900)
    player_groups = (
        player_counts[0],
        player_minor_count,
        player_counts[3],
        player_counts[4],
    )
    opponent_groups = (
        opponent_counts[0],
        opponent_minor_count,
        opponent_counts[3],
        opponent_counts[4],
    )
    gross_deficit = sum(
        max(0, opponent_count - player_count) * value
        for player_count, opponent_count, value in zip(
            player_groups,
            opponent_groups,
            grouped_values,
            strict=True,
        )
    )
    compensation = sum(
        max(0, player_count - opponent_count) * value
        for player_count, opponent_count, value in zip(
            player_groups,
            opponent_groups,
            grouped_values,
            strict=True,
        )
    )
    minor_count_deficit = max(0, opponent_minor_count - player_minor_count)
    rook_count_deficit = max(0, opponent_counts[3] - player_counts[3])
    queen_count_deficit = max(0, opponent_counts[-1] - player_counts[-1])
    minor_count_surplus = max(0, player_minor_count - opponent_minor_count)
    rook_count_surplus = max(0, player_counts[3] - opponent_counts[3])
    queen_count_surplus = max(0, player_counts[4] - opponent_counts[4])
    mode = _mode_for_material(
        deficit_cp=deficit,
        compensation_cp=compensation,
        minor_count_deficit=minor_count_deficit,
        minor_count_surplus=minor_count_surplus,
        rook_count_deficit=rook_count_deficit,
        rook_count_surplus=rook_count_surplus,
        queen_count_deficit=queen_count_deficit,
        queen_count_surplus=queen_count_surplus,
    )
    return MaterialAssessment(
        player_counts=player_counts,
        opponent_counts=opponent_counts,
        player_value_cp=player_value,
        opponent_value_cp=opponent_value,
        balance_cp=balance,
        deficit_cp=deficit,
        gross_deficit_cp=gross_deficit,
        compensation_cp=compensation,
        minor_count_deficit=minor_count_deficit,
        rook_count_deficit=rook_count_deficit,
        queen_count_deficit=queen_count_deficit,
        odds_mode=mode,
    )


def detect_odds_mode(board: chess.Board | None, player_color: chess.Color | None) -> str:
    """Map live material to the closest honestly available odds network mode."""

    assessment = assess_material(board, player_color)
    return assessment.odds_mode if assessment is not None else "none"


def odds_mode_label(odds_mode: str) -> str:
    """Return the honest user-facing name of an available network family."""

    return ODDS_MODE_LABELS.get(odds_mode, odds_mode.replace("_", " ").title())


def explain_material_assessment(assessment: MaterialAssessment) -> str:
    """Explain why an assessment maps to its coarse network family.

    The explanation deliberately shows composition, compensation, and net
    balance separately. A player who is missing a queen but has won two pawns
    back can therefore see both the nine-point gross gap and the resulting
    seven-point net deficit instead of receiving an ambiguous tier label.
    """

    gaps = _piece_count_differences(
        assessment.player_counts,
        assessment.opponent_counts,
    )
    compensation = _piece_count_differences(
        assessment.opponent_counts,
        assessment.player_counts,
    )
    balance = _format_balance(assessment.balance_cp)

    if gaps:
        gap_word = "gap" if len(gaps) == 1 else "gaps"
        composition = (
            f"{_join_phrases(gaps)} {gap_word} "
            f"({_format_points(assessment.gross_deficit_cp)})"
        )
        if compensation:
            composition += (
                f"; compensation: {_join_phrases(compensation)} "
                f"({_format_points(assessment.compensation_cp)})"
            )
        else:
            composition += "; no compensation"
    elif compensation:
        composition = (
            f"Ahead by {_join_phrases(compensation)} "
            f"({_format_points(assessment.compensation_cp)})"
        )
    else:
        composition = "Material equal"

    rationale = {
        "none": "BT4 matches this ordinary or sufficiently compensated imbalance.",
        "knight": "T1 is the closest available minor-equivalent odds family.",
        "rook": "T1 is the closest available rook-equivalent odds family.",
        "queen_for_knight": "T1 is the closest available intermediate odds family.",
        "queen": "LQO is the closest available near-full-queen odds family.",
    }.get(assessment.odds_mode, "This is the closest available network family.")
    return f"{composition}; {balance}. {rationale}"


def _piece_count_differences(
    lower_counts: tuple[int, int, int, int, int],
    higher_counts: tuple[int, int, int, int, int],
) -> tuple[str, ...]:
    phrases: list[str] = []
    for lower, higher, piece_name in zip(
        lower_counts,
        higher_counts,
        _PIECE_NAMES,
        strict=True,
    ):
        difference = max(0, higher - lower)
        if difference:
            phrases.append(
                piece_name
                if difference == 1
                else f"{difference} {piece_name}s"
            )
    return tuple(phrases)


def _join_phrases(phrases: tuple[str, ...]) -> str:
    if len(phrases) <= 1:
        return phrases[0] if phrases else ""
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return f"{', '.join(phrases[:-1])}, and {phrases[-1]}"


def _format_balance(balance_cp: int) -> str:
    points = _format_points(abs(balance_cp))
    if balance_cp < 0:
        return f"net -{points}"
    if balance_cp > 0:
        return f"net +{points}"
    return "net equal"


def _format_points(value_cp: int) -> str:
    return str(value_cp // 100)


def _mode_for_material(
    *,
    deficit_cp: int,
    compensation_cp: int,
    minor_count_deficit: int,
    minor_count_surplus: int,
    rook_count_deficit: int,
    rook_count_surplus: int,
    queen_count_deficit: int,
    queen_count_surplus: int,
) -> str:
    if deficit_cp <= 0:
        return "none"

    if queen_count_deficit:
        # LQO is an actual queen-odds network, so reserve it for a queen-count
        # gap with no meaningful captured-piece compensation. One pawn per
        # unmatched queen is allowed because a promoted opposing queen itself
        # consumes a pawn. Once the player wins a minor, rook, or several pawns
        # back, T1 is the honest coarse fallback while a full minor-equivalent
        # net deficit remains.
        if (
            deficit_cp >= QUEEN_ODDS_THRESHOLD_CP
            and compensation_cp <= queen_count_deficit * PIECE_VALUES_CP[chess.PAWN]
        ):
            return "queen"
        if deficit_cp >= MINOR_ODDS_THRESHOLD_CP:
            return "queen_for_knight"
        return "none"

    # Above a rook-equivalent deficit T1's highest compatibility label is the
    # least misleading available choice, regardless of which several pieces
    # compose it.
    if deficit_cp >= QUEEN_FOR_MINOR_THRESHOLD_CP:
        return "queen_for_knight"
    if (
        rook_count_deficit
        and not minor_count_surplus
        and not rook_count_surplus
        and not queen_count_surplus
        and deficit_cp >= ROOK_WITH_PAWN_COMPENSATION_THRESHOLD_CP
    ):
        return "rook"
    if deficit_cp >= ROOK_ODDS_THRESHOLD_CP:
        return "rook"
    if (
        minor_count_deficit
        and not rook_count_surplus
        and not queen_count_surplus
        and deficit_cp >= MINOR_WITH_PAWN_COMPENSATION_THRESHOLD_CP
    ):
        return "knight"
    if deficit_cp >= MINOR_ODDS_THRESHOLD_CP:
        return "knight"
    return "none"
