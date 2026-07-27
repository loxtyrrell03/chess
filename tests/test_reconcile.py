from __future__ import annotations

from dataclasses import replace

import chess
import pytest

from chess_trainer.models import Orientation, SyncState
from chess_trainer.reconcile import GameReconciler

from conftest import snapshot_for


def observe_move(starting_fen: str, uci: str) -> tuple[GameReconciler, chess.Move]:
    board = chess.Board(starting_fen)
    tracker = GameReconciler()
    initial = tracker.ingest(snapshot_for(board, seq=1, starting_fen=starting_fen))
    assert initial.state is SyncState.SYNCHRONIZED
    move = chess.Move.from_uci(uci)
    assert move in board.legal_moves
    board.push(move)
    transition = tracker.ingest(snapshot_for(board, seq=2, starting_fen=starting_fen))
    assert transition.state is SyncState.SYNCHRONIZED
    assert transition.moves == (move,)
    assert tracker.board and tracker.board.fen() == board.fen()
    return tracker, move


def test_infers_ordinary_move() -> None:
    observe_move(chess.STARTING_FEN, "e2e4")


def test_lichess_hydrated_state_replays_from_supplied_game_page() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    snapshot = replace(
        snapshot_for(board, seq=1, moves=("e4",)),
        url="https://lichess.org/eZY8AAa5eobo",
        game_key="lichess:eZY8AAa5",
        starting_fen=chess.STARTING_FEN,
        current_fen=board.fen(en_passant="fen"),
        orientation=Orientation.BLACK,
        orientation_confidence=0.995,
        player_color=chess.BLACK,
        side_to_move=chess.BLACK,
    )

    transition = GameReconciler().ingest(snapshot)

    assert transition.state is SyncState.SYNCHRONIZED
    assert transition.board is not None
    assert transition.board.fen(en_passant="fen") == board.fen(en_passant="fen")


@pytest.mark.parametrize("uci", ["e1g1", "e1c1"])
def test_infers_both_castles(uci: str) -> None:
    observe_move("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", uci)


def test_infers_en_passant() -> None:
    observe_move("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6")


@pytest.mark.parametrize("suffix", ["q", "r", "b", "n"])
def test_infers_every_promotion_piece(suffix: str) -> None:
    observe_move("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", f"a7a8{suffix}")


def test_replays_san_history_and_validates_placement() -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    tracker = GameReconciler()

    transition = tracker.ingest(snapshot_for(board, seq=1, moves=("e4", "e5", "Nf3", "Nc6")))

    assert transition.state is SyncState.SYNCHRONIZED
    assert tracker.board and len(tracker.board.move_stack) == 4


def test_replays_history_when_current_fen_confirms_the_position() -> None:
    board = chess.Board()
    for san in ("e4", "c5", "Nf3"):
        board.push_san(san)
    snapshot = snapshot_for(board, seq=1, moves=("e4", "c5", "Nf3"))
    snapshot = replace(snapshot, current_fen=board.fen())

    tracker = GameReconciler()
    transition = tracker.ingest(snapshot)

    assert transition.state is SyncState.SYNCHRONIZED
    assert transition.message == "Synchronized from move history"
    assert tracker.board and len(tracker.board.move_stack) == 3


def test_bootstraps_an_arbitrary_position_from_current_fen() -> None:
    fen = "r3k2r/ppp2ppp/2n5/3pP3/8/8/PPP2PPP/R3K2R w KQkq d6 0 14"
    board = chess.Board(fen)
    snapshot = snapshot_for(board, seq=1)
    snapshot = replace(snapshot, current_fen=fen)

    tracker = GameReconciler()
    transition = tracker.ingest(snapshot)

    assert transition.state is SyncState.SYNCHRONIZED
    assert transition.message == "Synchronized from current FEN"
    assert tracker.board and tracker.board.fen(en_passant="fen") == board.fen(en_passant="fen")


def test_replays_history_when_renderer_exposes_no_piece_nodes() -> None:
    board = chess.Board()
    board.push_san("e4")
    snapshot = snapshot_for(board, seq=1, moves=("e4",))
    history_only = snapshot.__class__(
        page_id=snapshot.page_id,
        seq=snapshot.seq,
        url=snapshot.url,
        game_key=snapshot.game_key,
        pieces={},
        moves=snapshot.moves,
        starting_fen=chess.STARTING_FEN,
        orientation=snapshot.orientation,
        orientation_confidence=snapshot.orientation_confidence,
        player_color=snapshot.player_color,
    )
    tracker = GameReconciler()
    transition = tracker.ingest(history_only)
    assert transition.state is SyncState.SYNCHRONIZED
    assert tracker.board and tracker.board.peek().uci() == "e2e4"


