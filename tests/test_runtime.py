from __future__ import annotations

from typing import Any

import chess
import pytest

from chess_trainer.config import AppConfig
from chess_trainer.engine import StockfishError
from chess_trainer.models import AnalysisResult, AnalysisVariation, Orientation
from chess_trainer.policy import classify_page
from chess_trainer.reconcile import GameReconciler
from chess_trainer.runtime import AnalysisGuard, RuntimeController, _is_opponent_turn, _player_color

from conftest import snapshot_for


class FakeEngine:
    def __init__(self, move: chess.Move) -> None:
        self.move = move
        self.variations: tuple[AnalysisVariation, ...] = ()

    def analyse(self, board: chess.Board, revision: int) -> AnalysisResult:
        return AnalysisResult(
            revision=revision,
            fen=board.fen(),
            best_move=self.move,
            score_cp=25,
            mate=None,
            depth=10,
            nodes=100,
            nps=1000,
            pv_uci=(self.move.uci(),),
            pv_san=(board.san(self.move),),
            time_ms=250,
            variations=self.variations,
        )

    def analyse_continuously(
        self,
        board: chess.Board,
        revision: int,
        publish: Any,
        _cancel_generation: int | None = None,
    ) -> None:
        publish(self.analyse(board, revision))

    def reserve_analysis(self) -> int:
        return 1

    def stop(self) -> None:
        return None

    def start(self) -> str:
        return "Stockfish 18"

    def preview(
        self,
        _board: chess.Board,
        _revision: int,
        _cancel_generation: int,
    ) -> None:
        return None


class FakeBridge:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, _client: Any, message: dict[str, Any]) -> bool:
        self.messages.append(message)
        return True


def prepared_runtime(board: chess.Board, *, player_color: chess.Color) -> tuple[RuntimeController, str, int, FakeBridge]:
    config = AppConfig(auto_play=True, monitoring=True)
    runtime = RuntimeController(config)
    page_id = "training-session"
    snapshot = snapshot_for(board, seq=1, game_key=page_id)
    object.__setattr__(snapshot, "page_id", page_id)
    object.__setattr__(snapshot, "position_hash", "a" * 64)
    object.__setattr__(snapshot, "player_color", player_color)
    tracker = GameReconciler()
    transition = tracker.ingest(snapshot)
    bridge = FakeBridge()
    runtime._engine = FakeEngine(next(iter(board.legal_moves)))  # type: ignore[assignment]
    runtime._trackers[page_id] = tracker
    runtime._snapshots[page_id] = snapshot
    runtime._decisions[page_id] = classify_page(snapshot.url)
    runtime._clients[page_id] = object()  # type: ignore[assignment]
    runtime._bridge = bridge  # type: ignore[assignment]
    return runtime, page_id, transition.revision, bridge


def test_player_color_is_not_guessed_from_flippable_orientation() -> None:
    snapshot = snapshot_for(chess.Board(), seq=1)
    object.__setattr__(snapshot, "player_color", None)
    object.__setattr__(snapshot, "orientation", Orientation.BLACK)
    object.__setattr__(snapshot, "orientation_confidence", 1.0)

    assert _player_color(snapshot) is None


def test_opponent_turn_detection_requires_known_player_color() -> None:
    board = chess.Board()
    assert not _is_opponent_turn(board, chess.WHITE)
    assert _is_opponent_turn(board, chess.BLACK)
    assert not _is_opponent_turn(board, None)


@pytest.mark.asyncio
async def test_manual_analysis_uses_arrow_instead_of_move_execution() -> None:
    board = chess.Board()
    board.push_uci("e2e4")  # Black is to move, but the local player is White.
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime._engine = FakeEngine(chess.Move.from_uci("e7e5"))  # type: ignore[assignment]

    await runtime._analyse(page_id, revision, board.copy(stack=True))

    assert [message["type"] for message in bridge.messages] == ["arrow.command"]
    assert bridge.messages[0]["uci"] == "e7e5"
    assert bridge.messages[0]["nodes"] == 100
    assert bridge.messages[0]["nps"] == 1000
    assert bridge.messages[0]["timeMs"] == 250
    assert bridge.messages[0]["opponentTurn"] is True
    assert bridge.messages[0]["showOverlays"] is False


@pytest.mark.asyncio
async def test_opponent_arrow_toggle_allows_overlay() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime.config.show_opponent_arrows = True
    runtime._engine = FakeEngine(chess.Move.from_uci("e7e5"))  # type: ignore[assignment]

    await runtime._analyse(page_id, revision, board.copy(stack=True))

    assert bridge.messages[0]["showOverlays"] is True


