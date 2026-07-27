from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import chess
from websockets.asyncio.server import ServerConnection

from .bridge import BridgeServer
from .config import AppConfig
from .engine import StockfishError, StockfishService, detect_odds_mode
from .events import AppEvent
from .models import AnalysisResult, Orientation, Snapshot, SyncState
from .policy import PageMode, PolicyDecision, classify_page, site_label
from .reconcile import GameReconciler, piece_map


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalysisGuard:
    game_key: str
    position_hash: str
    fen: str


class RuntimeController:
    """Runs the WebSocket bridge and engine away from the Qt event loop."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events: queue.SimpleQueue[AppEvent] = queue.SimpleQueue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._bridge: BridgeServer | None = None
        self._engine = StockfishService(config)
        self._trackers: dict[str, GameReconciler] = {}
        self._clients: dict[str, ServerConnection] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._decisions: dict[str, PolicyDecision] = {}
        self._analysis_tasks: dict[str, asyncio.Task[None]] = {}
        self._analysed_revision: dict[str, tuple[int, str]] = {}
        self._effective_odds_modes: dict[str, str] = {}
        self._sync_states: dict[str, SyncState] = {}
        self._active_page: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="trainer-runtime", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=20):
            raise RuntimeError("Runtime did not start")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # pragma: no cover - last-resort reporting
            LOGGER.exception("Runtime stopped unexpectedly")
            self._emit("error", f"Runtime stopped: {exc}")
            self._ready.set()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._bridge = BridgeServer(self.config.host, self.config.port, self._on_message)
        try:
            await self._bridge.start()
            self._emit("bridge", f"Bridge listening on ws://{self.config.host}:{self.config.port}", connected=False)
        except OSError as exc:
            self._emit("error", f"Cannot open local bridge port {self.config.port}: {exc}")
            self._ready.set()
            return

        try:
            name = await asyncio.to_thread(self._engine.start)
            await asyncio.to_thread(self._engine.prewarm_lc0_networks)
            self._emit(
                "engine",
                f"{name} ready",
                ready=True,
                name=name,
            )
        except StockfishError as exc:
            self._emit("engine", str(exc), ready=False)
        finally:
            self._ready.set()

        await self._stop_event.wait()
        self._engine.cancel()
        for task in self._analysis_tasks.values():
            task.cancel()
        await asyncio.gather(*self._analysis_tasks.values(), return_exceptions=True)
        if self._bridge:
            await self._bridge.stop()
        await asyncio.to_thread(self._engine.stop)

    async def _on_message(self, message: dict[str, Any], websocket: ServerConnection) -> None:
        message_type = message.get("type")
        if message_type == "bridge.connected":
            LOGGER.info("Browser extension connected")
            self._emit("bridge", "Browser extension connected", connected=True)
            return
        if message_type == "bridge.disconnected":
            disconnected_pages = [page for page, client in self._clients.items() if client is websocket]
            for page in disconnected_pages:
                self._clients.pop(page, None)
            self._emit("bridge", "Browser extension disconnected", connected=bool(self._bridge and self._bridge.clients))
            return
        if message_type in {"position.snapshot", "snapshot"}:
            await self._handle_snapshot(message, websocket)
            return
        if message_type == "dashboard.action":
            self._handle_dashboard_action(message)
            return
        if message_type == "log":
            self._emit("log", str(message.get("message", "Extension message")))

    async def _handle_snapshot(self, message: dict[str, Any], websocket: ServerConnection) -> None:
        try:
            snapshot = Snapshot.from_message(message)
        except (TypeError, ValueError) as exc:
            LOGGER.warning("Ignored invalid browser snapshot: %s", exc)
            self._emit("warning", f"Ignored invalid browser snapshot: {exc}")
            return

        LOGGER.info(
            "Browser snapshot received: page=%s url=%s seq=%s pieces=%s moves=%s orientation=%s player=%s visible=%s focused=%s detector=%s",
            snapshot.page_id,
            snapshot.url,
            snapshot.seq,
            len(snapshot.pieces),
            len(snapshot.moves),
            snapshot.orientation.value,
            snapshot.player_color,
            snapshot.visible,
            snapshot.focused,
            {
                "selector": (message.get("board") or {}).get("selector_hint"),
                "confidence": (message.get("board") or {}).get("confidence"),
                "piece_source": (message.get("board") or {}).get("pieceSource"),
            },
        )

        self._clients[snapshot.page_id] = websocket
        self._snapshots[snapshot.page_id] = snapshot
        if snapshot.focused and snapshot.visible:
            if self._active_page != snapshot.page_id:
                self._engine.cancel()
                for other_page, task in tuple(self._analysis_tasks.items()):
                    if other_page != snapshot.page_id and not task.done():
                        task.cancel()
                        self._analysed_revision.pop(other_page, None)
            self._active_page = snapshot.page_id

        decision = classify_page(snapshot.url)
        site = site_label(snapshot.url)
        self._decisions[snapshot.page_id] = decision
        tracker = self._trackers.setdefault(snapshot.page_id, GameReconciler())
        transition = tracker.ingest(snapshot)
        self._sync_states[snapshot.page_id] = transition.state
        if transition.new_game:
            self._effective_odds_modes.pop(snapshot.page_id, None)
        reconciled_moves = _san_history(transition.board) if transition.board else ()
        # Some renderers expose only the initially hydrated move list while
        # their live board continues updating. Prefer the reconciler's legal
        # history once it is at least as complete as the DOM history.
        display_moves = reconciled_moves if len(reconciled_moves) >= len(snapshot.moves) else snapshot.moves
        LOGGER.info(
            "Position reconciliation: page=%s seq=%s sync=%s revision=%s board_plies=%s detected_moves=%s detail=%s",
            snapshot.page_id,
            snapshot.seq,
            transition.state.value,
            transition.revision,
            len(transition.board.move_stack) if transition.board else 0,
            len(snapshot.moves),
            transition.message,
        )

        analysis_workspace = decision.mode is PageMode.ANALYSIS
        player_color = None if analysis_workspace else _player_color(snapshot)
        if snapshot.page_id == self._active_page:
            self._engine.set_player_color(player_color)
        orientation = snapshot.orientation.value.title()
        player_text = chess.COLOR_NAMES[player_color].title() if player_color is not None else "Unknown"
        provisional_lichess_frame = (
            site == "Lichess"
            and bool(snapshot.moves)
            and transition.message == "Recovered from observed board placement"
        )
        if self.config.engine_kind == "lc0" and self.config.lc0_auto_network:
            detected_odds_mode = detect_odds_mode(transition.board, player_color)
            if provisional_lichess_frame:
                effective_odds_mode = self._effective_odds_modes.get(snapshot.page_id, "none")
            else:
                effective_odds_mode = detected_odds_mode
                self._effective_odds_modes[snapshot.page_id] = detected_odds_mode
        else:
            effective_odds_mode = self.config.odds_mode
        self._emit(
            "position",
            transition.message or transition.state.value,
            page_id=snapshot.page_id,
            site=site,
            mode=decision.mode.value,
            policy_reason=decision.reason,
            can_analyze=decision.can_analyze,
            can_execute=decision.can_execute,
            sync=transition.state.value,
            revision=transition.revision,
            game_key=snapshot.game_key,
            orientation=orientation,
            player_color=player_text,
            fen=transition.board.fen() if transition.board else "",
            moves=display_moves,
            last_move=transition.board.peek().uci() if transition.board and transition.board.move_stack else "",
        )
        if self._bridge:
            await self._bridge.send(
                websocket,
                {
                    "type": "dashboard.state",
                    "pageId": snapshot.page_id,
                    "seq": snapshot.seq,
                    "positionHash": snapshot.position_hash,
                    "fen": transition.board.fen() if transition.board else "",
                    "moves": display_moves,
                    "sync": transition.state.value,
                    "orientation": snapshot.orientation.value,
                    "playerColor": player_text,
                    "site": site,
                    "monitoring": self.config.monitoring,
                    "showOverlays": self.config.show_arrow,
                    "engineKind": self.config.engine_kind,
                    "engineName": getattr(self._engine, "name", "Stockfish 18"),
                    "multiPv": self.config.multi_pv,
                    "oddsMode": effective_odds_mode,
                    "lc0Contempt": self.config.lc0_contempt,
                    "lc0AutoNetwork": self.config.lc0_auto_network,
                    "lc0AutoContempt": self.config.lc0_auto_contempt,
                    "analyzeOpponent": self.config.analyze_opponent,
                    "showOpponentArrows": self.config.show_opponent_arrows,
                    "opponentTurn": _is_opponent_turn(transition.board, player_color),
                },
            )

        if transition.new_game:
            self._analysed_revision.pop(snapshot.page_id, None)
            await asyncio.to_thread(self._engine.new_game)
            self._emit("game", f"New {site} session detected")

        if not self.config.monitoring or not decision.can_analyze:
            return
        if provisional_lichess_frame:
            # Chessground briefly removes a captured piece before placing the
            # mover. Wait for the following history-consistent snapshot rather
            # than switching networks or analyzing that animation frame.
            self._cancel_page_analysis(snapshot.page_id)
            return
        if transition.state is not SyncState.SYNCHRONIZED:
            task = self._analysis_tasks.get(snapshot.page_id)
            if task and not task.done():
                self._engine.cancel()
                task.cancel()
            return
        if transition.board is None or transition.board.is_game_over():
            self._cancel_page_analysis(snapshot.page_id)
            return
        if snapshot.page_id != self._active_page or not snapshot.visible or not snapshot.focused:
            return
        if player_color is None and not analysis_workspace:
            self._cancel_page_analysis(snapshot.page_id)
            return
        if not analysis_workspace and transition.board.turn != player_color and not self.config.analyze_opponent:
            self._cancel_page_analysis(snapshot.page_id)
            return
        analysis_identity = (transition.revision, snapshot.position_hash)
        if self._analysed_revision.get(snapshot.page_id) == analysis_identity:
            return
        self._schedule_analysis(snapshot.page_id, transition.revision, transition.board.copy(stack=True))

    def _cancel_page_analysis(self, page_id: str) -> None:
        task = self._analysis_tasks.get(page_id)
        if task and not task.done():
            self._engine.cancel()
            task.cancel()

    def _schedule_analysis(self, page_id: str, revision: int, board: chess.Board) -> None:
        existing = self._analysis_tasks.get(page_id)
        engine_generation = self._engine.reserve_analysis()
        if existing and not existing.done():
            existing.cancel()
        snapshot = self._snapshots.get(page_id)
        self._analysed_revision[page_id] = (revision, snapshot.position_hash if snapshot else "")
        guard = None if snapshot is None else AnalysisGuard(
            game_key=snapshot.game_key,
            position_hash=snapshot.position_hash,
            fen=board.fen(),
        )
        self._analysis_tasks[page_id] = asyncio.create_task(
            self._analyse(page_id, revision, board, guard, engine_generation)
        )

    async def _analyse(
        self,
        page_id: str,
        revision: int,
        board: chess.Board,
        guard: AnalysisGuard | None = None,
        engine_generation: int | None = None,
    ) -> None:
        engine_name = getattr(self._engine, "name", "Chess engine")
        self._emit("analysis", f"{engine_name} is thinking…", thinking=True, revision=revision)
        loop = asyncio.get_running_loop()

        def publish(result: AnalysisResult) -> None:
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._publish_live_analysis(page_id, revision, board, guard, result))
            )

        retry = False
        try:
            if engine_generation is not None and self.config.engine_kind == "lc0":
                preview = await asyncio.to_thread(
                    self._engine.preview,
                    board,
                    revision,
                    engine_generation,
                )
                if preview is not None:
                    await self._publish_live_analysis(page_id, revision, board, guard, preview)
            await asyncio.to_thread(
                self._engine.analyse_continuously,
                board,
                revision,
                publish,
                engine_generation,
            )
        except asyncio.CancelledError:
            return
        except StockfishError as exc:
            self._emit("error", str(exc))
            self._analysed_revision.pop(page_id, None)
            if self.config.engine_kind == "lc0":
                await self._fallback_to_stockfish(exc)
            retry = True
        finally:
            current = self._analysis_tasks.get(page_id)
            if current is asyncio.current_task():
                self._analysis_tasks.pop(page_id, None)
        if retry:
            await asyncio.sleep(1.0)
            tracker = self._trackers.get(page_id)
            snapshot = self._snapshots.get(page_id)
            if (
                tracker is not None
                and tracker.board is not None
                and tracker.revision == revision
                and tracker.board.fen() == board.fen()
                and snapshot is not None
                and snapshot.visible
                and snapshot.focused
                and page_id == self._active_page
            ):
                self._schedule_analysis(page_id, revision, board.copy(stack=True))

    async def _fallback_to_stockfish(self, failure: StockfishError) -> bool:
        """Keep analysis available if LCZero's CUDA process terminates."""

        if self.config.engine_kind != "lc0":
            return False
        LOGGER.warning("LCZero failed; falling back to Stockfish: %s", failure)
        try:
            await asyncio.to_thread(self._engine.stop)
            self.config.engine_kind = "stockfish"
            self.config.save()
            name = await asyncio.to_thread(self._engine.start)
        except (OSError, StockfishError) as exc:
            LOGGER.error("Stockfish fallback failed: %s", exc)
            self._emit("error", f"Stockfish fallback failed: {exc}")
            return False
        self._emit(
            "engine",
            f"LCZero stopped responding; switched to {name}",
            ready=True,
            name=name,
        )
        return True

    async def _publish_live_analysis(
        self,
        page_id: str,
        revision: int,
        board: chess.Board,
        guard: AnalysisGuard | None,
        result: AnalysisResult,
    ) -> None:
        """Publish one position-bound update from Stockfish's ongoing search."""

        tracker = self._trackers.get(page_id)
        snapshot = self._snapshots.get(page_id)
        decision = self._decisions.get(page_id)
        if not tracker or not snapshot or not decision or tracker.revision != revision:
            return
        if tracker.board is None or tracker.board.fen() != board.fen():
            return
        if self._sync_states.get(page_id, SyncState.SYNCHRONIZED) is not SyncState.SYNCHRONIZED:
            return
        if guard is not None and (
            snapshot.game_key != guard.game_key
            or snapshot.position_hash != guard.position_hash
            or board.fen() != guard.fen
        ):
            return
        if snapshot.pieces and snapshot.pieces != piece_map(board):
            return
        self._emit_analysis(result, board)
        player_color = None if decision.mode is PageMode.ANALYSIS else _player_color(snapshot)
        opponent_turn = _is_opponent_turn(board, player_color)

        client = self._clients.get(page_id)
        if not client or not self._bridge:
            return
        await self._bridge.send(
            client,
            {
                "type": "arrow.command",
                "pageId": page_id,
                "gameKey": snapshot.game_key,
                "seq": snapshot.seq,
                "positionHash": snapshot.position_hash,
                "uci": result.best_move.uci(),
                "whiteScoreCp": (
                    result.score_cp if board.turn == chess.WHITE else -result.score_cp
                ) if result.score_cp is not None else None,
                "whiteMate": (
                    result.mate if board.turn == chess.WHITE else -result.mate
                ) if result.mate is not None else None,
                "bestMoveSan": board.san(result.best_move),
                "evaluation": _white_evaluation_text(result, board),
                "pv": " ".join(result.pv_san),
                "depth": result.depth,
                "nodes": result.nodes,
                "nps": result.nps,
                "timeMs": result.time_ms,
                "showOverlays": self.config.show_arrow and (
                    not opponent_turn or self.config.show_opponent_arrows
                ),
                "engineKind": self.config.engine_kind,
                "engineName": result.engine_name or getattr(self._engine, "name", "Chess engine"),
                "multiPv": self.config.multi_pv,
                "variations": [
                    {
                        "rank": variation.rank,
                        "uci": variation.best_move.uci(),
                        "scoreCp": variation.score_cp,
                        "mate": variation.mate,
                        "depth": variation.depth,
                        "evaluation": _white_score_text(variation.score_cp, variation.mate, board),
                        "pv": " ".join(variation.pv_san),
                    }
                    for variation in result.variations
                ] or [{
                    "rank": 1,
                    "uci": result.best_move.uci(),
                    "scoreCp": result.score_cp,
                    "mate": result.mate,
                    "depth": result.depth,
                    "evaluation": _white_evaluation_text(result, board),
                    "pv": " ".join(result.pv_san),
                }],
                "oddsMode": result.odds_mode or self.config.odds_mode,
                "effectiveContempt": result.effective_contempt,
                "lc0AutoNetwork": self.config.lc0_auto_network,
                "lc0AutoContempt": self.config.lc0_auto_contempt,
                "opponentTurn": opponent_turn,
            },
        )

    async def _clear_arrow(self, page_id: str) -> None:
        client = self._clients.get(page_id)
        snapshot = self._snapshots.get(page_id)
        if not client or not snapshot or not self._bridge:
            return
        await self._bridge.send(
            client,
            {
                "type": "arrow.clear",
                "pageId": page_id,
                "seq": snapshot.seq,
                "positionHash": snapshot.position_hash,
            },
        )

    async def _clear_board_overlay(self, page_id: str) -> None:
        client = self._clients.get(page_id)
        snapshot = self._snapshots.get(page_id)
        if not client or not snapshot or not self._bridge:
            return
        await self._bridge.send(
            client,
            {
                "type": "overlay.clear",
                "pageId": page_id,
                "seq": snapshot.seq,
                "positionHash": snapshot.position_hash,
            },
        )

    def _emit_analysis(self, result: AnalysisResult, board: chess.Board) -> None:
        san = board.san(result.best_move)
        evaluation = _evaluation_text(result)
        self._emit(
            "analysis",
            f"Best move: {san} ({result.best_move.uci()})",
            thinking=False,
            best_move=result.best_move.uci(),
            best_move_san=san,
            evaluation=evaluation,
            depth=result.depth,
            nodes=result.nodes,
            nps=result.nps,
            pv=" ".join(result.pv_san),
        )

    def set_monitoring(self, enabled: bool) -> None:
        self.config.monitoring = bool(enabled)
        self.config.save()

        def update() -> None:
            self._emit("control", "Monitoring started" if enabled else "Monitoring paused", monitoring=enabled)
            if not enabled:
                self._engine.cancel()
                for task in self._analysis_tasks.values():
                    task.cancel()
                for page_id in tuple(self._snapshots):
                    asyncio.create_task(self._clear_arrow(page_id))
            else:
                self.analyze_now()

        self._call(update)

    def set_auto_play(self, enabled: bool) -> None:
        self.config.auto_play = bool(enabled)
        self.config.save()
        self._call(lambda: self._emit("control", "Auto-play enabled" if enabled else "Advisory mode enabled", auto_play=enabled))

    def set_show_arrow(self, enabled: bool) -> None:
        self.config.show_arrow = bool(enabled)
        self.config.save()

        def update() -> None:
            self._emit(
                "control",
                "Best-move arrow enabled" if enabled else "Best-move arrow hidden",
                show_arrow=enabled,
            )
            if not enabled:
                for page_id in tuple(self._snapshots):
                    asyncio.create_task(self._clear_board_overlay(page_id))

        self._call(update)

    def analyze_now(self) -> None:
        def schedule() -> None:
            page_id = self._active_page
            tracker = self._trackers.get(page_id or "")
            decision = self._decisions.get(page_id or "")
            if not page_id or not tracker or not tracker.board or not decision or not decision.can_analyze:
                self._emit("warning", "No synchronized training position is active")
                return
            self._analysed_revision.pop(page_id, None)
            self._schedule_analysis(page_id, tracker.revision, tracker.board.copy(stack=True))

        self._call(schedule)

    def recalibrate(self) -> None:
        def reset() -> None:
            page_id = self._active_page
            if not page_id:
                self._emit("warning", "No active board to recalibrate")
                return
            self._engine.cancel()
            task = self._analysis_tasks.pop(page_id, None)
            if task and not task.done():
                task.cancel()
            self._trackers.pop(page_id, None)
            self._analysed_revision.pop(page_id, None)
            self._sync_states.pop(page_id, None)
            client = self._clients.get(page_id)
            if client and self._bridge:
                asyncio.create_task(
                    self._bridge.send(client, {"type": "recalibrate.command", "pageId": page_id})
                )
            self._emit("control", "Recalibrating active board", recalibrating=True)

        self._call(reset)

    def _handle_dashboard_action(self, message: dict[str, Any]) -> None:
        action = str(message.get("action") or "")
        if action == "analyze":
            self.analyze_now()
        elif action == "recalibrate":
            self.recalibrate()
        elif action == "monitoring":
            self.set_monitoring(bool(message.get("enabled")))
        elif action == "overlays":
            self.set_show_arrow(bool(message.get("enabled")))
        elif action == "analyze_opponent":
            self._set_analyze_opponent(bool(message.get("enabled")))
        elif action == "opponent_arrows":
            self._set_opponent_arrows(bool(message.get("enabled")))
        elif action == "engine":
            value = str(message.get("value") or "stockfish").lower()
            if value in {"stockfish", "lc0"}:
                asyncio.create_task(self._apply_engine_settings(engine_kind=value))
        elif action == "multipv":
            try:
                value = int(message.get("value", 1))
            except (TypeError, ValueError, OverflowError):
                return
            if 1 <= value <= 3:
                asyncio.create_task(self._apply_engine_settings(multi_pv=value))
        elif action == "odds":
            value = str(message.get("value") or "none").lower()
            if value in {"none", "knight", "rook", "queen_for_knight", "queen"}:
                asyncio.create_task(self._apply_engine_settings(odds_mode=value))
        elif action == "contempt":
            try:
                value = int(message.get("value", 0))
            except (TypeError, ValueError, OverflowError):
                return
            asyncio.create_task(self._apply_engine_settings(lc0_contempt=max(-1000, min(1000, value))))
        elif action == "auto_network":
            asyncio.create_task(self._apply_engine_settings(lc0_auto_network=bool(message.get("enabled"))))
        elif action == "auto_contempt":
            asyncio.create_task(self._apply_engine_settings(lc0_auto_contempt=bool(message.get("enabled"))))

    def _set_analyze_opponent(self, enabled: bool) -> None:
        self.config.analyze_opponent = enabled
        self.config.save()
        self._emit("control", "Opponent-turn analysis enabled" if enabled else "Opponent-turn analysis disabled")
        if enabled:
            self.analyze_now()
            return
        page_id = self._active_page
        tracker = self._trackers.get(page_id or "")
        snapshot = self._snapshots.get(page_id or "")
        if page_id and tracker and snapshot and _is_opponent_turn(tracker.board, _player_color(snapshot)):
            self._engine.cancel()
            task = self._analysis_tasks.get(page_id)
            if task and not task.done():
                task.cancel()
            asyncio.create_task(self._clear_arrow(page_id))

    def _set_opponent_arrows(self, enabled: bool) -> None:
        self.config.show_opponent_arrows = enabled
        self.config.save()
        self._emit("control", "Opponent arrows enabled" if enabled else "Opponent arrows disabled")
        if not enabled and self._active_page:
            snapshot = self._snapshots.get(self._active_page)
            tracker = self._trackers.get(self._active_page)
            if snapshot and tracker and _is_opponent_turn(tracker.board, _player_color(snapshot)):
                asyncio.create_task(self._clear_arrow(self._active_page))

    async def _apply_engine_settings(
        self,
        *,
        engine_kind: str | None = None,
        multi_pv: int | None = None,
        odds_mode: str | None = None,
        lc0_contempt: int | None = None,
        lc0_auto_network: bool | None = None,
        lc0_auto_contempt: bool | None = None,
    ) -> None:
        self._engine.cancel()
        tasks = [task for task in self._analysis_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._analysis_tasks.clear()
        self._analysed_revision.clear()
        await asyncio.to_thread(self._engine.stop)
        # Only publish the new selection after every process belonging to the
        # previous selection has stopped. In particular, this prevents an
        # in-flight LC0 network prewarm from overlapping Stockfish selection.
        if engine_kind is not None:
            self.config.engine_kind = engine_kind
        if multi_pv is not None:
            self.config.multi_pv = multi_pv
        if odds_mode is not None:
            self.config.odds_mode = odds_mode
        if lc0_contempt is not None:
            self.config.lc0_contempt = lc0_contempt
        if lc0_auto_network is not None:
            self.config.lc0_auto_network = lc0_auto_network
        if lc0_auto_contempt is not None:
            self.config.lc0_auto_contempt = lc0_auto_contempt
        self.config.save()
        try:
            name = await asyncio.to_thread(self._engine.start)
            await asyncio.to_thread(self._engine.prewarm_lc0_networks)
            self._emit(
                "engine",
                f"{name} ready ({'auto network' if self.config.lc0_auto_network else self.config.odds_mode.replace('_', ' ')}; {'auto' if self.config.lc0_auto_contempt else f'{self.config.lc0_contempt:+d}'} contempt)",
                ready=True,
                name=name,
            )
        except StockfishError as exc:
            self._emit("engine", str(exc), ready=False)
            return
        self.analyze_now()

    def stop(self, timeout: float = 8.0) -> None:
        loop, stop_event, thread = self._loop, self._stop_event, self._thread
        if loop and stop_event and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if not thread or not thread.is_alive():
            self._thread = None
            self._loop = None
            self._stop_event = None

    def _call(self, callback: Callable[[], None]) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(callback)

    def _emit(self, kind: str, message: str, **data: Any) -> None:
        self.events.put(AppEvent(kind=kind, message=message, data=data))


def _player_color(snapshot: Snapshot) -> chess.Color | None:
    return snapshot.player_color


def _is_opponent_turn(board: chess.Board | None, player_color: chess.Color | None) -> bool:
    return board is not None and player_color is not None and board.turn != player_color


def _san_history(board: chess.Board) -> tuple[str, ...]:
    replay = board.root()
    sans: list[str] = []
    for move in board.move_stack:
        if move not in replay.legal_moves:
            break
        sans.append(replay.san(move))
        replay.push(move)
    return tuple(sans)


def _evaluation_text(result: AnalysisResult) -> str:
    if result.mate is not None:
        return f"Mate {result.mate:+d}"
    if result.score_cp is not None:
        return f"{result.score_cp / 100:+.2f}"
    return "—"


def _white_evaluation_text(result: AnalysisResult, board: chess.Board) -> str:
    return _white_score_text(result.score_cp, result.mate, board)


def _white_score_text(score_cp: int | None, mate: int | None, board: chess.Board) -> str:
    direction = 1 if board.turn == chess.WHITE else -1
    if mate is not None:
        return f"Mate {direction * mate:+d}"
    if score_cp is not None:
        return f"{direction * score_cp / 100:+.2f}"
    return "—"
