from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import chess
import pytest

from chess_trainer.config import AppConfig, default_engine_path, default_lc0_paths
from chess_trainer.engine import (
    StockfishService,
    _analysis_variations,
    _engine_process_kwargs,
    auto_contempt_for_mode,
    auto_practical_contempt,
    detect_odds_mode,
    player_pov_evaluation,
)
from chess_trainer.models import AnalysisResult


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


@pytest.mark.parametrize(("requested", "expected"), [(0, 1), (2, 2), (8, 3)])
def test_multipv_is_clamped_to_supported_arrow_count(requested: int, expected: int) -> None:
    config = AppConfig(multi_pv=requested)
    config.validate()
    assert config.multi_pv == expected


def test_analysis_variations_preserve_every_rank_in_order() -> None:
    board = chess.Board()

    def result(uci: str, score: int) -> AnalysisResult:
        move = chess.Move.from_uci(uci)
        return AnalysisResult(
            revision=1,
            fen=board.fen(),
            best_move=move,
            score_cp=score,
            mate=None,
            depth=12,
            nodes=100,
            nps=1000,
            pv_uci=(uci,),
            pv_san=(board.san(move),),
        )

    variations = _analysis_variations({
        3: result("g1f3", 10),
        1: result("e2e4", 30),
        2: result("e2e4", 20),
    })

    assert [(item.rank, item.best_move.uci()) for item in variations] == [
        (1, "e2e4"),
        (2, "e2e4"),
        (3, "g1f3"),
    ]


def test_new_analysis_generation_invalidates_queued_work() -> None:
    service = StockfishService(AppConfig())
    published: list[object] = []

    first = service.reserve_analysis()
    second = service.reserve_analysis()

    assert not service._generation_is_current(first)
    assert service._generation_is_current(second)
    service.analyse_continuously(chess.Board(), 1, published.append, first)
    assert published == []


def test_new_analysis_generation_stops_the_active_search() -> None:
    service = StockfishService(AppConfig())

    class ActiveAnalysis:
        stopped = False

        def stop(self) -> None:
            self.stopped = True

    active = ActiveAnalysis()
    service._active_analysis = active  # type: ignore[assignment]

    service.reserve_analysis()

    assert active.stopped


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
    t1_engine = object()
    queen_engine = object()
    service._engine = normal_engine  # type: ignore[assignment]
    service.set_player_color(chess.WHITE)
    service._loaded_weights = Path(config.lc0_bt4_weights)
    service._engine_pool[Path(config.lc0_t1_odds_weights)] = t1_engine  # type: ignore[assignment]
    service._engine_pool[Path(config.lc0_queen_odds_weights)] = queen_engine  # type: ignore[assignment]

    # The runtime's confirmed per-page mode is authoritative even if the board
    # passed here would independently classify differently.
    selected_t1, _ = service._engine_for_board(chess.Board(), "rook")
    selected_queen, _ = service._engine_for_board(chess.Board(), "queen")
    selected_normal, _ = service._engine_for_board(chess.Board(), "none")

    assert selected_t1 is t1_engine
    assert selected_queen is queen_engine
    assert selected_normal is normal_engine
    assert service._engine_pool[Path(config.lc0_t1_odds_weights)] is t1_engine
    assert service._engine_pool[Path(config.lc0_queen_odds_weights)] is queen_engine


def test_explicit_network_selection_switches_warm_process_without_search(tmp_path: Path) -> None:
    normal_weights = tmp_path / "normal.pb.gz"
    queen_weights = tmp_path / "queen.pb.gz"
    normal_weights.touch()
    queen_weights.touch()
    service = StockfishService(
        AppConfig(
            engine_kind="lc0",
            lc0_auto_network=True,
            lc0_bt4_weights=str(normal_weights),
            lc0_queen_odds_weights=str(queen_weights),
        )
    )
    normal_engine = FakeUciEngine()
    queen_engine = FakeUciEngine()
    defaults = {
        "SwapColors": False,
        "ScLimit": 0,
        "CPuct": 1.0,
        "FpuValue": 0.0,
        "DrawScore": 0.0,
    }
    for engine in (normal_engine, queen_engine):
        engine.options.update({
            name: SimpleNamespace(default=default)
            for name, default in defaults.items()
        })
    service._engine = normal_engine  # type: ignore[assignment]
    service._loaded_weights = normal_weights
    service._engine_pool[queen_weights] = queen_engine  # type: ignore[assignment]
    service.set_player_color(chess.WHITE)

    selected_queen = service.select_network(chess.Board(), "queen")
    selected_normal = service.select_network(chess.Board(), "none")

    assert selected_queen == "queen"
    assert selected_normal == "none"
    assert service._engine is normal_engine
    assert service._engine_pool[queen_weights] is queen_engine
    assert queen_engine.configured["SwapColors"] is False
    assert normal_engine.configured["SwapColors"] is False


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("none", "normal.pb.gz"),
        ("knight", "odds.pb.gz"),
        ("rook", "odds.pb.gz"),
        ("queen_for_knight", "odds.pb.gz"),
        ("queen", "queen.pb.gz"),
    ],
)
def test_ui_compatible_modes_map_to_only_the_honestly_available_networks(
    mode: str,
    expected: str,
) -> None:
    service = StockfishService(
        AppConfig(
            engine_kind="lc0",
            lc0_bt4_weights="normal.pb.gz",
            lc0_t1_odds_weights="odds.pb.gz",
            lc0_queen_odds_weights="queen.pb.gz",
        )
    )

    assert service._weights_path(chess.Board(), mode) == Path(expected)