@pytest.mark.asyncio
async def test_multipv_variations_are_published_for_multiple_arrows() -> None:
    board = chess.Board()
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    engine = FakeEngine(chess.Move.from_uci("e2e4"))
    engine.variations = (
        AnalysisVariation(1, chess.Move.from_uci("e2e4"), 35, None, 16),
        AnalysisVariation(2, chess.Move.from_uci("d2d4"), 20, None, 16),
        AnalysisVariation(3, chess.Move.from_uci("g1f3"), 5, None, 15),
    )
    runtime.config.multi_pv = 3
    runtime._engine = engine  # type: ignore[assignment]

    await runtime._analyse(page_id, revision, board.copy(stack=True))

    assert bridge.messages[0]["multiPv"] == 3
    assert bridge.messages[0]["variations"] == [
        {"rank": 1, "uci": "e2e4", "scoreCp": 35, "mate": None, "depth": 16},
        {"rank": 2, "uci": "d2d4", "scoreCp": 20, "mate": None, "depth": 16},
        {"rank": 3, "uci": "g1f3", "scoreCp": 5, "mate": None, "depth": 15},
    ]


@pytest.mark.asyncio
async def test_analysis_workspace_analyzes_either_side_and_shows_arrow() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime._decisions[page_id] = classify_page("https://www.chess.com/analysis")
    runtime._engine = FakeEngine(chess.Move.from_uci("e7e5"))  # type: ignore[assignment]

    await runtime._analyse(page_id, revision, board.copy(stack=True))

    assert bridge.messages[0]["type"] == "arrow.command"
    assert bridge.messages[0]["showOverlays"] is True
    assert bridge.messages[0]["opponentTurn"] is False


@pytest.mark.asyncio
async def test_lc0_failure_falls_back_to_stockfish(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeController(AppConfig(engine_kind="lc0"))
    runtime._engine = FakeEngine(chess.Move.from_uci("e2e4"))  # type: ignore[assignment]
    saved = False

    def save(_config: AppConfig) -> None:
        nonlocal saved
        saved = True

    monkeypatch.setattr(AppConfig, "save", save)
    recovered = await runtime._fallback_to_stockfish(StockfishError("CUDA device disappeared"))

    assert recovered
    assert runtime.config.engine_kind == "stockfish"
    assert saved


@pytest.mark.asyncio
async def test_selecting_stockfish_stops_lc0_before_changing_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeController(AppConfig(engine_kind="lc0"))
    observed: list[tuple[str, str]] = []

    class SwitchingEngine:
        def cancel(self) -> None:
            observed.append(("cancel", runtime.config.engine_kind))

        def stop(self) -> None:
            observed.append(("stop", runtime.config.engine_kind))

        def start(self) -> str:
            observed.append(("start", runtime.config.engine_kind))
            return "Stockfish 18"

        def prewarm_lc0_networks(self) -> None:
            observed.append(("prewarm", runtime.config.engine_kind))

    monkeypatch.setattr(AppConfig, "save", lambda _config: None)
    monkeypatch.setattr(runtime, "analyze_now", lambda: None)
    runtime._engine = SwitchingEngine()  # type: ignore[assignment]

    await runtime._apply_engine_settings(engine_kind="stockfish")

    assert observed == [
        ("cancel", "lc0"),
        ("stop", "lc0"),
        ("start", "stockfish"),
        ("prewarm", "stockfish"),
    ]


@pytest.mark.asyncio
async def test_focus_snapshot_sequence_change_does_not_stall_same_position() -> None:
    board = chess.Board()
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime._engine = FakeEngine(chess.Move.from_uci("e2e4"))  # type: ignore[assignment]
    newer_snapshot = snapshot_for(board, seq=2, game_key=page_id)
    object.__setattr__(newer_snapshot, "page_id", page_id)
    object.__setattr__(newer_snapshot, "position_hash", "a" * 64)
    object.__setattr__(newer_snapshot, "player_color", chess.WHITE)
    runtime._snapshots[page_id] = newer_snapshot
    guard = AnalysisGuard(game_key=page_id, position_hash="a" * 64, fen=board.fen())

    await runtime._analyse(page_id, revision, board.copy(stack=True), guard)

    assert bridge.messages[0]["type"] == "arrow.command"
    assert bridge.messages[0]["seq"] == 2


@pytest.mark.asyncio
async def test_engine_result_is_not_executed_against_latest_desynchronized_snapshot() -> None:
    board = chess.Board()
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime._engine = FakeEngine(chess.Move.from_uci("e2e4"))  # type: ignore[assignment]

    changed = board.copy(stack=True)
    for uci in ("e2e4", "e7e5", "g1f3"):
        changed.push_uci(uci)
    stale_snapshot = snapshot_for(changed, seq=2, game_key=page_id)
    object.__setattr__(stale_snapshot, "page_id", page_id)
    object.__setattr__(stale_snapshot, "position_hash", "b" * 64)
    runtime._snapshots[page_id] = stale_snapshot

    await runtime._analyse(page_id, revision, board.copy(stack=True))

    assert bridge.messages == []
