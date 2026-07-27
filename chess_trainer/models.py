from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any

import chess


class Orientation(StrEnum):
    WHITE = "white"
    BLACK = "black"
    UNKNOWN = "unknown"


class SyncState(StrEnum):
    WAITING = "waiting"
    SYNCHRONIZED = "synchronized"
    TRANSIENT = "transient"
    DESYNCHRONIZED = "desynchronized"


@dataclass(frozen=True, slots=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> Rect | None:
        if not value:
            return None
        try:
            return cls(
                left=float(value["left"]),
                top=float(value["top"]),
                width=float(value["width"]),
                height=float(value["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class Snapshot:
    page_id: str
    seq: int
    url: str
    game_key: str
    pieces: dict[str, str]
    moves: tuple[str, ...] = ()
    starting_fen: str | None = None
    current_fen: str | None = None
    orientation: Orientation = Orientation.UNKNOWN
    orientation_confidence: float = 0.0
    player_color: chess.Color | None = None
    side_to_move: chess.Color | None = None
    status: str = "playing"
    visible: bool = True
    focused: bool = True
    board_rect: Rect | None = None
    promotion_options: dict[str, Rect] = field(default_factory=dict)
    position_hash: str = ""

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> Snapshot:
        if message.get("type") not in {"position.snapshot", "snapshot"}:
            raise ValueError("message is not a position snapshot")

        page = message.get("page") if isinstance(message.get("page"), dict) else {}
        board = message.get("board") if isinstance(message.get("board"), dict) else {}
        raw_pieces = board.get("pieces") or message.get("pieces") or {}
        moves = message.get("moves") or []
        starting_fen = board.get("startingFen") or message.get("startingFen")
        current_fen = board.get("currentFen") or message.get("currentFen")
        if not isinstance(raw_pieces, dict):
            raise ValueError("board pieces must be an object")
        if not isinstance(moves, (list, tuple)):
            raise ValueError("moves must be an array")
        pieces = {
            str(square).lower(): normalize_piece_code(str(piece))
            for square, piece in raw_pieces.items()
            if is_square(str(square)) and normalize_piece_code(str(piece))
        }
        if pieces and (len(pieces) < 2 or "K" not in pieces.values() or "k" not in pieces.values()):
            raise ValueError("snapshot does not contain both kings")
        if not pieces and not starting_fen and not current_fen and not moves:
            raise ValueError("snapshot has neither readable pieces nor reconstructable history")

        orientation_text = str(board.get("orientation") or message.get("orientation") or "unknown").lower()
        try:
            orientation = Orientation(orientation_text)
        except ValueError:
            orientation = Orientation.UNKNOWN

        player_text = str(board.get("playerColor") or message.get("playerColor") or "").lower()
        player_color = chess.WHITE if player_text == "white" else chess.BLACK if player_text == "black" else None
        game = message.get("game") if isinstance(message.get("game"), dict) else {}
        turn_text = str(game.get("side_to_move") or game.get("sideToMove") or message.get("sideToMove") or "").lower()
        side_to_move = chess.WHITE if turn_text in {"w", "white"} else chess.BLACK if turn_text in {"b", "black"} else None

        confidence = _confidence(board.get("orientationConfidence", message.get("orientationConfidence", 0.0)))

        raw_promotions = board.get("promotionOptions") or message.get("promotionOptions") or {}
        if not isinstance(raw_promotions, dict):
            raw_promotions = {}
        promotions: dict[str, Rect] = {}
        for piece, value in raw_promotions.items():
            key = str(piece).lower()[:1]
            rect = Rect.from_dict(value) if isinstance(value, dict) else None
            if key in "qrbn" and rect is not None:
                promotions[key] = rect

        return cls(
            page_id=str(message.get("pageId") or message.get("session_id") or page.get("pageId") or "default"),
            seq=int(message.get("seq", 0)),
            url=str(page.get("url") or page.get("route") or message.get("url") or ""),
            game_key=str(message.get("gameKey") or message.get("session_id") or page.get("gameId") or "unknown"),
            pieces=pieces,
            moves=tuple(str(move).strip() for move in moves if str(move).strip()),
            starting_fen=starting_fen,
            current_fen=current_fen,
            orientation=orientation,
            orientation_confidence=confidence,
            player_color=player_color,
            side_to_move=side_to_move,
            status=str(message.get("status") or board.get("status") or "playing"),
            visible=bool(page.get("visible", message.get("visible", True))),
            focused=bool(page.get("focused", message.get("focused", True))),
            board_rect=Rect.from_dict(board.get("rect") or message.get("boardRect")),
            promotion_options=promotions,
            position_hash=str(message.get("positionHash") or message.get("position_hash") or ""),
        )


@dataclass(frozen=True, slots=True)
class Transition:
    state: SyncState
    revision: int
    board: chess.Board | None
    new_game: bool = False
    moves: tuple[chess.Move, ...] = ()
    message: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisVariation:
    rank: int
    best_move: chess.Move
    score_cp: int | None
    mate: int | None
    depth: int | None
    pv_uci: tuple[str, ...] = ()
    pv_san: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    revision: int
    fen: str
    best_move: chess.Move
    score_cp: int | None
    mate: int | None
    depth: int | None
    nodes: int | None
    nps: int | None
    pv_uci: tuple[str, ...]
    pv_san: tuple[str, ...]
    time_ms: int | None = None
    odds_mode: str | None = None
    effective_contempt: int | None = None
    engine_name: str | None = None
    variations: tuple[AnalysisVariation, ...] = ()


def is_square(value: str) -> bool:
    return len(value) == 2 and value[0].lower() in "abcdefgh" and value[1] in "12345678"


def normalize_piece_code(value: str) -> str:
    value = value.strip()
    if len(value) == 1 and value in "PNBRQKpnbrqk":
        return value
    compact = value.lower().replace("_", "-")
    aliases = {
        "wp": "P", "wn": "N", "wb": "B", "wr": "R", "wq": "Q", "wk": "K",
        "bp": "p", "bn": "n", "bb": "b", "br": "r", "bq": "q", "bk": "k",
        "white-pawn": "P", "white-knight": "N", "white-bishop": "B",
        "white-rook": "R", "white-queen": "Q", "white-king": "K",
        "black-pawn": "p", "black-knight": "n", "black-bishop": "b",
        "black-rook": "r", "black-queen": "q", "black-king": "k",
    }
    return aliases.get(compact, "")


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("orientation confidence must be numeric") from exc
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("orientation confidence must be finite and between 0 and 1")
    return confidence
