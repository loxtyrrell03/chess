from __future__ import annotations

import chess

from chess_trainer.models import Orientation, Snapshot
from chess_trainer.reconcile import piece_map


def snapshot_for(
    board: chess.Board,
    *,
    seq: int,
    game_key: str = "game-1",
    starting_fen: str | None = None,
    moves: tuple[str, ...] = (),
    url: str = "https://www.chess.com/play/computer",
) -> Snapshot:
    return Snapshot(
        page_id="page-1",
        seq=seq,
        url=url,
        game_key=game_key,
        pieces=piece_map(board),
        moves=moves,
        starting_fen=starting_fen,
        orientation=Orientation.WHITE,
        orientation_confidence=1.0,
        player_color=chess.WHITE,
    )