def test_duplicate_snapshot_does_not_advance_revision() -> None:
    board = chess.Board()
    tracker = GameReconciler()
    first = tracker.ingest(snapshot_for(board, seq=1))
    duplicate = tracker.ingest(snapshot_for(board, seq=1))
    assert duplicate.state is SyncState.TRANSIENT
    assert duplicate.revision == first.revision


def test_changed_game_key_resets_session() -> None:
    board = chess.Board()
    tracker = GameReconciler()
    tracker.ingest(snapshot_for(board, seq=1, game_key="old"))
    board.push_uci("e2e4")
    tracker.ingest(snapshot_for(board, seq=2, game_key="old"))

    new_board = chess.Board()
    transition = tracker.ingest(snapshot_for(new_board, seq=1, game_key="new"))

    assert transition.new_game
    assert tracker.game_key == "new"
    assert tracker.board and tracker.board.fen() == chess.STARTING_FEN


def test_recovers_one_dropped_snapshot() -> None:
    board = chess.Board()
    tracker = GameReconciler()
    tracker.ingest(snapshot_for(board, seq=1))
    board.push_uci("e2e4")
    board.push_uci("e7e5")

    transition = tracker.ingest(snapshot_for(board, seq=3))

    assert transition.state is SyncState.SYNCHRONIZED
    assert [move.uci() for move in transition.moves] == ["e2e4", "e7e5"]


def test_partial_capture_animation_does_not_replace_the_last_legal_position() -> None:
    board = chess.Board()
    tracker = GameReconciler()
    tracker.ingest(snapshot_for(board, seq=1))

    # Chess.com and Chessground can expose the captured/moving pieces at
    # different animation instants. Removing a queen alone is not a legal
    # transition and must not become the material state used for odds mode.
    partial = board.copy(stack=False)
    partial.remove_piece_at(chess.D1)
    snapshot = replace(snapshot_for(partial, seq=2), side_to_move=chess.BLACK)

    transition = tracker.ingest(snapshot)

    assert transition.state is SyncState.TRANSIENT
    assert tracker.board and tracker.board.fen() == board.fen()


def test_invalid_starting_fen_never_crashes_reconciliation() -> None:
    observed = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    tracker = GameReconciler()

    transition = tracker.ingest(snapshot_for(observed, seq=1, starting_fen="not a fen"))

    assert transition.state in {SyncState.TRANSIENT, SyncState.DESYNCHRONIZED}


def test_attaches_midgame_from_live_placement_when_history_is_not_replayable() -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6"):
        board.push_san(san)
    snapshot = snapshot_for(
        board,
        seq=1,
        starting_fen=chess.STARTING_FEN,
        moves=("e4", "e5", "not-a-move", "Nc6", "Bb5", "a6"),
    )
    snapshot = replace(snapshot, side_to_move=board.turn)

    tracker = GameReconciler()
    transition = tracker.ingest(snapshot)

    assert transition.state is SyncState.SYNCHRONIZED
    assert transition.message == "Recovered from observed board placement"
    assert transition.provisional
    assert transition.board and transition.board.board_fen() == board.board_fen()
    assert transition.board.turn == board.turn


def test_continues_detecting_moves_after_midgame_placement_recovery() -> None:
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6"):
        board.push_san(san)
    broken_moves = ("e4", "e5", "not-a-move", "Nc6", "Bb5", "a6")
    tracker = GameReconciler()
    initial = replace(
        snapshot_for(board, seq=1, starting_fen=chess.STARTING_FEN, moves=broken_moves),
        side_to_move=board.turn,
    )
    assert tracker.ingest(initial).state is SyncState.SYNCHRONIZED

    move = board.parse_san("Ba4")
    board.push(move)
    next_snapshot = replace(
        snapshot_for(board, seq=2, starting_fen=chess.STARTING_FEN, moves=broken_moves + ("Ba4",)),
        side_to_move=board.turn,
    )
    transition = tracker.ingest(next_snapshot)

    assert transition.state is SyncState.SYNCHRONIZED
    assert not transition.provisional
    assert transition.moves == (move,)
    assert transition.board and transition.board.board_fen() == board.board_fen()