@pytest.mark.parametrize(
    ("player_color", "swap_colors"),
    [
        (chess.WHITE, False),
        (chess.BLACK, True),
    ],
)
def test_lqo_color_configuration_follows_the_player_not_queen_presence(
    tmp_path: Path,
    player_color: chess.Color,
    swap_colors: bool,
) -> None:
    weights = tmp_path / "queen.pb.gz"
    weights.touch()
    service = StockfishService(
        AppConfig(
            engine_kind="lc0",
            lc0_auto_network=True,
            lc0_queen_odds_weights=str(weights),
        )
    )
    service.set_player_color(player_color)
    fake = FakeUciEngine()
    fake.options.update({
        name: object()
        for name in ("SwapColors", "ScLimit", "CPuct", "FpuValue", "DrawScore")
    })
    promoted_position = chess.Board()
    promoted_position.set_piece_at(
        chess.A4,
        chess.Piece(chess.QUEEN, not player_color),
    )

    mode, _ = service._prepare_for_board(
        fake,  # type: ignore[arg-type]
        promoted_position,
        odds_mode="queen",
    )

    assert mode == "queen"
    assert fake.configured["SwapColors"] is swap_colors


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


@pytest.mark.skipif(not default_lc0_paths()["engine"].exists(), reason="LCZero is not installed")
@pytest.mark.parametrize(
    ("mode", "missing_square", "weights_key"),
    [
        ("rook", chess.A1, "odds"),
        ("queen", chess.D1, "queen_odds"),
    ],
)
def test_lc0_odds_network_real_engine_smoke(
    mode: str,
    missing_square: chess.Square,
    weights_key: str,
) -> None:
    paths = default_lc0_paths()
    board = chess.Board()
    board.remove_piece_at(missing_square)
    config = AppConfig(
        engine_kind="lc0",
        odds_mode=mode,
        think_time_ms=100,
        threads=2,
        lc0_path=str(paths["engine"]),
        lc0_bt4_weights=str(paths["strongest"]),
        lc0_t1_odds_weights=str(paths["odds"]),
        lc0_queen_odds_weights=str(paths["queen_odds"]),
    )
    engine = StockfishService(config)
    engine.set_player_color(chess.WHITE)
    try:
        engine.start(board, mode)
        result = engine.analyse(board, revision=12, odds_mode=mode)

        assert result.best_move in board.legal_moves
        assert result.odds_mode == mode
        assert engine._loaded_weights == Path(paths[weights_key])
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


@pytest.mark.skipif(not default_engine_path().exists(), reason="Stockfish 18 is not installed")
def test_continuous_multipv_publishes_three_full_engine_lines() -> None:
    config = AppConfig(
        engine_path=str(default_engine_path()),
        threads=2,
        hash_mb=64,
        multi_pv=3,
    )
    engine = StockfishService(config)
    complete = []
    published_counts: list[int] = []

    def publish(result: AnalysisResult) -> None:
        published_counts.append(len(result.variations))
        if len(result.variations) == 3 and all(line.pv_uci and line.pv_san for line in result.variations):
            complete.append(result)
            engine.cancel()

    try:
        engine.start()
        engine.analyse_continuously(chess.Board(), revision=11, publish=publish)
        assert complete
        assert set(published_counts) == {3}
        assert [line.rank for line in complete[-1].variations] == [1, 2, 3]
        assert len({line.best_move for line in complete[-1].variations}) == 3
    finally:
        engine.stop()
