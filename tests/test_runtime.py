from __future__ import annotations

import asyncio
from typing import Any

import chess
import pytest

from chess_trainer.config import AppConfig
from chess_trainer.engine import StockfishError
from chess_trainer.models import AnalysisResult, AnalysisVariation, Orientation, SyncState
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
        odds_mode: str | None = None,
    ) -> None:
        result = self.analyse(board, revision)
        object.__setattr__(result, "odds_mode", odds_mode)
        publish(result)

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
        _odds_mode: str | None = None,
    ) -> None:
        return None


class FakeBridge:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, _client: Any, message: dict[str, Any]) -> bool:
        self.messages.append(message)
        return True


class RecordingLifecycleEngine:
    def __init__(self) -> None:
        self.name = "LCZero test double"
        self.cancel_calls = 0
        self.reserve_calls = 0
        self.new_game_calls = 0
        self.player_colors: list[chess.Color | None] = []

    def set_player_color(self, color: chess.Color | None) -> None:
        self.player_colors.append(color)

    def reserve_analysis(self) -> int:
        self.reserve_calls += 1
        return self.reserve_calls

    def cancel(self) -> None:
        self.cancel_calls += 1

    def new_game(self) -> None:
        self.new_game_calls += 1


def snapshot_message(
    board: chess.Board,
    *,
    seq: int,
    starting_fen: str,
    moves: tuple[str, ...],
    player_color: chess.Color,
    url: str,
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "position.snapshot",
        "pageId": "live-odds-page",
        "seq": seq,
        "gameKey": "live-odds-game",
        "positionHash": f"{seq:064x}",
        "page": {
            "url": url,
            "visible": True,
            "focused": True,
        },
        "board": {
            "pieces": {
                chess.square_name(square): piece.symbol()
                for square, piece in board.piece_map().items()
            },
            "startingFen": starting_fen,
            "orientation": "white" if player_color == chess.WHITE else "black",
            "orientationConfidence": 1.0,
            "playerColor": "white" if player_color == chess.WHITE else "black",
        },
        "moves": list(moves),
        "game": {
            "sideToMove": "white" if board.turn == chess.WHITE else "black",
        },
    }


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
        {"rank": 1, "uci": "e2e4", "scoreCp": 35, "mate": None, "depth": 16, "evaluation": "+0.35", "pv": ""},
        {"rank": 2, "uci": "d2d4", "scoreCp": 20, "mate": None, "depth": 16, "evaluation": "+0.20", "pv": ""},
        {"rank": 3, "uci": "g1f3", "scoreCp": 5, "mate": None, "depth": 15, "evaluation": "+0.05", "pv": ""},
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


@pytest.mark.parametrize(
    ("player_color", "starting_fen", "capture_uci", "recapture_uci"),
    [
        (
            chess.WHITE,
            "r3k3/8/8/8/8/8/3q4/R2Q3K b - - 0 1",
            "d2d1",
            "a1d1",
        ),
        (
            chess.BLACK,
            "r2q3k/3Q4/8/8/8/8/8/R3K3 w - - 0 1",
            "d7d8",
            "a8d8",
        ),
    ],
    ids=["white-player", "black-player"],
)
@pytest.mark.parametrize(
    "url",
    [
        "https://www.chess.com/play/computer",
        "https://lichess.org/eZY8AAa5",
    ],
    ids=["chess-com", "lichess"],
)
@pytest.mark.asyncio
async def test_live_odds_transition_ignores_partial_frame_then_switches_and_reverts(
    player_color: chess.Color,
    starting_fen: str,
    capture_uci: str,
    recapture_uci: str,
    url: str,
) -> None:
    runtime = RuntimeController(
        AppConfig(
            engine_kind="lc0",
            lc0_auto_network=True,
            analyze_opponent=True,
        )
    )
    engine = RecordingLifecycleEngine()
    bridge = FakeBridge()
    runtime._engine = engine  # type: ignore[assignment]
    runtime._bridge = bridge  # type: ignore[assignment]
    scheduled_modes: list[str] = []

    async def hold_analysis(
        _page_id: str,
        _revision: int,
        _board: chess.Board,
        _guard: AnalysisGuard | None,
        _generation: int | None,
        odds_mode: str | None,
    ) -> None:
        scheduled_modes.append(odds_mode or "none")
        await asyncio.Event().wait()

    runtime._analyse = hold_analysis  # type: ignore[method-assign]
    websocket = object()
    board = chess.Board(starting_fen)

    await runtime._handle_snapshot(
        snapshot_message(
            board,
            seq=1,
            starting_fen=starting_fen,
            moves=(),
            player_color=player_color,
            url=url,
        ),
        websocket,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)

    capture = chess.Move.from_uci(capture_uci)
    capture_san = board.san(capture)
    partial = board.copy(stack=True)
    partial.remove_piece_at(capture.to_square)
    partial.turn = player_color
    await runtime._handle_snapshot(
        snapshot_message(
            partial,
            seq=2,
            starting_fen=starting_fen,
            moves=(capture_san,),
            player_color=player_color,
            url=url,
        ),
        websocket,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)

    board.push(capture)
    await runtime._handle_snapshot(
        snapshot_message(
            board,
            seq=3,
            starting_fen=starting_fen,
            moves=(capture_san,),
            player_color=player_color,
            url=url,
        ),
        websocket,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)

    recapture = chess.Move.from_uci(recapture_uci)
    recapture_san = board.san(recapture)
    board.push(recapture)
    await runtime._handle_snapshot(
        snapshot_message(
            board,
            seq=4,
            starting_fen=starting_fen,
            moves=(capture_san, recapture_san),
            player_color=player_color,
            url=url,
        ),
        websocket,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)

    dashboard_states = [
        message for message in bridge.messages if message["type"] == "dashboard.state"
    ]
    assert [state["oddsMode"] for state in dashboard_states] == [
        "none",
        "none",
        "queen",
        "none",
    ]
    assert dashboard_states[1]["sync"] == "transient"
    assert scheduled_modes == ["none", "queen", "none"]
    assert engine.cancel_calls >= 1
    assert engine.reserve_calls == 3
    assert runtime._effective_odds_modes["live-odds-page"] == "none"

    for task in runtime._analysis_tasks.values():
        task.cancel()
    await asyncio.gather(*runtime._analysis_tasks.values(), return_exceptions=True)


