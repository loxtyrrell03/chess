from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import chess
import chess.engine

from .config import AppConfig
from .models import AnalysisResult


LOGGER = logging.getLogger(__name__)

LIVE_INFO = chess.engine.INFO_BASIC | chess.engine.INFO_SCORE | chess.engine.INFO_PV
AUTO_CONTEMPT_PROBE_NODES = 192


class StockfishError(RuntimeError):
    pass


class StockfishService:
    """Owns one UCI process. Calls are serialized and safe from UI threads."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._engine: chess.engine.SimpleEngine | None = None
        self._lock = threading.RLock()
        self._search_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_analysis: chess.engine.SimpleAnalysisResult | None = None
        self._preview_lock = threading.Lock()
        self._preview_engine: chess.engine.SimpleEngine | None = None
        self._cancel_generation = 0
        self._game_token: object = object()
        self._dynamic_options: dict[str, object] = {}
        self._loaded_weights: Path | None = None
        self._engine_pool: dict[Path, chess.engine.SimpleEngine] = {}
        self._player_color: chess.Color | None = None
        self.name = "Stockfish 18"

    @property
    def running(self) -> bool:
        return self._engine is not None

    def set_player_color(self, color: chess.Color | None) -> None:
        """Set the human player's side for player-relative auto-network selection."""

        with self._lock:
            self._player_color = color

    def start(self, board: chess.Board | None = None) -> str:
        with self._lock:
            if self._engine is not None:
                return self.name
            kind = self.config.engine_kind
            path = Path(
                self.config.lc0_path
                if kind == "lc0"
                else self.config.engine_path
            )
            if not path.is_file():
                raise StockfishError(f"{_engine_label(kind)} binary not found: {path}")
            weights = self._weights_path(board)
            if weights is not None and not weights.is_file():
                raise StockfishError(f"LCZero network not found: {weights}")
            try:
                engine, name = self._launch_engine(path, kind, board, weights)
                self.name = name
                self._engine = engine
                self._dynamic_options = {}
                self._loaded_weights = weights
                if kind == "lc0":
                    self._start_preview_engine()
                return self.name
            except (OSError, TimeoutError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                self._engine = None
                raise StockfishError(f"Unable to start {_engine_label(kind)}: {exc}") from exc

    def _launch_engine(
        self,
        path: Path,
        kind: str,
        board: chess.Board | None,
        weights: Path | None,
    ) -> tuple[chess.engine.SimpleEngine, str]:
        engine: chess.engine.SimpleEngine | None = None
        try:
            engine = chess.engine.SimpleEngine.popen_uci(
                str(path),
                timeout=30.0,
                **_engine_process_kwargs(path),
            )
            name = str(engine.id.get("name", "Stockfish"))
            if kind == "stockfish" and "Stockfish 18" not in name:
                raise StockfishError(f"Expected Stockfish 18, found {name}")
            if kind == "lc0" and "lc0" not in name.lower():
                raise StockfishError(f"Expected LCZero, found {name}")
            self._configure(engine, board, weights_path=weights)
            return engine, name
        except Exception:
            if engine is not None:
                try:
                    engine.quit()
                except Exception:
                    engine.close()
            raise

    def prewarm_lc0_networks(self) -> None:
        """Load every auto-network into a dedicated process before play.

        LC0 cannot safely hot-swap large CUDA networks in one process. Keeping
        one process per distinct weight file makes a later network selection a
        pointer swap rather than a process launch and CUDA weight load.
        """

        if self.config.engine_kind != "lc0" or not self.config.lc0_auto_network:
            return
        samples: list[tuple[Path, chess.Board]] = []
        for weights, board in self._lc0_network_samples():
            if weights not in {item[0] for item in samples}:
                samples.append((weights, board))
        path = Path(self.config.lc0_path)
        with self._search_lock:
            for weights, board in samples:
                if not weights.is_file():
                    LOGGER.warning("Cannot prewarm missing LCZero network: %s", weights)
                    continue
                candidate: chess.engine.SimpleEngine | None = None
                is_current = False
                with self._lock:
                    if weights == self._loaded_weights and self._engine is not None:
                        candidate = self._engine
                        is_current = True
                    elif weights in self._engine_pool:
                        candidate = self._engine_pool[weights]
                try:
                    if candidate is None:
                        candidate, _ = self._launch_engine(path, "lc0", board, weights)
                    # Starting a process is not enough: the CUDA backend loads
                    # the network on the first search. One node pays that cost
                    # now, before a live position needs this process.
                    candidate.analyse(board, chess.engine.Limit(nodes=1), info=chess.engine.INFO_BASIC)
                    if not is_current:
                        with self._lock:
                            existing = self._engine_pool.get(weights)
                            if existing is None:
                                self._engine_pool[weights] = candidate
                                candidate = None
                            elif existing is candidate:
                                candidate = None
                    LOGGER.info("LCZero network prewarmed: %s", weights.name)
                except (StockfishError, OSError, TimeoutError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                    LOGGER.warning("Could not prewarm LCZero network %s: %s", weights.name, exc)
                finally:
                    if candidate is not None and not is_current:
                        _close_engine(candidate)

    def _lc0_network_samples(self) -> list[tuple[Path, chess.Board]]:
        standard = chess.Board()
        minor_odds = chess.Board()
        minor_odds.remove_piece_at(chess.G1)
        queen_odds = chess.Board()
        queen_odds.remove_piece_at(chess.D1)
        return [
            (Path(self.config.lc0_bt4_weights), standard),
            (Path(self.config.lc0_t1_odds_weights), minor_odds),
            (Path(self.config.lc0_queen_odds_weights), queen_odds),
        ]

    def _start_preview_engine(self) -> None:
        """Keep a small Stockfish process warm while LCZero networks load."""

        with self._preview_lock:
            if self._preview_engine is not None:
                return
            path = Path(self.config.engine_path)
            if not path.is_file():
                LOGGER.warning("Stockfish preview binary not found: %s", path)
                return
            preview: chess.engine.SimpleEngine | None = None
            try:
                preview = chess.engine.SimpleEngine.popen_uci(
                    str(path),
                    timeout=10.0,
                    **_engine_process_kwargs(path),
                )
                requested = {
                    "Threads": 2,
                    "Hash": 64,
                    "UCI_LimitStrength": False,
                    "Skill Level": 20,
                    "UCI_ShowWDL": False,
                }
                preview.configure({key: value for key, value in requested.items() if key in preview.options})
                self._preview_engine = preview
            except (OSError, TimeoutError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                LOGGER.warning("Could not start Stockfish preview: %s", exc)
                if preview is not None:
                    try:
                        preview.quit()
                    except Exception:
                        preview.close()

    def preview(self, board: chess.Board, revision: int, cancel_generation: int) -> AnalysisResult | None:
        """Return a fast provisional move while the selected LCZero net warms."""

        if self.config.engine_kind != "lc0" or not self._generation_is_current(cancel_generation):
            return None
        self._start_preview_engine()
        with self._preview_lock:
            if not self._generation_is_current(cancel_generation):
                return None
            engine = self._preview_engine
            if engine is None:
                return None
            try:
                info = engine.analyse(board, chess.engine.Limit(depth=1), info=LIVE_INFO)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                LOGGER.warning("Stockfish preview failed: %s", exc)
                self._preview_engine = None
                return None
        if not self._generation_is_current(cancel_generation):
            return None
        result = _result_from_info(board, revision, info)
        if result is None:
            return None
        return replace(
            result,
            odds_mode=self._effective_odds_mode(board),
            engine_name="Stockfish preview",
        )

    def _configure(
        self,
        engine: chess.engine.SimpleEngine,
        board: chess.Board | None = None,
        *,
        weights_path: Path | None = None,
    ) -> None:
        if self.config.engine_kind == "lc0":
            weights = weights_path or self._weights_path(board)
            requested: dict[str, object] = {
                "WeightsFile": str(weights) if weights else "",
                "Backend": "cuda-fp16",
                "Threads": min(4, self.config.threads),
                "MinibatchSize": 0,
                "UCI_ShowWDL": True,
                "Contempt": str(self.config.lc0_contempt),
                "ContemptMode": "play",
            }
            if self.config.odds_mode == "queen" and not self.config.lc0_auto_network:
                requested.update({"CPuct": 1.5, "FpuValue": 0.4, "ScLimit": 40})
        else:
            requested = {
                "Threads": self.config.threads,
                "Hash": self.config.hash_mb,
                "UCI_LimitStrength": False,
                "Skill Level": 20,
                "UCI_ShowWDL": False,
                "NumaPolicy": "auto",
            }
            if self.config.syzygy_path:
                requested["SyzygyPath"] = self.config.syzygy_path
        supported = engine.options
        options = {key: value for key, value in requested.items() if key in supported}
        engine.configure(options)

    def _engine_for_board(self, board: chess.Board) -> tuple[chess.engine.SimpleEngine, object]:
        """Return an engine loaded with the board's network.

        LC0's CUDA backend is not reliable when very large networks are hot-
        swapped repeatedly. Each network therefore keeps a dedicated process.
        """

        desired_weights = self._weights_path(board)
        duplicate: chess.engine.SimpleEngine | None = None
        with self._lock:
            if (
                self._engine is not None
                and self.config.engine_kind == "lc0"
                and desired_weights != self._loaded_weights
            ):
                LOGGER.info(
                    "LCZero network change: %s -> %s (mode=%s)",
                    self._loaded_weights.name if self._loaded_weights else "none",
                    desired_weights.name if desired_weights else "none",
                    self._effective_odds_mode(board),
                )
                assert self._loaded_weights is not None
                if self._loaded_weights not in self._engine_pool:
                    self._engine_pool[self._loaded_weights] = self._engine
                else:
                    duplicate = self._engine
                self._engine = self._engine_pool.pop(desired_weights, None) if desired_weights else None
                self._loaded_weights = desired_weights if self._engine is not None else None
                self._dynamic_options = {}
                if self._engine is not None:
                    LOGGER.info("LCZero network switch used warm process: %s", desired_weights.name)
        if duplicate is not None:
            _close_engine(duplicate)
        with self._lock:
            if self._engine is None:
                self.start(board)
            assert self._engine is not None
            return self._engine, self._game_token

    def _weights_path(self, board: chess.Board | None = None, odds_mode: str | None = None) -> Path | None:
        if self.config.engine_kind != "lc0":
            return None
        mode = odds_mode or self._effective_odds_mode(board)
        if mode == "queen":
            return Path(self.config.lc0_queen_odds_weights)
        if mode != "none":
            return Path(self.config.lc0_t1_odds_weights)
        return Path(self.config.lc0_bt4_weights)

    def _effective_odds_mode(self, board: chess.Board | None = None) -> str:
        if self.config.engine_kind != "lc0":
            return "none"
        if self.config.lc0_auto_network:
            return detect_odds_mode(board, self._player_color) if board is not None else "none"
        return self.config.odds_mode

    def _prepare_for_board(
        self,
        engine: chess.engine.SimpleEngine,
        board: chess.Board,
        *,
        contempt: int | None = None,
    ) -> tuple[str, int]:
        if self.config.engine_kind != "lc0":
            return "none", 0
        odds_mode = self._effective_odds_mode(board)
        weights = self._weights_path(board, odds_mode)
        if weights is None or not weights.is_file():
            raise StockfishError(f"LCZero network not found: {weights}")
        effective_contempt = self.config.lc0_contempt if contempt is None else contempt
        requested: dict[str, object] = {
            "Contempt": str(effective_contempt),
            "ContemptMode": "play",
        }
        white_has_queen = bool(board.pieces(chess.QUEEN, chess.WHITE))
        black_has_queen = bool(board.pieces(chess.QUEEN, chess.BLACK))
        lc0_plays_black = white_has_queen and not black_has_queen
        if odds_mode == "queen":
            requested.update({
                "SwapColors": lc0_plays_black,
                "ScLimit": 32 if lc0_plays_black else 40,
                "CPuct": 1.5,
                "FpuValue": 0.4,
                "DrawScore": 0.6 if lc0_plays_black else -0.4,
            })
        else:
            for key in ("SwapColors", "ScLimit", "CPuct", "FpuValue", "DrawScore"):
                option = engine.options.get(key)
                if option is not None and option.default is not None:
                    requested[key] = option.default
        options = {key: value for key, value in requested.items() if key in engine.options}
        if options != self._dynamic_options:
            engine.configure(options)
            self._dynamic_options = options
        return odds_mode, effective_contempt

    def _analysis_limit(self, board: chess.Board) -> chess.engine.Limit | None:
        # The dashboard streams whatever the engine has found so far and keeps
        # refining until the position changes. No mode has a fixed search cap.
        return None

    def new_game(self) -> None:
        with self._lock:
            if self._engine is None:
                self.start()
            self._game_token = object()

    def analyse(self, board: chess.Board, revision: int) -> AnalysisResult:
        with self._search_lock:
            engine, game_token = self._engine_for_board(board)
            odds_mode, effective_contempt = self._prepare_for_board(engine, board)
            analysis: chess.engine.SimpleAnalysisResult | None = None
            try:
                analysis = engine.analysis(
                    board,
                    chess.engine.Limit(time=self.config.think_time_ms / 1000.0),
                    info=LIVE_INFO,
                    game=game_token,
                )
                with self._active_lock:
                    self._active_analysis = analysis
                analysis.wait()
                info = dict(analysis.info)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                with self._lock:
                    if self._engine is engine:
                        self._engine = None
                        self._loaded_weights = None
                raise StockfishError(f"{_engine_label(self.config.engine_kind)} analysis failed: {exc}") from exc
            finally:
                with self._active_lock:
                    if self._active_analysis is analysis:
                        self._active_analysis = None

        result = _result_from_info(board, revision, info)
        if result is None:
            raise StockfishError(f"{_engine_label(self.config.engine_kind)} returned no principal variation")
        return replace(result, odds_mode=odds_mode, effective_contempt=effective_contempt)

    def analyse_continuously(
        self,
        board: chess.Board,
        revision: int,
        publish: Callable[[AnalysisResult], None],
        cancel_generation: int | None = None,
    ) -> None:
        """Analyze until cancelled and publish throttled snapshots of the live search."""

        if cancel_generation is None:
            with self._active_lock:
                cancel_generation = self._cancel_generation
        if not self._generation_is_current(cancel_generation):
            return
        with self._search_lock:
            # asyncio cancellation cannot remove a function already queued in
            # the thread pool. Reject stale jobs again after they reach the
            # serialized engine lock so an old position can never restart LC0
            # or delay the newest board.
            if not self._generation_is_current(cancel_generation):
                return
            engine, game_token = self._engine_for_board(board)
            if not self._generation_is_current(cancel_generation):
                return
            selected_odds_mode = self._effective_odds_mode(board)
            automatic_contempt = self.config.engine_kind == "lc0" and self.config.lc0_auto_contempt
            # An odds-trained network already models its expected handicap.
            # Raw eval is therefore not a useful auto-contempt signal in that
            # mode and would usually pin the slider at its maximum.
            probe_with_neutral_contempt = automatic_contempt and selected_odds_mode == "none"
            odds_mode, effective_contempt = self._prepare_for_board(
                engine,
                board,
                contempt=0 if automatic_contempt else None,
            )
            analysis: chess.engine.SimpleAnalysisResult | None = None
            last_signature: tuple[object, ...] | None = None
            last_published_at = 0.0
            try:
                if probe_with_neutral_contempt:
                    analysis = engine.analysis(
                        board,
                        limit=chess.engine.Limit(nodes=AUTO_CONTEMPT_PROBE_NODES),
                        info=LIVE_INFO,
                        game=game_token,
                    )
                    with self._active_lock:
                        self._active_analysis = analysis
                    probe_result: AnalysisResult | None = None
                    for probe_info in analysis:
                        if not self._generation_is_current(cancel_generation):
                            analysis.stop()
                            return
                        candidate = _result_from_info(board, revision, probe_info)
                        if candidate is None:
                            continue
                        probe_result = candidate
                        # Do not leave the overlay blank while the short
                        # neutral contempt probe is running. Its live PV is a
                        # genuine engine result and can be replaced normally
                        # once the effective contempt value is selected.
                        publish(replace(
                            candidate,
                            odds_mode=odds_mode,
                            effective_contempt=0,
                        ))
                    if probe_result is None:
                        probe_result = _result_from_info(board, revision, dict(analysis.info))
                    with self._active_lock:
                        if self._active_analysis is analysis:
                            self._active_analysis = None
                        if cancel_generation != self._cancel_generation:
                            return
                    player_score, player_mate = player_pov_evaluation(
                        probe_result.score_cp if probe_result else None,
                        probe_result.mate if probe_result else None,
                        board.turn,
                        self._player_color,
                    )
                    effective_contempt = auto_contempt_for_mode(odds_mode, player_score, player_mate)
                    odds_mode, effective_contempt = self._prepare_for_board(
                        engine,
                        board,
                        contempt=effective_contempt,
                    )
                    if probe_result is not None:
                        publish(replace(
                            probe_result,
                            odds_mode=odds_mode,
                            effective_contempt=effective_contempt,
                        ))
                    with self._active_lock:
                        if cancel_generation != self._cancel_generation:
                            return
                    analysis = None
                analysis = engine.analysis(
                    board,
                    limit=self._analysis_limit(board),
                    info=LIVE_INFO,
                    game=game_token,
                )
                with self._active_lock:
                    self._active_analysis = analysis
                for info in analysis:
                    if not self._generation_is_current(cancel_generation):
                        analysis.stop()
                        return
                    result = _result_from_info(board, revision, info)
                    if result is None:
                        continue
                    result = replace(
                        result,
                        odds_mode=odds_mode,
                        effective_contempt=effective_contempt,
                    )
                    signature = (result.best_move, result.depth, result.score_cp, result.mate)
                    now = time.monotonic()
                    best_move_changed = last_signature is None or signature[0] != last_signature[0]
                    if not best_move_changed and now - last_published_at < 0.10:
                        continue
                    if signature == last_signature and now - last_published_at < 0.50:
                        continue
                    publish(result)
                    last_signature = signature
                    last_published_at = now
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
                with self._lock:
                    if self._engine is engine:
                        self._engine = None
                        self._loaded_weights = None
                raise StockfishError(f"{_engine_label(self.config.engine_kind)} analysis failed: {exc}") from exc
            finally:
                with self._active_lock:
                    if self._active_analysis is analysis:
                        self._active_analysis = None

    def reserve_analysis(self) -> int:
        """Invalidate older work and reserve a generation for a new search."""

        with self._active_lock:
            self._cancel_generation += 1
            generation = self._cancel_generation
            analysis = self._active_analysis
        if analysis is not None:
            analysis.stop()
        return generation

    def _generation_is_current(self, generation: int) -> bool:
        with self._active_lock:
            return generation == self._cancel_generation

    def cancel(self) -> None:
        self.reserve_analysis()

    def stop(self) -> None:
        self.cancel()
        with self._preview_lock:
            preview, self._preview_engine = self._preview_engine, None
            if preview is not None:
                try:
                    preview.quit()
                except (chess.engine.EngineError, chess.engine.EngineTerminatedError, TimeoutError):
                    preview.close()
        with self._lock:
            engine, self._engine = self._engine, None
            pooled = list(self._engine_pool.values())
            self._engine_pool.clear()
            self._dynamic_options = {}
            self._loaded_weights = None
        for item in ([engine] if engine is not None else []) + pooled:
            _close_engine(item)


def _engine_label(kind: str) -> str:
    return "LCZero" if kind == "lc0" else "Stockfish"


def _close_engine(engine: chess.engine.SimpleEngine) -> None:
    try:
        engine.quit()
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError, TimeoutError):
        try:
            engine.close()
        except Exception:  # pragma: no cover - defensive process cleanup
            LOGGER.exception("Could not close engine cleanly")


def _engine_process_kwargs(path: Path) -> dict[str, object]:
    """Launch UCI engines without creating a visible Windows console."""

    options: dict[str, object] = {"cwd": str(path.parent)}
    if os.name == "nt":
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = subprocess.SW_HIDE
        options.update({
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startup_info,
        })
    return options


def detect_odds_mode(board: chess.Board | None, player_color: chess.Color | None) -> str:
    """Select an odds network only when the human player's side is deficient.

    Captures suffered by the opponent never trigger an odds network. Equal
    trades also remain on the normal network.
    """

    if board is None or player_color is None:
        return "none"
    opponent_color = not player_color
    differences = {
        piece_type: len(board.pieces(piece_type, player_color)) - len(board.pieces(piece_type, opponent_color))
        for piece_type in (chess.KNIGHT, chess.ROOK, chess.QUEEN)
    }
    queen_difference = differences[chess.QUEEN]
    knight_difference = differences[chess.KNIGHT]
    if queen_difference < 0:
        if knight_difference > 0:
            return "queen_for_knight"
        return "queen"
    if differences[chess.ROOK] < 0:
        return "rook"
    if knight_difference < 0:
        return "knight"
    return "none"


def auto_practical_contempt(score_cp: int | None, mate: int | None) -> int:
    """Conservative swindle bias from an evaluation in the player's POV.

    Contempt is an Elo-advantage model, not an objective strength control. The
    automatic mode therefore stays neutral when equal/winning and caps the
    practical bias at +250 when substantially worse.
    """

    if mate is not None:
        return 250 if mate < 0 else 0
    if score_cp is None or score_cp >= -75:
        return 0
    deficit = -score_cp
    if deficit < 150:
        return 25
    if deficit < 300:
        return 50
    if deficit < 500:
        return 100
    if deficit < 800:
        return 150
    if deficit < 1200:
        return 200
    return 250


def auto_contempt_for_mode(odds_mode: str, score_cp: int | None, mate: int | None) -> int:
    """Use eval-driven contempt only with the general-purpose network."""

    return 0 if odds_mode != "none" else auto_practical_contempt(score_cp, mate)


def player_pov_evaluation(
    score_cp: int | None,
    mate: int | None,
    side_to_move: chess.Color,
    player_color: chess.Color | None,
) -> tuple[int | None, int | None]:
    """Convert an engine's side-to-move score to the human player's POV."""

    if player_color is None:
        return None, None
    if side_to_move == player_color:
        return score_cp, mate
    return (-score_cp if score_cp is not None else None, -mate if mate is not None else None)


def _result_from_info(board: chess.Board, revision: int, info: dict[str, object]) -> AnalysisResult | None:
    pv = info.get("pv") or []
    if not isinstance(pv, list) or not pv:
        return None
    best_move = pv[0]
    if not isinstance(best_move, chess.Move) or best_move not in board.legal_moves:
        return None
    score = info.get("score")
    pov_score = score.pov(board.turn) if isinstance(score, chess.engine.PovScore) else None
    score_cp = pov_score.score(mate_score=100_000) if pov_score is not None and not pov_score.is_mate() else None
    mate = pov_score.mate() if pov_score is not None and pov_score.is_mate() else None

    pv_board = board.copy(stack=True)
    pv_san: list[str] = []
    pv_uci: list[str] = []
    for move in pv[:12]:
        if not isinstance(move, chess.Move):
            break
        if move not in pv_board.legal_moves:
            break
        pv_san.append(pv_board.san(move))
        pv_uci.append(move.uci())
        pv_board.push(move)

    return AnalysisResult(
        revision=revision,
        fen=board.fen(),
        best_move=best_move,
        score_cp=score_cp,
        mate=mate,
        depth=info.get("depth") if isinstance(info.get("depth"), int) else None,
        nodes=info.get("nodes") if isinstance(info.get("nodes"), int) else None,
        nps=info.get("nps") if isinstance(info.get("nps"), int) else None,
        pv_uci=tuple(pv_uci),
        pv_san=tuple(pv_san),
        time_ms=(
            max(0, round(float(info["time"]) * 1000))
            if isinstance(info.get("time"), (int, float))
            else None
        ),
    )
