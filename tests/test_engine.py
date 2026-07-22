from __future__ import annotations

from pathlib import Path

import chess
import pytest

from chess_trainer.config import AppConfig, default_engine_path, default_lc0_paths
from chess_trainer.engine import (
    StockfishService,
    _engine_process_kwargs,
    auto_contempt_for_mode,
    auto_practical_contempt,
    detect_odds_mode,
    player_pov_evaluation,
)


class FakeUciEngine:
    def __init__(self) -> None:
        self.options = {name: object() for name in (
            "WeightsFile", "Backend", "Threads", "MinibatchSize", "UCI_ShowWDL", "Contempt", "ContemptMode"
        )}
        self.configured: dict[str, object] = {}

    def configure(self, options: dict[str, object]) -> None:
        self.configured.update(options)


def test_engine_process_is_hidden_on_windows() -> None:
    options = _engine_process_kwargs(Path("C:/engines/stockfish.exe"))
    assert options["cwd"] == "C:\\engines"
    if "creationflags" in options:
        import subprocess

        assert int(options["creationflags"]) & subprocess.CREATE_NO_WINDOW


def test_lc0_configuration_uses_fp16_and_contempt() -> None:
    config = AppConfig(engine_kind="lc0", lc0_contempt=375)
    config.validate()
    service = StockfishService(config)
    fake = FakeUciEngine()

    service._configure(fake)  # type: ignore[arg-type]

    assert fake.configured["Backend"] == "cuda-fp16"
    assert fake.configured["Contempt"] == "375"
    assert fake.configured["ContemptMode"] == "play"


def test_lc0_contempt_is_clamped() -> None:
    config = AppConfig(lc0_contempt=5000)
    config.validate()
    assert config.lc0_contempt == 1000


def test_new_analysis_generation_invalidates_queued_work() -> None:
    service = StockfishService(AppConfig())
    published: list[object] = []

    first = service.reserve_analysis()
    second = service.reserve_analysis()

    assert not service._generation_is_current(first)
    assert service._generation_is_current(second)
    service.analyse_continuously(chess.Board(), 1, published.append, first)
    assert published == []


def test_auto_network_detects_current_material_imbalance() -> None:
    board = chess.Board()
    assert detect_odds_mode(board, chess.WHITE) == "none"

    board.remove_piece_at(chess.D1)
    assert detect_odds_mode(board, chess.WHITE) == "queen"
    assert detect_odds_mode(board, chess.BLACK) == "none"
    board.remove_piece_at(chess.B8)
    assert detect_odds_mode(board, chess.WHITE) == "queen_for_knight"

    board = chess.Board()
    board.remove_piece_at(chess.A1)
    assert detect_odds_mode(board, chess.WHITE) == "rook"
    assert detect_odds_mode(board, chess.BLACK) == "none"

    board = chess.Board()
    board.remove_piece_at(chess.G1)
    assert detect_odds_mode(board, chess.WHITE) == "knight"
    assert detect_odds_mode(board, chess.BLACK) == "none"

    board = chess.Board()
    board.remove_piece_at(chess.D1)
    board.remove_piece_at(chess.D8)
    assert detect_odds_mode(board, chess.WHITE) == "none"
    assert detect_odds_mode(board, chess.BLACK) == "none"


def test_auto_network_requires_a_known_player_side() -> None:
    board = chess.Board()
    board.remove_piece_at(chess.D1)
    assert detect_odds_mode(board, None) == "none"


def test_lc0_network_switch_reuses_warm_process() -> None:
    config = AppConfig(
        engine_kind="lc0",
        lc0_auto_network=True,
        lc0_bt4_weights="normal.pb.gz",
        lc0_t1_odds_weights="odds.pb.gz",
        lc0_queen_odds_weights="queen.pb.gz",
    )
    service = StockfishService(config)
    normal_engine = object()
    queen_engine = object()
    service._engine = normal_engine  # type: ignore[assignment]
    service.set_player_color(chess.WHITE)
    service._loaded_weights = Path(config.lc0_bt4_weights)
    service._engine_pool[Path(config.lc0_queen_odds_weights)] = queen_engine  # type: ignore[assignment]
    queen_odds = chess.Board()
    queen_odds.remove_piece_at(chess.D1)

    selected, _ = service._engine_for_board(queen_odds)

    assert selected is queen_engine
    assert service._engine_pool[Path(config.lc0_bt4_weights)] is normal_engine