@pytest.mark.parametrize(
    ("promotion", "expected"),
    [
        ("q", "queen"),
        ("r", "rook"),
        ("b", "knight"),
        ("n", "knight"),
    ],
)
def test_confirmed_live_promotions_select_from_the_promoted_material(
    promotion: str,
    expected: str,
) -> None:
    starting_fen = "4k3/8/8/8/8/8/p7/4K3 b - - 0 1"
    board = chess.Board(starting_fen)
    tracker = GameReconciler()
    tracker.ingest(snapshot_for(board, seq=1, starting_fen=starting_fen))
    board.push_uci(f"a2a1{promotion}")
    transition = tracker.ingest(snapshot_for(board, seq=2, starting_fen=starting_fen))
    runtime = RuntimeController(AppConfig(engine_kind="lc0", lc0_auto_network=True))

    mode = runtime._effective_odds_mode_for(
        "promotion-page",
        transition.board,
        chess.WHITE,
        confirmed=transition.state is SyncState.SYNCHRONIZED,
    )

    assert mode == expected


@pytest.mark.asyncio
async def test_stale_analysis_from_previous_effective_mode_is_not_published() -> None:
    board = chess.Board()
    runtime, page_id, revision, bridge = prepared_runtime(board, player_color=chess.WHITE)
    runtime.config.engine_kind = "lc0"
    runtime.config.lc0_auto_network = True
    runtime._effective_odds_modes[page_id] = "queen"
    result = FakeEngine(chess.Move.from_uci("e2e4")).analyse(board, revision)
    guard = AnalysisGuard(
        game_key=page_id,
        position_hash="a" * 64,
        fen=board.fen(),
        odds_mode="none",
    )

    await runtime._publish_live_analysis(page_id, revision, board, guard, result)

    assert bridge.messages == []


def test_effective_odds_cache_is_isolated_per_page_and_ignores_provisional_values() -> None:
    runtime = RuntimeController(AppConfig(engine_kind="lc0", lc0_auto_network=True))
    white_down_queen = chess.Board()
    white_down_queen.remove_piece_at(chess.D1)

    assert runtime._effective_odds_mode_for(
        "queen-page",
        white_down_queen,
        chess.WHITE,
        confirmed=True,
    ) == "queen"
    assert runtime._effective_odds_mode_for(
        "normal-page",
        chess.Board(),
        chess.WHITE,
        confirmed=True,
    ) == "none"

    # Provisional values cannot leak between pages or overwrite either cache.
    assert runtime._effective_odds_mode_for(
        "queen-page",
        chess.Board(),
        chess.WHITE,
        confirmed=False,
    ) == "queen"
    assert runtime._effective_odds_mode_for(
        "normal-page",
        white_down_queen,
        chess.WHITE,
        confirmed=False,
    ) == "none"