@pytest.mark.parametrize(
    ("score_cp", "mate", "expected"),
    [
        (100, None, 0),
        (-74, None, 0),
        (-100, None, 25),
        (-200, None, 50),
        (-400, None, 100),
        (-700, None, 150),
        (-1000, None, 200),
        (-1500, None, 250),
        (None, -3, 250),
        (None, 3, 0),
    ],
)
def test_auto_practical_contempt_is_capped_at_250(
    score_cp: int | None,
    mate: int | None,
    expected: int,
) -> None:
    assert auto_practical_contempt(score_cp, mate) == expected


def test_auto_contempt_uses_the_players_evaluation_not_side_to_move() -> None:
    # White to move is +4: good for a white player, bad for a black player.
    white_score = player_pov_evaluation(400, None, chess.WHITE, chess.WHITE)
    black_score = player_pov_evaluation(400, None, chess.WHITE, chess.BLACK)

    assert auto_practical_contempt(*white_score) == 0
    assert auto_practical_contempt(*black_score) == 100

    # An unknown player side must never infer practical contempt for either side.
    assert player_pov_evaluation(-1000, None, chess.BLACK, None) == (None, None)
    assert auto_practical_contempt(*player_pov_evaluation(-1000, None, chess.BLACK, None)) == 0


def test_auto_contempt_is_neutral_when_an_odds_network_is_active() -> None:
    assert auto_contempt_for_mode("queen", -5000, None) == 0
    assert auto_contempt_for_mode("queen_for_knight", None, -3) == 0
    assert auto_contempt_for_mode("none", -5000, None) == 250


@pytest.mark.skipif(not default_lc0_paths()["engine"].exists(), reason="LCZero is not installed")
def test_lc0_auto_modes_publish_effective_choices() -> None:
    paths = default_lc0_paths()
    config = AppConfig(
        engine_kind="lc0",
        lc0_auto_network=True,
        lc0_auto_contempt=True,
        lc0_path=str(paths["engine"]),
        lc0_bt4_weights=str(paths["strongest"]),
        lc0_t1_odds_weights=str(paths["odds"]),
        lc0_queen_odds_weights=str(paths["queen_odds"]),
    )
    engine = StockfishService(config)
    results = []

    def publish(result: object) -> None:
        results.append(result)
        engine.cancel()

    try:
        engine.analyse_continuously(chess.Board(), revision=9, publish=publish)
        assert results
        assert results[0].odds_mode == "none"
        assert results[0].effective_contempt == 0
    finally:
        engine.stop()


@pytest.mark.skipif(
    not default_lc0_paths()["engine"].exists() or not default_engine_path().exists(),
    reason="LCZero or Stockfish is not installed",
)
def test_lc0_has_immediate_stockfish_preview() -> None:
    paths = default_lc0_paths()
    config = AppConfig(
        engine_kind="lc0",
        lc0_path=str(paths["engine"]),
        lc0_bt4_weights=str(paths["strongest"]),
        engine_path=str(default_engine_path()),
    )
    engine = StockfishService(config)
    board = chess.Board()
    try:
        engine.start(board)
        generation = engine.reserve_analysis()
        result = engine.preview(board, revision=3, cancel_generation=generation)

        assert result is not None
        assert result.best_move in board.legal_moves
        assert result.engine_name == "Stockfish preview"
    finally:
        engine.stop()


@pytest.mark.skipif(not default_engine_path().exists(), reason="Stockfish 18 is not installed")
def test_stockfish_18_smoke() -> None:
    config = AppConfig(
        engine_path=str(default_engine_path()),
        threads=2,
        hash_mb=64,
        think_time_ms=100,
    )
    engine = StockfishService(config)
    try:
        assert "Stockfish 18" in engine.start()
        result = engine.analyse(chess.Board(), revision=1)
        assert result.best_move in chess.Board().legal_moves
        assert result.revision == 1
    finally:
        engine.stop()


@pytest.mark.skipif(not default_engine_path().exists(), reason="Stockfish 18 is not installed")
def test_continuous_analysis_publishes_before_cancellation() -> None:
    config = AppConfig(engine_path=str(default_engine_path()), threads=2, hash_mb=64)
    engine = StockfishService(config)
    results = []

    def publish(result: object) -> None:
        results.append(result)
        engine.cancel()

    try:
        engine.start()
        engine.analyse_continuously(chess.Board(), revision=7, publish=publish)
        assert results
        assert results[0].revision == 7
        assert results[0].best_move in chess.Board().legal_moves
    finally:
        engine.stop()
